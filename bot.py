#!/usr/bin/env python3
"""Публикует следующий пост из queue.json в Telegram-канал. Без внешних зависимостей."""
import json
import os
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent
QUEUE = ROOT / "queue.json"
STATE = ROOT / "state.json"
API_BASE = os.environ.get("TELEGRAM_API_BASE", "https://api.telegram.org")
LIMITS = {"text": 4096, "photo": 1024}


def call(token, method, payload):
    url = f"{API_BASE}/bot{token}/{method}"
    data = json.dumps(payload).encode()
    for attempt in range(3):
        req = urllib.request.Request(
            url, data=data, headers={"Content-Type": "application/json"}
        )
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                return json.load(resp)
        except urllib.error.HTTPError as err:
            body = json.loads(err.read().decode() or "{}")
            wait = body.get("parameters", {}).get("retry_after")
            if err.code == 429 and wait and attempt < 2:
                time.sleep(wait)
                continue
            # Токен входит в URL, поэтому в лог идёт только описание ошибки.
            raise RuntimeError(f"Telegram {err.code}: {body.get('description')}") from None
    raise RuntimeError("Telegram: слишком много запросов")


def build(post, channel):
    kind = post.get("type", "text")
    if kind == "text":
        return "sendMessage", {"chat_id": channel, "text": post["text"]}
    if kind == "photo":
        return "sendPhoto", {
            "chat_id": channel,
            "photo": post["image"],
            "caption": post.get("text", ""),
        }
    if kind == "poll":
        return "sendPoll", {
            "chat_id": channel,
            "question": post["question"],
            "options": [{"text": o} for o in post["options"]],
        }
    raise ValueError(f"Неизвестный тип поста: {kind}")


def validate(posts):
    for i, post in enumerate(posts):
        limit = LIMITS.get(post.get("type", "text"))
        if limit and len(post.get("text", "")) > limit:
            raise ValueError(f"Пост №{i}: текст длиннее {limit} символов")


def main():
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    channel = os.environ.get("TELEGRAM_CHANNEL")
    if not token or not channel:
        sys.exit("Не заданы TELEGRAM_BOT_TOKEN и TELEGRAM_CHANNEL")

    posts = json.loads(QUEUE.read_text(encoding="utf-8"))
    validate(posts)
    state = json.loads(STATE.read_text()) if STATE.exists() else {"next": 0}
    index = state["next"]
    if index >= len(posts):
        print("Очередь закончилась, добавь новые посты в queue.json")
        return

    method, payload = build(posts[index], channel)
    call(token, method, payload)
    STATE.write_text(json.dumps({"next": index + 1}) + "\n")
    print(f"Опубликован пост {index + 1} из {len(posts)}")


if __name__ == "__main__":
    main()
