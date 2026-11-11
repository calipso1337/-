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
    size = (side // 12, side // 12)
    if len(detector.detectMultiScale(gray, scaleFactor=1.1, minNeighbors=5, minSize=size)):
        return True
    # Лицо вполоборота: детектор профиля, в обе стороны.
    profile = cv2.CascadeClassifier(cv2.data.haarcascades + "haarcascade_profileface.xml")
    return any(len(profile.detectMultiScale(img, scaleFactor=1.1, minNeighbors=6, minSize=size))
               for img in (gray, cv2.flip(gray, 1)))


def letterboxed(path):
    """Вертикальный кадр с тёмными полосами по бокам (Shorts) плохо смотрится на фоне."""
    from PIL import Image, ImageStat

    with Image.open(path) as image:
        gray = image.convert("L")
        width, height = gray.size
        band = width // 6
        sides = [ImageStat.Stat(gray.crop(box)) for box in ((0, 0, band, height), (width - band, 0, width, height))]
    return all(stat.mean[0] < 25 and stat.stddev[0] < 12 for stat in sides)


# Ролики, где почти наверняка живые люди: подкасты, новостные выпуски, киберспорт, интервью.
PEOPLE_TITLES = re.compile(
    r"(podcast|brawl talk|esports|championship|world finals|finals|interview|q&a|behind the|"
    r"developer|dev diary|live|creator|irl|reaction|ft\.|feat\.|vs\.? pro|tournament|bsc|"
    r"monthly finals|last chance|qualifier|time to explain|brawl stars x )", re.IGNORECASE)


def channel_video_ids(channel_id, limit):
    """До limit обычных роликов канала (без Shorts). Список берём через yt-dlp, без скачивания
    видео; если его нет, из RSS (там только 15 последних)."""
    import subprocess

    try:
        done = subprocess.run(
            ["yt-dlp", "--flat-playlist", "--playlist-end", str(limit), "--print", "%(id)s\t%(title)s",
             f"https://www.youtube.com/channel/{channel_id}/videos"],
            capture_output=True, text=True, timeout=120,
        )
        rows = [line.split("\t", 1) for line in done.stdout.splitlines() if "\t" in line]
        if rows:
            return rows
        print(f"yt-dlp не вернул список роликов: {done.stderr.strip()[-200:]}")
    except (OSError, subprocess.TimeoutExpired) as error:
        print(f"yt-dlp недоступен ({error}), беру ролики из RSS")
    xml = sources.get(f"https://www.youtube.com/feeds/videos.xml?channel_id={channel_id}")
    rows = []
    for entry in ET.fromstring(xml).findall("a:entry", MEDIA):
        link = entry.find("a:link", MEDIA)
        if link is not None and "/shorts/" in link.get("href", ""):
            continue  # В Shorts часто живые люди и вертикальный кадр.
        rows.append([entry.findtext("yt:videoId", "", MEDIA), entry.findtext("a:title", "", MEDIA)])
    return rows


def youtube_art(channel_id, limit):
    items = []
    for video_id, title in channel_video_ids(channel_id, limit):
        # Кадры примерно с 25, 50 и 75 процентов ролика: моменты из игры без надписей превью.
        for n in (1, 2, 3):
            items.append({"id": f"yt-{video_id}-{n}", "title": title, "video": video_id,
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


def gameplay_art(channel, limit=8):
    """Скриншоты из самой игры: кадры геймплейных роликов ютуберов (только русские ролики, без Shorts)."""
    videos = sources.parse_youtube(sources.get(
        f"https://www.youtube.com/feeds/videos.xml?channel_id={channel['channel_id']}"))
    items = []
    for video in [v for v in videos if sources.good_video(v)][:limit]:
        for n in (1, 2, 3):
            items.append({"id": f"gp-{video['id']}-{n}", "title": video["title"], "video": video["id"],
                          "credit": f"Кадр: {channel['name']}",
                          "urls": [f"https://i.ytimg.com/vi/{video['id']}/maxres{n}.jpg",
                                   f"https://i.ytimg.com/vi/{video['id']}/hq{n}.jpg"]})
    return items


def collect(config, limit=60):
    """Скачивает фоны. Ошибка одного источника не мешает остальным."""
    CACHE.mkdir(exist_ok=True)
    found = []
    for name, job in (
        ("YouTube", lambda: [i for c in config.get("youtube", []) for i in youtube_art(c["channel_id"], limit)]),
        ("Скриншоты", lambda: [i for c in config.get("video_channels", []) for i in gameplay_art(c)]),
        # Картинки статей блога выключены по умолчанию: на них крупные надписи.
        ("Блог", lambda: blog_art(config["supercell_blog"]["url"], 20) if config.get("art_from_blog") else []),
    ):
        try:
            found += job()
        except Exception as error:  # noqa: BLE001
            print(f"Фоны ({name}): не удалось получить список: {error}")
    index, people, faces = [], set(), {}
    for item in found:
        path = CACHE / f"{item['id']}.jpg"
        for url in item["urls"]:
            try:
                if not path.exists():
                    download(url, path)
                video = item.get("video", item["id"])
                if PEOPLE_TITLES.search(item["title"]):
                    people.add(video)
                    break
                if real_face(path):
                    # Лица на двух кадрах из трёх: скорее всего, весь ролик с людьми.
                    faces[video] = faces.get(video, 0) + 1
                    if faces[video] >= 2:
                        people.add(video)
                    break
                if letterboxed(path):
                    break
                index.append({"id": item["id"], "title": item["title"], "path": str(path),
                              "video": item.get("video", item["id"]),
                              "credit": item.get("credit", "Арт: Supercell")})
                break
            except Exception as error:  # noqa: BLE001
                print(f"Фон {item['id']}: {type(error).__name__}: {error}")
                continue
    index = [entry for entry in index if entry["video"] not in people]
    titles = {}
    for item in found:
        titles.setdefault(item.get("video", item["id"]), item["title"])
    for video, title in titles.items():
        mark = "ЛЮДИ" if video in people else sum(1 for e in index if e["video"] == video)
        print(f"  ролик {video}: {mark} | {title[:70]}")
    print(f"Роликов с живыми людьми (все их кадры отброшены): {len(people)}")
    INDEX.write_text(json.dumps(index, ensure_ascii=False), encoding="utf-8")
    print(f"Официальных фонов: {len(index)}")
    return index


def load():
    return json.loads(INDEX.read_text(encoding="utf-8")) if INDEX.exists() else []


RECENT = 60  # Фон не повторяется, пока не пройдут столько других обложек.


def pick(text, index, used, screenshot_share=0.5):
    """Фон для поста ({path, credit, ...}) или None. used: список id, обновляется.

    Примерно половина обложек на скриншотах из игры, половина на арте из мультфильмов."""
    import random

    if not index:
        return None
    screens = [item for item in index if item["id"].startswith("gp-")]
    arts = [item for item in index if not item["id"].startswith("gp-")]
    if screens and arts:
        index = screens if random.random() < screenshot_share else arts
    recent = set(used[-min(RECENT, max(len(index) - 1, 0)):])
    fresh = [item for item in index if item["id"] not in recent] or index
    topic = words(text)
    counts = {item["id"]: used.count(item["id"]) for item in index}
    best = max(fresh, key=lambda item: (len(topic & words(item["title"])), -counts[item["id"]], random.random()))
    used.append(best["id"])
    del used[:-300]
    return best
