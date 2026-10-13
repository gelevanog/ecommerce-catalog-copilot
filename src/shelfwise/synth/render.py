"""Synthetic product renders (Pillow only): a known color, pattern, material texture and unit count per image.

These are the evaluation's "photos". Because every render is drawn from its parameters, the gold attributes are
exact (the color is the RGB that was painted, the count is the number of items drawn), and no image is someone
else's work. They look like simple catalog illustrations, not photographs; the README says what that means for the
vision numbers.
"""

from __future__ import annotations

import math
import random
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from PIL import Image, ImageChops, ImageDraw, ImageFilter

from shelfwise.vocab import COLORS

S = 1200  # drawing size; images are saved at half size (supersampled anti-aliasing)
OUT = 600
RGB = tuple[int, int, int]


@dataclass(frozen=True)
class RenderSpec:
    kind: str
    color: str
    secondary: str
    pattern: str
    material: str | None
    count: int = 1
    seed: int = 0


def _rgb(name: str) -> RGB:
    return COLORS[name]


def _shade(color: RGB, factor: float) -> RGB:
    if factor >= 1:
        return tuple(min(255, int(c + (255 - c) * (factor - 1))) for c in color)  # type: ignore[return-value]
    return tuple(max(0, int(c * factor)) for c in color)  # type: ignore[return-value]


# ---------------------------------------------------------------- patterns and textures


