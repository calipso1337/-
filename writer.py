#!/usr/bin/env python3
"""Пополняет queue.json постами, которые пишет Gemini с поиском Google.

python3 writer.py          дописать посты, если неопубликованных меньше TARGET
python3 writer.py --dry    показать, что получилось бы, ничего не меняя
python3 writer.py --publish tip|poll|meme   сразу опубликовать один новый пост (проверка)
python3 writer.py --restyle  перерисовать обложки неопубликованных постов на официальном фоне

Нужен секрет GEMINI_API_KEY (бесплатный ключ: https://aistudio.google.com/apikey).
Gemini сам ищет новости и пишет тексты. Скрипт проверяет каждый пост, рисует обложки
и отбрасывает всё, что нарушает правила канала.
"""
import json
import os
import re
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

import art
import bot
import sources

ROOT = Path(__file__).resolve().parent
TARGET = 14
MAX_PER_RUN = 10
PER_CALL = 5  # Большой ответ Gemini пишет долго, поэтому просим порциями.
API = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
DEFAULT_MODELS = ["gemini-3.8-flash", "gemini-flash-latest", "gemini-flash-lite-latest"]
OFFICIAL = {"supercell.com", "brawlstars.com", "youtube.com", "inbox.supercell.com"}
COVER_KINDS = {"tip", "news", "update", "buff", "nerf", "brawler", "poll", "warning", "esports",
               "creator", "record", "fun", "meme"}
BANNED = re.compile(
    r"\b(слив|сливы|сливов|слива|утечк\w*|приватн\w* сервер\w*|чит|читы|читам\w*|читер\w*|взлом\w*|vpn|впн|"
    r"обход\w* блокиров\w*|бесплатн\w* гем\w*|розыгрыш\w*|раздач\w* гем\w*|казино|ставк\w* на матч\w*|"
    r"заработ\w*|продаж\w* аккаунт\w*|купить аккаунт\w*|политик\w*)\b|18\+",
    re.IGNORECASE,
)


RULES = """Ты редактор русскоязычного Telegram-канала «Brawl Stars Новости» (t.me/All_About_Brawl).
Читатели: школьники и студенты, среди них дети.

Задача: напиши {count} новых постов о Brawl Stars на {date}. Сначала поищи в Google свежие
новости игры за последние 7 дней: официальный блог Supercell (supercell.com/en/games/brawlstars/blog),
обновления, баланс, новые бойцы, события, киберспорт, официальный YouTube Brawl Stars.
Каждый факт, число и дату бери только из найденных страниц. Не выдумывай новости. Если свежих
новостей мало, делай опросы, советы и мемы по уже известным фактам игры.

Сделай смесь: новости и разборы, 2-3 опроса, 1-2 мема, советы. Не повторяй эти темы, они уже были:
{recent}

Формат текста поста (HTML Telegram, только теги b, i, blockquote, a). Каждый факт в своей
цитате, так в Telegram получаются красивые карточки:
<b>🔥 ЗАГОЛОВОК ЗАГЛАВНЫМИ БУКВАМИ</b>
<i>Короткая живая подводка в одну строку.</i>

<blockquote><b>⚡ Подзаголовок</b>
Факт или совет в 1-2 предложения.</blockquote>
<blockquote><b>🎯 Подзаголовок</b>
Факт или совет в 1-2 предложения.</blockquote>

🏆 <b>Вердикт:</b> <i>мнение редакции в одном предложении.</i>

💬 Вопрос читателям? Ставь 🔥 или 🗿

#BrawlStars #тема

Эмодзи подбирай по смыслу и не повторяй одни и те же во всех постах.
Правила: текст не длиннее 800 символов; символы & и < пиши как &amp; и &lt;; не добавляй
ссылку на источник в текст (её добавит бот); не заканчивай обещаниями вроде «оставайся на связи».
Нельзя: сливы неанонсированного контента, приватные серверы, читы, обход блокировок и VPN,
азартные механики, продажа аккаунтов, «бесплатные гемы», розыгрыши, заработок, политика, 18+,
оскорбления, реклама, слухи о личной жизни ютуберов.

Ответь ТОЛЬКО JSON-массивом без пояснений. Каждый элемент один из видов:
{{"kind": "news", "text": "...", "source": "https://адрес страницы, откуда факты",
  "cover": {{"kind": "news|update|buff|nerf|brawler|esports|creator|record|warning",
             "title": "до 40 символов", "subtitle": "до 60 символов", "stat": "короткое число или пусто",
             "stat_label": "подпись к числу или пусто"}}}}
{{"kind": "tip", "text": "...", "cover": {{...как выше, kind tip, fun или brawler...}}}}
{{"kind": "poll", "text": "короткий пост перед опросом", "question": "до 250 символов",
  "options": ["2-6 вариантов до 90 символов"], "cover": {{"kind": "poll", "title": "...", "subtitle": "..."}}}}
{{"kind": "meme", "text": "короткая подпись к мему в формате выше, без blockquote",
  "top": "верхняя надпись мема до 60 символов", "bottom": "нижняя надпись до 60 символов"}}
Мемы только про игровые ситуации, не про реальных людей."""


