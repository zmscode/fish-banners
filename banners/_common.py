"""Helpers shared by the banner renderers."""

import itertools
import os
from collections.abc import Sequence
from typing import Any, Protocol

import numpy as np
import numpy.typing as npt

# Numpy arrays of any dtype and shape; docstrings say which they are.
Array = npt.NDArray[Any]
Colour = tuple[int, int, int]
Stops = Sequence[tuple[float, Colour]]


class Noise(Protocol):
    def __call__(self, x: Array, y: Array, octaves: int = 4) -> Array: ...


def fbm_noise(rng: np.random.Generator, G: int = 256) -> Noise:
    """Return fbm(x, y, octaves): smooth fractal value noise in roughly [0, 1)."""
    lattice = rng.random((G, G), dtype=np.float32)
    offsets = rng.random((8, 2)) * G

    def value_noise(x: Array, y: Array) -> Array:
        xi, yi = np.floor(x), np.floor(y)
        fx, fy = x - xi, y - yi
        fx = fx * fx * fx * (fx * (fx * 6 - 15) + 10)
        fy = fy * fy * fy * (fy * (fy * 6 - 15) + 10)
        x0, y0 = xi.astype(np.int64) % G, yi.astype(np.int64) % G
        x1, y1 = (x0 + 1) % G, (y0 + 1) % G
        a, b = lattice[y0, x0], lattice[y0, x1]
        c, d = lattice[y1, x0], lattice[y1, x1]
        return (a + (b - a) * fx) + ((c + (d - c) * fx) - (a + (b - a) * fx)) * fy

    def fbm(x: Array, y: Array, octaves: int = 4) -> Array:
        total = np.zeros_like(x)
        amp, freq = 0.52, 1.0
        for o in range(octaves):
            ox, oy = float(offsets[o, 0]), float(offsets[o, 1])
            # Rotate each octave a little so the lattice grid never lines up.
            c, s = np.cos(o * 0.7), np.sin(o * 0.7)
            u, v = x * freq, y * freq
            total += value_noise(u * c - v * s + ox, u * s + v * c + oy) * amp
            amp *= 0.42
            freq *= 2.03
        return total

    return fbm


def blur(img: Array, r: int) -> Array:
    """Approximate gaussian blur: three box passes along each axis."""
    k = 2 * r + 1
    for _ in range(3):
        c = np.cumsum(np.pad(img, ((r + 1, r), (0, 0))), axis=0)
        img = (c[k:] - c[:-k]) / k
        c = np.cumsum(np.pad(img, ((0, 0), (r + 1, r))), axis=1)
        img = (c[:, k:] - c[:, :-k]) / k
    return img


def gradient(t: Array, stops: Stops) -> Array:
    """Map t (any shape, 0–1) through colour stops [(pos, (r, g, b)), ...]."""
    out = np.zeros((*t.shape, 3), np.float32)
    for (t0, c0), (t1, c1) in itertools.pairwise(stops):
        seg = np.clip((t - t0) / (t1 - t0), 0, 1)[..., None]
        inside = ((t >= t0) & (t <= t1))[..., None]
        out = np.where(inside, np.array(c0) + (np.array(c1) - np.array(c0)) * seg, out)
    return out


def rgba(rgb: Array, alpha: Array) -> Array:
    """Pack (…, 3) colour and (…) coverage, both floats, into uint8 RGBA."""
    out = np.empty((*alpha.shape, 4), np.uint8)
    out[..., :3] = np.clip(rgb, 0, 255)
    out[..., 3] = np.clip(alpha * 255, 0, 255)
    return out


# Fonts tried in order for each glyph: the terminal's own font first, then
# system fonts that fill its gaps (kana, dot and symbol punctuation).
FONTS = (
    "~/Library/Fonts/MartianMonoNerdFontMono-Regular.ttf",
    "/System/Library/Fonts/ヒラギノ角ゴシック W4.ttc",
    "/System/Library/Fonts/Apple Symbols.ttf",
    "/System/Library/Fonts/Geneva.ttf",
    "/System/Library/Fonts/Menlo.ttc",
)
NO_SUCH_GLYPH = "\U0010fffd"  # a private-use code point no font draws


