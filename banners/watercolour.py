"""Watercolour: pigments bleeding into each other like wet paint, with
pigment gathering at the edges where washes dry, stippled in katakana and
letters: each character cell shows how much paint pooled there as an
inkier or lighter glyph, picked at random from those of similar weight so
the stipple never repeats.

Domain-warped noise, warped twice over, gives the slow marbled flow; the
warp drifts round a circle so the paint swirls in a seamless loop.

Inspired by https://pro.reactbits.dev/docs/components/watercolor
"""

from dataclasses import dataclass

import numpy as np

from _common import Array, Noise, blur, fbm_noise, glyph_atlas, gradient, rgba, stamp

FRAMES = 72  # frames per loop; the warp circles once
POST = [
    "trails",
    "chromatic",
    "bloom",
    "grain",
]  # post-processing effects from _post.py, in order
SCALE = 3.6  # washes per banner height
WARP = 3.2  # how far the paint swirls into itself
DRIFT = 0.35  # how far the flow wanders over a loop
ROWS = 11  # rows of glyphs in the banner
GLYPHS = (
    "ｦｧｨｩｪｫｬｭｮｯｱｲｳｴｵｶｷｸｹｺｻｼｽｾｿﾀﾁﾂﾃﾄﾅﾆﾇﾈﾉﾊﾋﾌﾍﾎﾏﾐﾑﾒﾓﾔﾕﾖﾗﾘﾙﾚﾛﾜﾝABCDEFGHIJKLMNOPQRSTUVWXYZ"
)
LEVELS = 7  # density steps; each holds glyphs of similar ink

# 0x96f pigments, wrapping round so every colour can bleed into the next.
PIGMENTS = (
    (0.0, (0x00, 0xCD, 0xE8)),
    (0.17, (0xA3, 0x92, 0xE8)),
    (0.33, (0xFC, 0xFC, 0xFA)),
    (0.5, (0xFF, 0x66, 0x6D)),
    (0.67, (0xFF, 0xC7, 0x39)),
    (0.83, (0xB3, 0xE0, 0x3A)),
    (1.0, (0x00, 0xCD, 0xE8)),
)


@dataclass
class State:
    """A sheet of paper and the paint that will flow across it."""

    W: int
    H: int
    gh: int  # glyph cell height
    atlas: Array  # (n, gh, gw) glyph masks, blank first
    groups: list[list[int]]  # glyphs at each density level
    pick: Array  # (R, C) each cell's fixed pick within its level
    x: Array  # (R, C) paper coordinates of cell centres
    y: Array
    fbm: Noise
    start: Array  # (2,) warp phases
    grain: Array  # (R, C) paper grain
    edge: Array  # (1, C) fade at the ends
    tint: float  # where on the palette this painting starts


def prepare(W: int, H: int, seed: int) -> State:
    rng = np.random.default_rng(seed)
    gh = max(12, round(H / ROWS))
    gw = max(7, round(gh * 0.62))
    R, C = -(-H // gh), -(-W // gw)
    atlas = glyph_atlas(" " + GLYPHS, gw, gh)
    # Rank glyphs by how much ink each uses and split them into LEVELS
    # buckets, so cells at the same density still vary. Level 0 is blank.
    order = [i for i in np.argsort(atlas.sum(axis=(1, 2)), kind="stable") if i]
    groups = [[0]] + [list(b) for b in np.array_split(order, LEVELS)]
    # Paint is sampled once per character cell, at its centre.
    x = (np.arange(C, dtype=np.float32)[None, :] + 0.5) * gw / H * SCALE
    y = (np.arange(R, dtype=np.float32)[:, None] + 0.5) * gh / H * SCALE
    x, y = np.broadcast_arrays(x, y)
    fbm = fbm_noise(rng)
    xs = (np.arange(C)[None, :] + 0.5) / C
    return State(
        W=W,
        H=H,
        gh=gh,
        atlas=atlas,
        groups=groups,
        # Each cell's fixed pick among the glyphs for its level.
        pick=rng.random((R, C)),
        x=x.copy(),
        y=y.copy(),
        fbm=fbm,
        start=rng.random(2) * 2 * np.pi,
        # Paper grain: fixed fine speckle the pigment settles into.
        grain=0.93
        + 0.14 * np.random.default_rng(seed + 1).random(x.shape, dtype=np.float32),
        edge=np.clip(np.minimum(xs, 1 - xs) / 0.05, 0, 1),
        tint=rng.random(),
    )


def frame(S: State, phase: float) -> Array:
    fbm, x, y = S.fbm, S.x, S.y
    t = 2 * np.pi * phase
    a, b = S.start + t
    # Warp the paper twice over; the first warp drifts round a circle.
    qx = fbm(x + DRIFT * np.cos(a), y + DRIFT * np.sin(a), 4)
    qy = fbm(x + 5.2 + DRIFT * np.sin(b), y + 1.3 + DRIFT * np.cos(b), 4)
    rx = fbm(x + WARP * qx + 1.7, y + WARP * qy + 9.2, 4)
    ry = fbm(x + WARP * qx + 8.3, y + WARP * qy + 2.8, 4)
    f = fbm(x + WARP * rx, y + WARP * ry, 5)

    # Pigment: where the paint pooled thickly enough to show.
    wash = np.clip((f - 0.37) / 0.28, 0, 1)
    wash = wash * wash * (3 - 2 * wash)
    # Wet edges: pigment gathers in a dark rim where a wash dried.
    rim = np.exp(-np.abs(f - 0.41) / 0.02)
    density = np.clip(wash * 0.7 + rim * 0.45, 0, 1) * S.grain

    # Colour comes from where the paint flowed from, so neighbouring washes
    # carry different pigments that bleed into each other.
    colour = gradient((qx * 0.8 + qy * 0.4 + S.tint + phase) % 1.0, PIGMENTS)
    density = np.clip(density, 0, 1) * S.edge

    # More paint, inkier glyphs.
    groups = S.groups
    level = (np.clip(density, 0, 0.999) * len(groups)).astype(int)
    idx = np.zeros(level.shape, np.int64)
    for k, members in enumerate(groups):
        here = level == k
        idx[here] = np.array(members)[(S.pick[here] * len(members)).astype(int)]
    inten = np.clip(density * 2.2, 0, 1) ** 0.5
    alpha, rgb = stamp(S.atlas, idx, colour, inten)
    alpha, rgb = alpha[: S.H, : S.W], rgb[: S.H, : S.W]

    # A faint glow, like pigment bleeding into damp paper.
    r = max(2, S.gh // 2)
    glow = blur(alpha, r)
    glow_c = np.stack([blur(alpha * rgb[..., c], r) for c in range(3)], -1)
    a = np.clip(alpha * 1.6 + glow * 1.3, 0, 1)
    out = (alpha[..., None] * rgb + glow_c * 0.8) / np.maximum(
        alpha + glow * 0.8, 1e-6
    )[..., None]
    return rgba(out, a)
