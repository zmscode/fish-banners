#!/usr/bin/env -S uv run --quiet --script
# /// script
# requires-python = ">=3.11"
# dependencies = ["numpy", "pillow"]
# ///
"""Animated image banners for the fish greeting.

Each other module in this folder (not starting with "_") is a banner:

    FRAMES = 36                        # frames per seamless loop (optional)
    def prepare(W, H, seed): ...       # slow, once: anything that stays fixed
    def frame(state, phase): ...       # fast, per frame: H×W×4 uint8 RGBA
    POST = ["dither"]                  # effects from _post.py, in order (optional)

Each greeting picks a banner at random and shows it inline with the Kitty
graphics protocol (Ghostty, kitty), transparent so the terminal's own
background shows through.

The image sits in Unicode placeholder cells, so it scrolls like text. A
detached animator then keeps replacing the image data behind the same id
with the next frame of the loop. It writes nothing but graphics commands,
so it never disturbs the prompt.

Rendering a banner's frames takes a few seconds, so each greeting plays one
rendered ahead of time and then renders a replacement in the background.

usage: run.py ROWS STATEFILE    show a banner; write "pid id" to STATEFILE
"""

from __future__ import annotations

import base64
import fcntl
import functools
import importlib
import os
import random
import shutil
import signal
import struct
import sys
import termios
import time
import zlib
from collections import deque
from collections.abc import Callable, Iterable, Iterator
from pathlib import Path
from typing import TYPE_CHECKING, Any, NoReturn, Protocol, cast

if TYPE_CHECKING:
    from types import FrameType

    from _common import Array  # numpy, which plays from the cache never import

