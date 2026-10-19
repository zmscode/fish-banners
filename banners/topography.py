"""Topography: a fractal landscape in perspective, drawn as glowing contour
lines coloured by height, flowing downhill.

Inspired by https://www.reactbits.dev/backgrounds/topography
"""

from dataclasses import dataclass

import numpy as np

from _common import Array, blur, fbm_noise, gradient, rgba

FRAMES = 36  # frames per loop; each loop moves lines one level
LEVELS = 18  # contour lines between plain and highest peak
SEA = 0.36  # noise level flooded into empty plains
AMP = 0.75  # peak height, as a fraction of the image height

# Hypsometric tint in 0x96f colours, by height: blue lowlands → green →
# yellow → red peaks.
STOPS = (
    (0.00, (0x00, 0xCD, 0xE8)),
    (0.35, (0xB3, 0xE0, 0x3A)),
    (0.68, (0xFF, 0xC7, 0x39)),
    (1.00, (0xFF, 0x66, 0x6D)),
)


@dataclass
class State:
    """A landscape, as seen from the camera, ready to animate."""

    F: Array  # (H, W) contour coordinate at each pixel
    D: Array  # (H, W) depth, 0 near … 1 far
    fw: Array  # (H, W) screen-space change in F per pixel
    mask: Array  # (H, W) where lines may show
    bright: Array  # (H, W) line brightness
    rgb: Array  # (H, W, 3) line colour
    px: float  # line width scale for the display's density


def prepare(W: int, H: int, seed: int) -> State:
    """Everything about a landscape that stays fixed while it animates."""
    fbm = fbm_noise(np.random.default_rng(seed))

    # World samples: one column per pixel, depth sampled ~3× denser than rows.
    NZ = H * 3
    depth = np.linspace(0, 1, NZ, dtype=np.float32)[:, None]
    sx = np.linspace(-0.5, 0.5, W, dtype=np.float32)[None, :]
    span = W / H * 0.5
    wx = sx * span * (1 + 1.6 * depth)  # far rows see a wider strip
    wz = depth * 2.6

    # Warp the domain a little for more organic, river-like ridges.
    wx2 = wx + 0.25 * (fbm(wx * 0.6 + 11, wz * 0.6, 2) - 0.5)
    noise = fbm(wx2, wz)
    t = (noise - SEA) / (1 - SEA)  # <0 under the plain
    height = np.where(t > 0, np.maximum(t, 0) ** 1.35, t)
    lift = np.maximum(height, 0)

    # Project: the plain recedes upward, peaks rise above it. The near edge
    # starts below the frame, so no cliff face shows.
    base = H * (1 + AMP * 0.6) - H * (1 + AMP * 0.6 - 0.03) * (1 - (1 - depth) ** 1.3)
    Y = base - lift * H * AMP * (1 - 0.4 * depth)

    # Floating horizon: drawing front to back, a sample shows only above
    # everything already drawn in its column.
    horizon = np.minimum.accumulate(Y, axis=0)

    # For each pixel, find the (fractional) depth sample visible through it.
    rows = np.arange(H, dtype=np.float32) + 0.5
    raw = np.full((H, W), np.nan, dtype=np.float32)  # contour coordinate
    D = np.zeros((H, W), dtype=np.float32)  # depth
    f_world = height * LEVELS
    for x in range(W):
        hz = horizon[:, x]
        i = np.searchsorted(-hz, -rows)  # first sample above row
        ok = (i > 0) & (i < NZ)
        i0 = np.clip(i - 1, 0, NZ - 1)
        i1 = np.clip(i, 0, NZ - 1)
        a, b = hz[i0], hz[i1]
        w = np.where(a > b, (a - rows) / np.maximum(a - b, 1e-6), 0)
        f = f_world[i0, x] * (1 - w) + f_world[i1, x] * w
        raw[ok, x] = f[ok]
        D[ok, x] = depth[i0[ok], 0]

    sky = np.isnan(raw)
    F = np.nan_to_num(raw, nan=-9.0)

    # Screen-space gradient of the contour coordinate, for uniform line width.
    gy, gx = np.gradient(F)
    fw = np.abs(gx) + np.abs(gy) + 1e-6
    mask = np.clip((0.5 - fw) / 0.3, 0, 1)  # fade where too dense
    mask *= np.clip(F + 0.5, 0, 1)  # nothing under the plain
    mask[sky] = 0

    lvl = np.clip(F / LEVELS, 0, 1)
    near = 1 - D
    bright = (0.55 + 0.45 * lvl**0.8) * (0.3 + 0.7 * near**1.2)
    yy = np.arange(H, dtype=np.float32)[:, None] / H
    bright *= np.clip(yy / 0.18, 0, 1) ** 1.5  # fade into the distance

    # Stretch to this landscape's own range, so the tallest peak reaches red.
    tint = np.clip(F / max(F[~sky].max(initial=1), 1), 0, 1)

    return State(
        F=F,
        D=D,
        fw=fw,
        mask=mask,
        bright=bright,
        rgb=gradient(tint, STOPS),
        px=max(1.0, H / 220),
    )


def frame(L: State, phase: float) -> Array:
    """RGBA image of landscape L at loop phase 0 ≤ phase < 1."""
    F, fw, px = L.F, L.fw, L.px
    # Shader-style anti-aliased lines: distance to the nearest level divided
    # by the screen gradient. Shifting by phase slides every line downhill;
    # after a full loop each line has taken its neighbour's place.
    dist = np.abs((F + phase + 0.5) % 1.0 - 0.5) / fw
    line = np.clip(px * 0.65 - dist + 0.5, 0, 1) * L.mask
    # A soft swell of light rolls from the horizon towards you each loop.
    swell = 0.82 + 0.18 * np.cos(2 * np.pi * (phase + L.D * 1.5))
    alpha = line * L.bright * swell

    glow = blur(alpha, max(2, int(px * 3)))
    a = np.clip(alpha * 1.15 + glow * 1.8, 0, 1)
    # Glow halos lean darker than the line cores.
    core = np.clip(alpha / np.maximum(a, 1e-6), 0, 1)[..., None]
    return rgba(L.rgb * (0.7 + 0.3 * core), a)
