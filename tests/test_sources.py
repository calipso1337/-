"""Проверки автоматических источников на образцах данных, без сети."""
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import bot
import sources

NOW = 1794204000

RSS = b"""<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns:yt="http://www.youtube.com/xml/schemas/2015" xmlns="http://www.w3.org/2005/Atom">
 <entry><yt:videoId>new1</yt:videoId><title>Brawl Talk: Frogs &amp; more!</title>
  <published>2026-11-09T05:00:00+00:00</published></entry>
 <entry><yt:videoId>old1</yt:videoId><title>Old video</title>
  <published>2026-10-01T05:00:00+00:00</published></entry>
</feed>"""

BLOG = b"""<html><a href="/en/games/brawlstars/blog/news/frog-update/">a</a>
<a href="https://supercell.com/en/games/brawlstars/blog/release-notes/nov-2026">b</a>
<a href="/en/games/brawlstars/blog/news/">category</a>
<a href="/en/games/brawlstars/blog/news/frog-update/">dup</a></html>"""

ARTICLE = b"""<html><head><meta property="og:title" content="Frogs &amp; Kings arrive">
<meta property="og:description" content="Three frogs, one brawler. Read <all> about it.">
<meta property="og:image" content="https://cdn.supercell.com/frog.jpg"></head></html>"""


def reddit_item(**extra):
    data = {"id": "r1", "title": "When the frogs jump", "author": "brawler42", "score": 900,
            "link_flair_text": "Humor", "url": "https://i.redd.it/abc.png", "subreddit": "Brawlstars",
            "permalink": "/r/Brawlstars/comments/r1/x/", "over_18": False}
    data.update(extra)
    return {"data": data}


CONFIG = {
    "youtube": [{"name": "Brawl Stars", "channel_id": "UC1"}],
    "supercell_blog": {"url": "https://supercell.com/en/games/brawlstars/blog/", "fallback_image": "images/auto-supercell-news.png"},
    "reddit": {"subreddit": "Brawlstars", "flairs": ["humor", "meme"], "min_score": 300},
}


def fake_get(responses):
    def get(url, headers=None, data=None, timeout=20):
        for part, body in responses.items():
            if part in url:
                if isinstance(body, Exception):
                    raise body
                return body
        raise AssertionError(f"неожиданный адрес {url}")
    return get


def test_parse_youtube():
    videos = sources.parse_youtube(RSS)
    assert [v["id"] for v in videos] == ["new1", "old1"]
    assert videos[0]["published"] == 1794200400


def test_youtube_post_is_valid():
    post = sources.youtube_post(sources.parse_youtube(RSS)[0], "Brawl Stars")
    assert bot.validate_post(post) == []
    assert "Frogs &amp; more" in post["text"] and post["video_url"] == post["source"]


def test_blog_links_and_post():
    links = sources.parse_blog_links(BLOG, CONFIG["supercell_blog"]["url"])
    assert links == [
        "https://supercell.com/en/games/brawlstars/blog/news/frog-update/",
        "https://supercell.com/en/games/brawlstars/blog/release-notes/nov-2026/",
    ]
    post = sources.blog_post(links[0], sources.parse_meta(ARTICLE), "images/auto-supercell-news.png")
    assert post["image"] == "https://cdn.supercell.com/frog.jpg"
    assert "&lt;all&gt;" in post["text"] and bot.validate_post(post) == []


def test_meme_filter():
    listing = {"data": {"children": [
        reddit_item(),
        reddit_item(id="nsfw", over_18=True),
        reddit_item(id="low", score=10),
        reddit_item(id="art", link_flair_text="Fan Art"),
        reddit_item(id="vid", url="https://v.redd.it/xyz"),
        reddit_item(id="bad", title="free gems giveaway"),
        reddit_item(id="best", score=5000, link_flair_text="Meme"),
    ]}}
    memes = sources.parse_reddit(listing, CONFIG["reddit"])
    assert [m["id"] for m in memes] == ["best", "r1"]
    post = sources.meme_post(memes[1])
    assert bot.validate_post(post) == [] and "u/brawler42" in post["text"]


def test_first_run_only_remembers(monkeypatch):
    monkeypatch.setattr(sources, "get", fake_get({"youtube": RSS, "/blog/news/frog": ARTICLE, "/blog/": BLOG,
                                                  "reddit": json.dumps({"data": {"children": [reddit_item()]}}).encode()}))
    inbox, seen = [], {}
    sources.fetch_all(CONFIG, inbox, seen, NOW, bot.validate_post)
    assert [item["kind"] for item in inbox] == ["meme"]
    assert seen["youtube:UC1"] == ["old1", "new1"] and len(seen["blog"]) == 2


def test_new_items_go_to_inbox_once(monkeypatch):
    monkeypatch.setattr(sources, "get", fake_get({"youtube": RSS, "/blog/news/frog": ARTICLE,
                                                  "/blog/release-notes": ARTICLE, "/blog/": BLOG,
                                                  "reddit": OSError("403")}))
    seen = {"youtube:UC1": ["old1"], "blog": [], "reddit": []}
    inbox = []
    sources.fetch_all(CONFIG, inbox, seen, NOW, bot.validate_post)
    assert [item["id"] for item in inbox] == ["new1", *sources.parse_blog_links(BLOG, CONFIG["supercell_blog"]["url"])]
    sources.fetch_all(CONFIG, inbox, seen, NOW, bot.validate_post)
    assert len(inbox) == 3


def test_old_items_expire():
    inbox = [{"kind": "news", "id": "a", "added": NOW - 40 * 3600, "post": {}},
             {"kind": "meme", "id": "b", "added": NOW - 40 * 3600, "post": {}}]
    assert [item["id"] for item in sources.expire(inbox, NOW)] == ["b"]


def test_real_config_is_valid():
    config = json.loads((ROOT / "config.json").read_text(encoding="utf-8"))
    assert all(len(slot) == 5 and slot[2] == ":" for slot in config["slots"])
    assert set(config["meme_slots"]) <= set(config["slots"])
    assert (ROOT / config["supercell_blog"]["fallback_image"]).is_file()


def test_failed_source_stays_in_first_run(monkeypatch):
    monkeypatch.setattr(sources, "get", fake_get({"youtube": OSError("403"), "/blog/": b"<html></html>", "reddit": OSError("403")}))
    seen, inbox = {}, []
    sources.fetch_all(CONFIG, inbox, seen, NOW, bot.validate_post)
    assert seen == {} and inbox == []