HERE = Path(__file__).resolve().parent
CACHE = Path(os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache")) / "fish-banners"

FPS = 12
MAX_SECONDS = 600  # animation stops after this even if never dismissed

DIACRITICS = [
    chr(int(c, 16))
    for c in [
        "0305",
        "030D",
        "030E",
        "0310",
        "0312",
        "033D",
        "033E",
        "033F",
        "0346",
        "034A",
        "034B",
        "034C",
        "0350",
        "0351",
        "0352",
        "0357",
        "035B",
        "0363",
        "0364",
        "0365",
        "0366",
        "0367",
        "0368",
        "0369",
        "036A",
        "036B",
        "036C",
        "036D",
        "036E",
        "036F",
    ]
]
PLACEHOLDER = "\U0010eeee"


class Banner(Protocol):
    """What a banner module provides (FRAMES and POST are optional)."""

    def prepare(self, W: int, H: int, seed: int) -> Any: ...
    def frame(self, state: Any, phase: float) -> Array: ...


def banners() -> list[str]:
    return sorted(
        p.stem
        for p in HERE.glob("*.py")
        if not p.stem.startswith("_") and p.stem != "run"
    )


def load(name: str) -> Banner:
    sys.path.insert(0, str(HERE))
    return cast(Banner, importlib.import_module(name))


def frame_count(mod: Banner) -> int:
    """Frames per loop."""
    return int(getattr(mod, "FRAMES", 36))


def effects(mod: Banner) -> list[str]:
    """The banner's post-processing effects, in order."""
    return [str(n) for n in getattr(mod, "POST", ())]


def preroll(mod: Banner) -> int:
    """Frames an effect pipeline needs to look back over before its first
    frame looks right (trails need the frames before them)."""
    names = effects(mod)
    if not names:
        return 0
    import _post

    return sum(_post.HISTORY[n][1] - 1 for n in names if n in _post.HISTORY)


def render_sequence(
    mod: Banner, state: Any, phases: Iterable[float]
) -> Iterator[Array]:
    """Frames at successive phases, with the banner's effects applied. Effects
    that use history see the frames that came before in this sequence."""
    import _post

    names = effects(mod)
    history: dict[int, deque[Array]] = {}
    for phase in phases:
        img = mod.frame(state, phase)
        for i, name in enumerate(names):
            if name in _post.HISTORY:
                fn, depth = _post.HISTORY[name]
                recent = history.setdefault(i, deque(maxlen=depth))
                recent.append(img)
                img = fn(list(recent), phase)
            else:
                img = _post.EFFECTS[name](img, phase)
        yield img


def render(mod: Banner, state: Any, phase: float) -> Array:
    """One frame of a banner at phase, effects and all."""
    frames, k = frame_count(mod), preroll(mod)
    phases = [(phase - j / frames) % 1.0 for j in range(k, -1, -1)]
    *_, img = render_sequence(mod, state, phases)
    return img


def render_frames(mod: Banner, state: Any, out_dir: Path) -> None:
    """Write every frame of the loop as zlib-compressed RGBA, then mark ready.
    History effects are pre-rolled from the end of the loop, so frame 0
    follows on seamlessly from the last."""
    frames, k = frame_count(mod), preroll(mod)
    out_dir.mkdir(parents=True, exist_ok=True)
    phases = [(n / frames) % 1.0 for n in range(-k, frames)]
    for n, img in enumerate(render_sequence(mod, state, phases), start=-k):
        if n < 0:
            continue
        path = out_dir / f"{n:03}.z"
        tmp = path.with_suffix(".tmp")
        tmp.write_bytes(zlib.compress(img.tobytes(), 3))
        tmp.replace(path)
    (out_dir / "ready").touch()


# ── terminal output ──────────────────────────────────────────────────────────


def apc(head: str, payload: bytes = b"") -> bytes:
    return b"\x1b_G" + head.encode() + b";" + payload + b"\x1b\\"


def transmit_direct(
    data: bytes, W: int, H: int, image_id: int, cols: int, rows: int
) -> bytes:
    """Send compressed RGBA inline and create a virtual placement for it."""
    data = base64.standard_b64encode(data)
    chunks = [data[i : i + 4096] for i in range(0, len(data), 4096)]
    out: list[bytes] = []
    for n, chunk in enumerate(chunks):
        more = int(n < len(chunks) - 1)
        head = (
            f"a=T,U=1,f=32,o=z,s={W},v={H},i={image_id},c={cols},r={rows},q=2,m={more}"
            if n == 0
            else f"m={more}"
        )
        out.append(apc(head, chunk))
    return b"".join(out)


def transmit_file(
    path: Path, W: int, H: int, image_id: int, cols: int, rows: int
) -> bytes:
    """Replace the image behind image_id with a frame the terminal reads from disk."""
    return apc(
        f"a=T,U=1,t=f,f=32,o=z,s={W},v={H},i={image_id},c={cols},r={rows},q=2",
        base64.standard_b64encode(str(path).encode()),
    )


def placeholders(image_id: int, cols: int, rows: int) -> bytes:
    """Text cells that the terminal fills with the image. The first cell of
    each row names its row and column; the rest of the row is inferred."""
    r, g, b = image_id >> 16 & 255, image_id >> 8 & 255, image_id & 255
    lines = [
        f"\x1b[38;2;{r};{g};{b}m"
        + PLACEHOLDER
        + DIACRITICS[y]
        + DIACRITICS[0]
        + PLACEHOLDER * (cols - 1)
        + "\x1b[39m"
        for y in range(rows)
    ]
    return ("\r\n".join(lines) + "\r\n").encode()


def winsize() -> tuple[int, int, int, int] | None:
    """(columns, rows, width px, height px) of the terminal, if it says."""
    for fd in (sys.stdout.fileno(), sys.stdin.fileno()):
        try:
            r, c, xp, yp = struct.unpack(
                "HHHH", fcntl.ioctl(fd, termios.TIOCGWINSZ, b"\0" * 8)
            )
            if c and xp and yp:
                return c, r, xp, yp
        except OSError:
            pass
    return None


# ── processes ────────────────────────────────────────────────────────────────


def log(msg: str) -> None:
    with open(CACHE / "log.txt", "a") as f:
        f.write(time.strftime("%Y-%m-%d %H:%M:%S ") + msg + "\n")


def log_exception(doing: str) -> None:
    import traceback

    log(f"error while {doing}:\n{traceback.format_exc()}")


def detach() -> int:
    """Fork a fully detached grandchild. Returns 0 in the grandchild and the
    grandchild's pid in the caller."""
    r, w = os.pipe()
    if pid := os.fork():
        os.close(w)
        os.waitpid(pid, 0)
        grandchild = int(os.read(r, 32) or 0)
        os.close(r)
        return grandchild
    os.close(r)
    os.setsid()
    if os.fork():
        os._exit(0)
    os.write(w, str(os.getpid()).encode())
    os.close(w)
    return 0


def animate(
    tty: int,
    d: Path,
    frames: int,
    W: int,
    H: int,
    image_id: int,
    cols: int,
    rows: int,
    finish: Callable[[], None] | None = None,
) -> NoReturn:
    """Cycle the frames in d until killed, the terminal closes, or time runs out."""

    def stop(signum: int, frame: FrameType | None) -> NoReturn:
        raise SystemExit

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGHUP, stop)
    why, n = "timed out", 1
    try:
        if finish:
            finish()
        start = time.monotonic()
        while time.monotonic() - start < MAX_SECONDS:
            os.write(
                tty, transmit_file(d / f"{n % frames:03}.z", W, H, image_id, cols, rows)
            )
            n += 1
            time.sleep(max(0.0, start + n / FPS - time.monotonic()))
    except SystemExit:
        why = "dismissed"
    except OSError as e:
        why = f"terminal gone ({e.strerror})"
    except Exception:
        why = "crashed"
        log_exception(f"animating {d.name}")
    finally:
        log(f"{d.name}: stopped after {n - 1} frames, {why}")
        shutil.rmtree(d, ignore_errors=True)
        os._exit(0)


