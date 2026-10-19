#!/usr/bin/env python3
"""Text-only fallback banner, for terminals that can't show images (or when
the image banners can't run).

Renders a random fractal landscape as contour lines in perspective, drawn
with braille dots (2×4 per cell) in 24-bit colour: the same 0x96f height
colours as the topography banner, still rather than animated. Hidden
surfaces are removed with a floating-horizon pass, front to back.

Standard library only, and runs on macOS's system Python (3.9), so it works
even without uv or numpy.

usage: _text.py [rows] [seed]
"""

from __future__ import annotations

import math
import random
import shutil
import sys

ROWS = int(sys.argv[1]) if len(sys.argv) > 1 else 12
SEED = int(sys.argv[2]) if len(sys.argv) > 2 else random.randrange(1 << 30)
COLS = shutil.get_terminal_size((100, 24)).columns

LEVELS = 13  # contour lines between lowest and highest point
AMP = 0.55  # peak height, as a fraction of the banner height

# Hypsometric tint in 0x96f colours, as in topography.py: blue lowlands →
# green → yellow → red peaks.
STOPS = (
    (0.00, (0x00, 0xCD, 0xE8)),
    (0.35, (0xB3, 0xE0, 0x3A)),
    (0.68, (0xFF, 0xC7, 0x39)),
    (1.00, (0xFF, 0x66, 0x6D)),
)

DW, DH = COLS * 2, ROWS * 4  # dot canvas
NZ = DH * 3  # depth samples, denser than dot rows

rng = random.Random(SEED)
perm = list(range(256))
rng.shuffle(perm)
perm += perm
grad = [rng.random() for _ in range(256)]


def value_noise(x: float, y: float) -> float:
    xi, yi = math.floor(x), math.floor(y)
    xf, yf = x - xi, y - yi
    xi &= 255
    yi &= 255
    u = xf * xf * (3 - 2 * xf)
    v = yf * yf * (3 - 2 * yf)
    a = grad[perm[perm[xi] + yi]]
    b = grad[perm[perm[xi + 1] + yi]]
    c = grad[perm[perm[xi] + yi + 1]]
    d = grad[perm[perm[xi + 1] + yi + 1]]
    return a + (b - a) * u + (c - a) * v + (a - b - c + d) * u * v


def height(x: float, z: float) -> float:
    total, amp, freq = 0.0, 0.55, 1.0
    for _ in range(3):
        total += value_noise(x * freq, z * freq) * amp
        amp *= 0.5
        freq *= 2.0
    # Flood the lowlands so peaks rise out of empty dark plains.
    h = max(0.0, total - 0.40) / 0.55
    return h**1.4


def colour(h: float, fade: float) -> tuple[int, int, int]:
    """Height colour for h (0–1), dimmed with distance."""
    (t0, c0), (t1, c1) = STOPS[-2], STOPS[-1]
    for lo, hi in zip(STOPS, STOPS[1:]):
        if h <= hi[0]:
            (t0, c0), (t1, c1) = lo, hi
            break
    f = (h - t0) / (t1 - t0)
    dim = 0.4 + 0.6 * fade

    def mix(i: int) -> int:
        return int((c0[i] + (c1[i] - c0[i]) * f) * dim)

    return mix(0), mix(1), mix(2)


# World: x spans the terminal width; z runs from near (0) to far (1).
aspect = DW / (DH * 2.2)
sx, sz = 2.0, 2.6
H = [
    [height(x / DW * sx * aspect, z / NZ * sz + 7.3) for x in range(DW + 1)]
    for z in range(NZ + 1)
]
peak = max(max(row) for row in H) or 1

horizon: list[float] = [DH] * DW  # highest row drawn so far, per column
dots: list[list[tuple[float, float] | None]] = [
    [None] * DW for _ in range(DH)
]  # (height, fade) per lit dot

for z in range(NZ):
    depth = z / NZ
    # Mild perspective: near rows spread out, far rows bunch up at the top.
    base = (DH - 1) - (DH * 0.95) * (1 - (1 - depth) ** 1.25)
    lift = DH * AMP * (1 - 0.45 * depth)
    fade = 1 - 0.75 * depth
    row, nxt = H[z], H[z + 1]
    for x in range(DW):
        h = row[x]
        y: float = base - h * lift
        if y >= horizon[x]:
            continue
        horizon[x] = y
        lvl = int(h * LEVELS)
        if h > 0 and (lvl != int(row[x + 1] * LEVELS) or lvl != int(nxt[x] * LEVELS)):
            yi = round(y)
            if 0 <= yi < DH:
                lit = dots[yi][x]
                if lit is None or lit[1] < fade:
                    dots[yi][x] = (h / peak, fade)

BITS = ((0x01, 0x08), (0x02, 0x10), (0x04, 0x20), (0x40, 0x80))
out: list[str] = []
for r in range(ROWS):
    line: list[str] = []
    last: tuple[int, int, int] | None = None
    for c in range(COLS):
        mask = 0
        best: tuple[float, float] | None = None  # nearest lit dot in the cell
        for dy in range(4):
            drow = dots[r * 4 + dy]
            for dx in range(2):
                d = drow[c * 2 + dx]
                if d:
                    mask |= BITS[dy][dx]
                    if best is None or d[1] > best[1]:
                        best = d
        if best is None:  # no dots lit
            line.append(" ")
            continue
        rgb = colour(*best)
        if rgb != last:
            line.append(f"\x1b[38;2;{rgb[0]};{rgb[1]};{rgb[2]}m")
            last = rgb
        line.append(chr(0x2800 + mask))
    out.append("".join(line).rstrip() + "\x1b[0m")

sys.stdout.write("\n".join(out) + "\n")
