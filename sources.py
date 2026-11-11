"""Автоматические источники постов: YouTube, блог Supercell и мемы с Reddit.

Работает без внешних зависимостей и без Claude. Новые материалы попадают в inbox.json,
а bot.py публикует их в слоты расписания. Уже виденные материалы хранятся в seen.json.
"""
import base64
import calendar
import html
import json
import os
import re
import time
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from pathlib import Path
from html.parser import HTMLParser

USER_AGENT = "AllAboutBrawlBot/1.0 (+https://t.me/All_About_Brawl)"
SEEN_LIMIT = 300
NEWS_TTL = 36 * 3600  # Новость старше полутора суток уже не новость.
MEME_TTL = 72 * 3600
MEME_STOCK = 15

# Детская аудитория: мем с такими словами в заголовке пропускаем.
BAD_WORDS = re.compile(
    r"\b(fuck\w*|shit\w*|bitch\w*|dick\w*|porn\w*|sex\w*|nsfw|nude\w*|kill yourself|kys|"
    r"retard\w*|nazi\w*|hitler|suicide|drug\w*|weed|casino|gambl\w*|free gems?|giveaway|"
    r"hack\w*|cheat\w*|private server\w*|leak\w*|хуй\w*|пизд\w*|бля\w*|еба\w*|сука)\b",
    re.IGNORECASE,
)


def get(url, headers=None, data=None, timeout=20):
    request = urllib.request.Request(url, data=data, headers={"User-Agent": USER_AGENT, **(headers or {})})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return response.read()


def clean(text, limit):
    """Текст из чужого источника: без разметки, с экранированием, не длиннее limit."""
    text = re.sub(r"\s+", " ", html.unescape(text or "")).strip()
    if len(text) > limit:
        text = text[: limit - 1].rsplit(" ", 1)[0] + "…"
    return html.escape(text, quote=False)


# ---------- YouTube ----------

ATOM = {"a": "http://www.w3.org/2005/Atom", "yt": "http://www.youtube.com/xml/schemas/2015"}


def parse_youtube(xml_bytes):
    """Возвращает список {id, title, url, published} из RSS канала."""
    root = ET.fromstring(xml_bytes)
    videos = []
    for entry in root.findall("a:entry", ATOM):
        video_id = entry.findtext("yt:videoId", "", ATOM)
        published = entry.findtext("a:published", "", ATOM)
        if not video_id:
            continue
        stamp = calendar.timegm(time.strptime(published[:19], "%Y-%m-%dT%H:%M:%S")) if published else 0
        link = entry.find("a:link", ATOM)
        videos.append(
            {
                "id": video_id,
                "title": entry.findtext("a:title", "", ATOM),
                "url": f"https://www.youtube.com/watch?v={video_id}",
                "published": stamp,
                "short": link is not None and "/shorts/" in link.get("href", ""),
            }
        )
    return videos


def russian(title):
    """Название на русском: хотя бы 5 кириллических букв и не меньше 30% всех букв."""
    letters = re.findall(r"[^\W\d_]", title or "")
    cyrillic = [c for c in letters if "а" <= c.lower() <= "я" or c.lower() == "ё"]
    return len(cyrillic) >= 5 and len(cyrillic) / len(letters) >= 0.3


def author_line(channel):
    url = f"https://www.youtube.com/channel/{channel['channel_id']}"
    return f'👤 Автор: <a href="{url}">{html.escape(channel["name"], quote=False)}</a>'


def youtube_post(video, channel):
    text = (
        "<b>🎬 НОВОЕ ВИДЕО</b>\n"
        f"<i>{clean(video['title'], 200)}</i>\n\n"
        f"{author_line(channel)}\n"
        "▶️ Плеер прямо над постом, смотри не выходя из Telegram\n\n"
        "🔥 уже посмотрел · 👀 смотрю сейчас\n\n"
        "#BrawlStars #видео"
    )
    return {
        "type": "text",
        "text": text,
        "video_url": video["url"],
        "source": video["url"],
        "source_name": f"YouTube: {channel['name']}",
    }


def good_video(video):
    return not video.get("short") and russian(video["title"]) and not BAD_WORDS.search(video["title"])


# ---------- Блог Supercell ----------


