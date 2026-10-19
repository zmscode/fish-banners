"""Terminal Rain: streams of glowing glyphs raining down in two layered
depths, each led by a bright head glyph.

Inspired by https://pro.reactbits.dev/docs/components/terminal-rain
"""

from dataclasses import dataclass

import numpy as np

from _common import Array, blur, glyph_atlas, gradient, rgba, stamp

FRAMES = 72  # frames per loop; every stream falls whole cycles
TRAIL = 14  # glyphs in each fading trail
GLYPHS = "ｦｧｨｩｪｫｬｭｮｯｱｲｳｴｵｶｷｸｹｺｻｼｽｾｿﾀﾁﾂﾃﾄﾅﾆﾇﾈﾉﾊﾋﾌﾍﾎﾏﾐﾑﾒﾓﾔﾕﾖﾗﾘﾙﾚﾛﾜﾝ0123456789ABCDEF"

# 0x96f colours sweep across the columns; heads glint near-white.
STOPS = (
    (0.00, (0xFF, 0x66, 0x6D)),
    (0.25, (0xFF, 0xC7, 0x39)),
    (0.50, (0xB3, 0xE0, 0x3A)),
    (0.75, (0x00, 0xCD, 0xE8)),
    (1.00, (0xA3, 0x92, 0xE8)),
)
HEAD = np.array((0xFC, 0xFC, 0xFA), np.float32)


@dataclass
class Layer:
    """One depth of rain: a grid of glyph cells and the streams falling down it."""

    R: int  # glyph rows
    C: int  # glyph columns
    gh: int  # glyph cell height
    W: int
    H: int
    trail: int  # glyphs in each fading trail
    brightness: float
    atlas: Array  # (n, gh, gw) glyph masks
    period: Array  # (C,) rows per stream cycle, gap included
    cycles: Array  # (C,) whole cycles per loop
    offset: Array  # (C,)
    glyph: Array  # (R, C) starting glyph in each cell
    shuffle_every: Array  # (R, C) frames between glyph changes
    shuffle_at: Array  # (R, C)
    colour: Array  # (R, C, 3)


@dataclass
class State:
    """Two layers of rain: small, dim and slow behind; large and bright in front."""

    W: int
    H: int
    back: Layer
    front: Layer


def layer(
    rng: np.random.Generator,
    W: int,
    H: int,
    gh: int,
    cycles: tuple[int, ...],
    brightness: float,
) -> Layer:
    gw = max(6, round(gh * 0.6))
    R, C = -(-H // gh), -(-W // gw)
    trail = TRAIL if gh > 20 else TRAIL * 2 // 3
    return Layer(
        R=R,
        C=C,
        gh=gh,
        W=W,
        H=H,
        trail=trail,
        brightness=brightness,
        atlas=glyph_atlas(GLYPHS, gw, gh),
        # Each column cycles through one stream plus a gap, a whole number of
        # times per loop; longer gaps make sparser, faster-looking columns.
        period=R + trail + rng.integers(2, R * 2 + 4, C),
        cycles=rng.choice(cycles, C),
        offset=rng.random(C),
        glyph=rng.integers(0, len(GLYPHS), (R, C)),
        shuffle_every=rng.integers(4, 24, (R, C)),
        shuffle_at=rng.integers(0, 24, (R, C)),
        colour=gradient(np.linspace(0, 1, C, dtype=np.float32), STOPS)[
            None, :, :
        ].repeat(R, axis=0),
    )


def prepare(W: int, H: int, seed: int) -> State:
    rng = np.random.default_rng(seed)
    gh = max(14, round(H / 9))
    return State(
        W=W,
        H=H,
        back=layer(rng, W, H, max(9, round(gh * 0.6)), (1,), 0.35),
        front=layer(rng, W, H, gh, (1, 2), 1.0),
    )


def draw(L: Layer, phase: float) -> tuple[Array, Array]:
    R, trail = L.R, L.trail
    n = round(phase * FRAMES)
    # Head row of each column's stream, stepping a glyph at a time.
    head = np.floor(((L.offset + L.cycles * phase) % 1.0) * L.period) - trail
    behind = head[None, :] - np.arange(R)[:, None]  # rows behind head
    lit = (behind >= 0) & (behind < trail)
    fade = np.where(lit, np.clip(1 - behind / trail, 0, 1) ** 1.6, 0)
    is_head = behind == 0

    # Glyphs stay put as streams pass, changing now and then.
    glyph = (L.glyph + (n + L.shuffle_at) // L.shuffle_every * 7) % len(GLYPHS)
    colour = np.where(is_head[..., None], L.colour * 0.3 + HEAD * 0.7, L.colour)
    inten = np.where(is_head, 1.15, fade * 0.85) * L.brightness
    alpha, rgb = stamp(L.atlas, glyph, colour, inten)
    return alpha[: L.H, : L.W], rgb[: L.H, : L.W]


def frame(S: State, phase: float) -> Array:
    ab, cb = draw(S.back, phase)
    af, cf = draw(S.front, phase)
    a = af + ab * (1 - af)
    rgb = (cf * af[..., None] + cb * (ab * (1 - af))[..., None]) / np.maximum(a, 1e-6)[
        ..., None
    ]

    r = max(2, S.front.gh // 4)
    glow = blur(af, r)
    glow_c = np.stack([blur(af * cf[..., c], r) for c in range(3)], -1)
    out_a = np.clip(a + glow * 1.2, 0, 1)
    out = (rgb * a[..., None] + glow_c * 1.2) / np.maximum(a + glow * 1.2, 1e-6)[
        ..., None
    ]
    return rgba(out, out_a)