def prerender(size: str, W: int, H: int) -> NoReturn:
    """Render one landscape ahead for every banner that has none ready."""
    devnull = os.open(os.devnull, os.O_RDWR)
    for fd in (0, 1, 2):
        os.dup2(devnull, fd)
    try:
        # One pre-renderer at a time; if another window's is already
        # running, it will cover this one's needs too.
        with open(CACHE / "prerender.lock", "w") as lock:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError:
                os._exit(0)
            for name in banners():
                key_dir = CACHE / name / size
                if any(key_dir.glob("*/ready")):
                    continue
                key_dir.mkdir(parents=True, exist_ok=True)
                seed = int.from_bytes(os.urandom(4))
                tmp = key_dir / f".{seed:08x}"
                mod = load(name)
                render_frames(mod, mod.prepare(W, H, seed), tmp)
                tmp.rename(key_dir / f"{seed:08x}")
    except Exception:
        log_exception("pre-rendering")
    finally:
        os._exit(0)


def show(rows: int, state_file: str) -> None:
    ws = winsize()
    if not ws or rows > len(DIACRITICS):
        sys.exit(1)  # caller falls back
    cols, term_rows, xp, yp = ws
    W, H = xp, round(yp / term_rows * rows)
    size = f"{cols}x{rows}-{W}x{H}"

    # Animators remove what they play when they finish; sweep up any that
    # were killed outright.
    for old in CACHE.glob("playing-*"):
        if time.time() - old.stat().st_mtime > MAX_SECONDS * 2:
            shutil.rmtree(old, ignore_errors=True)

    # Pick at random among the banners ready to play; if none are, pick any
    # and render it now.
    names = banners()
    ready = {
        n: sorted(p.parent for p in (CACHE / n / size).glob("*/ready")) for n in names
    }
    ready = {n: r for n, r in ready.items() if r}
    name = random.choice(sorted(ready) or names)
    mod = load(name)
    frames = frame_count(mod)
    finish: Callable[[], None] | None = None
    state: Any = None
    if name in ready:
        src = ready[name][0]
        first = (src / "000.z").read_bytes()
    else:
        seed = int.from_bytes(os.urandom(4))
        src = CACHE / name / size / f"{seed:08x}"
        state = mod.prepare(W, H, seed)
        first = zlib.compress(render(mod, state, 0).tobytes(), 3)
    # Claim it so another window can't play it at the same time.
    d = CACHE / f"playing-{name}-{src.name}-{os.getpid()}"
    if src.exists():
        src.rename(d)
    else:
        # Nothing was ready: the animator renders the rest of the frames first.
        finish = functools.partial(render_frames, mod, state, d)

    image_id = int.from_bytes(os.urandom(3)) | 0x010000  # 24-bit, never 0
    out = sys.stdout.buffer
    out.write(transmit_direct(first, W, H, image_id, cols, rows))
    out.write(placeholders(image_id, cols, rows))
    out.flush()

    tty = os.dup(sys.stdout.fileno())
    pid = detach()
    if not pid:
        animate(tty, d, frames, W, H, image_id, cols, rows, finish)
    os.close(tty)
    if not detach():
        prerender(size, W, H)

    # Tell fish which process to stop and which image to delete.
    Path(state_file).write_text(f"{pid} {image_id}\n")


def main() -> None:
    args = sys.argv[1:]
    show(int(args[0]) if args else 10, args[1] if len(args) > 1 else os.devnull)


if __name__ == "__main__":
    main()
