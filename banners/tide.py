"""Contour Tide: an ocean of particles rolling in waves towards you, with
light gathering on the crests and fading into the distance.

Inspired by https://pro.reactbits.dev/docs/components/contour-tide
"""

from dataclasses import dataclass

import numpy as np

from _common import Array, blur, gradient, pixelate, rgba, splat

FRAMES = 48  # frames per loop; every wave travels whole cycles
POST = ["scanlines"]  # post-processing effects from _post.py, in order
HORIZON = 0.1  # height of the horizon, from the top
CHOP = 0.8  # how sharply crests peak and bunch up
PIXEL = 5  # size of each water pixel, in screen pixels
DENSITY = 0.9  # particles per (big) pixel

# Every 0x96f colour, banded across the sea by distance and rolling in
# towards you once per loop. Crests catch a paler, brighter version.
WATER = (
    (0.0, (0xB3, 0xE0, 0x3A)),
    (0.2, (0x00, 0xCD, 0xE8)),
    (0.4, (0xA3, 0x92, 0xE8)),
    (0.6, (0xFF, 0x66, 0x6D)),
    (0.8, (0xFF, 0xC7, 0x39)),
    (1.0, (0xB3, 0xE0, 0x3A)),
)
WHITE = np.array((0xFC, 0xFC, 0xFA), np.float32)
BANDS = 1.6  # times the palette repeats from shore to horizon


@dataclass
class Waves:
    """A handful of Gerstner swells, one entry per swell."""

    dx: Array  # direction of travel
    dz: Array
    wavelength: Array
    amp: Array
    cycles: Array  # whole cycles travelled per loop
    offset: Array


@dataclass
class State:
    """Particles on a calm sea, and the swells that will move them."""

    W: int  # simulation grid, in big pixels
    H: int
    W_full: int  # output size, in screen pixels
    H_full: int
    f: float  # focal length, in grid pixels
    hy: float  # horizon row
    x: Array  # (n,) particle positions on the calm sea
    z: Array
    waves: Waves
    size: Array  # (n,) particle brightness


def prepare(W_full: int, H_full: int, seed: int) -> State:
    rng = np.random.default_rng(seed)
    # Simulate on a coarse grid of big pixels.
    W, H = -(-W_full // PIXEL), -(-H_full // PIXEL)
    f = H * 1.4  # focal length in pixels
    hy = H * HORIZON

    # Scatter particles evenly over the screen below the horizon (and a
    # little beyond the bottom edge for waves to lift into view), then find
    # where each sits on the calm sea.
    n = int(W * H * DENSITY)
    # Wider than the screen, so waves never push the sea away from the edges.
    sx = (rng.random(n, dtype=np.float32) * 1.3 - 0.15) * W
    sy = hy + 2 + rng.random(n, dtype=np.float32) * (H * 1.15 - hy)
    z = f / (sy - hy)  # camera 1 unit above the sea
    x = (sx - W / 2) * z / f

    # A few swells rolling roughly towards the viewer, longest first.
    k = 5
    angle = rng.uniform(-0.45, 0.45, k)
    wavelength = 2.2 * 0.62 ** np.arange(k) * rng.uniform(0.85, 1.15, k)
    amp = 0.09 * 0.6 ** np.arange(k)
    waves = Waves(
        dx=np.sin(angle),
        dz=np.cos(angle),
        wavelength=wavelength,
        amp=amp,
        cycles=np.maximum(1, np.round(3 / np.sqrt(wavelength))).astype(int),
        offset=rng.random(k),
    )
    return State(
        W=W,
        H=H,
        W_full=W_full,
        H_full=H_full,
        f=f,
        hy=hy,
        x=x,
        z=z,
        waves=waves,
        size=rng.uniform(0.6, 1.4, n).astype(np.float32),
    )


def frame(S: State, phase: float) -> Array:
    x, z, w = S.x, S.z, S.waves
    h = np.zeros_like(x)
    gx, gz = x.copy(), z.copy()
    for i in range(len(w.amp)):
        # Gerstner waves: points circle as a crest passes, bunching at the tops.
        # Whole cycles per loop keep it seamless.
        arg = (
            2
            * np.pi
            * (
                (w.dx[i] * x + w.dz[i] * z) / w.wavelength[i]
                + w.cycles[i] * phase
                + w.offset[i]
            )
        )
        h += w.amp[i] * np.cos(arg)
        push = CHOP * w.amp[i] * np.sin(arg)
        gx -= w.dx[i] * push
        gz -= w.dz[i] * push
    gz = np.maximum(gz, 0.2)

    sx = S.W / 2 + S.f * gx / gz
    sy = S.hy + S.f * (1 - h) / gz

    crest = np.clip(h / w.amp.sum() * 1.3, 0, 1)
    fog = np.exp(-gz * 0.09)
    inten = (0.3 + 1.1 * crest**2) * fog * S.size
    # Bands of colour ride in with the swell: a full palette cycle per loop.
    depth = np.clip(gz / 10, 0, 1) ** 0.7
    colour = gradient((depth * BANDS + phase) % 1.0, WATER)
    lift = (crest**2 * 0.55)[:, None]
    colour = colour * (1 - lift) + WHITE * lift

    A, C = splat(sx, sy, inten, colour, S.W, S.H, smooth=False)
    glow = blur(A, 1)
    glow_c = np.stack([blur(C[..., c], 1) for c in range(3)], -1)
    a = np.clip(A * 0.8 + glow * 0.5, 0, 1)
    rgb = (C + glow_c * 0.5) / np.maximum(A + glow * 0.5, 1e-6)[..., None]
    return pixelate(rgba(rgb, a), PIXEL, S.W_full, S.H_full)
