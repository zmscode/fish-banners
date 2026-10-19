"""Glyph Ember: ASCII fire licking up from a white-hot bed of glyphs, with
sparks drifting up and faint smoke above the flame tips.

Inspired by https://pro.reactbits.dev/docs/components/glyph-ember
"""

from collections.abc import Callable
from dataclasses import dataclass

import numpy as np

from _common import Array, blur, glyph_atlas, gradient, rgba, stamp

FRAMES = 48  # frames per loop
HEIGHT = 0.8  # how high the flames reach, as a share of the banner
WIND = 0.25  # sideways lean of the rising heat
CHARSET = " .'`^\",:;Il!i><~+_-?][}{1)(|/tfjrxnuvczXYUJCLQ0OZmwqpdbkhao*#MW&8%B@$"
SPARKS = "*+·"

# 0x96f colours: each stretch of flame burns in its own theme colour, the
# colours drift slowly sideways, and everything runs white-hot at the bed.
FLAME = (
    (0.0, (0xFF, 0x66, 0x6D)),
    (0.2, (0xFF, 0xC7, 0x39)),
    (0.4, (0xB3, 0xE0, 0x3A)),
    (0.6, (0x00, 0xCD, 0xE8)),
    (0.8, (0xA3, 0x92, 0xE8)),
    (1.0, (0xFF, 0x66, 0x6D)),
)
WHITE = np.array((0xFC, 0xFC, 0xFA), np.float32)
SMOKE = np.array((0xA3, 0x92, 0xE8), np.float32) * 0.8
SPARK_COLOURS = ((0xFF, 0xC7, 0x39), (0xFF, 0x7E, 0x83), (0xFC, 0xFC, 0xFA))


# A 2D noise field: values for arrays of x and y.
Field = Callable[[Array, Array], Array]


def periodic_fbm(
    rng: np.random.Generator, width: float, period: int, octaves: int = 3
) -> Field:
    """fbm(x, y) noise that repeats every `period` units in y, so sliding it
    by exactly one period loops seamlessly."""
    layers: list[tuple[int, Array]] = []
    for o in range(octaves):
        f = 2**o
        layers.append(
            (f, rng.random((period * f, int(width * f) + 2), dtype=np.float32))
        )

    def fbm(x: Array, y: Array) -> Array:
        total: Array | float = 0.0
        amp, norm = 1.0, 0.0
        for f, lat in layers:
            px, py = x * f, y * f
            x0, y0 = np.floor(px), np.floor(py)
            fx, fy = px - x0, py - y0
            fx = fx * fx * (3 - 2 * fx)
            fy = fy * fy * (3 - 2 * fy)
            xi = x0.astype(np.int64) % lat.shape[1]
            yi = y0.astype(np.int64) % lat.shape[0]
            xj, yj = (xi + 1) % lat.shape[1], (yi + 1) % lat.shape[0]
            a, b, c, d = lat[yi, xi], lat[yi, xj], lat[yj, xi], lat[yj, xj]
            total = (
                total + ((a + (b - a) * fx) * (1 - fy) + (c + (d - c) * fx) * fy) * amp
            )
            norm += amp
            amp *= 0.5
        return np.asarray(total / norm)

    return fbm


@dataclass
class Sparks:
    """Sparks rising from the bed, one entry per spark."""

    x: Array  # starting column
    off: Array  # where in its rise each spark starts the loop
    rate: Array  # whole rises per loop
    sway: Array
    glyph: Array  # index into SPARKS
    colour: Array  # (n, 3)


@dataclass
class State:
    """The fire's glyphs, fields and sparks."""

    W: int
    H: int
    R: int  # glyph rows
    C: int  # glyph columns
    gh: int  # glyph cell height
    atlas: Array  # (n, gh, gw) glyph masks
    ramp: Array  # glyph indices from least to most ink
    up: Array  # (R, 1) rows above the bed
    col: Array  # (1, C) column index
    turb: Field  # rising turbulence
    tongue: Field  # flame height per column
    smoke: Field
    flicker: Array  # (8, R, C) shimmer per frame
    sparks: Sparks