def unpublished():
    posts = json.loads(bot.QUEUE.read_text(encoding="utf-8"))
    return posts, len(posts) - bot.load_state()["next"]


def headline(text):
    match = re.search(r"<b>(.*?)</b>", text or "")
    return re.sub(r"<[^>]+>", "", match.group(1) if match else (text or "")[:80]).strip()


def recent_topics(posts, inbox):
    topics = [headline(p.get("text") or p.get("question")) for p in posts[-40:]]
    topics += [headline(item["post"].get("text")) for item in inbox]
    return "\n".join(f"- {topic}" for topic in topics if topic)


OFFLINE = """

ВАЖНО: поиск сейчас недоступен. Не пиши новости (kind news) совсем: только опросы, советы
и мемы по общеизвестным механикам игры, без чисел, дат и названий свежих обновлений."""


def ask_gemini(prompt, models, search=True):
    key = os.environ.get("GEMINI_API_KEY")
    request_body = {
        "contents": [{"role": "user", "parts": [{"text": prompt}]}],
        "generationConfig": {"temperature": 0.8},
    }
    if search:
        request_body["tools"] = [{"google_search": {}}]
    body = json.dumps(request_body).encode()
    last = None
    for model in models:
        request = urllib.request.Request(
            API.format(model=model), data=body,
            headers={"Content-Type": "application/json", "x-goog-api-key": key},
        )
        for attempt in range(3):
            try:
                with urllib.request.urlopen(request, timeout=420) as response:
                    reply = json.load(response)
                print(f"Ответил {model}")
                return reply
            except urllib.error.HTTPError as error:
                detail = " ".join(error.read().decode("utf-8", "replace").split())[:700]
                last = f"{model}: HTTP {error.code} {detail}"
                no_quota = error.code == 429 and "exceeded your current quota" in detail
                if error.code in (429, 500, 503) and attempt < 2 and not no_quota:
                    time.sleep(20 * (attempt + 1))
                    continue
                break
            except (urllib.error.URLError, TimeoutError, OSError) as error:
                last = f"{model}: {getattr(error, 'reason', error)}"
                print(f"Gemini не ответил: {last}")
                break
        print(f"Gemini не ответил: {last}")
    raise RuntimeError(f"Gemini недоступен: {last}")


def parse_reply(reply):
    candidate = (reply.get("candidates") or [{}])[0]
    text = "".join(part.get("text", "") for part in candidate.get("content", {}).get("parts", []))
    match = re.search(r"\[.*\]", text, re.DOTALL)
    if not match:
        raise ValueError(f"в ответе Gemini нет JSON: {text[:300]}")
    items = json.loads(match.group(0))
    grounded = set()
    for chunk in candidate.get("groundingMetadata", {}).get("groundingChunks", []):
        title = (chunk.get("web") or {}).get("title", "").lower()
        if title:
            grounded.add(title.removeprefix("www."))
    return items, grounded


def domain(url):
    host = (urllib.parse.urlsplit(url).hostname or "").lower().removeprefix("www.")
    return host


def trusted(url, grounded):
    host = domain(url)
    if not url.startswith("https://") or "vertexaisearch" in host or "google." in host:
        return False
    return any(host == d or host.endswith("." + d) for d in OFFICIAL | grounded)


def official_image(url):
    """Картинка og:image со страницы Supercell, если она есть."""
    if domain(url) != "supercell.com":
        return None
    try:
        image = sources.parse_meta(sources.get(url)).get("og:image", "")
    except Exception as error:  # noqa: BLE001
        print(f"Не удалось взять картинку со страницы {url}: {error}")
        return None
    return image if image.startswith("https://") else None


ART_USED = ROOT / "art_used.json"
BACKGROUNDS = {"index": [], "used": []}


def prepare_backgrounds(config):
    BACKGROUNDS["index"] = art.collect(config)
    BACKGROUNDS["used"] = bot.read_json(ART_USED, [])


