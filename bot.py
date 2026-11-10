#!/usr/bin/env python3
"""Публикует посты в Telegram-канал строго по расписанию из config.json. Без внешних зависимостей.

python3 bot.py          если подошёл слот расписания, собрать свежие материалы и опубликовать пост
python3 bot.py --now    опубликовать следующий пост сразу
python3 bot.py --check  проверить неопубликованные посты, ничего не отправляя
python3 bot.py --sleep  поспать до ближайшего слота (не дольше MAX_SLEEP), ничего не отправляя
python3 bot.py --test meme|news|video  сразу опубликовать свежий материал из источника

Что выходит в слот: мем с Reddit (в слотах meme_slots), иначе свежая новость
(видео YouTube или статья Supercell), иначе следующий пост из queue.json.
"""
import html
import json
import mimetypes
import os
import random
import re
import secrets
import sys
import time
from datetime import datetime, timedelta, timezone
import urllib.error
import urllib.request
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parent
QUEUE = ROOT / "queue.json"
STATE = ROOT / "state.json"
CONFIG = ROOT / "config.json"
INBOX = ROOT / "inbox.json"
SEEN = ROOT / "seen.json"
API_BASE = os.environ.get("TELEGRAM_API_BASE", "https://api.telegram.org")
LIMITS = {"text": 4096, "photo": 1024, "video": 1024}
VIDEO_SIZE = {"width": 1280, "height": 720, "duration": 6}
EARLY = 6 * 60  # Запуск чуть раньше слота: бот всё подготовит и дождётся точной минуты.
LATE = 20 * 60  # Если запуск опоздал больше чем на 20 минут, слот пропускается.
WAKE = 4 * 60  # Цепочка запусков просыпается за 4 минуты до слота.
MAX_SLEEP = 5 * 3600 + 30 * 60  # Задача GitHub живёт не больше 6 часов.
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


def send_file(token, method, fields, field_name, path, data=None):
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
    body += (path.read_bytes() if data is None else data) + f"\r\n--{boundary}--\r\n".encode()
    headers = {"Content-Type": f"multipart/form-data; boundary={boundary}"}
    return send(f"{API_BASE}/bot{token}/{method}", bytes(body), headers)


def download(url):
    request = urllib.request.Request(url, headers={"User-Agent": "AllAboutBrawlBot/1.0"})
    with urllib.request.urlopen(request, timeout=30) as response:
        data = response.read(10_000_001)
    if len(data) > 10_000_000:
        raise RuntimeError("картинка больше 10 МБ")
    return data


def download_video(url):
    """Скачивает видео (mp4 или HLS со звуком) через ffmpeg, не больше 50 МБ и 2 минут."""
    import subprocess
    import tempfile

    with tempfile.TemporaryDirectory() as folder:
        out = Path(folder) / "video.mp4"
        subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", url, "-t", "120", "-c", "copy",
                        "-movflags", "+faststart", str(out)], check=True, timeout=300)
        data = out.read_bytes()
    if len(data) > 50_000_000:
        raise RuntimeError("видео больше 50 МБ")
    return data


def send_photo(token, channel, image, caption, keyboard=None, upload=False):
    fields = {"chat_id": channel, "caption": caption, "parse_mode": "HTML"}
    if is_url(image) and not upload:
        payload = {**fields, "photo": image}
        if keyboard:
            payload["reply_markup"] = keyboard
        return call(token, "sendPhoto", payload)
    if keyboard:
        fields["reply_markup"] = json.dumps(keyboard)
    if is_url(image):
        name = Path(urlsplit(image).path).name or "image.jpg"
        return send_file(token, "sendPhoto", fields, "photo", Path(name), download(image))
    return send_file(token, "sendPhoto", fields, "photo", ROOT / image)


def link_preview(post):
    if not post.get("video_url"):
        return {"is_disabled": True}
    return {"url": post["video_url"], "prefer_large_media": True, "show_above_text": True}