def prepare(W: int, H: int, seed: int) -> State:
    rng = np.random.default_rng(seed)
    gh = max(12, round(H / 15))
    gw = max(7, round(gh * 0.62))
    R, C = -(-H // gh), -(-W // gw)

    atlas = glyph_atlas(CHARSET + SPARKS, gw, gh)
    # Sort the ramp by how much ink each glyph uses, so heat maps to density.
    ink = atlas[: len(CHARSET)].sum(axis=(1, 2))
    ramp = np.argsort(ink, kind="stable")

    up = (R - 1 - np.arange(R))[:, None].astype(np.float32)  # rows above the bed
    col = np.arange(C, dtype=np.float32)[None, :]

    n = max(8, C // 7)
    sparks = Sparks(
        x=rng.random(n) * C,
        off=rng.random(n),
        rate=rng.integers(1, 3, n),
        sway=rng.random(n) * 2 * np.pi,
        glyph=rng.integers(0, len(SPARKS), n),
        colour=np.array(SPARK_COLOURS, np.float32)[rng.integers(0, 3, n)],
    )

    return State(
        W=W,
        H=H,
        R=R,
        C=C,
        gh=gh,
        atlas=atlas,
        ramp=ramp,
        up=up,
        col=col,
        turb=periodic_fbm(rng, C * 0.2 + 8, 3),
        tongue=periodic_fbm(rng, C * 0.07 + 8, 2, 2),
        smoke=periodic_fbm(rng, C * 0.12 + 8, 2, 2),
        flicker=rng.random((8, R, C), dtype=np.float32),
        sparks=sparks,
    )


def frame(S: State, phase: float) -> Array:
    R, C, up, col = S.R, S.C, S.up, S.col
    n_frame = round(phase * FRAMES)

    # Flame tongues: each column's flame height breathes slowly.
    tongue = S.tongue(col * 0.07, np.full_like(col, phase * 2))
    reach = HEIGHT * R * (0.35 + 1.0 * tongue**1.4)
    # Turbulent heat rises through the flames, leaning with the wind.
    turb = S.turb(col * 0.2 + up * WIND * 0.2, up * 0.22 - phase * 3)
    heat = 1 - up / reach + 0.85 * (turb - 0.5) * (0.6 + up / R)
    heat = np.clip(heat, 0, 1)
    heat[-1] = np.maximum(heat[-1], 0.93)  # white-hot bed
    heat[-2] = np.maximum(heat[-2], 0.75 + 0.2 * turb[-2])

    # Glyphs shimmer between neighbours on the density ramp.
    jitter = (S.flicker[n_frame % 8] - 0.5) * 0.08
    level = np.clip(heat + jitter, 0, 0.999)
    idx = S.ramp[(level * len(S.ramp)).astype(int)]
    inten = np.clip((heat - 0.05) / 0.4, 0, 1) ** 0.8 * (0.55 + 0.45 * heat)
    # Flame colour by column (wrapping round the palette once per loop),
    # deepened at the tips and blending to white-hot towards the bed.
    hue = gradient((S.col / S.C * 1.3 + phase) % 1.0, FLAME)
    hot = np.clip((heat - 0.55) / 0.4, 0, 1)[..., None] ** 1.5
    colour = hue * (0.75 + 0.25 * heat[..., None]) * (1 - hot) + WHITE * hot

    # Faint smoke curls above the flame tips.
    smoke = S.smoke(col * 0.12 + up * 0.05, up * 0.12 - phase * 2)
    smoky = (heat < 0.05) & (up > reach * 0.5) & (smoke > 0.6)
    idx = np.where(smoky, S.ramp[3 + (smoke * 6).astype(int)], idx)
    inten = np.where(smoky, (smoke - 0.6) * 1.2, inten)
    colour = np.where(smoky[..., None], SMOKE, colour)

    # Sparks rise from the bed a whole number of times per loop.
    sp = S.sparks
    t = (sp.off + sp.rate * phase) % 1.0
    sy = (R - 1 - t * (R + 1)).astype(int)
    sx = (sp.x + np.sin(sp.sway + t * 6) * 1.5 + t * WIND * R).astype(int) % C
    ok = (sy >= 0) & (sy < R - 1)
    sy, sx = sy[ok], sx[ok]
    idx[sy, sx] = len(CHARSET) + sp.glyph[ok]
    inten[sy, sx] = 1.1 * (1 - t[ok]) ** 0.5
    colour[sy, sx] = sp.colour[ok]

    alpha, rgb = stamp(S.atlas, idx, colour, inten)
    alpha, rgb = alpha[: S.H, : S.W], rgb[: S.H, : S.W]
    glow = blur(alpha, max(2, S.gh // 3))
    glow_c = np.stack(
        [blur(alpha * rgb[..., c], max(2, S.gh // 3)) for c in range(3)], -1
    )
    a = np.clip(alpha + glow * 1.5, 0, 1)
    out = (alpha[..., None] * rgb + glow_c * 1.5) / np.maximum(
        alpha + glow * 1.5, 1e-6
    )[..., None]
    return rgba(out, a)
