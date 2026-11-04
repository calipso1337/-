#!/usr/bin/env python3
"""Публикует следующий пост из queue.json в Telegram-канал. Без внешних зависимостей.

python3 bot.py          опубликовать следующий пост
python3 bot.py --check  проверить неопубликованные посты, ничего не отправляя
"""
import html
import json
import mimetypes
import os
import re
import secrets
import sys
import time
import urllib.error
import urllib.request
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parent
QUEUE = ROOT / "queue.json"
STATE = ROOT / "state.json"
API_BASE = os.environ.get("TELEGRAM_API_BASE", "https://api.telegram.org")
LIMITS = {"text": 4096, "photo": 1024}
SHARE_URL = "https://t.me/share/url?url=https://t.me/All_About_Brawl"
SHARE_BUTTON = {"inline_keyboard": [[{"text": "📤 Поделиться каналом", "url": SHARE_URL}]]}
ALLOWED_TAGS = {"b", "i", "u", "s", "a", "code", "blockquote", "tg-spoiler"}


class Markup(HTMLParser):
    """Проверяет разметку Telegram и считает видимые символы."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.stack, self.visible, self.errors = [], [], []

    def handle_starttag(self, tag, attrs):
        if tag not in ALLOWED_TAGS:
            self.errors.append(f"тег <{tag}> не поддерживается")
        self.stack.append(tag)

    def handle_endtag(self, tag):
        if not self.stack or self.stack.pop() != tag:
            self.errors.append(f"лишний или перепутанный тег </{tag}>")

    def handle_data(self, data):
        self.visible.append(data)


def inspect_markup(text):
    """Возвращает (ошибки, число видимых символов)."""
    errors = []
    if re.search(r"&(?!(lt|gt|amp|quot);)", text):
        errors.append("символ & нужно писать как &amp;")
    if re.search(r"<(?!/?[a-z-]+(\s[^<>]*)?>)", text):
        errors.append("символ < нужно писать как &lt;")
    parser = Markup()
    parser.feed(text)
    parser.close()
    errors += parser.errors
    if parser.stack:
        errors.append(f"не закрыт тег <{parser.stack[-1]}>")
    return errors, len("".join(parser.visible))


def full_text(post):
    """Текст поста со строкой источника, которую бот добавляет сам."""
    text = post.get("text", "").strip()
    if not post.get("source"):
        return text
    url = html.escape(post["source"], quote=True)
    name = html.escape(post.get("source_name") or urlsplit(post["source"]).hostname or "ссылка")
    return f'{text}\n\n<a href="{url}">Источник: {name}</a>'


def is_url(image):
    return image.startswith(("https://", "http://"))


def parse_reply(raw):
    try:
        return json.loads(raw.decode("utf-8", "replace") or "{}")
    except ValueError:
        return {}


def send(url, data, headers):
    for attempt in range(3):
        request = urllib.request.Request(url, data=data, headers=headers)
        try:
            with urllib.request.urlopen(request, timeout=60) as response:
                return json.load(response)
        except urllib.error.HTTPError as error:
            body = parse_reply(error.read())
            wait = body.get("parameters", {}).get("retry_after")
            if error.code == 429 and wait and attempt < 2:
                time.sleep(wait)
                continue
            if error.code >= 500 and attempt < 2:
                time.sleep(5)
                continue
            # Токен входит в URL, поэтому в лог идёт только описание ошибки.
            raise RuntimeError(f"Telegram {error.code}: {body.get('description')}") from None
        except urllib.error.URLError as error:
            if attempt == 2:
                raise RuntimeError(f"Нет связи с Telegram: {error.reason}") from None
            time.sleep(5)
    raise RuntimeError("Telegram: слишком много запросов")


def call(token, method, payload):
    return send(
        f"{API_BASE}/bot{token}/{method}",
        json.dumps(payload).encode(),
        {"Content-Type": "application/json"},
    )


def send_file(token, method, fields, field_name, path):
    boundary = secrets.token_hex(16)
    body = bytearray()
    for name, value in fields.items():
        body += (
            f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"\r\n\r\n{value}\r\n'
        ).encode()
    kind = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    body += (
        f'--{boundary}\r\nContent-Disposition: form-data; name="{field_name}"; '
        f'filename="{path.name}"\r\nContent-Type: {kind}\r\n\r\n'
    ).encode()
    body += path.read_bytes() + f"\r\n--{boundary}--\r\n".encode()
    headers = {"Content-Type": f"multipart/form-data; boundary={boundary}"}
    return send(f"{API_BASE}/bot{token}/{method}", bytes(body), headers)


def send_photo(token, channel, image, caption, keyboard=None):
    fields = {"chat_id": channel, "caption": caption, "parse_mode": "HTML"}
    if is_url(image):
        return call(token, "sendPhoto", {**fields, "photo": image, "reply_markup": keyboard})
    if keyboard:
        fields["reply_markup"] = json.dumps(keyboard)
    return send_file(token, "sendPhoto", fields, "photo", ROOT / image)


def publish(token, channel, post):
    kind = post.get("type", "text")
    if kind == "text":
        call(
            token,
            "sendMessage",
            {
                "chat_id": channel,
                "text": full_text(post),
                "parse_mode": "HTML",
                "link_preview_options": {"is_disabled": True},
                "reply_markup": SHARE_BUTTON,
            },
        )
    elif kind == "photo":
        send_photo(token, channel, post["image"], full_text(post), SHARE_BUTTON)
    elif kind == "poll":
        # В опрос нельзя вложить картинку, поэтому она выходит сообщением перед ним.
        if post.get("image"):
            send_photo(token, channel, post["image"], full_text(post))
        call(
            token,
            "sendPoll",
            {
                "chat_id": channel,
                "question": post["question"],
                "options": [{"text": option} for option in post["options"]],
                "reply_markup": SHARE_BUTTON,
            },
        )
    else:
        raise ValueError(f"Неизвестный тип поста: {kind}")


def validate_post(post):
    errors = []
    kind = post.get("type", "text")
    if kind not in ("text", "photo", "poll"):
        return [f"неизвестный тип {kind}"]
    if kind == "poll":
        options = post.get("options", [])
        if not 0 < len(post.get("question", "")) <= 300:
            errors.append("вопрос опроса пуст или длиннее 300 символов")
        if not 2 <= len(options) <= 10 or len(set(options)) != len(options):
            errors.append("в опросе нужно от 2 до 10 разных вариантов")
        if any(not 0 < len(option) <= 100 for option in options):
            errors.append("вариант опроса пуст или длиннее 100 символов")
    if kind == "text" and not post.get("text", "").strip():
        errors.append("пустой текст")
    if kind == "photo" and not post.get("image"):
        errors.append("нет картинки")
    image = post.get("image")
    if image and not is_url(image):
        path = (ROOT / image).resolve()
        if ROOT not in path.parents or not path.is_file():
            errors.append(f"файл {image} не найден")
        elif path.stat().st_size > 10_000_000:
            errors.append(f"файл {image} больше 10 МБ")
    if kind != "poll" or image:
        limit = LIMITS["text"] if kind == "text" else LIMITS["photo"]
        markup_errors, visible = inspect_markup(full_text(post))
        errors += markup_errors
        if visible > limit:
            errors.append(f"текст длиннее {limit} символов на {visible - limit}")
    return errors


def validate(posts, start=0):
    for index, post in enumerate(posts[start:], start):
        errors = validate_post(post)
        if errors:
            raise ValueError(f"Пост №{index}: " + "; ".join(errors))


def load_state():
    return json.loads(STATE.read_text()) if STATE.exists() else {"next": 0}


def main():
    posts = json.loads(QUEUE.read_text(encoding="utf-8"))
    index = load_state()["next"]
    validate(posts, index)
    if "--check" in sys.argv:
        print(f"Проверено постов: {max(len(posts) - index, 0)}. Ошибок нет.")
        return

    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    channel = os.environ.get("TELEGRAM_CHANNEL")
    if not token or not channel:
        sys.exit("Не заданы TELEGRAM_BOT_TOKEN и TELEGRAM_CHANNEL")
    if index >= len(posts):
        print("Очередь закончилась, добавь новые посты в queue.json")
        return

    publish(token, channel, posts[index])
    STATE.write_text(json.dumps({"next": index + 1}) + "\n")
    print(f"Опубликован пост {index + 1} из {len(posts)}")


if __name__ == "__main__":
    main()
