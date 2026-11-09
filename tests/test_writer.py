"""Проверки писателя постов на подготовленных ответах Gemini, без сети."""
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import writer

NEWS = {"kind": "news", "source": "https://supercell.com/en/games/brawlstars/blog/news/x/",
        "text": "<b>🚀 НОВОСТЬ</b>\n\n<i>Подводка.</i>\n\n#BrawlStars",
        "cover": {"kind": "news", "title": "Новость", "subtitle": "Коротко"}}


def reply(items, domains=("supercell.com",)):
    text = "Вот посты:\n```json\n" + json.dumps(items, ensure_ascii=False) + "\n```"
    chunks = [{"web": {"uri": "https://vertexaisearch.cloud.google.com/x", "title": d}} for d in domains]
    return {"candidates": [{"content": {"parts": [{"text": text}]}, "groundingMetadata": {"groundingChunks": chunks}}]}


@pytest.fixture
def no_network(monkeypatch, tmp_path):
    monkeypatch.setattr(writer, "ROOT", tmp_path)
    (tmp_path / "images").mkdir()
    (tmp_path / "tools").symlink_to(ROOT / "tools")
    monkeypatch.setattr(writer, "official_image", lambda url: None)
    monkeypatch.setattr(writer.bot, "ROOT", tmp_path)
    return tmp_path


def test_parse_reply_reads_json_and_sources():
    items, grounded = writer.parse_reply(reply([NEWS], ("www.dexerto.com", "supercell.com")))
    assert items == [NEWS] and grounded == {"dexerto.com", "supercell.com"}


def test_trusted_sources():
    assert writer.trusted("https://supercell.com/a", set())
    assert writer.trusted("https://www.dexerto.com/a", {"dexerto.com"})
    assert not writer.trusted("https://random.site/a", {"dexerto.com"})
    assert not writer.trusted("https://vertexaisearch.cloud.google.com/a", {"vertexaisearch.cloud.google.com"})
    assert not writer.trusted("", set())


def test_build_news_poll_meme(no_network):
    post, reason = writer.build(NEWS, set(), "2026-11-09", 1)
    assert reason is None and post["type"] == "photo" and post["source_name"] == "Supercell"
    assert (no_network / post["image"]).is_file()
    poll = {"kind": "poll", "text": "<b>ОПРОС</b>", "question": "Кто лучше?", "options": ["А", "Б"],
            "cover": {"kind": "poll", "title": "Опрос"}}
    post, reason = writer.build(poll, set(), "2026-11-09", 2)
    assert reason is None and post["type"] == "poll" and post["options"] == ["А", "Б"]
    meme = {"kind": "meme", "text": "<b>😂 МЕМ</b>", "top": "Когда открыл ящик", "bottom": "А там монетки"}
    post, reason = writer.build(meme, set(), "2026-11-09", 3)
    assert reason is None and post["image"].endswith("-03-meme.png")


@pytest.mark.parametrize(
    "item, reason",
    [
        ({**NEWS, "source": "https://fake.example/a"}, "не подтверждён"),
        ({**NEWS, "text": "Как получить бесплатные гемы"}, "запрещённая тема"),
        ({**NEWS, "text": "<b>не закрыт"}, "не закрыт"),
        ({"kind": "meme", "text": "x", "top": "", "bottom": "y"}, "нет подписей"),
        ({"kind": "story"}, "неизвестный"),
    ],
)
def test_build_rejects(no_network, item, reason):
    post, why = writer.build(item, set(), "2026-11-09", 1)
    assert post is None and reason in why
    assert list((no_network / "images").iterdir()) == []


@pytest.mark.parametrize("text", ["Учитывай перезарядку", "Считаю, что Мортис силён", "Читай карту заранее",
                                  "Защита ворот в Броулболе", "Где апать ранги проще", "Заставка нового сезона"])
def test_banned_filter_allows_normal_words(text):
    assert not writer.BANNED.search(text)


@pytest.mark.parametrize("text", ["Где скачать читы", "Бесплатные гемы тут", "Приватный сервер Brawl", "Слив нового бойца",
                                  "Розыгрыш аккаунта", "Как заработать на игре"])
def test_banned_filter_blocks(text):
    assert writer.BANNED.search(text)