def save_backgrounds():
    if BACKGROUNDS["index"]:
        bot.write_json(ART_USED, BACKGROUNDS["used"])


def draw(path, args, topic=""):
    background = art.pick(topic, BACKGROUNDS["index"], BACKGROUNDS["used"])
    extra = ["--bg", background] if background else []
    subprocess.run([sys.executable, str(ROOT / "tools" / "make_cover.py"), str(path), *args, *extra],
                   check=True, capture_output=True, text=True)


def cover_args(cover):
    kind = cover.get("kind") if cover.get("kind") in COVER_KINDS else "news"
    args = ["--kind", kind, "--title", str(cover.get("title") or "Brawl Stars")[:60]]
    for field, flag in (("subtitle", "--subtitle"), ("stat", "--stat"), ("stat_label", "--stat-label")):
        if cover.get(field):
            args += [flag, str(cover[field])[:80]]
    return args


def build(item, grounded, stamp, number):
    """Превращает ответ Gemini в пост для queue.json или возвращает причину отказа."""
    kind = item.get("kind")
    text = (item.get("text") or "").strip()
    everything = " ".join(str(item.get(k, "")) for k in ("text", "question", "options", "top", "bottom"))
    if BANNED.search(everything):
        return None, f"запрещённая тема: {BANNED.search(everything).group(0)}"
    name = f"images/{stamp}-gemini-{number:02d}" + ("-meme" if kind == "meme" else "")
    post = {"text": text}
    if kind == "news":
        source = item.get("source", "")
        if not trusted(source, grounded):
            return None, f"источник не подтверждён поиском: {source or 'нет'}"
        post.update(source=source, source_name="Supercell" if domain(source) == "supercell.com" else domain(source))
    if kind in ("news", "tip", "poll"):
        draw(ROOT / f"{name}.png", cover_args(item.get("cover") or {}), everything)
        post.update(type="photo", image=f"{name}.png")
        image = official_image(post.get("source", "")) if kind == "news" else None
        if image:
            post.update(image=image, fallback_image=f"{name}.png")
    elif kind == "meme":
        top, bottom = str(item.get("top", ""))[:80], str(item.get("bottom", ""))[:80]
        if not top or not bottom:
            return None, "у мема нет подписей"
        draw(ROOT / f"{name}.png", ["--meme", top, bottom], everything)
        post.update(type="photo", image=f"{name}.png")
    else:
        return None, f"неизвестный вид {kind}"
    if kind == "poll":
        post.update(type="poll", question=str(item.get("question", "")).strip(),
                    options=[str(option).strip() for option in item.get("options", [])][:10])
    errors = bot.validate_post(post)
    if errors:
        for path in {post.get("image"), post.get("fallback_image")} - {None}:
            if not bot.is_url(path):
                (ROOT / path).unlink(missing_ok=True)
        return None, "; ".join(errors)
    return post, None


def generate(count, posts, inbox, config, only=None):
    """Просит у Gemini count постов. only: нужен ровно один вид (tip, poll, meme)."""
    models = [config["gemini_model"]] if config.get("gemini_model") else DEFAULT_MODELS
    zone = timezone(timedelta(hours=config.get("timezone_offset_hours", 3)))
    today = datetime.now(zone).date().isoformat()
    items, grounded, search = [], set(), config.get("gemini_search", True) and not only
    while len(items) < count:
        part = min(PER_CALL, count - len(items))
        done = "".join(f"\n- {headline(i.get('text'))}" for i in items if isinstance(i, dict))
        prompt = RULES.format(count=part, date=today, recent=(recent_topics(posts, inbox) + done) or "- пока ничего")
        if only:
            prompt += f"\n\nСделай ровно {part} пост(а) вида kind={only}, другие виды не нужны."
        try:
            if not search:
                raise RuntimeError("поиск выключен")
            got, found = parse_reply(ask_gemini(prompt, models))
        except RuntimeError as error:
            # На бесплатном тарифе поиск Google бывает недоступен: тогда без новостей.
            print(f"Без поиска ({str(error)[:200]}), пишу посты без новостей")
            search = False
            try:
                got, found = parse_reply(ask_gemini(prompt + OFFLINE, models, search=False))
            except (RuntimeError, ValueError) as error:
                if not items:
                    raise
                print(f"Следующая порция не получилась: {str(error)[:200]}")
                break
            got = [item for item in got if isinstance(item, dict) and item.get("kind") != "news"]
        if only:
            got = [item for item in got if isinstance(item, dict) and item.get("kind") == only]
        if not got:
            break
        items += got[:part]
        grounded |= found
    print(f"Gemini предложил {len(items)} постов, источники поиска: {', '.join(sorted(grounded)) or 'нет'}")
    return items, grounded


