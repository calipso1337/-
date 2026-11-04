#!/usr/bin/env python3
"""Рисует оригинальный мем с подписями сверху и снизу.

python3 tools/make_meme.py images/meme.png "Верхняя подпись" "Нижняя подпись" --style 0
"""
import argparse
import re
import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont, ImageOps

sys.path.insert(0, str(Path(__file__).resolve().parent))

from make_image import BOLD, HANDLE, star

SIZE = 1080
MARGIN = 70
PALETTES = [
    ((30, 27, 75), (190, 24, 93), (253, 224, 71)),
    ((12, 74, 110), (21, 128, 61), (255, 255, 255)),
    ((88, 28, 135), (37, 99, 235), (250, 204, 21)),
    ((127, 29, 29), (217, 119, 6), (255, 255, 255)),
    ((15, 23, 42), (13, 148, 136), (190, 242, 100)),
]
NOT_DRAWABLE = re.compile("[\U00010000-\U0010ffff☀-➿️‍]")


def clean(text):
    return " ".join(NOT_DRAWABLE.sub("", text).split())


def wrap(draw, text, font, width):
    lines, line = [], ""
    for word in text.split():
        candidate = f"{line} {word}".strip()
        if draw.textlength(candidate, font=font) <= width:
            line = candidate
        else:
            if line:
                lines.append(line)
            line = word
    if line:
        lines.append(line)
    return lines


def draw_block(image, text, top, height, color):
    draw = ImageDraw.Draw(image)
    width = SIZE - 2 * MARGIN
    for size in range(92, 31, -4):
        font = ImageFont.truetype(BOLD, size)
        lines = wrap(draw, text, font, width)
        line_height = int(size * 1.25)
        widest = max(draw.textlength(line, font=font) for line in lines)
        if widest <= width and len(lines) * line_height <= height:
            break
    else:
        raise ValueError(f"Подпись не помещается: {text}")
    y = top + (height - len(lines) * line_height) // 2
    for line in lines:
        x = (SIZE - draw.textlength(line, font=font)) // 2
        draw.text((x + 4, y + 4), line, font=font, fill=(0, 0, 0))
        draw.text((x, y), line, font=font, fill=color)
        y += line_height


def render(output, top, bottom, style=0):
    start, end, accent = PALETTES[style % len(PALETTES)]
    gradient = Image.linear_gradient("L").resize((SIZE, SIZE))
    image = ImageOps.colorize(gradient, black=start, white=end).convert("RGBA")
    layer = Image.new("RGBA", image.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(layer)
    star(draw, 950, 130, 140, 0.3, (*accent, 40))
    star(draw, 130, 960, 110, 0.9, (*accent, 40))
    image = Image.alpha_composite(image, layer)
    draw = ImageDraw.Draw(image)
    draw.rectangle((MARGIN, SIZE // 2 - 3, SIZE - MARGIN, SIZE // 2 + 3), fill=accent)
    draw_block(image, clean(top), 70, 420, (255, 255, 255))
    draw_block(image, clean(bottom), SIZE // 2 + 30, 400, accent)
    draw.text((MARGIN, SIZE - 58), HANDLE, font=ImageFont.truetype(BOLD, 26), fill=accent)
    output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    image.convert("RGB").save(output, optimize=True)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("output")
    parser.add_argument("top")
    parser.add_argument("bottom")
    parser.add_argument("--style", type=int, default=0)
    args = parser.parse_args()
    render(args.output, args.top, args.bottom, args.style)


if __name__ == "__main__":
    main()
