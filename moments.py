"""Мемы-моменты из игры: самый смешной фрагмент русской нарезки «мемы бравл старс».

Видео не скачивается и не перезаливается: YouTube не даёт скачивать его с серверов GitHub,
а нарезки принадлежат их авторам. Вместо этого:
1. yt-dlp ищет нарезки на YouTube (только список, без скачивания);
2. если YouTube отдаёт «самые пересматриваемые моменты», берём их пик;
3. Gemini смотрит ролик (Google разрешает анализ публичных видео YouTube) и выбирает
   смешной игровой момент рядом с пиком или в начале ролика;
4. пост: ссылка с таймкодом, Telegram показывает мини-плеер над текстом, автор указан.
"""
import html
import json
import os
import re
import subprocess
import urllib.error
import urllib.request

import sources

QUERIES = ["мемы бравл старс", "смешные моменты бравл старс", "бравл старс приколы нарезка"]
API = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
PROMPT = """Это нарезка смешных моментов из игры Brawl Stars. Найди в показанном фрагменте ОДИН самый
смешной момент именно из игры (геймплей, забавная ситуация в матче), длиной 5-40 секунд.
Без моментов с политикой, оскорблениями, рекламой, 18+ и реальными людьми крупным планом.
Время считай в секундах от начала ВСЕГО ролика.
Ответь только JSON: {"start": секунда начала, "end": секунда конца,
"title": "короткая смешная подпись по-русски до 80 символов", "ok": true}
Если подходящего момента нет, ответь {"ok": false}."""


def search(query, limit=15):
    done = subprocess.run(
        ["yt-dlp", "--flat-playlist", "--print",
         "%(id)s\t%(duration)s\t%(channel_id)s\t%(channel)s\t%(title)s", f"ytsearch{limit}:{query}"],
        capture_output=True, text=True, timeout=120)
    rows = []
    for line in done.stdout.splitlines():
        parts = line.split("\t")
        if len(parts) == 5:
            video_id, duration, channel_id, channel, title = parts
            rows.append({"id": video_id, "duration": float(duration) if duration not in ("NA", "") else 0,
                         "channel_id": channel_id, "channel": channel, "title": title})
    return rows


def good_compilation(video, used):
    return (video["id"] not in used and 120 <= video["duration"] <= 1800 and sources.russian(video["title"])
            and not sources.BAD_WORDS.search(video["title"]))


def replay_peak(video_id, duration):
    """Самый пересматриваемый момент по данным YouTube, если он их отдаёт; иначе None."""
    try:
        done = subprocess.run(["yt-dlp", "--skip-download", "-j", f"https://www.youtube.com/watch?v={video_id}"],
                              capture_output=True, text=True, timeout=120)
        heat = json.loads(done.stdout).get("heatmap") or []
    except (ValueError, subprocess.TimeoutExpired, OSError):
        return None
    # Первые секунды пересматривают всегда (заставка), их не считаем.
    heat = [h for h in heat if h.get("start_time", 0) > max(10, duration * 0.05)]
    if not heat:
        return None
    peak = max(heat, key=lambda h: h.get("value", 0))
    return int(peak["start_time"])


def ask_video(video_id, start, end, models):
    key = os.environ.get("GEMINI_API_KEY")
    body = json.dumps({
        "contents": [{"role": "user", "parts": [
            {"file_data": {"file_uri": f"https://www.youtube.com/watch?v={video_id}"},
             "video_metadata": {"start_offset": f"{start}s", "end_offset": f"{end}s"}},
            {"text": PROMPT}]}],
        "generationConfig": {"temperature": 0.4, "media_resolution": "MEDIA_RESOLUTION_LOW"},
    }).encode()
    for model in models:
        request = urllib.request.Request(API.format(model=model), data=body,
                                         headers={"Content-Type": "application/json", "x-goog-api-key": key})
        try:
            with urllib.request.urlopen(request, timeout=240) as response:
                reply = json.load(response)
        except (urllib.error.URLError, TimeoutError, OSError) as error:
            detail = error.read().decode("utf-8", "replace")[:200] if hasattr(error, "read") else error
            print(f"Gemini ({model}) не посмотрел видео: {detail}")
            continue
        text = "".join(p.get("text", "") for p in (reply.get("candidates") or [{}])[0]
                       .get("content", {}).get("parts", []))
        match = re.search(r"\{.*\}", text, re.DOTALL)
        if match:
            return json.loads(match.group(0))
    return None


def moment_post(video, start, title):
    url = f"https://www.youtube.com/watch?v={video['id']}&t={start}s"
    channel_url = f"https://www.youtube.com/channel/{video['channel_id']}"
    text = (
        "<b>😂 МОМЕНТ ИЗ ИГРЫ</b>\n"
        f"<i>{sources.clean(title, 120)}</i>\n\n"
        f'👤 Автор нарезки: <a href="{channel_url}">{html.escape(video["channel"], quote=False)}</a>\n'
        f"▶️ Плеер над постом, смотри с {start // 60}:{start % 60:02d}\n\n"
        "Ставь 😂 если зашло\n\n"
        "#BrawlStars #мем"
    )
    return {"type": "text", "text": text, "video_url": url, "source": url,
            "source_name": f"YouTube: {video['channel']}", "kind": "meme", "moment_video": video["id"]}


def find_moments(count, used, models):
    """До count постов-моментов из нарезок, которых ещё не было (used: id роликов)."""
    posts = []
    for query in QUERIES:
        if len(posts) >= count:
            break
        try:
            videos = [v for v in search(query) if good_compilation(v, used)]
        except (OSError, subprocess.TimeoutExpired) as error:
            print(f"Поиск нарезок не удался: {error}")
            continue
        for video in videos:
            if len(posts) >= count:
                break
            used.add(video["id"])
            peak = replay_peak(video["id"], video["duration"])
            start = max(peak - 30, 0) if peak is not None else 15
            window_end = min(start + 90 if peak is not None else 300, int(video["duration"]))
            print(f"Нарезка {video['id']} ({video['channel']}): "
                  f"{'пик просмотров ' + str(peak) + ' с' if peak is not None else 'пика нет, смотрю начало'}")
            answer = ask_video(video["id"], start, window_end, models)
            if not answer or not answer.get("ok"):
                continue
            moment = int(answer.get("start", -1))
            # Момент вне показанного фрагмента Gemini не видел, значит выдумал.
            if not start <= moment <= window_end or sources.BAD_WORDS.search(str(answer.get("title", ""))):
                print(f"Момент не подходит: {answer}")
                continue
            posts.append(moment_post(video, moment, str(answer.get("title") or video["title"])))
            print(f"Момент: {video['id']} с {moment} с")
    return posts
