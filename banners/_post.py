"""Post-processing effects for banner frames.

Each effect takes an H×W×4 uint8 RGBA frame (straight alpha, transparent
background) and the loop phase, and returns a new frame. Effects that look
back in time (trails) take the previous frames too.
"""

from collections.abc import Callable

import numpy as np

from _common import Array, blur

Effect = Callable[[Array, float], Array]
HistoryEffect = Callable[[list[Array], float], Array]

# The 0x96f theme's 16 terminal colours.
PALETTE = np.array(
    [
        (0x26, 0x24, 0x27),
        (0xFF, 0x66, 0x6D),
        (0xB3, 0xE0, 0x3A),
        (0xFF, 0xC7, 0x39),
        (0x00, 0xCD, 0xE8),
        (0xA3, 0x92, 0xE8),
        (0x9D, 0xEA, 0xF6),
        (0xFC, 0xFC, 0xFA),
        (0x54, 0x54, 0x52),
        (0xFF, 0x7E, 0x83),
        (0xBE, 0xE5, 0x5E),
        (0xFF, 0xD0, 0x5E),
        (0x1B, 0xD5, 0xEB),
        (0xB0, 0xA3, 0xEB),
        (0xAC, 0xED, 0xF8),
        (0xFC, 0xFC, 0xFA),
    ],
    np.float32,
)


def split(img: Array) -> tuple[Array, Array]:
    """uint8 RGBA → float colour (0–255) and coverage (0–1)."""
    f = img.astype(np.float32)
    return f[..., :3], f[..., 3] / 255


def join(rgb: Array, a: Array) -> Array:
    """Float colour and coverage → uint8 RGBA."""
    out = np.empty((*a.shape, 4), np.uint8)
    out[..., :3] = np.clip(rgb, 0, 255)
    out[..., 3] = np.clip(a * 255, 0, 255)
    return out


def bloom(img: Array, phase: float, strength: float = 1.4) -> Array:
    """A wide, soft halo of light around everything bright."""
    rgb, a = split(img)
    H = a.shape[0]
    radii = [max(2, int(H * f)) for f in (0.02, 0.05, 0.1)]
    halos = [blur(a, r) for r in radii]
    halo = (halos[0] + halos[1] + halos[2]) / 3
    tints = [np.stack([blur(a * rgb[..., c], r) for c in range(3)], -1) for r in radii]
    halo_c = (tints[0] + tints[1] + tints[2]) / 3
    out_a = np.clip(a + halo * strength, 0, 1)
    out = (a[..., None] * rgb + halo_c * strength) / np.maximum(
        a + halo * strength, 1e-6
    )[..., None]
    return join(out, out_a)


