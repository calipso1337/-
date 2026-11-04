#!/usr/bin/env python3
"""Рисует таблицу картинкой: в постах Telegram настоящих таблиц нет.

python3 tools/make_table.py images/table.png data.json

Формат data.json:
{"title": "...", "subtitle": "...", "columns": ["Боец", "Что", "Было → стало"],
 "sections": [{"name": "Усиления", "tone": "good", "rows": [["Хэнк", "Здоровье", "5500 → 6000"]]}]}
"""
import json
import sys
from pathlib import Path

from PIL import Image, ImageDraw

sys.path.insert(0, str(Path(__file__).resolve().parent))

from make_image import BOLD, HANDLE, REGULAR, WHITE, star
from PIL import ImageFont

WIDTH = 1080
MARGIN = 44
ROW = 66
HEADER = 58
SECTION = 74
COLUMNS = (0, 300, 700)
TOP = (24, 18, 66)
BOTTOM = (15, 23, 42)
ACCENT = (253, 224, 71)
TONES = {
    "good": ((16, 185, 129), (134, 239, 172)),
    "bad": ((239, 68, 68), (252, 165, 165)),
    "neutral": ((59, 130, 246), (147, 197, 253)),
}


def face(path, size):
    return ImageFont.truetype(path, size)


def gradient(height):
    image = Image.new("RGB", (WIDTH, height))
    draw = ImageDraw.Draw(image)
    for y in range(height):
        mix = y / height
        draw.line((0, y, WIDTH, y), fill=tuple(int(a + (b - a) * mix) for a, b in zip(TOP, BOTTOM)))
    return image.convert("RGBA")


def check_width(draw, text, font, width):
    if draw.textlength(text, font=font) > width:
        raise ValueError(f"Текст не помещается в ячейку: {text}")


def render(output, data):
    sections = data["sections"]
    height = 200 + HEADER + sum(SECTION + ROW * len(item["rows"]) for item in sections) + 100
    image = gradient(height)
    layer = Image.new("RGBA", image.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(layer)
    star(draw, WIDTH - 90, 80, 150, 0.3, (*ACCENT, 34))

    draw.text((MARGIN, 44), data["title"], font=face(BOLD, 56), fill=WHITE)
    draw.text((MARGIN, 124), data["subtitle"], font=face(REGULAR, 28), fill=(203, 213, 225))

    y = 200
    inner = WIDTH - 2 * MARGIN
    draw.rounded_rectangle((MARGIN, y, WIDTH - MARGIN, y + HEADER), radius=14, fill=(*ACCENT, 255))
    label = face(BOLD, 26)
    positions = [MARGIN + 22 + column for column in COLUMNS]
    for x, column in zip(positions, data["columns"]):
        draw.text((x, y + 14), column, font=label, fill=(24, 18, 66))
    y += HEADER

    cell = face(REGULAR, 28)
    name = face(BOLD, 28)
    value = face(BOLD, 28)
    for item in sections:
        strong, soft = TONES[item["tone"]]
        draw.text((MARGIN + 6, y + 24), item["name"], font=face(BOLD, 30), fill=strong)
        y += SECTION
        for index, (brawler, change, result) in enumerate(item["rows"]):
            if index % 2 == 0:
                draw.rectangle((MARGIN, y, MARGIN + inner, y + ROW), fill=(*WHITE, 14))
            draw.rectangle((MARGIN, y, MARGIN + 6, y + ROW), fill=strong)
            check_width(draw, brawler, name, COLUMNS[1] - 30)
            check_width(draw, change, cell, COLUMNS[2] - COLUMNS[1] - 30)
            check_width(draw, result, value, inner - COLUMNS[2] - 30)
            draw.text((positions[0], y + 17), brawler, font=name, fill=WHITE)
            draw.text((positions[1], y + 17), change, font=cell, fill=(226, 232, 240))
            draw.text((positions[2], y + 17), result, font=value, fill=soft)
            y += ROW

    draw.text((MARGIN, y + 34), HANDLE, font=face(BOLD, 26), fill=ACCENT)
    final = Image.alpha_composite(image, layer).convert("RGB")
    output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    final.save(output, optimize=True)


def main():
    render(sys.argv[1], json.loads(Path(sys.argv[2]).read_text(encoding="utf-8")))


if __name__ == "__main__":
    main()
