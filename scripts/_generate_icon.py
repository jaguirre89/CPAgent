#!/usr/bin/env python3
"""Generate a CPAgent icon PNG + .icns. Used once at install time."""

import os
import sys
from pathlib import Path

try:
    from PIL import Image, ImageDraw, ImageFont
except ImportError:
    print("Pillow not installed; skipping icon generation")
    sys.exit(0)

SCRIPT_DIR = Path(os.path.dirname(os.path.abspath(__file__)))
OUT_PNG = SCRIPT_DIR / "CPAgent_icon.png"
OUT_ICONSET = SCRIPT_DIR / "CPAgent.iconset"
OUT_ICNS = SCRIPT_DIR / "CPAgent.icns"

SIZE = 1024
BG = (14, 165, 233)  # tailwind sky-500
FG = (255, 255, 255)
ACCENT = (15, 23, 42)  # slate-900

def make_icon(size: int) -> Image.Image:
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    # Rounded square background
    radius = int(size * 0.22)
    draw.rounded_rectangle([0, 0, size - 1, size - 1], radius=radius, fill=BG)

    # Big "CPA" text
    text = "CPA"
    font_size = int(size * 0.42)
    font = None
    for fontname in (
        "/System/Library/Fonts/SFCompact.ttf",
        "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
        "/System/Library/Fonts/Helvetica.ttc",
    ):
        try:
            font = ImageFont.truetype(fontname, font_size)
            break
        except (OSError, IOError):
            continue
    if font is None:
        font = ImageFont.load_default()

    bbox = draw.textbbox((0, 0), text, font=font)
    w = bbox[2] - bbox[0]
    h = bbox[3] - bbox[1]
    x = (size - w) // 2 - bbox[0]
    y = int(size * 0.24) - bbox[1]
    draw.text((x, y), text, font=font, fill=FG)

    # Subtitle
    sub = "AGENT"
    try:
        sub_font = ImageFont.truetype("/System/Library/Fonts/Helvetica.ttc", int(size * 0.14))
    except (OSError, IOError):
        sub_font = font
    sub_bbox = draw.textbbox((0, 0), sub, font=sub_font)
    sw = sub_bbox[2] - sub_bbox[0]
    sx = (size - sw) // 2 - sub_bbox[0]
    sy = int(size * 0.66) - sub_bbox[1]
    draw.text((sx, sy), sub, font=sub_font, fill=ACCENT)
    return img


def main():
    icon = make_icon(SIZE)
    icon.save(OUT_PNG)
    print(f"Wrote {OUT_PNG}")

    OUT_ICONSET.mkdir(exist_ok=True)
    targets = [(16, 1), (16, 2), (32, 1), (32, 2), (128, 1), (128, 2), (256, 1), (256, 2), (512, 1), (512, 2)]
    for size, scale in targets:
        w = size * scale
        resized = icon.resize((w, w), Image.LANCZOS)
        suffix = f"icon_{size}x{size}{'@2x' if scale == 2 else ''}.png"
        resized.save(OUT_ICONSET / suffix)

    # iconutil will be invoked by the shell wrapper
    print(f"Wrote iconset at {OUT_ICONSET}")


if __name__ == "__main__":
    main()
