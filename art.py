"""Официальные фоны для обложек: кадры из роликов канала Brawl Stars (мультфильмы, геймплей).

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
    if len(data) < 8000:  # Заглушка YouTube для отсутствующего кадра весит ~1 КБ.
        raise ValueError("слишком маленькая картинка")
    path.write_bytes(data)


def real_face(path):
    """Есть ли на картинке крупное лицо реального человека (детектор OpenCV).

    Детектор обучен на фотографиях людей, мультяшных бойцов он обычно не находит."""
    try:
        import cv2

        cv2.CascadeClassifier  # noqa: B018 - в OpenCV 5 детектора нет
    except (ImportError, AttributeError):
        print("Нет детектора лиц OpenCV 4: фоны не проверяются на живых людей")
        return False
    image = cv2.imread(str(path))
    if image is None:
        return True
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    detector = cv2.CascadeClassifier(cv2.data.haarcascades + "haarcascade_frontalface_default.xml")
    side = min(gray.shape[:2])
    faces = detector.detectMultiScale(gray, scaleFactor=1.1, minNeighbors=5, minSize=(side // 12, side // 12))
    return len(faces) > 0


def letterboxed(path):
    """Вертикальный кадр с тёмными полосами по бокам (Shorts) плохо смотрится на фоне."""
    from PIL import Image, ImageStat

    with Image.open(path) as image:
        gray = image.convert("L")
        width, height = gray.size
        band = width // 6
        sides = [ImageStat.Stat(gray.crop(box)) for box in ((0, 0, band, height), (width - band, 0, width, height))]
    return all(stat.mean[0] < 25 and stat.stddev[0] < 12 for stat in sides)


def youtube_art(channel_id, limit):
    xml = sources.get(f"https://www.youtube.com/feeds/videos.xml?channel_id={channel_id}")
    items = []
    for entry in ET.fromstring(xml).findall("a:entry", MEDIA)[:limit]:
        video_id = entry.findtext("yt:videoId", "", MEDIA)
        title = entry.findtext("a:title", "", MEDIA)
        link = entry.find("a:link", MEDIA)
        if link is not None and "/shorts/" in link.get("href", ""):
            continue  # В Shorts часто живые люди и вертикальный кадр.
        if video_id:
            # Кадры примерно с 25, 50 и 75 процентов ролика: моменты из игры без надписей превью.
            for n in (1, 2, 3):
                items.append({"id": f"yt-{video_id}-{n}", "title": title,
                              "urls": [f"https://i.ytimg.com/vi/{video_id}/maxres{n}.jpg",
                                       f"https://i.ytimg.com/vi/{video_id}/hq{n}.jpg"]})
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
        # Картинки статей блога выключены по умолчанию: на них крупные надписи.
        ("Блог", lambda: blog_art(config["supercell_blog"]["url"], 20) if config.get("art_from_blog") else []),
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
                if letterboxed(path) or real_face(path):
                    print(f"Фон {item['id']} не подходит: вертикальный кадр или живой человек")
                    break
                index.append({"id": item["id"], "title": item["title"], "path": str(path)})
                break
            except Exception as error:  # noqa: BLE001
                print(f"Фон {item['id']}: {type(error).__name__}: {error}")
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