def pattern_layer(pattern: str, c1: RGB, c2: RGB, rng: random.Random, scale: int = 70) -> Image.Image:
    img = Image.new("RGB", (S, S), c1)
    d = ImageDraw.Draw(img)
    if pattern == "striped":
        for y in range(0, S, scale):
            d.rectangle((0, y, S, y + scale // 2 - 4), fill=c2)
    elif pattern == "polka dot":
        r = scale // 5
        for row, y in enumerate(range(0, S + scale, scale)):
            offset = (scale // 2) if row % 2 else 0
            for x in range(-scale, S + scale, scale):
                cx = x + offset
                d.ellipse((cx - r, y - r, cx + r, y + r), fill=c2)
    elif pattern == "checked":
        band = Image.new("RGB", (S, S), c1)
        bd = ImageDraw.Draw(band)
        half = _mix(c1, c2, 0.55)
        for x in range(0, S, scale):
            bd.rectangle((x, 0, x + scale // 2, S), fill=half)
        for y in range(0, S, scale):
            bd.rectangle((0, y, S, y + scale // 2), fill=half)
        for x in range(0, S, scale):
            for y in range(0, S, scale):
                bd.rectangle((x, y, x + scale // 2, y + scale // 2), fill=c2)
        img = band
    elif pattern == "floral":
        petal = scale // 5
        center = _mix(c2, (250, 236, 170), 0.5)
        for row, y in enumerate(range(0, S + scale, int(scale * 1.25))):
            for x in range(0, S + scale, int(scale * 1.25)):
                cx = x + (scale // 2 if row % 2 else 0) + rng.randint(-8, 8)
                cy = y + rng.randint(-8, 8)
                for k in range(5):
                    a = 2 * math.pi * k / 5 + rng.random() * 0.3
                    px, py = cx + math.cos(a) * petal, cy + math.sin(a) * petal
                    d.ellipse((px - petal * 0.75, py - petal * 0.75, px + petal * 0.75, py + petal * 0.75), fill=c2)
                d.ellipse((cx - petal * 0.55, cy - petal * 0.55, cx + petal * 0.55, cy + petal * 0.55), fill=center)
                lx, ly = cx + scale * 0.45, cy + scale * 0.35
                d.ellipse((lx - petal * 0.9, ly - petal * 0.35, lx + petal * 0.9, ly + petal * 0.35), fill=_shade(c2, 0.7))
    elif pattern == "geometric":
        h = scale
        for row, y in enumerate(range(-h, S + h, h)):
            color = c2 if row % 2 else c1
            points: list[tuple[float, float]] = []
            for i, x in enumerate(range(-h, S + 2 * h, h)):
                points.append((x, y + (h // 2 if i % 2 else 0)))
            poly = [*points, (S + 2 * h, y + h * 2), (-h, y + h * 2)]
            d.polygon(poly, fill=color)
    return img


def _mix(a: RGB, b: RGB, t: float) -> RGB:
    return tuple(int(x + (y - x) * t) for x, y in zip(a, b, strict=True))  # type: ignore[return-value]


def _noise(rng: random.Random, size: int, blur: float, amplitude: int) -> Image.Image:
    small = max(8, size // 4)
    data = bytes(max(0, min(255, 128 + int(rng.gauss(0, amplitude)))) for _ in range(small * small))
    img = Image.frombytes("L", (small, small), data).resize((size, size), Image.Resampling.BICUBIC)
    return img.filter(ImageFilter.GaussianBlur(blur)) if blur else img


def texture_layer(material: str | None, rng: random.Random) -> Image.Image:
    """An L image around 128: multiplied into the fill (values above 128 lighten, below darken)."""
    tex = Image.new("L", (S, S), 128)
    d = ImageDraw.Draw(tex)
    if material in {"cotton", "polyester", None, "wax"}:
        return _noise(rng, S, 1.0, 10 if material == "cotton" else 4)
    if material == "wool":
        for x in range(0, S, 14):
            for y in range(0, S, 16):
                d.line((x, y, x + 7, y + 12), fill=118, width=3)
                d.line((x + 14, y, x + 7, y + 12), fill=138, width=3)
        return tex.filter(ImageFilter.GaussianBlur(1.5))
    if material == "denim":
        for k in range(-S, S, 9):
            d.line((k, 0, k + S, S), fill=158 if k % 18 else 100, width=3)
        return ImageChops.multiply(tex, _noise(rng, S, 0.8, 18)).point(lambda v: min(255, v * 2))
    if material == "leather":
        grain = _noise(rng, S, 2, 14)
        return grain.point(lambda v: 128 + (v - 128) // 3)
    if material == "canvas":
        for k in range(0, S, 7):
            d.line((k, 0, k, S), fill=118, width=2)
            d.line((0, k, S, k), fill=140, width=2)
        return tex.filter(ImageFilter.GaussianBlur(0.6))
    if material == "linen":
        for _ in range(900):
            y = rng.randrange(S)
            x = rng.randrange(S)
            d.line((x, y, x + rng.randint(40, 220), y), fill=rng.choice((108, 150)), width=2)
        return tex.filter(ImageFilter.GaussianBlur(0.8))
    if material in {"ceramic", "glass", "terracotta"}:
        base = _noise(rng, S, 1.0, 3)
        if material == "terracotta":
            for _ in range(2500):
                x, y = rng.randrange(S), rng.randrange(S)
                d.point((x, y), fill=90)
            return ImageChops.multiply(base, tex).point(lambda v: min(255, v * 2))
        return base
    if material == "stainless steel":
        for x in range(0, S, 3):
            shade = 128 + int(40 * math.sin(x / 55.0)) + rng.randint(-10, 10)
            d.line((x, 0, x, S), fill=shade, width=3)
        return tex.filter(ImageFilter.GaussianBlur(1))
    if material in {"wood", "bamboo"}:
        if material == "bamboo":
            for x in range(0, S, 16):
                d.line((x, 0, x, S), fill=rng.choice((112, 140)), width=4)
            for y in range(0, S, 160):
                d.line((0, y, S, y), fill=96, width=6)
            return tex.filter(ImageFilter.GaussianBlur(1))
        warp = _noise(rng, S, 25, 40)
        pixels = warp.load()
        assert pixels is not None
        for y in range(0, S, 2):
            for x in range(0, S, 2):
                v = pixels[x, y]
                assert isinstance(v, int)
                ring = math.sin((x * 0.045) + (v - 128) * 0.12 + y * 0.002)
                d.rectangle((x, y, x + 1, y + 1), fill=128 + int(ring * 22))
        return tex.filter(ImageFilter.GaussianBlur(1))
    return tex


def _overlay(fill: Image.Image, tex: Image.Image) -> Image.Image:
    lighten = tex.point(lambda v: max(0, (v - 128) * 2))
    darken = tex.point(lambda v: max(0, (128 - v) * 2))
    out = ImageChops.subtract(fill, Image.merge("RGB", (darken, darken, darken)))
    return ImageChops.add(out, Image.merge("RGB", (lighten, lighten, lighten)))


# ---------------------------------------------------------------- compositing helpers


class Canvas:
    def __init__(self, rng: random.Random) -> None:
        self.rng = rng
        top, bottom = (246, 245, 242), (226, 224, 219)
        self.img = Image.new("RGB", (S, S), top)
        d = ImageDraw.Draw(self.img)
        for y in range(S):
            d.line((0, y, S, y), fill=_mix(top, bottom, y / S))

    def shadow(self, box: tuple[float, float, float, float], strength: int = 70) -> None:
        mask = Image.new("L", (S, S), 0)
        ImageDraw.Draw(mask).ellipse(box, fill=strength)
        mask = mask.filter(ImageFilter.GaussianBlur(28))
        self.img.paste((120, 116, 110), (0, 0), mask)

    def fill(
        self,
        mask: Image.Image,
        layer: Image.Image | RGB,
        *,
        texture: Image.Image | None = None,
        shade: str = "radial",
        outline: bool = True,
        gloss: bool = False,
        alpha: float = 1.0,
    ) -> None:
        if isinstance(layer, tuple):
            layer = Image.new("RGB", (S, S), layer)
        if texture is not None:
            layer = _overlay(layer, texture)
        box = mask.getbbox()
        if box is None:
            return
        if shade != "none":
            layer = _overlay(layer, _shading(box, shade))
        if gloss:
            hl = Image.new("L", (S, S), 0)
            x0, y0, x1, y1 = box
            w = x1 - x0
            ImageDraw.Draw(hl).rounded_rectangle(
                (x0 + w * 0.16, y0 + (y1 - y0) * 0.12, x0 + w * 0.26, y1 - (y1 - y0) * 0.18), radius=30, fill=120
            )
            hl = ImageChops.multiply(hl.filter(ImageFilter.GaussianBlur(14)), mask)
            layer.paste((255, 255, 255), (0, 0), hl)
        paste_mask = mask if alpha >= 1 else mask.point(lambda v: int(v * alpha))
        self.img.paste(layer, (0, 0), paste_mask)
        if outline:
            edge = ImageChops.subtract(mask.filter(ImageFilter.MaxFilter(5)), mask)
            self.img.paste((60, 58, 56), (0, 0), edge.point(lambda v: v // 3))

    def done(self) -> Image.Image:
        return self.img.resize((OUT, OUT), Image.Resampling.LANCZOS)


def _shading(box: tuple[int, int, int, int], kind: str) -> Image.Image:
    x0, y0, x1, y1 = box
    small = Image.new("L", (64, 64), 128)
    px = small.load()
    assert px is not None
    for y in range(64):
        for x in range(64):
            if kind == "horizontal":
                t = x / 63
                v = 128 + 26 * math.sin(math.pi * t) - 30 * (1 - math.sin(math.pi * t))
            else:
                dx, dy = (x - 24) / 40, (y - 20) / 44
                v = 150 - 60 * min(1.0, math.hypot(dx, dy))
            px[x, y] = max(0, min(255, int(v)))
    out = Image.new("L", (S, S), 128)
    out.paste(small.resize((max(1, x1 - x0), max(1, y1 - y0)), Image.Resampling.BICUBIC), (x0, y0))
    return out


def _mask(draw_fn: Callable[[ImageDraw.ImageDraw], None]) -> Image.Image:
    m = Image.new("L", (S, S), 0)
    draw_fn(ImageDraw.Draw(m))
    return m


def _thick_path(d: ImageDraw.ImageDraw, points: list[tuple[float, float]], width: int) -> None:
    d.line(points, fill=255, width=width, joint="curve")
    r = width / 2
    for x, y in (points[0], points[-1]):
        d.ellipse((x - r, y - r, x + r, y + r), fill=255)


# ---------------------------------------------------------------- products


def _body(spec: RenderSpec, rng: random.Random, scale: int = 70) -> tuple[Image.Image, Image.Image]:
    c1, c2 = _rgb(spec.color), _rgb(spec.secondary)
    return pattern_layer(spec.pattern, c1, c2, rng, scale), texture_layer(spec.material, rng)


def _tshirt(cv: Canvas, spec: RenderSpec, long_sleeves: bool = False) -> None:
    layer, tex = _body(spec, cv.rng)
    cv.shadow((330, 930, 870, 1010))
    if long_sleeves:
        pts = [(470, 250), (330, 290), (215, 520), (175, 860), (285, 880), (360, 560), (380, 960), (820, 960),
               (840, 560), (915, 880), (1025, 860), (985, 520), (870, 290), (730, 250)]
    else:
        pts = [(470, 250), (330, 290), (180, 440), (265, 540), (370, 470), (380, 960), (820, 960), (830, 470),
               (935, 540), (1020, 440), (870, 290), (730, 250)]
    body = _mask(lambda d: d.polygon(pts, fill=255))
    ImageDraw.Draw(body).ellipse((480, 190, 720, 320), fill=0)
    cv.fill(body, layer, texture=tex)
    collar = _mask(lambda d: d.arc((476, 186, 724, 324), 0, 180, fill=255, width=26))
    cv.fill(collar, _shade(_rgb(spec.color), 0.8), texture=tex, shade="none")
    if long_sleeves:
        rib = _shade(_rgb(spec.color), 0.82)
        hem = _mask(lambda d: d.rectangle((382, 905, 818, 960), fill=255))
        cuffs = _mask(lambda d: (d.polygon([(178, 830), (288, 850), (285, 880), (175, 860)], fill=255),
                                 d.polygon([(912, 850), (1022, 830), (1025, 860), (915, 880)], fill=255)))
        cv.fill(hem, rib, texture=tex, shade="none")
        cv.fill(cuffs, rib, texture=tex, shade="none")


def _socks(cv: Canvas, spec: RenderSpec) -> None:
    n = spec.count
    layer, tex = _body(spec, cv.rng, 50)
    gap = 860 / n
    width = int(min(120, gap * 0.62))
    for i in range(n):
        x = 170 + gap * i + gap / 2 - 70
        cv.shadow((x - 40, 900, x + 230, 960), 50)
        m = _mask(lambda d, x=x: _thick_path(d, [(x, 260), (x, 690), (x + 150, 860)], width))
        cv.fill(m, layer, texture=tex)
        cuff = _mask(lambda d, x=x: d.rectangle((x - width / 2, 230, x + width / 2, 300), fill=255))
        cv.fill(cuff, _rgb(spec.secondary), texture=tex, shade="none")
        toe = _mask(lambda d, x=x: d.ellipse((x + 110, 800, x + 210, 905), fill=255))
        cv.fill(ImageChops.multiply(toe, m), _rgb(spec.secondary), texture=tex, shade="none")


def _cap(cv: Canvas, spec: RenderSpec) -> None:
    layer, tex = _body(spec, cv.rng)
    cv.shadow((280, 760, 940, 860))
    crown = _mask(lambda d: d.pieslice((320, 300, 880, 900), 180, 360, fill=255))
    ImageDraw.Draw(crown).rectangle((320, 600, 880, 640), fill=255)
    cv.fill(crown, layer, texture=tex)
    brim = _mask(lambda d: d.ellipse((260, 520, 940, 800), fill=255))
    ImageDraw.Draw(brim).ellipse((300, 470, 900, 690), fill=0)
    cv.fill(brim, _shade(_rgb(spec.color), 0.82), texture=tex, shade="horizontal")
    seams = _mask(
        lambda d: (
            d.line((600, 305, 600, 630), fill=255, width=6),
            d.arc((440, 300, 760, 960), 180, 360, fill=255, width=5),
        )
    )
    cv.fill(ImageChops.multiply(seams, crown), _shade(_rgb(spec.color), 0.65), shade="none", outline=False)
    button = _mask(lambda d: d.ellipse((578, 282, 622, 326), fill=255))
    cv.fill(button, _rgb(spec.secondary), shade="none")


def _scarf(cv: Canvas, spec: RenderSpec) -> None:
    layer, tex = _body(spec, cv.rng, 60)
    cv.shadow((380, 960, 820, 1020), 50)
    a = _mask(lambda d: d.polygon([(420, 200), (600, 200), (640, 940), (470, 960)], fill=255))
    b = _mask(lambda d: d.polygon([(560, 260), (760, 230), (780, 980), (600, 990)], fill=255))
    cv.fill(a, layer, texture=tex)
    cv.fill(b, layer, texture=tex, shade="horizontal")
    fringe = _mask(lambda d: [d.line((x, 960 + (x % 3), x + 4, 1010), fill=255, width=6) for x in range(474, 640, 16)]
                   + [d.line((x, 985, x + 3, 1040), fill=255, width=6) for x in range(604, 780, 16)])
    cv.fill(fringe, _rgb(spec.color), shade="none", outline=False)


def _tote(cv: Canvas, spec: RenderSpec) -> None:
    layer, tex = _body(spec, cv.rng)
    cv.shadow((300, 950, 900, 1030))
    handles = _mask(lambda d: (d.arc((410, 230, 590, 660), 180, 360, fill=255, width=34),
                               d.arc((610, 230, 790, 660), 180, 360, fill=255, width=34)))
    cv.fill(handles, _rgb(spec.secondary), texture=tex, shade="none")
    body = _mask(lambda d: d.polygon([(340, 440), (860, 440), (895, 990), (305, 990)], fill=255))
    cv.fill(body, layer, texture=tex)


def _backpack(cv: Canvas, spec: RenderSpec) -> None:
    layer, tex = _body(spec, cv.rng)
    cv.shadow((360, 950, 840, 1030))
    loop = _mask(lambda d: d.arc((540, 210, 660, 360), 180, 360, fill=255, width=26))
    cv.fill(loop, _shade(_rgb(spec.color), 0.6), shade="none")
    body = _mask(lambda d: d.rounded_rectangle((380, 280, 820, 990), radius=130, fill=255))
    cv.fill(body, layer, texture=tex)
    pocket = _mask(lambda d: d.rounded_rectangle((450, 640, 750, 930), radius=60, fill=255))
    cv.fill(pocket, _rgb(spec.secondary), texture=tex)
    zips = _mask(lambda d: (d.arc((410, 310, 790, 700), 200, 340, fill=255, width=8), d.line((470, 700, 730, 700), fill=255, width=8)))
    cv.fill(zips, (70, 70, 74), shade="none", outline=False)


def _sneaker(cv: Canvas, spec: RenderSpec) -> None:
    layer, tex = _body(spec, cv.rng, 60)
    cv.shadow((200, 800, 1040, 890))
    outline = [(235, 790), (228, 640), (262, 560), (330, 530), (420, 560), (500, 520), (560, 450), (640, 440),
               (700, 470), (740, 545), (850, 590), (950, 640), (1010, 700), (1020, 790)]
    upper = _mask(lambda d: d.polygon(outline, fill=255))
    cv.fill(upper, layer, texture=tex)
    collar = _mask(lambda d: d.chord((250, 500, 520, 640), 180, 360, fill=255))
    cv.fill(ImageChops.multiply(collar, upper), _shade(_rgb(spec.color), 0.7), shade="none", outline=False)
    panel = _mask(lambda d: d.polygon([(330, 720), (560, 600), (820, 640), (900, 700), (420, 760)], fill=255))
    cv.fill(ImageChops.multiply(panel, upper), _rgb(spec.secondary), texture=tex, shade="none")
    sole = _mask(lambda d: d.rounded_rectangle((210, 765, 1035, 860), radius=44, fill=255))
    cv.fill(sole, (238, 236, 230), shade="horizontal")
    stripe = _mask(lambda d: d.line((240, 815, 1005, 815), fill=255, width=8))
    cv.fill(stripe, (200, 198, 192), shade="none", outline=False)
    laces = _mask(lambda d: [d.line((585 + i * 42, 470 + i * 26, 640 + i * 42, 455 + i * 26), fill=255, width=13) for i in range(4)])
    cv.fill(laces, (248, 248, 246), shade="none")


def _mug(cv: Canvas, spec: RenderSpec) -> None:
    n = spec.count
    layer, tex = _body(spec, cv.rng, 60)
    positions = [600] if n == 1 else [600 + (i - (n - 1) / 2) * (900 / n) for i in range(n)]
    w = 400 if n == 1 else int(560 / n)
    for cx in positions:
        x0, x1 = cx - w / 2, cx + w / 2
        cv.shadow((x0 - 20, 870, x1 + 90, 940), 60)
        handle = _mask(lambda d, x1=x1: d.arc((x1 - w * 0.3, 500, x1 + w * 0.32, 790), -90, 90, fill=255, width=int(w * 0.11)))
        cv.fill(handle, _rgb(spec.color), texture=tex, shade="none")
        body = _mask(lambda d, x0=x0, x1=x1: d.rounded_rectangle((x0, 420, x1, 905), radius=40, fill=255))
        cv.fill(body, layer, texture=tex, shade="horizontal", gloss=spec.material == "ceramic")
        rim = _mask(lambda d, x0=x0, x1=x1: d.ellipse((x0, 395, x1, 455), fill=255))
        cv.fill(rim, _shade(_rgb(spec.color), 0.55), shade="none")


def _plates(cv: Canvas, spec: RenderSpec) -> None:
    n = spec.count
    layer, tex = _body(spec, cv.rng, 55)
    grid = {1: (1, 1), 2: (2, 1), 3: (3, 1), 4: (2, 2), 6: (3, 2)}.get(n, (n, 1))
    cols, rows = grid
    r = min(330 / cols, 330 / rows) * (0.95 if n > 1 else 1.0)
    for i in range(n):
        col, row = i % cols, i // cols
        cx = 600 + (col - (cols - 1) / 2) * (2 * r + 30)
        cy = 600 + (row - (rows - 1) / 2) * (2 * r * 0.9 + 30)
        cv.shadow((cx - r, cy - r * 0.8 + 30, cx + r, cy + r * 0.95 + 30), 50)
        plate = _mask(lambda d, cx=cx, cy=cy: d.ellipse((cx - r, cy - r * 0.9, cx + r, cy + r * 0.9), fill=255))
        cv.fill(plate, layer, texture=tex, gloss=True)
        well = _mask(lambda d, cx=cx, cy=cy: d.ellipse((cx - r * 0.62, cy - r * 0.56, cx + r * 0.62, cy + r * 0.56), outline=255, width=6))
        cv.fill(well, _shade(_rgb(spec.color), 0.75), shade="none", outline=False)


def _vase(cv: Canvas, spec: RenderSpec) -> None:
    layer, tex = _body(spec, cv.rng, 60)
    cv.shadow((400, 930, 800, 1000))
    left, right = [], []
    for k in range(0, 71):
        y = 260 + k * 10
        t = k / 70
        # neck (narrow), shoulder, round belly, slightly narrower foot
        half = 75 + 175 * math.sin(math.pi * (0.15 + 0.8 * t)) ** 2 * min(1.0, t * 3)
        left.append((600 - half, y))
        right.append((600 + half, y))
    body = _mask(lambda d: d.polygon(left + right[::-1], fill=255))
    glass = spec.material == "glass"
    cv.fill(body, layer, texture=tex, shade="horizontal", gloss=spec.material in {"ceramic", "glass"}, alpha=0.78 if glass else 1)
    mouth = _mask(lambda d: d.ellipse((525, 240, 675, 285), fill=255))
    cv.fill(mouth, _shade(_rgb(spec.color), 0.5), shade="none")


def _cushion(cv: Canvas, spec: RenderSpec) -> None:
    layer, tex = _body(spec, cv.rng, 70)
    cv.shadow((300, 870, 900, 960))
    pts: list[tuple[float, float]] = []
    corners = [(300, 300), (900, 300), (900, 900), (300, 900)]
    for i in range(4):
        (ax, ay), (bx, by) = corners[i], corners[(i + 1) % 4]
        mx, my = (ax + bx) / 2, (ay + by) / 2
        nx, ny = (600 - mx) * 0.12, (600 - my) * 0.12
        for k in range(20):
            t = k / 20
            qx = (1 - t) ** 2 * ax + 2 * (1 - t) * t * (mx + nx) + t**2 * bx
            qy = (1 - t) ** 2 * ay + 2 * (1 - t) * t * (my + ny) + t**2 * by
            pts.append((qx, qy))
    body = _mask(lambda d: d.polygon(pts, fill=255))
    cv.fill(body, layer, texture=tex)


def _candles(cv: Canvas, spec: RenderSpec) -> None:
    n = spec.count
    layer, tex = _body(spec, cv.rng, 50)
    gap = 860 / n
    w = min(200, gap * 0.7)
    for i in range(n):
        cx = 170 + gap * i + gap / 2
        h = 430 - (i % 2) * 80
        top = 930 - h
        cv.shadow((cx - w * 0.7, 900, cx + w * 0.7, 960), 60)
        wick = _mask(lambda d, cx=cx, top=top: d.line((cx, top - 50, cx, top + 5), fill=255, width=8))
        cv.fill(wick, (40, 40, 40), shade="none", outline=False)
        flame = _mask(lambda d, cx=cx, top=top: d.ellipse((cx - 22, top - 120, cx + 22, top - 40), fill=255))
        cv.fill(flame, (255, 196, 70), shade="radial", outline=False)
        body = _mask(lambda d, cx=cx, top=top: d.rounded_rectangle((cx - w / 2, top, cx + w / 2, 930), radius=18, fill=255))
        cv.fill(body, layer, texture=tex, shade="horizontal")
        rim = _mask(lambda d, cx=cx, top=top: d.ellipse((cx - w / 2, top - 18, cx + w / 2, top + 22), fill=255))
        cv.fill(rim, _shade(_rgb(spec.color), 1.12), shade="none")


def _lamp(cv: Canvas, spec: RenderSpec) -> None:
    layer, tex = _body(spec, cv.rng, 60)
    cv.shadow((420, 900, 780, 970))
    stem = _mask(lambda d: d.rectangle((585, 540, 615, 890), fill=255))
    cv.fill(stem, _rgb(spec.secondary), shade="horizontal")
    base = _mask(lambda d: d.ellipse((460, 860, 740, 950), fill=255))
    cv.fill(base, _rgb(spec.secondary), shade="horizontal")
    shade_mask = _mask(lambda d: d.polygon([(430, 240), (770, 240), (880, 570), (320, 570)], fill=255))
    cv.fill(shade_mask, layer, texture=tex, shade="horizontal")


def _bottle(cv: Canvas, spec: RenderSpec) -> None:
    layer, tex = _body(spec, cv.rng, 60)
    cv.shadow((440, 950, 760, 1010))
    cap = _mask(lambda d: d.rounded_rectangle((520, 210, 680, 335), radius=24, fill=255))
    cv.fill(cap, _rgb(spec.secondary), shade="horizontal")
    body = _mask(lambda d: d.rounded_rectangle((470, 320, 730, 990), radius=90, fill=255))
    cv.fill(body, layer, texture=tex, shade="horizontal", gloss=True)


def _towels(cv: Canvas, spec: RenderSpec) -> None:
    n = spec.count
    layer, tex = _body(spec, cv.rng, 50)
    h = min(150, 560 / n)
    bottom = 930
    cv.shadow((300, bottom - 30, 900, bottom + 40))
    for i in range(n):
        y1 = bottom - i * (h + 8)
        y0 = y1 - h
        dx = (i % 2) * 18
        m = _mask(lambda d, y0=y0, y1=y1, dx=dx: d.rounded_rectangle((320 + dx, y0, 880 + dx, y1), radius=int(h * 0.35), fill=255))
        cv.fill(m, layer, texture=tex, shade="horizontal")
        band = _mask(lambda d, y0=y0, y1=y1, dx=dx: d.rectangle((700 + dx, y0 + 6, 735 + dx, y1 - 6), fill=255))
        cv.fill(ImageChops.multiply(band, m), _rgb(spec.secondary), shade="none", outline=False)


def _planter(cv: Canvas, spec: RenderSpec) -> None:
    layer, tex = _body(spec, cv.rng, 60)
    cv.shadow((400, 920, 800, 990))
    body = _mask(lambda d: d.polygon([(390, 520), (810, 520), (750, 950), (450, 950)], fill=255))
    cv.fill(body, layer, texture=tex, shade="horizontal")
    rim = _mask(lambda d: d.rounded_rectangle((360, 460, 840, 545), radius=14, fill=255))
    cv.fill(rim, _shade(_rgb(spec.color), 0.92), texture=tex, shade="horizontal")
    soil = _mask(lambda d: d.ellipse((390, 455, 810, 490), fill=255))
    cv.fill(soil, (78, 58, 44), shade="none", outline=False)


def _board(cv: Canvas, spec: RenderSpec) -> None:
    layer, tex = _body(spec, cv.rng, 60)
    cv.shadow((330, 900, 900, 980))
    body = _mask(lambda d: (d.rounded_rectangle((340, 340, 860, 930), radius=60, fill=255), d.rounded_rectangle((530, 190, 670, 380), radius=40, fill=255)))
    ImageDraw.Draw(body).ellipse((575, 225, 625, 275), fill=0)
    cv.fill(body, layer, texture=tex)


def _blanket(cv: Canvas, spec: RenderSpec) -> None:
    layer, tex = _body(spec, cv.rng, 80)
    cv.shadow((270, 860, 930, 950))
    body = _mask(lambda d: d.rounded_rectangle((280, 400, 920, 890), radius=30, fill=255))
    cv.fill(body, layer, texture=tex)
    fold = _mask(lambda d: d.line((285, 620, 915, 620), fill=255, width=10))
    cv.fill(fold, _shade(_rgb(spec.color), 0.6), shade="none", outline=False)
    fringe = _mask(lambda d: [d.line((x, 890, x + 3, 940), fill=255, width=7) for x in range(296, 910, 18)])
    cv.fill(fringe, _rgb(spec.color), shade="none", outline=False)


def _glasses(cv: Canvas, spec: RenderSpec) -> None:
    n = spec.count
    gap = 880 / n
    w = min(220, gap * 0.72)
    tint = _rgb(spec.color)
    for i in range(n):
        cx = 160 + gap * i + gap / 2
        cv.shadow((cx - w * 0.6, 900, cx + w * 0.6, 950), 40)
        body = _mask(lambda d, cx=cx: d.polygon([(cx - w / 2, 430), (cx + w / 2, 430), (cx + w * 0.42, 930), (cx - w * 0.42, 930)], fill=255))
        cv.fill(body, tint, texture=texture_layer("glass", cv.rng), shade="horizontal", gloss=True, alpha=0.62)
        rim = _mask(lambda d, cx=cx: d.ellipse((cx - w / 2, 410, cx + w / 2, 450), outline=255, width=8))
        cv.fill(rim, _shade(tint, 0.7), shade="none", outline=False)


DRAWERS: dict[str, Callable[[Canvas, RenderSpec], None]] = {
    "tshirt": _tshirt,
    "sweater": lambda cv, spec: _tshirt(cv, spec, long_sleeves=True),
    "socks": _socks,
    "cap": _cap,
    "scarf": _scarf,
    "tote": _tote,
    "backpack": _backpack,
    "sneaker": _sneaker,
    "mug": _mug,
    "plates": _plates,
    "vase": _vase,
    "cushion": _cushion,
    "candles": _candles,
    "lamp": _lamp,
    "bottle": _bottle,
    "towels": _towels,
    "planter": _planter,
    "board": _board,
    "blanket": _blanket,
    "glasses": _glasses,
}


def render(spec: RenderSpec) -> Image.Image:
    rng = random.Random(spec.seed)
    canvas = Canvas(rng)
    DRAWERS[spec.kind](canvas, spec)
    return canvas.done()


def save(spec: RenderSpec, path: Path, quality: int = 84) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    render(spec).save(path, "JPEG", quality=quality, optimize=True, progressive=True)
