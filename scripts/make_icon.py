"""Draw the app icon (a little blob companion) to assets/icon.ico and assets/icon.png.

Run: uv run --with pillow scripts/make_icon.py
"""
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter

S = 1024  # draw big, downscale for smooth edges
OUT = Path(__file__).resolve().parent.parent / "assets"

NAVY, NAVY_LIGHT = (16, 24, 48), (30, 46, 84)
GOLD, GOLD_DARK = (240, 200, 90), (176, 132, 44)
MINT, MINT_DARK, MINT_LIGHT = (120, 226, 196), (64, 170, 150), (196, 250, 232)
INK, BLUSH, WHITE = (28, 30, 46), (255, 140, 160), (255, 255, 255)


def star(d: ImageDraw.ImageDraw, cx, cy, r, fill, outline=None, width=0):
    from math import cos, pi, sin
    pts = []
    for i in range(10):
        rr = r if i % 2 == 0 else r * 0.48
        a = -pi / 2 + i * pi / 5
        pts.append((cx + rr * cos(a), cy + rr * sin(a)))
    d.polygon(pts, fill=fill, outline=outline, width=width)


def draw() -> Image.Image:
    img = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)

    # tile: gold rim around a navy rounded square with a soft lighter center
    d.rounded_rectangle((24, 24, S - 24, S - 24), radius=220, fill=GOLD_DARK)
    d.rounded_rectangle((40, 40, S - 40, S - 40), radius=205, fill=GOLD)
    d.rounded_rectangle((72, 72, S - 72, S - 72), radius=180, fill=NAVY)
    glow = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    ImageDraw.Draw(glow).ellipse((200, 180, S - 200, S - 220), fill=NAVY_LIGHT + (255,))
    glow = glow.filter(ImageFilter.GaussianBlur(90))
    mask = Image.new("L", (S, S), 0)
    ImageDraw.Draw(mask).rounded_rectangle((72, 72, S - 72, S - 72), radius=180, fill=255)
    img.paste(glow, (0, 0), Image.composite(glow, Image.new("RGBA", (S, S)), mask))
    d = ImageDraw.Draw(img)

    # antenna with a star
    d.line((512, 400, 560, 250), fill=MINT_DARK, width=26)
    star(d, 570, 220, 82, GOLD, outline=GOLD_DARK, width=10)

    # blob body: flat-bottomed squishy dome with a shadow and a highlight
    d.ellipse((230, 820, 794, 890), fill=(8, 12, 26))  # ground shadow
    d.chord((210, 360, 814, 1000), 180, 360, fill=MINT_DARK)
    d.rounded_rectangle((210, 640, 814, 860), radius=120, fill=MINT_DARK)
    d.chord((222, 372, 802, 976), 180, 360, fill=MINT)
    d.rounded_rectangle((222, 640, 802, 842), radius=110, fill=MINT)
    d.ellipse((300, 440, 430, 540), fill=MINT_LIGHT)  # shine

    # eyes: big, with sparkles
    for ex in (395, 629):
        d.ellipse((ex - 62, 560, ex + 62, 700), fill=INK)
        d.ellipse((ex - 34, 578, ex + 6, 618), fill=WHITE)
        d.ellipse((ex + 14, 640, ex + 34, 660), fill=WHITE)

    # cheeks and smile
    for cx in (300, 724):
        d.ellipse((cx - 50, 700, cx + 50, 748), fill=BLUSH)
    d.arc((470, 690, 554, 760), 20, 160, fill=INK, width=16)
    return img


def main() -> None:
    OUT.mkdir(exist_ok=True)
    big = draw()
    big.resize((256, 256), Image.LANCZOS).save(OUT / "icon.png")
    sizes = [(n, n) for n in (16, 24, 32, 48, 64, 128, 256)]
    big.save(OUT / "icon.ico", sizes=sizes)
    print("wrote", OUT / "icon.ico", OUT / "icon.png")


if __name__ == "__main__":
    main()