def glyph_atlas(chars: str, gw: int, gh: int, bold: bool = False) -> Array:
    """Rasterise chars into an (n, gh, gw) float32 array of coverage masks,
    each centred in a gw×gh cell, using the first font in FONTS that has
    each glyph."""
    from PIL import Image, ImageDraw, ImageFont

    def draw(ch: str, font: ImageFont.FreeTypeFont) -> Array:
        # Centre on the font's metrics, not the glyph's ink, so every glyph
        # shares a baseline.
        img = Image.new("L", (gw * 2, gh * 2))
        ImageDraw.Draw(img).text((gw, gh), ch, font=font, fill=255, anchor="mm")
        x0, y0 = gw - gw // 2, gh - gh // 2
        return np.asarray(img.crop((x0, y0, x0 + gw, y0 + gh)), np.float32) / 255

    size = max(6, round(gh * 0.78))
    fonts: list[tuple[ImageFont.FreeTypeFont, Array]] = []
    for path in FONTS:
        path = os.path.expanduser(path)
        if bold and "Martian" in path:
            path = path.replace("Regular", "Bold")
        try:
            font = ImageFont.truetype(path, size)
        except OSError:
            continue
        # What this font draws for a character it doesn't have.
        fonts.append((font, draw(NO_SUCH_GLYPH, font)))

    out = np.zeros((len(chars), gh, gw), np.float32)
    for i, ch in enumerate(chars):
        for font, missing in fonts:
            glyph = draw(ch, font)
            if not np.array_equal(glyph, missing):
                break
        else:
            glyph = draw(ch, fonts[-1][0])
        out[i] = glyph
    return out


def stamp(atlas: Array, idx: Array, colour: Array, inten: Array) -> tuple[Array, Array]:
    """Compose a glyph grid into an image.

    atlas: (n, gh, gw) masks; idx: (R, C) glyph indices; colour: (R, C, 3);
    inten: (R, C) brightness. Returns (alpha, rgb) at (R·gh, C·gw).
    """
    R, C = idx.shape
    _, gh, gw = atlas.shape
    cov = atlas[idx] * inten[..., None, None]  # R, C, gh, gw
    alpha = cov.transpose(0, 2, 1, 3).reshape(R * gh, C * gw)
    rgb = np.repeat(np.repeat(colour, gh, axis=0), gw, axis=1)
    return alpha, rgb


def splat(
    x: Array,
    y: Array,
    inten: Array,
    colour: Array,
    W: int,
    H: int,
    smooth: bool = True,
) -> tuple[Array, Array]:
    """Accumulate point particles into an image.

    x, y, inten: (n,) positions in pixels and brightness; colour: (n, 3).
    smooth spreads each particle over its four nearest pixels; otherwise it
    lands whole in one pixel, for a crisp pixel-art look.
    Returns (A, C): (H, W) summed brightness and (H, W, 3) brightness-weighted
    colour.
    """
    light = np.zeros(W * H, np.float32)
    tint = np.zeros((3, W * H), np.float32)
    taps: list[tuple[int, int, Array | float]]
    if smooth:
        x0, y0 = np.floor(x - 0.5), np.floor(y - 0.5)
        fx, fy = x - 0.5 - x0, y - 0.5 - y0
        taps = [
            (0, 0, (1 - fx) * (1 - fy)),
            (1, 0, fx * (1 - fy)),
            (0, 1, (1 - fx) * fy),
            (1, 1, fx * fy),
        ]
    else:
        x0, y0 = np.floor(x), np.floor(y)
        taps = [(0, 0, 1.0)]
    for dx, dy, wt in taps:
        xi, yi = (x0 + dx).astype(np.int64), (y0 + dy).astype(np.int64)
        ok = (xi >= 0) & (xi < W) & (yi >= 0) & (yi < H)
        idx, w = yi[ok] * W + xi[ok], (inten * wt)[ok]
        light += np.bincount(idx, w, W * H)
        for c in range(3):
            tint[c] += np.bincount(idx, w * colour[ok, c], W * H)
    return light.reshape(H, W), tint.reshape(3, H, W).transpose(1, 2, 0)


def pixelate(img: Array, scale: int, W: int, H: int) -> Array:
    """Blow a low-resolution RGBA image up by whole pixels, cropped to W×H."""
    return np.repeat(np.repeat(img, scale, axis=0), scale, axis=1)[:H, :W]
