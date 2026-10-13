"""Render the AskPhilo logo to raster icons (favicon.ico, PNG touch icons).

The vector source is askphilo/static/img/logo.svg. Pillow cannot rasterise
SVG, so the same geometry is redrawn here at high resolution and
downsampled. Run:  python scripts/make_icons.py
"""

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw

OUT = Path(__file__).resolve().parent.parent / "askphilo" / "static" / "img"
S = 16  # supersampling factor: draw on a 64*S canvas
TOP = (110, 114, 247)
BOTTOM = (75, 79, 224)
SPARK = (255, 255, 255)
DOT = (255, 138, 152)


def _gradient(size: int) -> Image.Image:
    img = Image.new("RGB", (size, size))
    px = img.load()
    for y in range(size):
        for x in range(size):
            t = min(1.0, max(0.0, (x + y) / (2 * size)))
            px[x, y] = tuple(round(a + (b - a) * t) for a, b in zip(TOP, BOTTOM))
    return img


def _bezier(p0, p1, p2, p3, steps=24):
    pts = []
    for i in range(steps + 1):
        t = i / steps
        mt = 1 - t
        x = mt**3 * p0[0] + 3 * mt**2 * t * p1[0] + 3 * mt * t**2 * p2[0] + t**3 * p3[0]
        y = mt**3 * p0[1] + 3 * mt**2 * t * p1[1] + 3 * mt * t**2 * p2[1] + t**3 * p3[1]
        pts.append((x * S, y * S))
    return pts


def render(size_units: int = 64) -> Image.Image:
    canvas = 64 * S
    mask = Image.new("L", (canvas, canvas), 0)
    d = ImageDraw.Draw(mask)
    # Speech bubble body + tail (matches logo.svg path)
    d.rounded_rectangle([6 * S, 8 * S, 58 * S, 48 * S], radius=10 * S, fill=255)
    d.polygon([(19 * S, 46 * S), (19 * S, 57 * S), (31 * S, 46 * S)], fill=255)

    base = Image.new("RGBA", (canvas, canvas), (0, 0, 0, 0))
    base.paste(_gradient(canvas), (0, 0), mask)

    draw = ImageDraw.Draw(base)
    spark = []
    spark += _bezier((31, 15), (32.2, 23.2), (34.8, 25.8), (43, 27))
    spark += _bezier((43, 27), (34.8, 28.2), (32.2, 30.8), (31, 39))
    spark += _bezier((31, 39), (29.8, 30.8), (27.2, 28.2), (19, 27))
    spark += _bezier((19, 27), (27.2, 25.8), (29.8, 23.2), (31, 15))
    draw.polygon(spark, fill=SPARK)
    r = 3.5 * S
    draw.ellipse([45.5 * S - r, 17.5 * S - r, 45.5 * S + r, 17.5 * S + r], fill=DOT)
    return base


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    big = render()
    big.resize((512, 512), Image.LANCZOS).save(OUT / "icon-512.png")
    big.resize((192, 192), Image.LANCZOS).save(OUT / "icon-192.png")

    # Apple touch icon: opaque background, logo inset
    touch = Image.new("RGBA", (180, 180), (255, 255, 255, 255))
    inner = big.resize((148, 148), Image.LANCZOS)
    touch.alpha_composite(inner, (16, 16))
    touch.convert("RGB").save(OUT / "apple-touch-icon.png")

    ico_src = big.resize((256, 256), Image.LANCZOS)
    ico_src.save(OUT / "favicon.ico", sizes=[(16, 16), (32, 32), (48, 48)])
    print("icons written to", OUT)


if __name__ == "__main__":
    main()