def build_all(items, grounded, config, tag="gemini"):
    zone = timezone(timedelta(hours=config.get("timezone_offset_hours", 3)))
    stamp = datetime.now(zone).strftime("%Y-%m-%d-%H%M") + ("" if tag == "gemini" else f"-{tag}")
    added = []
    for number, item in enumerate(items, 1):
        if not isinstance(item, dict):
            continue
        post, reason = build(item, grounded, stamp, number)
        if post:
            added.append(post)
            print(f"+ {headline(post.get('text') or post.get('question'))}")
        else:
            print(f"- пропущен ({reason}): {headline(item.get('text'))}")
    return added


GUESS = [("poll", "poll"), ("баф", "buff"), ("усилен", "buff"), ("нерф", "nerf"), ("ослабл", "nerf"),
         ("киберспорт", "esports"), ("чемпионат", "esports"), ("финал", "esports"), ("ютуб", "creator"),
         ("креатор", "creator"), ("рекорд", "record"), ("обновлен", "update"), ("патч", "update"),
         ("совет", "tip"), ("гайд", "tip"), ("как играть", "tip"), ("как забивать", "tip"),
         ("секрет", "tip"), ("бойц", "brawler"), ("важно", "warning"), ("мошен", "warning")]


def guess_kind(post):
    text = (post.get("type", "") + " " + (post.get("text") or "")).lower()
    for word, kind in GUESS:
        if word in text:
            return kind
    return "news"


def restyle(posts, start):
    """Новые обложки для неопубликованных постов с обычными обложками (мемы не трогаем)."""
    changed = 0
    for index in range(start, len(posts)):
        post = posts[index]
        field = "fallback_image" if bot.is_url(post.get("image", "")) else "image"
        image = post.get(field, "")
        if not image or bot.is_url(image) or "meme" in image or "#мем" in (post.get("text") or ""):
            continue
        from PIL import Image

        with Image.open(ROOT / image) as picture:
            if picture.width == picture.height:  # Квадрат это мем с подписями: его не перерисовать.
                continue
        lead = re.search(r"<i>(.*?)</i>", post.get("text") or "", re.DOTALL)
        title = headline(post.get("text") or post.get("question"))
        subtitle = re.sub(r"<[^>]+>", "", lead.group(1)) if lead else ""
        new = f"images/restyle-{Path(image).stem.removeprefix('restyle-')}.png"
        draw(ROOT / new, ["--kind", guess_kind(post), "--title", title[:70], "--subtitle", subtitle[:90]],
             (post.get("text") or "") + " " + (post.get("question") or ""))
        post[field] = new
        changed += 1
        print(f"Новая обложка: {title}")
    return changed


def main():
    config = bot.load_config()
    posts, left = unpublished()
    if "--restyle" in sys.argv:
        prepare_backgrounds(config)
        changed = restyle(posts, bot.load_state()["next"])
        bot.QUEUE.write_text(json.dumps(posts, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        save_backgrounds()
        print(f"Перерисовано обложек: {changed}")
        return
    only = sys.argv[sys.argv.index("--publish") + 1] if "--publish" in sys.argv else None
    if left >= TARGET and not only:
        print(f"В очереди {left} постов, пополнять не нужно")
        return
    if not os.environ.get("GEMINI_API_KEY"):
        sys.exit("Не задан секрет GEMINI_API_KEY: добавь ключ из https://aistudio.google.com/apikey")
    count = 1 if only else min(TARGET - left, MAX_PER_RUN)
    inbox = bot.read_json(bot.INBOX, [])
    prepare_backgrounds(config)
    items, grounded = generate(count, posts, inbox, config, only)
    added = build_all(items[:count], grounded, config, "test" if only else "gemini")
    save_backgrounds()
    if only:
        if not added:
            sys.exit("Gemini не сделал подходящий пост, попробуй ещё раз")
        bot.publish(os.environ["TELEGRAM_BOT_TOKEN"], os.environ["TELEGRAM_CHANNEL"], added[0])
        print(f"Пробный пост ({only}) опубликован")
        return
    if "--dry" in sys.argv:
        print(json.dumps(added, ensure_ascii=False, indent=1))
        return
    if added:
        bot.QUEUE.write_text(json.dumps(posts + added, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"Добавлено постов: {len(added)}, теперь в очереди: {left + len(added)}")


if __name__ == "__main__":
    main()
