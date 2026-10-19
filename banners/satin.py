"""Satin Ridges: flowing satin folds catching soft highlights, drawn in
glowing symbols: each character cell shows the cloth's light there as a
glyph from a density ramp.

Inspired by https://pro.reactbits.dev/docs/components/satin-ridges
"""

from dataclasses import dataclass

import numpy as np

from _common import Array, blur, fbm_noise, glyph_atlas, gradient, rgba, stamp

FRAMES = 72  # frames per loop; the folds flow through whole cycles
POST = ["trails", "soft-focus"]  # post-processing effects from _post.py, in order
ANGLE = -20  # direction the folds run, in degrees from vertical
FOLDS = 5  # folds across the banner
GLOSS = 0.55  # 0 soft broad sheen … 1 crisp thin glints
ROWS = 13  # rows of symbols in the banner
CHARSET = " .,:;-~=+*^%&$#@"  # sorted by ink below, so any order works

# 0x96f colours: the sheen drifts along the cloth through the palette.
SHEEN = (
    (0.0, (0xA3, 0x92, 0xE8)),
    (0.2, (0x00, 0xCD, 0xE8)),
    (0.4, (0xB3, 0xE0, 0x3A)),
    (0.6, (0xFF, 0xC7, 0x39)),
    (0.8, (0xFF, 0x66, 0x6D)),
    (1.0, (0xA3, 0x92, 0xE8)),
)
WHITE = np.array((0xFC, 0xFC, 0xFA), np.float32)


@dataclass
class Glyphs:
    """The grid of symbol cells the cloth is drawn in."""

    gh: int  # cell height
    gw: int  # cell width
    R: int  # rows
    C: int  # columns
    atlas: Array  # (n, gh, gw) glyph masks
    ramp: Array  # glyph indices from least to most ink


@dataclass
class State:
    """The cloth's coordinates and fold fields, and the symbols to draw it in."""

    W: int
    H: int
    u: Array  # (H, W) across-fold coordinate
    v: Array  # (H, W) along-fold coordinate
    meander: Array  # (H, W) how far the folds bend
    span: float  # banner width in banner heights
    detail: Array  # (H, W) small ripples
    offset: float  # where on the palette the sheen starts
    edge: Array  # (1, W) fade at the ends
    g: Glyphs


def prepare(W: int, H: int, seed: int) -> State:
    rng = np.random.default_rng(seed)
    fbm = fbm_noise(rng)
    x = np.arange(W, dtype=np.float32)[None, :] / H
    y = np.arange(H, dtype=np.float32)[:, None] / H
    a = np.radians(ANGLE)
    # Across-fold and along-fold coordinates.
    u = x * np.cos(a) + y * np.sin(a)
    v = -x * np.sin(a) + y * np.cos(a)
    # A slow meander so the folds bend instead of running dead straight.
    meander = fbm(u * 0.35 + 5, v * 0.6 + 2, 3) - 0.5
    return State(
        W=W,
        H=H,
        u=u,
        v=v,
        meander=meander,
        span=W / H,
        detail=fbm(u * 3 + 30, v * 1.5, 2) - 0.5,
        offset=rng.random(),
        # Soft fade at the ends of the banner.
        edge=np.clip(np.minimum(x, x.max() - x) / (x.max() * 0.05), 0, 1),
        g=glyphs(W, H),
    )


