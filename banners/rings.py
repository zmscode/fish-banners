"""Ring Field: wavy rings of fine glittering dust drifting outward from a
dark centre, seen at an angle so they stretch across the banner.

Inspired by https://pro.reactbits.dev/docs/components/ring-field
"""

from dataclasses import dataclass

import numpy as np

from _common import Array, blur, fbm_noise, gradient, pixelate, rgba, splat

FRAMES = 36  # frames per loop; each loop moves rings one spacing
PIXEL = 5  # size of each dust pixel, in screen pixels
DENSITY = 0.6  # dust particles per (big) pixel

# 0x96f colours by distance from the centre: rings are born red, then cool
# through yellow, green and cyan to lavender as they drift out. Rims and
# sparkles glint white.
STOPS = (
    (0.00, (0xFF, 0x66, 0x6D)),
    (0.22, (0xFF, 0xC7, 0x39)),
    (0.45, (0xB3, 0xE0, 0x3A)),
    (0.70, (0x00, 0xCD, 0xE8)),
    (1.00, (0xA3, 0x92, 0xE8)),
)
WHITE = (0xFC, 0xFC, 0xFA)
RINGS_TO_LAVENDER = 7.5  # ring spacings from the centre to the last stop


@dataclass
class State:
    """A field of dust particles and the rings they light up."""

    W: int  # simulation grid, in big pixels
    H: int
    W_full: int  # output size, in screen pixels
    H_full: int
    x: Array  # (n,) particle positions on the grid
    y: Array
    rw: Array  # (n,) warped ring coordinate
    thick: Array  # (n,) ring thickness there
    life: Array  # (n,) fade-in near the centre
    weight: Array  # (n,) brightness
    twinkle_phase: Array
    twinkle_rate: Array  # whole cycles per loop
    sparkle: Array  # (n,) bool
    colour: Array  # (n, 3)


def prepare(W_full: int, H_full: int, seed: int) -> State:
    rng = np.random.default_rng(seed)
    fbm = fbm_noise(rng)
    # Simulate on a coarse grid of big pixels.
    W, H = -(-W_full // PIXEL), -(-H_full // PIXEL)

    # Dust is scattered evenly; the rings are where it lights up.
    n = int(W * H * DENSITY)
    x = rng.random(n, dtype=np.float32) * W
    y = rng.random(n, dtype=np.float32) * H

    # Ring space: ellipses around a centre a little above the middle, one
    # unit per ring spacing.
    cx = W * rng.uniform(0.35, 0.65)
    cy = H * 0.42
    spacing = max(W / 17, H * 0.32)
    squash = 0.4
    u = (x - cx) / spacing
    v = (y - cy) / (spacing * squash)
    r = np.hypot(u, v)

    # A fixed warp field makes the rings wavy; as they drift through it
    # their shapes keep changing. A second field swells them into folds.
    rw = r + 1.05 * (fbm(u * 0.3 + 3.1, v * 0.28 + 7.7, 3) - 0.5)
    fold = np.clip((fbm(u * 0.45 + 40, v * 0.45 + 13, 3) - 0.42) * 3.2, 0, 1)
    thick = 0.05 + 0.2 * fold**1.6

    # Dark centre, and rings fade in as they are born.
    life = np.clip((rw - 0.7) / 1.3, 0, 1) ** 1.5

    # Per-particle glints: brightness, twinkle, and an occasional sparkle.
    weight = (0.35 + rng.exponential(0.45, n)).astype(np.float32)
    twinkle_phase = rng.random(n, dtype=np.float32)
    twinkle_rate = rng.integers(1, 4, n)  # whole cycles per loop
    sparkle = rng.random(n) < 0.035
    colour = gradient(np.clip((rw - 0.8) / RINGS_TO_LAVENDER, 0, 1), STOPS)
    colour[sparkle] = WHITE
    weight[sparkle] *= 1.6

    return State(
        W=W,
        H=H,
        W_full=W_full,
        H_full=H_full,
        x=x,
        y=y,
        rw=rw,
        thick=thick,
        life=life,
        weight=weight,
        twinkle_phase=twinkle_phase,
        twinkle_rate=twinkle_rate,
        sparkle=sparkle,
        colour=colour,
    )


def frame(S: State, phase: float) -> Array:
    W, H = S.W, S.H
    # Signed offset from the nearest ring centre, in ring spacings; rings
    # move outward as phase grows, and a full loop is one spacing.
    s = (S.rw - phase + 0.5) % 1.0 - 0.5
    th = S.thick
    band = np.exp(-((s / th) ** 2))
    # A crisp bright rim on each ring's outer edge.
    rim = np.exp(-(((s - th * 0.85) / 0.022) ** 2))
    twinkle = 0.65 + 0.35 * np.sin(
        2 * np.pi * (S.twinkle_rate * phase + S.twinkle_phase)
    )
    inten = (band * 0.8 + rim * 0.9 + 0.035) * S.weight * S.life * twinkle

    rim_mix = np.clip(rim * 0.6, 0, 1)[:, None]
    col = S.colour * (1 - rim_mix) + np.array(WHITE, np.float32) * rim_mix
    col = np.where(S.sparkle[:, None], S.colour, col)

    A, C = splat(S.x, S.y, inten, col, W, H, smooth=False)
    glow = blur(A, 1)
    glow_c = np.stack([blur(C[..., c], 1) for c in range(3)], -1)
    a = np.clip(A * 0.9 + glow * 0.5, 0, 1)
    rgb = (C + glow_c * 0.5) / np.maximum(A + glow * 0.5, 1e-6)[..., None]
    return pixelate(rgba(rgb, a), PIXEL, S.W_full, S.H_full)