def send_post_photo(token, channel, post, keyboard=None):
    try:
        return send_photo(token, channel, post["image"], full_text(post), keyboard)
    except RuntimeError as error:
        if not is_url(post["image"]):
            raise
        print(f"Telegram не смог забрать картинку по ссылке: {error}")
    # Telegram не всегда скачивает чужие ссылки сам, поэтому пробуем загрузить файл.
    try:
        return send_photo(token, channel, post["image"], full_text(post), keyboard, upload=True)
    except (RuntimeError, OSError, ValueError) as error:
        # Чужая ссылка может перестать работать, поэтому есть запасная обложка.
        if not post.get("fallback_image"):
            raise RuntimeError(f"картинка не загрузилась: {error}") from None
        print(f"Картинка не загрузилась, беру запасную: {error}")
        return send_photo(token, channel, post["fallback_image"], full_text(post), keyboard)


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
                "link_preview_options": link_preview(post),
            },
        )
    elif kind == "photo":
        send_post_photo(token, channel, post)
    elif kind == "video":
        fields = {
            "chat_id": channel,
            "caption": full_text(post),
            "parse_mode": "HTML",
            "supports_streaming": "true",
            **VIDEO_SIZE,
        }
        if is_url(post["video"]):
            # Видео-мем с площадки: скачиваем по адресу из API и загружаем файлом.
            for key in VIDEO_SIZE:
                fields.pop(key)
            send_file(token, "sendVideo", fields, "video", Path("meme.mp4"), download_video(post["video"]))
        else:
            send_file(token, "sendVideo", fields, "video", ROOT / post["video"])
    elif kind == "poll":
        # В опрос нельзя вложить картинку, поэтому она выходит сообщением перед ним.
        if post.get("image"):
            send_post_photo(token, channel, post)
        call(
            token,
            "sendPoll",
            {
                "chat_id": channel,
                "question": post["question"],
                "options": [{"text": option} for option in post["options"]],
            },
        )
    else:
        raise ValueError(f"Неизвестный тип поста: {kind}")


def validate_post(post):
    errors = []
    kind = post.get("type", "text")
    if kind not in ("text", "photo", "poll", "video"):
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
    if post.get("video_url") and not (kind == "text" and post["video_url"] == post.get("source")):
        errors.append("видео по ссылке: нужен текстовый пост, а source должен совпадать с video_url")
    if kind == "photo" and not post.get("image"):
        errors.append("нет картинки")
    for image in (post.get("image"), post.get("fallback_image")):
        if image and not is_url(image):
            path = (ROOT / image).resolve()
            if ROOT not in path.parents or not path.is_file():
                errors.append(f"файл {image} не найден")
            elif path.stat().st_size > 10_000_000:
                errors.append(f"файл {image} больше 10 МБ")
    if kind == "video" and is_url(post.get("video", "")):
        if not post["video"].startswith("https://"):
            errors.append("видео по ссылке только через https")
    elif kind == "video":
        video = (ROOT / post.get("video", "")).resolve()
        if ROOT not in video.parents or video.suffix != ".mp4" or not video.is_file():
            errors.append(f"видео {post.get('video')} не найдено")
        elif video.stat().st_size > 50_000_000:
            errors.append("видео больше 50 МБ")
    if kind != "poll" or image:
        limit = LIMITS.get(kind, LIMITS["photo"])
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


def read_json(path, default):
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else default


def write_json(path, data):
    text = json.dumps(data, ensure_ascii=False, indent=1 if path != STATE else None)
    path.write_text(text + "\n", encoding="utf-8")


def load_state():
    return read_json(STATE, {"next": 0})


def load_config():
    return read_json(CONFIG, {"slots": [], "timezone_offset_hours": 3})


def jitter(config, day, slot):
    """Случайный, но одинаковый для всех запусков сдвиг слота в минутах (в пределах jitter_minutes)."""
    import hashlib

    start, end = config.get("jitter_minutes", [0, 0])
    if end <= start:
        return start
    digest = hashlib.sha256(f"{day.isoformat()} {slot}".encode()).hexdigest()
    return start + int(digest, 16) % (end - start + 1)


def slot_times(config, now):
    """Слоты вчера, сегодня и завтра: [(время в секундах, "ГГГГ-ММ-ДД ЧЧ:ММ", "ЧЧ:ММ")].

    Время слота сдвинуто на случайные минуты, чтобы посты выходили не ровно в начале часа."""
    zone = timezone(timedelta(hours=config.get("timezone_offset_hours", 3)))
    today = datetime.fromtimestamp(now, zone).date()
    result = []
    for shift in (-1, 0, 1):
        day = today + timedelta(days=shift)
        for slot in config.get("slots", []):
            hour, minute = map(int, slot.split(":"))
            moment = datetime(day.year, day.month, day.day, hour, minute, tzinfo=zone)
            moment += timedelta(minutes=jitter(config, day, slot))
            result.append((moment.timestamp(), f"{day.isoformat()} {slot}", slot))
    return sorted(result)


def due_slot(config, state, now):
    """Ближайший неотработанный слот, до которого меньше EARLY или после которого меньше LATE."""
    for moment, label, short in slot_times(config, now):
        if -EARLY <= now - moment <= LATE and label > state.get("last_slot", ""):
            return moment, label, short
    return None