def chromatic(img: Array, phase: float, shift: int | None = None) -> Array:
    """Red and blue fringes pulled apart sideways, like a cheap lens."""
    rgb, a = split(img)
    s = shift or max(2, a.shape[0] // 90)
    pre = rgb * a[..., None]
    r = np.roll(pre[..., 0], -s, axis=1)
    b = np.roll(pre[..., 2], s, axis=1)
    ar, ab = np.roll(a, -s, axis=1), np.roll(a, s, axis=1)
    out_a = np.maximum(a, np.maximum(ar, ab))
    out = np.stack([r, pre[..., 1], b], -1) / np.maximum(out_a, 1e-6)[..., None]
    return join(out, out_a)


def scanlines(img: Array, phase: float) -> Array:
    """CRT scanlines with a gentle vignette towards the edges."""
    rgb, a = split(img)
    H, W = a.shape
    period = max(3, H // 80)
    rows = np.less(np.arange(H, dtype=np.int64) % period, period // 2)
    lines = np.where(rows, 1.0, 0.45)[:, None]
    y = np.linspace(-1, 1, H)[:, None]
    x = np.linspace(-1, 1, W)[None, :]
    vignette = np.clip(1.15 - 0.35 * (x * x) - 0.5 * (y * y), 0, 1)
    return join(rgb * 1.1, a * lines * vignette)


def grain(img: Array, phase: float, amount: float = 0.35) -> Array:
    """Animated film grain: brightness flickers pixel by pixel each frame."""
    rgb, a = split(img)
    rng = np.random.default_rng(int(phase * 1e6))
    noise = rng.standard_normal(a.shape).astype(np.float32)
    return join(
        rgb * (1 + amount * 0.4 * noise[..., None]),
        np.clip(a * (1 + amount * noise), 0, 1),
    )


def tilt_shift(img: Array, phase: float) -> Array:
    """A sharp band through the middle, melting into blur above and below."""
    rgb, a = split(img)
    H = a.shape[0]
    r = max(3, H // 25)
    ba = blur(a, r)
    bc = (
        np.stack([blur(a * rgb[..., c], r) for c in range(3)], -1)
        / np.maximum(ba, 1e-6)[..., None]
    )
    y = np.abs(np.linspace(-1, 1, H))[:, None]
    m = np.clip((y - 0.25) / 0.5, 0, 1)
    out_a = a * (1 - m) + ba * m
    out = rgb * (1 - m)[..., None] + bc * m[..., None]
    return join(out, out_a)


BAYER = (
    np.array([[0, 8, 2, 10], [12, 4, 14, 6], [3, 11, 1, 9], [15, 7, 13, 5]], np.float32)
    + 0.5
) / 16


def dither(img: Array, phase: float, cell: int = 3) -> Array:
    """Ordered dithering down to the 0x96f palette, in chunky pixels."""
    rgb, a = split(img)
    H, W = a.shape
    h, w = -(-H // cell), -(-W // cell)
    pad = ((0, h * cell - H), (0, w * cell - W))
    small_a = np.pad(a, pad).reshape(h, cell, w, cell).mean(axis=(1, 3))
    small_c = (
        np.pad(rgb * a[..., None], (*pad, (0, 0)))
        .reshape(h, cell, w, cell, 3)
        .mean(axis=(1, 3))
    )
    small_c /= np.maximum(small_a, 1e-6)[..., None]
    threshold = np.tile(BAYER, (-(-h // 4), -(-w // 4)))[:h, :w]
    # Coverage becomes on/off pixels; colour snaps to the nearest theme colour.
    on = (small_a > threshold).astype(np.float32)
    jitter = (threshold[..., None] - 0.5) * 48
    d = ((small_c + jitter)[..., None, :] - PALETTE[None, None, 1:]) ** 2
    snapped = PALETTE[1:][d.sum(-1).argmin(-1)]

    def big(x: Array) -> Array:
        return np.repeat(np.repeat(x, cell, 0), cell, 1)[:H, :W]

    return join(big(snapped), big(on))


def glitch(img: Array, phase: float, bands: int = 7) -> Array:
    """Slices of the frame jolt sideways with split colour, changing often."""
    rgb, a = split(img)
    H, W = a.shape
    rng = np.random.default_rng(int(phase * 9) + 11)  # new glitch every few frames
    out_rgb, out_a = rgb.copy(), a.copy()
    for _ in range(bands):
        y0 = rng.integers(0, H)
        h = rng.integers(max(2, H // 40), max(3, H // 8))
        s = rng.integers(-W // 30, W // 30)
        sl = slice(y0, min(H, y0 + h))
        out_rgb[sl] = np.roll(rgb[sl], s, axis=1)
        out_a[sl] = np.roll(a[sl], s, axis=1)
        out_rgb[sl, :, 0] = np.roll(out_rgb[sl, :, 0], s // 3, axis=1)
    return join(out_rgb, out_a)


def soft_focus(img: Array, phase: float) -> Array:
    """A dreamy haze: the frame blended with a blurred copy of itself."""
    rgb, a = split(img)
    r = max(3, a.shape[0] // 40)
    ba = blur(a, r)
    bc = np.stack([blur(a * rgb[..., c], r) for c in range(3)], -1)
    out_a = np.clip(a * 0.7 + ba * 0.9, 0, 1)
    out = (a[..., None] * rgb * 0.7 + bc * 0.9) / np.maximum(a * 0.7 + ba * 0.9, 1e-6)[
        ..., None
    ]
    return join(out, out_a)


def halftone(img: Array, phase: float, cell: int | None = None) -> Array:
    """Print-style halftone: each cell becomes a dot sized by its brightness."""
    rgb, a = split(img)
    H, W = a.shape
    cell = cell or max(4, H // 45)
    h, w = -(-H // cell), -(-W // cell)
    pad = ((0, h * cell - H), (0, w * cell - W))
    small_a = np.pad(a, pad).reshape(h, cell, w, cell).mean(axis=(1, 3))
    small_c = (
        np.pad(rgb * a[..., None], (*pad, (0, 0)))
        .reshape(h, cell, w, cell, 3)
        .mean(axis=(1, 3))
    )
    small_c /= np.maximum(small_a, 1e-6)[..., None]
    yy, xx = np.mgrid[0:cell, 0:cell]
    dist = np.hypot(yy - (cell - 1) / 2, xx - (cell - 1) / 2) / (cell / 2)
    radius = np.sqrt(np.clip(small_a * 1.4, 0, 1))
    dots = np.clip((radius[:, None, :, None] - dist[None, :, None, :]) * cell / 2, 0, 1)
    out_a = dots.reshape(h * cell, w * cell)[:H, :W]
    out = np.repeat(np.repeat(small_c, cell, 0), cell, 1)[:H, :W]
    return join(out, out_a)


def trails(frames: list[Array], phase: float, decay: float = 0.55) -> Array:
    """Motion trails: earlier frames linger and fade behind the current one.
    `frames` runs oldest to newest; the last is the current frame."""
    rgb, acc_a = split(frames[0])
    acc_c = rgb * acc_a[..., None]
    for img in frames[1:]:
        rgb, a = split(img)
        acc_a = acc_a * decay
        acc_c = acc_c * decay
        acc_a = a + acc_a * (1 - a)
        acc_c = rgb * a[..., None] + acc_c * (1 - a)[..., None]
    return join(acc_c / np.maximum(acc_a, 1e-6)[..., None], acc_a)


EFFECTS: dict[str, Effect] = {
    "bloom": bloom,
    "chromatic": chromatic,
    "scanlines": scanlines,
    "grain": grain,
    "tilt-shift": tilt_shift,
    "dither": dither,
    "glitch": glitch,
    "soft-focus": soft_focus,
    "halftone": halftone,
}

# Effects that look back over recent frames: name → (function, frames used).
HISTORY: dict[str, tuple[HistoryEffect, int]] = {
    "trails": (trails, 5),
}
