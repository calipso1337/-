"""Официальные фоны для обложек: превью роликов канала Brawl Stars и картинки статей Supercell.

Картинки скачиваются на GitHub в папку .art (в репозиторий не попадают). Для каждого поста
выбирается фон, у которого больше всего общих слов с текстом, а при равенстве самый
редко использованный, чтобы обложки не повторялись.
"""
import json
import re
import xml.etree.ElementTree as ET
from pathlib import Path

import sources

ROOT = Path(__file__).resolve().parent
CACHE = ROOT / ".art"
INDEX = CACHE / "index.json"
MEDIA = {"m": "http://search.yahoo.com/mrss/", **sources.ATOM}
STOP = {"brawl", "stars", "the", "and", "for", "with", "new", "update", "бравл", "старс", "это", "как", "что"}


def words(text):
    return {w for w in re.findall(r"[a-zа-яё0-9]{3,}", (text or "").lower()) if w not in STOP}


def download(url, path):
    data = sources.get(url, timeout=30)
    if len(data) < 5000:  # Заглушка YouTube для отсутствующего превью весит ~1 КБ.
        raise ValueError("слишком маленькая картинка")
    path.write_bytes(data)


def youtube_art(channel_id, limit):
    xml = sources.get(f"https://www.youtube.com/feeds/videos.xml?channel_id={channel_id}")
    items = []
    for entry in ET.fromstring(xml).findall("a:entry", MEDIA)[:limit]:
        video_id = entry.findtext("yt:videoId", "", MEDIA)
        title = entry.findtext("a:title", "", MEDIA)
        if video_id:
            items.append({"id": f"yt-{video_id}", "title": title,
                          "urls": [f"https://i.ytimg.com/vi/{video_id}/maxresdefault.jpg",
                                   f"https://i.ytimg.com/vi/{video_id}/hqdefault.jpg"]})
    return items


def blog_art(blog_url, limit):
    items = []
    for link in sources.parse_blog_links(sources.get(blog_url), blog_url)[:limit]:
        meta = sources.parse_meta(sources.get(link))
        image = meta.get("og:image", "")
        if image.startswith("https://"):
            slug = link.rstrip("/").rsplit("/", 1)[-1]
            items.append({"id": f"blog-{slug}", "title": meta.get("og:title", slug), "urls": [image]})
    return items


def collect(config, limit=15):
    """Скачивает фоны. Ошибка одного источника не мешает остальным."""
    CACHE.mkdir(exist_ok=True)
    found = []
    for name, job in (
        ("YouTube", lambda: [i for c in config.get("youtube", []) for i in youtube_art(c["channel_id"], limit)]),
        ("Блог", lambda: blog_art(config["supercell_blog"]["url"], 6) if config.get("supercell_blog") else []),
    ):
        try:
            found += job()
        except Exception as error:  # noqa: BLE001
            print(f"Фоны ({name}): не удалось получить список: {error}")
    index = []
    for item in found:
        path = CACHE / f"{item['id']}.jpg"
        for url in item["urls"]:
            try:
                if not path.exists():
                    download(url, path)
                index.append({"id": item["id"], "title": item["title"], "path": str(path)})
                break
            except Exception:  # noqa: BLE001
                continue
    INDEX.write_text(json.dumps(index, ensure_ascii=False), encoding="utf-8")
    print(f"Официальных фонов: {len(index)}")
    return index


def load():
    return json.loads(INDEX.read_text(encoding="utf-8")) if INDEX.exists() else []


def pick(text, index, used):
    """Путь к фону для поста или None. used: список id, обновляется."""
    if not index:
        return None
    topic = words(text)
    counts = {item["id"]: used.count(item["id"]) for item in index}
    recent = used[-3:]
    best = max(index, key=lambda item: (len(topic & words(item["title"])) if item["id"] not in recent else -1,
                                        -counts[item["id"]]))
    used.append(best["id"])
    del used[:-200]
    return best["path"]
