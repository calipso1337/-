#!/usr/bin/env python3
"""Рисует анимированную обложку поста в MP4. Нужны Pillow и ffmpeg.

python3 tools/make_video.py videos/post.mp4 --kind news --title "Заголовок" \
    --subtitle "Подзаголовок" --stat 120 --stat-label "очков опыта"
"""
import argparse
import math
import random
import re
import subprocess
import sys
from pathlib import Path

from PIL import Image, ImageDraw

sys.path.insert(0, str(Path(__file__).resolve().parent))

from make_image import (
    BOLD,
    HANDLE,
    HEIGHT,
    INK,
    KINDS,
    REGULAR,
    SCALE,
    WHITE,
    WIDTH,
    background,
    draw_badge,
    draw_lines,
    fit,
    font,
    star,
)

FPS = 30
SECONDS = 6
PANEL = (880, 190, 1216, 560)


def ease(value):
    value = min(max(value, 0.0), 1.0)
    return 1 - (1 - value) ** 3


def transparent():
    return Image.new("RGBA", (WIDTH * SCALE, HEIGHT * SCALE), (0, 0, 0, 0))


def shrink(layer):
    return layer.resize((WIDTH, HEIGHT), Image.LANCZOS)


def text_layers(kind, title, subtitle, has_stat):
    accent = KINDS[kind][3]
    badge = transparent()
    draw_badge(ImageDraw.Draw(badge), KINDS[kind][0], accent)

    width = 760 if has_stat else 1150
    measure = ImageDraw.Draw(badge)
    face, lines, height = fit(measure, title, BOLD, width, 340, 78, 40)
    title_layer = transparent()
    y = draw_lines(ImageDraw.Draw(title_layer), 64 * SCALE, 148 * SCALE, lines, face, height, WHITE)

    subtitle_layer = transparent()
    if subtitle:
        face, lines, height = fit(measure, subtitle, REGULAR, width, 150, 34, 22)
        draw_lines(
            ImageDraw.Draw(subtitle_layer),
            64 * SCALE,
            y + 18 * SCALE,
            lines,
            face,
            height,
            WHITE,
            shadow=False,
        )

    footer = transparent()
    ImageDraw.Draw(footer).text((64 * SCALE, 650 * SCALE), HANDLE, font=font(BOLD, 26), fill=accent)
    return [shrink(layer) for layer in (badge, title_layer, subtitle_layer, footer)]


def panel_layer(kind, stat_label):
    accent = KINDS[kind][3]
    layer = transparent()
    draw = ImageDraw.Draw(layer)
    box = tuple(value * SCALE for value in PANEL)
    draw.rounded_rectangle(box, radius=36 * SCALE, fill=(*INK, 190), outline=accent, width=4 * SCALE)
    if stat_label:
        face, lines, height = fit(draw, stat_label, REGULAR, 290, 130, 30, 20)
        y = 410 * SCALE
        for line in lines:
            width = draw.textlength(line, font=face)
            draw.text((1048 * SCALE - width / 2, y), line, font=face, fill=WHITE)
            y += height
    return shrink(layer)


def counted(stat, progress):
    """Число в подписи считается вверх, остальной текст остаётся на месте."""
    match = re.search(r"\d[\d ]*", stat)
    if not match:
        return stat
    target = int(match[0].replace(" ", ""))
    value = round(target * ease(progress))
    number = f"{value:,}".replace(",", " ") if " " in match[0] else str(value)
    return stat[: match.start()] + number + stat[match.end() :]


def stat_layer(kind, stat, progress):
    layer = Image.new("RGBA", (WIDTH * SCALE, HEIGHT * SCALE), (0, 0, 0, 0))
    draw = ImageDraw.Draw(layer)
    face, _, _ = fit(draw, stat, BOLD, 290, 150, 96, 40)
    text = counted(stat, progress)
    width = draw.textlength(text, font=face)
    draw.text((1048 * SCALE - width / 2, 270 * SCALE), text, font=face, fill=KINDS[kind][3])
    return layer.resize((WIDTH, HEIGHT), Image.BILINEAR)


def faded(layer, amount):
    if amount >= 1:
        return layer
    copy = layer.copy()
    copy.putalpha(copy.getchannel("A").point(lambda value: int(value * amount)))
    return copy


def slide(canvas, layer, delay, moment, shift=70):
    progress = ease((moment - delay) / 0.6)
    if progress <= 0:
        return
    moved = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
    moved.paste(faded(layer, progress), (int(-shift * (1 - progress)), 0))
    canvas.alpha_composite(moved)


def motion_layer(kind, stars, moment):
    accent = KINDS[kind][3]
    layer = Image.new("RGBA", (WIDTH, HEIGHT), (0, 0, 0, 0))
    draw = ImageDraw.Draw(layer)
    for x, y, radius, speed, phase in stars:
        star(
            draw,
            x + 40 * math.sin(moment * speed + phase),
            (y - moment * 34 * speed) % (HEIGHT + 200) - 100,
            radius,
            phase + moment * speed,
            (*accent, 58),
        )
    sweep = (moment % SECONDS) / SECONDS * (WIDTH + HEIGHT + 500) - HEIGHT - 250
    draw.polygon(
        [
            (sweep, HEIGHT),
            (sweep + 150, HEIGHT),
            (sweep + 150 + HEIGHT, 0),
            (sweep + HEIGHT, 0),
        ],
        fill=(*WHITE, 22),
    )
    return layer


def render(output, kind, title, subtitle="", stat="", stat_label=""):
    if kind not in KINDS:
        raise ValueError(f"Неизвестная рубрика: {kind}")
    base = background(kind).resize((WIDTH, HEIGHT), Image.LANCZOS)
    badge, title_layer, subtitle_layer, footer = text_layers(kind, title, subtitle, bool(stat))
    panel = panel_layer(kind, stat_label) if stat else None
    rng = random.Random(title)
    stars = [
        (
            rng.randint(0, WIDTH),
            rng.randint(0, HEIGHT),
            rng.randint(14, 42),
            rng.uniform(0.5, 1.4),
            rng.uniform(0, math.tau),
        )
        for _ in range(16)
    ]

    output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    command = [
        "ffmpeg", "-y", "-loglevel", "error",
        "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{WIDTH}x{HEIGHT}", "-r", str(FPS), "-i", "-",
        "-c:v", "libx264", "-preset", "slow", "-crf", "24", "-pix_fmt", "yuv420p",
        "-movflags", "+faststart", str(output),
    ]
    encoder = subprocess.Popen(command, stdin=subprocess.PIPE)
    for index in range(FPS * SECONDS):
        moment = index / FPS
        frame = base.copy()
        frame.alpha_composite(motion_layer(kind, stars, moment))
        slide(frame, badge, 0.1, moment)
        slide(frame, title_layer, 0.35, moment)
        slide(frame, subtitle_layer, 0.7, moment)
        slide(frame, footer, 1.0, moment)
        if panel:
            slide(frame, panel, 0.9, moment, shift=-70)
            if moment >= 1.2:
                frame.alpha_composite(stat_layer(kind, stat, (moment - 1.2) / 1.6))
        encoder.stdin.write(frame.convert("RGB").tobytes())
    encoder.stdin.close()
    if encoder.wait() != 0:
        raise RuntimeError("ffmpeg не смог собрать видео")


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