class Meta(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.meta = {}

    def handle_starttag(self, tag, attrs):
        if tag == "meta":
            attrs = dict(attrs)
            key = attrs.get("property") or attrs.get("name")
            if key and "content" in attrs:
                self.meta.setdefault(key, attrs["content"])


def parse_meta(page):
    parser = Meta()
    parser.feed(page.decode("utf-8", "replace"))
    return parser.meta


def parse_blog_links(page, blog_url):
    """Ссылки на статьи (раздел/статья) в порядке появления на странице блога."""
    base = urllib.parse.urlsplit(blog_url)
    path = base.path.rstrip("/")
    pattern = re.compile(r'href="((?:https://supercell\.com)?' + re.escape(path) + r'/[a-z0-9-]+/[a-z0-9-]+)/?"')
    links = []
    for match in pattern.finditer(page.decode("utf-8", "replace")):
        link = urllib.parse.urljoin(f"{base.scheme}://{base.netloc}", match.group(1)) + "/"
        if link not in links:
            links.append(link)
    return links


def blog_post(url, meta, fallback_image):
    title = clean(meta.get("og:title") or meta.get("twitter:title"), 200)
    if not title:
        return None
    description = clean(meta.get("og:description") or meta.get("description"), 400)
    text = f"<b>📰 СВЕЖЕЕ ОТ SUPERCELL</b>\n<i>{title}</i>"
    if description:
        text += f"\n\n<blockquote>{description}</blockquote>"
    text += "\n\n📌 Всё подробно в статье по ссылке ниже\n\n💬 Ждал такое? Ставь 🔥 или 🗿\n\n#BrawlStars #новости"
    image = meta.get("og:image") or ""
    if image.startswith("https://"):
        return {"type": "photo", "image": image, "fallback_image": fallback_image, "text": text,
                "source": url, "source_name": "Supercell"}
    # Без картинки Telegram покажет превью статьи.
    return {"type": "text", "text": text, "video_url": url, "source": url, "source_name": "Supercell"}


# ---------- Reddit ----------


def reddit_token():
    client_id = os.environ.get("REDDIT_CLIENT_ID")
    secret = os.environ.get("REDDIT_CLIENT_SECRET")
    if not client_id or not secret:
        return None
    auth = base64.b64encode(f"{client_id}:{secret}".encode()).decode()
    raw = get(
        "https://www.reddit.com/api/v1/access_token",
        {"Authorization": f"Basic {auth}", "Content-Type": "application/x-www-form-urlencoded"},
        b"grant_type=client_credentials",
    )
    return json.loads(raw)["access_token"]


def fetch_reddit(subreddit):
    token = reddit_token()
    if token:
        url = f"https://oauth.reddit.com/r/{subreddit}/top?t=day&limit=50&raw_json=1"
        raw = get(url, {"Authorization": f"Bearer {token}"})
    else:
        raw = get(f"https://www.reddit.com/r/{subreddit}/top.json?t=day&limit=50&raw_json=1")
    return json.loads(raw)


def reddit_video(post):
    """Адрес видео из ответа API Reddit (HLS со звуком) или None."""
    video = ((post.get("secure_media") or post.get("media") or {}).get("reddit_video") or {})
    if not post.get("is_video") or video.get("duration", 999) > 90:
        return None
    return video.get("hls_url") or video.get("fallback_url")


def good_meme(post, config):
    flair = (post.get("link_flair_text") or "").lower()
    url = post.get("url", "")
    media = reddit_video(post) or (re.match(r"https://i\.redd\.it/[\w-]+\.(jpe?g|png)$", url) and url)
    return bool(
        not post.get("over_18")
        and not post.get("spoiler")
        and not post.get("stickied")
        and post.get("score", 0) >= config.get("min_score", 300)
        and any(word in flair for word in config.get("flairs", ["humor", "meme"]))
        and media
        and not BAD_WORDS.search(post.get("title", ""))
    )


def parse_reddit(listing, config):
    posts = [child["data"] for child in listing.get("data", {}).get("children", [])]
    posts = [post for post in posts if good_meme(post, config)]
    # Видео-мемы в приоритете, внутри группы по рейтингу.
    return sorted(posts, key=lambda post: (not reddit_video(post), -post.get("score", 0)))


def meme_post(post):
    author = post.get("author") or "автор"
    text = (
        "<b>😂 МЕМ ДНЯ</b>\n"
        f"<i>{clean(post.get('title'), 250)}</i>\n\n"
        f"✍️ Автор: u/{html.escape(author, quote=False)}\n\n"
        "Узнал себя? Ставь 😂\n\n"
        "#BrawlStars #мем"
    )
    video = reddit_video(post)
    media = {"type": "video", "video": video} if video else {"type": "photo", "image": post["url"]}
    return {
        **media,
        "text": text,
        "source": "https://www.reddit.com" + post["permalink"],
        "source_name": f"Reddit r/{post.get('subreddit', 'Brawlstars')}",
    }


# ---------- Общая часть ----------


def remember(seen, key, item_id):
    seen.setdefault(key, []).append(item_id)
    seen[key] = seen[key][-SEEN_LIMIT:]


def add(inbox, kind, item_id, post, now):
    inbox.append({"kind": kind, "id": item_id, "added": int(now), "post": post})


def fetch_youtube(config, inbox, seen, now):
    """Новые ролики на русском с каналов video_channels (ютуберы по Brawl Stars)."""
    for channel in config.get("video_channels", []):
        key = f"youtube:{channel['channel_id']}"
        first_run = key not in seen
        url = f"https://www.youtube.com/feeds/videos.xml?channel_id={channel['channel_id']}"
        try:
            videos = parse_youtube(get(url))
        except Exception as error:  # noqa: BLE001
            print(f"YouTube {channel['name']}: {error}")
            continue
        seen.setdefault(key, [])  # Только после успешной загрузки, иначе потом выйдет весь архив.
        for video in reversed(videos):
            if video["id"] in seen[key]:
                continue
            remember(seen, key, video["id"])
            # При первом запуске старые ролики просто запоминаем, чтобы не завалить канал.
            if not first_run and now - video["published"] < NEWS_TTL and good_video(video):
                add(inbox, "news", video["id"], youtube_post(video, channel), now)
                print(f"YouTube: {channel['name']}: {video['title']}")


def fetch_blog(config, inbox, seen, now):
    blog = config.get("supercell_blog")
    if not blog:
        return
    first_run = "blog" not in seen
    links = parse_blog_links(get(blog["url"]), blog["url"])
    if not links:
        raise ValueError("на странице блога не нашлось ссылок на статьи")
    seen.setdefault("blog", [])
    fresh = [link for link in links if link not in seen["blog"]]
    for link in fresh[:3] if not first_run else []:
        remember(seen, "blog", link)
        post = blog_post(link, parse_meta(get(link)), blog["fallback_image"])
        if post:
            add(inbox, "news", link, post, now)
            print(f"Блог: новая статья {link}")
    if first_run:
        for link in links:
            remember(seen, "blog", link)


def shorts_list(channel_id, limit=30):
    """Shorts канала: [(id, название)]. Только список, без скачивания."""
    import subprocess

    done = subprocess.run(
        ["yt-dlp", "--flat-playlist", "--playlist-end", str(limit), "--print", "%(id)s\t%(title)s",
         f"https://www.youtube.com/channel/{channel_id}/shorts"],
        capture_output=True, text=True, timeout=120,
    )
    return [line.split("\t", 1) for line in done.stdout.splitlines() if "\t" in line]


def short_post(video_id, title, channel):
    url = f"https://www.youtube.com/watch?v={video_id}"
    text = (
        "<b>😂 МЕМ ДНЯ</b>\n"
        f"<i>{clean(title, 200)}</i>\n\n"
        f"{author_line(channel)}\n"
        "▶️ Смотри прямо здесь, плеер над постом\n\n"
        "Ставь 😂 если зашло\n\n"
        "#BrawlStars #мем"
    )
    return {"type": "text", "text": text, "video_url": url, "source": url, "source_name": f"YouTube: {channel['name']}"}


def meme_candidates(config, seen, per_channel=10):
    """Русские мемы-Shorts с каналов из meme_channels, которых ещё не было: [(ключ, id, пост)]."""
    import random

    found = []
    channels = list(config.get("meme_channels", []))
    random.shuffle(channels)
    for channel in channels:
        key = f"shorts:{channel['channel_id']}"
        try:
            rows = shorts_list(channel["channel_id"], per_channel)
        except Exception as error:  # noqa: BLE001
            print(f"Мемы {channel['name']}: {error}")
            continue
        for video_id, title in rows:
            if video_id not in seen.get(key, []) and russian(title) and not BAD_WORDS.search(title):
                found.append((key, video_id, short_post(video_id, title, channel)))
    random.shuffle(found)
    return found


def fetch_shorts(config, inbox, seen, now):
    """Видео-мемы без ключей Reddit: русские Shorts мем-каналов (плеером, без скачивания)."""
    stock = sum(1 for item in inbox if item["kind"] == "meme")
    if stock >= 3:
        return
    for key, video_id, post in meme_candidates(config, seen)[: 3 - stock]:
        remember(seen, key, video_id)
        add(inbox, "meme", video_id, post, now)
        print(f"Мем: {post['source_name']} {video_id}")


def fetch_memes(config, inbox, seen, now):
    reddit = config.get("reddit")
    if not reddit:
        return
    if not (os.environ.get("REDDIT_CLIENT_ID") and os.environ.get("REDDIT_CLIENT_SECRET")):
        print("Reddit: нет ключей API (REDDIT_CLIENT_ID и REDDIT_CLIENT_SECRET), беру Shorts")
        return
    stock = sum(1 for item in inbox if item["kind"] == "meme")
    for post in parse_reddit(fetch_reddit(reddit["subreddit"]), reddit):
        if stock >= MEME_STOCK:
            break
        if post["id"] in seen.get("reddit", []):
            continue
        remember(seen, "reddit", post["id"])
        add(inbox, "meme", post["id"], meme_post(post), now)
        stock += 1
        print(f"Reddit: мем {post['id']} ({post.get('score')} очков)")


def expire(inbox, now):
    ttl = {"news": NEWS_TTL, "meme": MEME_TTL}
    return [item for item in inbox if now - item["added"] < ttl.get(item["kind"], NEWS_TTL)]


def fetch_all(config, inbox, seen, now, validate):
    """Пополняет inbox. Ошибка одного источника не мешает остальным и публикации."""
    for name, fetch in (("YouTube", fetch_youtube), ("Блог Supercell", fetch_blog), ("Reddit", fetch_memes),
                        ("Shorts", fetch_shorts)):
        before = len(inbox)
        try:
            fetch(config, inbox, seen, now)
        except Exception as error:  # noqa: BLE001 - источник не должен ронять публикацию
            print(f"{name}: не удалось получить данные ({type(error).__name__}: {error})")
        # Посты из чужих данных проверяем так же, как ручные.
        for item in inbox[before:]:
            errors = validate(item["post"])
            if errors:
                print(f"{name}: пропускаю {item['id']}: {'; '.join(errors)}")
                item["broken"] = True
        inbox[:] = [item for item in inbox if not item.get("broken")]
    inbox[:] = expire(inbox, now)


def latest(kind, config):
    """Самый свежий материал нужного вида без учёта seen.json: для пробных постов.

    Возвращает (ключ seen, id, пост)."""
    if kind == "video":
        import random

        seen = json.loads(Path("seen.json").read_text(encoding="utf-8")) if Path("seen.json").exists() else {}
        found = []
        for channel in config.get("video_channels", []):
            key = f"youtube:{channel['channel_id']}"
            try:
                videos = parse_youtube(get(f"https://www.youtube.com/feeds/videos.xml?channel_id={channel['channel_id']}"))
            except Exception:  # noqa: BLE001
                continue
            found += [(key, v["id"], youtube_post(v, channel)) for v in videos[:5]
                      if good_video(v) and v["id"] not in seen.get(key, [])]
        if not found:
            raise ValueError("нет свежих русских роликов")
        return random.choice(found)
    if kind == "news":
        blog = config["supercell_blog"]
        links = parse_blog_links(get(blog["url"]), blog["url"])
        for link in links[:5]:
            post = blog_post(link, parse_meta(get(link)), blog["fallback_image"])
            if post:
                return "blog", link, post
        raise ValueError("в блоге Supercell не нашлось статей")
    if kind == "meme" and not os.environ.get("REDDIT_CLIENT_ID"):
        seen = json.loads(Path("seen.json").read_text(encoding="utf-8")) if Path("seen.json").exists() else {}
        found = meme_candidates(config, seen)
        if not found:
            raise ValueError("нет новых мемов")
        return found[0]
    if kind == "meme":
        memes = parse_reddit(fetch_reddit(config["reddit"]["subreddit"]), config["reddit"])
        if not memes:
            raise ValueError("на Reddit сейчас нет подходящих мемов")
        return "reddit", memes[0]["id"], meme_post(memes[0])
    raise ValueError(f"неизвестный вид пробного поста: {kind}")
