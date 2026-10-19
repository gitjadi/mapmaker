"""Draws MapMaker's icon (mapmaker/gui/icon.png and installer/MapMaker.ico)."""
from pathlib import Path

from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parent.parent


def draw(size=256):
    s = 4 * size
    im = Image.new("RGBA", (s, s), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    r = s * 0.2
    d.rounded_rectangle([0, 0, s - 1, s - 1], radius=r, fill=(32, 34, 38, 255))
    # a folded map: three panels, alternately lit and shaded
    top, bot = s * 0.25, s * 0.80
    xs = [s * 0.16, s * 0.39, s * 0.61, s * 0.84]
    dy = s * 0.05
    cols = [(236, 232, 220, 255), (206, 201, 186, 255), (236, 232, 220, 255)]
    for i in range(3):
        y0a = top + (dy if i % 2 else 0)
        y0b = top + (0 if i % 2 else dy)
        poly = [(xs[i], y0a), (xs[i + 1], y0b), (xs[i + 1], y0b + (bot - top)), (xs[i], y0a + (bot - top))]
        d.polygon(poly, fill=cols[i])
    # contour lines and a river
    w = max(3, s // 110)
    for k in (0.0, 0.05, 0.10):
        d.ellipse([s * (0.22 + k), s * (0.36 + k * 0.8), s * (0.50 - k), s * (0.58 - k * 0.8)],
                  outline=(160, 138, 104, 255), width=w)
    d.line([(s * 0.18, s * 0.70), (s * 0.34, s * 0.62), (s * 0.50, s * 0.68), (s * 0.82, s * 0.56)],
           fill=(61, 142, 230, 255), width=int(w * 2.2), joint="curve")
    # a pin in the accent blue
    cx, cy, pr = s * 0.62, s * 0.36, s * 0.13
    d.polygon([(cx - pr * 0.82, cy + pr * 0.45), (cx + pr * 0.82, cy + pr * 0.45), (cx, cy + pr * 2.0)],
              fill=(61, 142, 230, 255))
    d.ellipse([cx - pr, cy - pr, cx + pr, cy + pr], fill=(61, 142, 230, 255))
    d.ellipse([cx - pr * 0.42, cy - pr * 0.42, cx + pr * 0.42, cy + pr * 0.42], fill=(255, 255, 255, 255))
    mask = Image.new("L", (s, s), 0)
    ImageDraw.Draw(mask).rounded_rectangle([0, 0, s - 1, s - 1], radius=r, fill=255)
    im.putalpha(Image.composite(im.getchannel("A"), mask, mask))
    return im.resize((size, size), Image.LANCZOS)


if __name__ == "__main__":
    big = draw(256)
    big.save(ROOT / "mapmaker" / "gui" / "icon.png")
    big.save(ROOT / "installer" / "MapMaker.ico", sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
    print("icon written")
