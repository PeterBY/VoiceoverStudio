"""Generate the app icon: assets/app.png (256, runtime iconphoto) + assets/app.ico (exe).

A narrator microphone with sound waves on a rounded blue badge (darkly-theme palette).
Pure Pillow, drawn at 4x supersampling. Tweak, rerun, rebuild bundles:
    .venv-app/bin/python packaging/make_icon.py
"""
import struct
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "assets"

S = 4          # supersample factor
B = 1024       # base canvas
C = B * S

GRAD_TOP = (74, 134, 196)
GRAD_BOT = (28, 48, 76)
GLYPH = (245, 248, 252, 255)
WAVE2 = (245, 248, 252, 150)


def sc(*xy):
    return [v * S for v in xy]


def badge(img):
    """Rounded-square badge with a vertical gradient."""
    grad = Image.new("RGBA", (C, C))
    gd = ImageDraw.Draw(grad)
    for y in range(C):
        t = y / (C - 1)
        col = tuple(round(a + (b - a) * t) for a, b in zip(GRAD_TOP, GRAD_BOT))
        gd.line([(0, y), (C, y)], fill=col + (255,))
    mask = Image.new("L", (C, C), 0)
    ImageDraw.Draw(mask).rounded_rectangle(sc(64, 64, 960, 960), radius=224 * S, fill=255)
    img.paste(grad, (0, 0), mask)


def glyph(img):
    d = ImageDraw.Draw(img)
    # sound waves to the right of the mic (second one fainter)
    d.arc(sc(148, 92, 748, 692), start=-42, end=42, fill=GLYPH, width=44 * S)
    d.arc(sc(53, -3, 843, 787), start=-34, end=34, fill=WAVE2, width=44 * S)
    # microphone: capsule, U-holder, stem, base
    d.rounded_rectangle(sc(370, 240, 526, 545), radius=78 * S, fill=GLYPH)
    d.arc(sc(280, 337, 616, 673), start=0, end=180, fill=GLYPH, width=46 * S)
    d.rounded_rectangle(sc(425, 660, 471, 775), radius=23 * S, fill=GLYPH)
    d.rounded_rectangle(sc(318, 762, 578, 808), radius=23 * S, fill=GLYPH)


def save_ico(base, path, sizes):
    """Spec-correct ICO writer: 32bpp DIB entries with the AND mask included.
    Pillow's own writer emits PNG entries (unreadable for Tk) or, with
    bitmap_format="bmp", DIBs whose AND mask is missing — strict parsers then
    read past the pixel data and render garbage."""
    entries = []
    for s in sizes:
        arr = np.asarray(base.resize((s, s), Image.LANCZOS), dtype=np.uint8)
        bgra = arr[::-1, :, [2, 1, 0, 3]].tobytes()          # bottom-up BGRA
        row_bytes = ((s + 31) // 32) * 4
        bits = np.packbits(arr[::-1, :, 3] == 0, axis=1)     # 1 = transparent
        mask = np.zeros((s, row_bytes), dtype=np.uint8)
        mask[:, : bits.shape[1]] = bits
        header = struct.pack("<IiiHHIIiiII", 40, s, s * 2, 1, 32, 0,
                             len(bgra) + mask.size, 0, 0, 0, 0)
        entries.append((s, header + bgra + mask.tobytes()))
    blob = struct.pack("<HHH", 0, 1, len(entries))
    offset = 6 + 16 * len(entries)
    for s, data in entries:
        blob += struct.pack("<BBBBHHII", s % 256, s % 256, 0, 0, 1, 32, len(data), offset)
        offset += len(data)
    Path(path).write_bytes(blob + b"".join(d for _, d in entries))


def main():
    OUT.mkdir(exist_ok=True)
    img = Image.new("RGBA", (C, C), (0, 0, 0, 0))
    badge(img)
    glyph(img)
    base = img.resize((B, B), Image.LANCZOS)
    base.resize((256, 256), Image.LANCZOS).save(OUT / "app.png")
    save_ico(base, OUT / "app.ico", [16, 24, 32, 48, 64, 128, 256])
    print(f"-> {OUT / 'app.png'} + {OUT / 'app.ico'}")


if __name__ == "__main__":
    main()
