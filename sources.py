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
        videos.append(
            {
                "id": video_id,
                "title": entry.findtext("a:title", "", ATOM),
                "url": f"https://www.youtube.com/watch?v={video_id}",
                "published": stamp,
            }
        )
    return videos


def youtube_post(video, channel_name):
    text = (
        f"<b>🎬 НОВОЕ ВИДЕО {html.escape(channel_name.upper(), quote=False)}</b>\n\n"
        f"<i>{clean(video['title'], 200)}</i>\n\n"
        "Плеер прямо над текстом, смотри не выходя из Telegram 👆\n\n"
        "Как тебе ролик? 👇\n\n"
        "#BrawlStars #видео"
    )
    return {
        "type": "text",
        "text": text,
        "video_url": video["url"],
        "source": video["url"],
        "source_name": f"YouTube: {channel_name}",
    }


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
    text = f"<b>📰 НОВОЕ В БЛОГЕ SUPERCELL</b>\n\n<i>{title}</i>"
    if description:
        text += f"\n\n<blockquote>{description}</blockquote>"
    text += "\n\nЧто думаешь об этом? 👇\n\n#BrawlStars #новости"
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


def good_meme(post, config):
    flair = (post.get("link_flair_text") or "").lower()
    url = post.get("url", "")
    return (
        not post.get("over_18")
        and not post.get("spoiler")
        and not post.get("stickied")
        and not post.get("is_video")
        and post.get("score", 0) >= config.get("min_score", 300)
        and any(word in flair for word in config.get("flairs", ["humor", "meme"]))
        and re.match(r"https://i\.redd\.it/[\w-]+\.(jpe?g|png)$", url)
        and not BAD_WORDS.search(post.get("title", ""))
    )


def parse_reddit(listing, config):
    posts = [child["data"] for child in listing.get("data", {}).get("children", [])]
    posts = [post for post in posts if good_meme(post, config)]
    return sorted(posts, key=lambda post: -post.get("score", 0))


def meme_post(post):
    author = post.get("author") or "автор"
    text = (
        "<b>😂 МЕМ ДНЯ</b>\n\n"
        f"<i>{clean(post.get('title'), 250)}</i>\n\n"
        f"Автор: u/{html.escape(author, quote=False)}\n\n"
        "Узнал себя? 👇\n\n"
        "#BrawlStars #мем"
    )
    return {
        "type": "photo",
        "image": post["url"],
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
    for channel in config.get("youtube", []):
        key = f"youtube:{channel['channel_id']}"
        first_run = key not in seen
        url = f"https://www.youtube.com/feeds/videos.xml?channel_id={channel['channel_id']}"
        videos = parse_youtube(get(url))
        seen.setdefault(key, [])  # Только после успешной загрузки, иначе потом выйдет весь архив.
        for video in reversed(videos):
            if video["id"] in seen[key]:
                continue
            remember(seen, key, video["id"])
            # При первом запуске старые ролики просто запоминаем, чтобы не завалить канал.
            if not first_run and now - video["published"] < NEWS_TTL:
                add(inbox, "news", video["id"], youtube_post(video, channel["name"]), now)
                print(f"YouTube: новое видео {video['title']}")


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


def fetch_memes(config, inbox, seen, now):
    reddit = config.get("reddit")
    if not reddit:
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
    for name, fetch in (("YouTube", fetch_youtube), ("Блог Supercell", fetch_blog), ("Reddit", fetch_memes)):
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
