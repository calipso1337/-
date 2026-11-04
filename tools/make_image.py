#!/usr/bin/env python3
"""Рисует обложку поста. Нужен только автору постов, боту в GitHub Actions он не нужен.

python3 tools/make_image.py images/post.png --kind news --title "Заголовок" \
    --subtitle "Подзаголовок" --stat 5000 --stat-label "кубков"
"""
import argparse
import math
import random
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

ASSETS = Path(__file__).resolve().parent / "assets"
BOLD = str(ASSETS / "DejaVuSans-Bold.ttf")
REGULAR = str(ASSETS / "DejaVuSans.ttf")
WIDTH, HEIGHT, SCALE = 1280, 720, 2
HANDLE = "@All_About_Brawl"
WHITE = (255, 255, 255)
INK = (17, 12, 40)

# Рубрика: подпись, два цвета фона, акцент.
KINDS = {
    "news": ("НОВОСТЬ", (58, 12, 163), (247, 37, 133), (255, 209, 102)),
    "update": ("ОБНОВЛЕНИЕ", (15, 32, 90), (0, 150, 199), (255, 209, 102)),
    "buff": ("УСИЛЕНИЯ", (0, 77, 64), (16, 185, 129), (217, 249, 157)),
    "nerf": ("ОСЛАБЛЕНИЯ", (90, 12, 40), (239, 68, 68), (254, 215, 170)),
    "brawler": ("НОВЫЕ БОЙЦЫ", (49, 16, 120), (217, 70, 239), (253, 224, 71)),
    "poll": ("ОПРОС", (7, 89, 133), (124, 58, 237), (167, 243, 208)),
    "warning": ("ВАЖНО", (69, 26, 3), (234, 88, 12), (254, 240, 138)),
    "esports": ("КИБЕРСПОРТ", (10, 10, 45), (30, 64, 175), (251, 191, 36)),
    "creator": ("КРЕАТОРЫ", (127, 29, 29), (219, 39, 119), (253, 224, 71)),
    "record": ("РЕКОРД", (66, 32, 6), (202, 138, 4), (255, 255, 255)),
    "fun": ("ПРИКОЛ", (6, 78, 59), (37, 99, 235), (253, 224, 71)),
    "meme": ("МЕМ", (31, 41, 55), (219, 39, 119), (253, 224, 71)),
}


def font(path, size):
    return ImageFont.truetype(path, size * SCALE)


def star(draw, x, y, radius, rotation, fill):
    points = []
    for step in range(10):
        length = radius if step % 2 == 0 else radius * 0.45
        angle = rotation + step * math.pi / 5
        points.append((x + length * math.cos(angle), y + length * math.sin(angle)))
    draw.polygon(points, fill=fill)


def wrap(draw, text, face, width):
    lines = []
    for paragraph in text.split("\n"):
        line = ""
        for word in paragraph.split():
            candidate = f"{line} {word}".strip()
            if draw.textlength(candidate, font=face) <= width:
                line = candidate
            else:
                if line:
                    lines.append(line)
                line = word
        lines.append(line)
    return lines


def fit(draw, text, path, width, height, largest, smallest):
    for size in range(largest, smallest - 1, -2):
        face = font(path, size)
        lines = wrap(draw, text, face, width * SCALE)
        widest = max(draw.textlength(line, font=face) for line in lines)
        total = len(lines) * size * 1.22
        if widest <= width * SCALE and total <= height:
            return face, lines, int(size * 1.22) * SCALE
    raise ValueError(f"Текст не помещается на картинку: {text}")