def glyphs(W: int, H: int) -> Glyphs:
    gh = max(12, round(H / ROWS))
    gw = max(7, round(gh * 0.62))
    atlas = glyph_atlas(CHARSET, gw, gh)
    # Order the ramp by how much ink each glyph uses.
    ramp = np.argsort(atlas.sum(axis=(1, 2)), kind="stable")
    return Glyphs(gh=gh, gw=gw, R=-(-H // gh), C=-(-W // gw), atlas=atlas, ramp=ramp)


def shade(S: State, phase: float) -> tuple[Array, Array]:
    """Light and colour of the cloth at every pixel."""
    u, v, span = S.u, S.v, S.span
    k = 2 * np.pi * FOLDS / span
    t = 2 * np.pi * phase
    # Height of the cloth: a few fold frequencies, bent by the meander. Each
    # term drifts a whole number of cycles per loop.
    bend = S.meander * 2.2 + 0.25 * np.sin(v * 3 + t)
    h = (
        np.sin(k * (u + bend * 0.25) + t)
        + 0.5 * np.sin(k * 1.9 * (u + bend * 0.18) - 2 * t + 1.3)
        + 0.12 * np.sin(k * 3.7 * u + v * 4 + 3 * t)
        + 0.15 * S.detail * np.sin(k * 5 * u + t)
    )
    # Light it: normals from the height field, a raking key light, and a
    # specular sheen whose sharpness comes from GLOSS.
    gy, gx = np.gradient(h)
    scale = S.H / 7
    nx, ny = -gx * scale, -gy * scale
    nz = np.ones_like(h)
    norm = np.sqrt(nx * nx + ny * ny + nz * nz)
    nx, ny, nz = nx / norm, ny / norm, nz / norm
    light = np.array([0.55, -0.35, 0.76])
    light /= np.linalg.norm(light)
    diffuse = np.clip(nx * light[0] + ny * light[1] + nz * light[2], 0, 1)
    half = light + np.array([0, 0, 1])
    half /= np.linalg.norm(half)
    spec = np.clip(nx * half[0] + ny * half[1] + nz * half[2], 0, 1)
    spec = spec ** (8 + 90 * GLOSS)

    # Crests sit forward and catch light; deep folds sink into shadow.
    crest = np.clip(h / 1.9 * 0.5 + 0.5, 0, 1)
    body = diffuse**3 * (0.2 + 0.8 * crest) * 0.4
    hue = gradient((u / span * 1.2 + S.offset + phase) % 1.0, SHEEN)

    light_amt = body + spec * 1.1
    colour = (
        hue * (1 - np.clip(spec * 0.8, 0, 1))[..., None]
        + WHITE * np.clip(spec * 0.8, 0, 1)[..., None]
    )
    alpha = np.clip(light_amt, 0, 1) ** 0.9
    return colour, alpha * S.edge


def frame(S: State, phase: float) -> Array:
    colour, light = shade(S, phase)
    R, C, gh, gw = S.g.R, S.g.C, S.g.gh, S.g.gw
    # Average the light and colour over each character cell.
    pad = ((0, R * gh - S.H), (0, C * gw - S.W))
    light = np.pad(light, pad, mode="edge")
    colour = np.pad(colour, (*pad, (0, 0)), mode="edge")
    cell = light.reshape(R, gh, C, gw).mean(axis=(1, 3))
    tint = (colour * light[..., None]).reshape(R, gh, C, gw, 3).sum(axis=(1, 3))
    tint /= np.maximum(cell * gh * gw, 1e-6)[..., None]

    # Brighter cells get inkier symbols.
    level = np.clip(cell * 1.3, 0, 0.999)
    idx = S.g.ramp[(level * len(CHARSET)).astype(int)]
    inten = np.clip(cell * 1.8, 0, 1) ** 0.6
    alpha, rgb = stamp(S.g.atlas, idx, tint, inten)
    alpha, rgb = alpha[: S.H, : S.W], rgb[: S.H, : S.W]

    # A light glow around the symbols.
    r = max(2, gh // 2)
    glow = blur(alpha, r)
    glow_c = np.stack([blur(alpha * rgb[..., c], r) for c in range(3)], -1)
    a = np.clip(alpha + glow * 0.9, 0, 1)
    out = (alpha[..., None] * rgb + glow_c * 0.9) / np.maximum(
        alpha + glow * 0.9, 1e-6
    )[..., None]
    return rgba(out, a)