def next_slot(config, state, now):
    """Ближайший слот, который ещё можно отработать."""
    for moment, label, short in slot_times(config, now):
        if now - moment <= LATE and label > state.get("last_slot", ""):
            return moment, label, short
    return None


def sleep_until_slot(config, state, now):
    slot = next_slot(config, state, now)
    wait = MAX_SLEEP if slot is None else min(max(slot[0] - WAKE - now, 0), MAX_SLEEP)
    target = f"слота {slot[1]}" if slot and slot[0] - WAKE - now <= MAX_SLEEP else "следующего пробуждения"
    print(f"Сплю {int(wait // 60)} мин до {target}")
    return wait


def test_post(token, channel, kind, config):
    import sources

    key, item_id, post = sources.latest(kind, config)
    errors = validate_post(post)
    if errors:
        raise ValueError(f"пробный пост не прошёл проверку: {'; '.join(errors)}")
    publish(token, channel, post)
    seen = read_json(SEEN, {})
    if item_id not in seen.get(key, []) and key in seen:
        sources.remember(seen, key, item_id)
        write_json(SEEN, seen)
    print(f"Пробный пост ({kind}) опубликован: {item_id}")


def choose(posts, state, inbox, config, short, day, manual):
    """Что публиковать: ("meme"|"news", элемент inbox) или ("queue", номер) или None."""
    memes = [item for item in inbox if item["kind"] == "meme"]
    news = [item for item in inbox if item["kind"] == "news"]
    news_today = state.get("news_today", {}).get(day, 0)
    if memes and short in config.get("meme_slots", []):
        return "meme", memes[0]
    if news and news_today < config.get("max_news_per_day", 4):
        return "news", news[0]
    if state["next"] < len(posts):
        return "queue", state["next"]
    if memes and not manual:
        return "meme", memes[0]
    return None


def main():
    posts = json.loads(QUEUE.read_text(encoding="utf-8"))
    state = load_state()
    config = load_config()
    inbox = read_json(INBOX, [])
    validate(posts, state["next"])
    if "--check" in sys.argv:
        for item in inbox:
            errors = validate_post(item["post"])
            if errors:
                raise ValueError(f"inbox {item['id']}: " + "; ".join(errors))
        print(f"Проверено постов: {max(len(posts) - state['next'], 0)}, в inbox: {len(inbox)}. Ошибок нет.")
        return
    if "--sleep" in sys.argv:
        time.sleep(sleep_until_slot(config, state, time.time()))
        return

    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    channel = os.environ.get("TELEGRAM_CHANNEL")
    if not token or not channel:
        sys.exit("Не заданы TELEGRAM_BOT_TOKEN и TELEGRAM_CHANNEL")

    if "--test" in sys.argv:
        test_post(token, channel, sys.argv[sys.argv.index("--test") + 1], config)
        return

    now = time.time()
    manual = "--now" in sys.argv or os.environ.get("POST_NOW") == "true"
    slot = None if manual else due_slot(config, state, now)
    if not manual and not slot:
        print("Сейчас нет слота для публикации")
        return

    if os.environ.get("SKIP_FETCH") != "1":
        import sources

        seen = read_json(SEEN, {})
        sources.fetch_all(config, inbox, seen, now, validate_post)
        write_json(SEEN, seen)
        write_json(INBOX, inbox)

    zone = timezone(timedelta(hours=config.get("timezone_offset_hours", 3)))
    if slot:
        moment, label, short = slot
        wait = moment - time.time()
        if wait > 0:
            print(f"Жду слот {label}: {int(wait)} с")
            time.sleep(wait)
        day = label[:10]
    else:
        short, day = "", datetime.fromtimestamp(now, zone).date().isoformat()

    choice = choose(posts, state, inbox, config, short, day, manual)
    if choice is None:
        print("Публиковать нечего: очередь закончилась, добавь новые посты в queue.json")
    else:
        where, item = choice
        if where == "queue":
            publish(token, channel, posts[item])
            state["next"] = item + 1
            print(f"Опубликован пост {item + 1} из {len(posts)}")
        else:
            inbox.remove(item)
            write_json(INBOX, inbox)  # Сломанный материал не должен блокировать слот навсегда.
            publish(token, channel, item["post"])
            if where == "news":
                state["news_today"] = {day: state.get("news_today", {}).get(day, 0) + 1}
            print(f"Опубликован автоматический пост ({where}): {item['id']}")
    if slot:
        state["last_slot"] = slot[1]
    state.pop("next_at", None)
    write_json(STATE, state)


if __name__ == "__main__":
    main()