def background(kind):
    _, start, end, accent = KINDS[kind]
    image = Image.new("RGB", (WIDTH * SCALE, HEIGHT * SCALE))
    pixels = image.load()
    limit = (WIDTH + HEIGHT) * SCALE
    for y in range(image.height):
        for x in range(image.width):
            mix = (x + y) / limit
            pixels[x, y] = tuple(int(a + (b - a) * mix) for a, b in zip(start, end))
    layer = Image.new("RGBA", image.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(layer)
    rng = random.Random(kind)
    for offset in range(-HEIGHT * SCALE, WIDTH * SCALE, 220 * SCALE):
        draw.polygon(
            [
                (offset, image.height),
                (offset + 90 * SCALE, image.height),
                (offset + 90 * SCALE + image.height, 0),
                (offset + image.height, 0),
            ],
            fill=(*WHITE, 12),
        )
    for _ in range(9):
        star(
            draw,
            rng.randint(0, image.width),
            rng.randint(0, image.height),
            rng.randint(24, 90) * SCALE,
            rng.random() * math.tau,
            (*accent, 26),
        )
    star(draw, image.width * 0.9, image.height * 0.12, 190 * SCALE, 0.3, (*accent, 42))
    return Image.alpha_composite(image.convert("RGBA"), layer)


def draw_lines(draw, x, y, lines, face, height, fill, shadow=True):
    for line in lines:
        if shadow:
            draw.text((x + 4 * SCALE, y + 4 * SCALE), line, font=face, fill=(0, 0, 0, 110))
        draw.text((x, y), line, font=face, fill=fill)
        y += height
    return y


def draw_badge(draw, label, accent):
    face = font(BOLD, 26)
    width = draw.textlength(label, font=face)
    box = (64 * SCALE, 54 * SCALE, 64 * SCALE + width + 56 * SCALE, 110 * SCALE)
    draw.rounded_rectangle(box, radius=28 * SCALE, fill=accent)
    draw.text((92 * SCALE, 67 * SCALE), label, font=face, fill=INK)


def render(output, kind, title, subtitle="", stat="", stat_label=""):
    if kind not in KINDS:
        raise ValueError(f"Неизвестная рубрика: {kind}")
    label, _, _, accent = KINDS[kind]
    image = background(kind)
    draw = ImageDraw.Draw(image)
    draw_badge(draw, label, accent)

    text_width = 760 if stat else 1150
    title_face, title_lines, title_height = fit(draw, title, BOLD, text_width, 340, 78, 40)
    y = 148 * SCALE
    y = draw_lines(draw, 64 * SCALE, y, title_lines, title_face, title_height, WHITE)
    if subtitle:
        face, lines, height = fit(draw, subtitle, REGULAR, text_width, 150, 34, 22)
        draw_lines(draw, 64 * SCALE, y + 18 * SCALE, lines, face, height, (*WHITE, 235), shadow=False)

    if stat:
        panel = (880 * SCALE, 190 * SCALE, 1216 * SCALE, 560 * SCALE)
        draw.rounded_rectangle(panel, radius=36 * SCALE, fill=(*INK, 130), outline=accent, width=4 * SCALE)
        face, _, _ = fit(draw, stat, BOLD, 290, 150, 96, 40)
        width = draw.textlength(stat, font=face)
        draw.text((1048 * SCALE - width / 2, 270 * SCALE), stat, font=face, fill=accent)
        if stat_label:
            face, lines, height = fit(draw, stat_label, REGULAR, 290, 130, 30, 20)
            y = 410 * SCALE
            for line in lines:
                width = draw.textlength(line, font=face)
                draw.text((1048 * SCALE - width / 2, y), line, font=face, fill=WHITE)
                y += height

    face = font(BOLD, 26)
    draw.text((64 * SCALE, 650 * SCALE), HANDLE, font=face, fill=accent)
    final = image.convert("RGB").resize((WIDTH, HEIGHT), Image.LANCZOS)
    output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    final.save(output, optimize=True)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("output")
    parser.add_argument("--kind", default="news", choices=sorted(KINDS))
    parser.add_argument("--title", required=True)
    parser.add_argument("--subtitle", default="")
    parser.add_argument("--stat", default="")
    parser.add_argument("--stat-label", default="")
    args = parser.parse_args()
    render(args.output, args.kind, args.title, args.subtitle, args.stat, args.stat_label)


if __name__ == "__main__":
    main()
