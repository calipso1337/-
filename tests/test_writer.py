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
    monkeypatch.setitem(writer.BACKGROUNDS, "index", [])
    monkeypatch.setitem(writer.BACKGROUNDS, "used", [])
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


@pytest.mark.parametrize("text", ["Сливайся с кустами", "Делай ставку на контроль", "Чекай кусты"])
def test_banned_filter_game_slang(text):
    assert not writer.BANNED.search(text)


TEXT = "<b>💡 КАК ИГРАТЬ</b>\n<i>Подводка.</i>\n\n<blockquote>Факт</blockquote>\n\n🏆 <b>Вердикт:</b> <i>мнение.</i>\n\n💬 Вопрос?\n\n#BrawlStars"


def test_strip_verdict():
    text = writer.strip_verdict(TEXT)
    assert "Вердикт" not in text and "\n\n\n" not in text
    assert text.endswith("💬 Вопрос?\n\n#BrawlStars") and "<blockquote>Факт</blockquote>\n\n💬" in text


def test_verdict_only_for_balance():
    assert writer.is_balance({"text": "<b>⚖️ БАЛАНС: УСИЛЕНИЯ</b>"})
    assert writer.is_balance({"text": "<b>x</b>"}, "nerf")
    assert not writer.is_balance({"text": "<b>КАКОЙ БАФФИ ВЫБРАТЬ</b>"})
    assert not writer.is_balance({"text": "<b>КАК ЗАБИВАТЬ В БРОУЛБОЛЕ</b>"})


def test_polls_are_rare():
    posts = [{"type": "photo"}] * 12
    assert writer.poll_allowed(posts)
    assert not writer.poll_allowed(posts + [{"type": "poll"}] + [{"type": "photo"}] * 9)
    assert writer.poll_allowed(posts + [{"type": "poll"}] + [{"type": "photo"}] * 10)


def test_batch_plan_is_varied():
    plan, text = writer.batch_plan(8, True, True)
    assert plan.count("tip") <= 2 and plan.count("news") == 2 and plan.count("poll") == 1
    assert {"fact", "top"} <= set(plan) and "совет" in text
    plan, _ = writer.batch_plan(5, False, False)
    assert "news" not in plan and "poll" not in plan


def test_diversify_no_same_kind_in_a_row():
    posts = [{"kind": "old"}] + [{"kind": "tip", "n": i} for i in range(5)] + \
            [{"kind": "art"}, {"kind": "art"}, {"kind": "fact"}, {"type": "poll"}, {"kind": "top"}]
    writer.diversify(posts, 1)
    kinds = [writer.kind_of(p) for p in posts[1:]]
    assert posts[0] == {"kind": "old"} and sorted(kinds) == sorted(["tip"] * 5 + ["art", "art", "fact", "poll", "top"])
    assert sum(a == b for a, b in zip(kinds, kinds[1:])) <= 1


def test_art_albums(monkeypatch, tmp_path):
    monkeypatch.setattr(writer, "ROOT", tmp_path)
    monkeypatch.setitem(writer.BACKGROUNDS, "index", [
        {"id": "yt-v1-1", "video": "v1", "title": "Gus's Origin Story! (Brawl Stars Animation)"},
        {"id": "yt-v1-3", "video": "v1", "title": "Gus's Origin Story! (Brawl Stars Animation)"},
        {"id": "gp-v2-1", "video": "v2", "title": "Скриншот"}, {"id": "gp-v2-2", "video": "v2", "title": "x"},
        {"id": "yt-v3-2", "video": "v3", "title": "Один кадр"}])
    albums = writer.art_albums(2, [])
    assert len(albums) == 1 and albums[0]["images"] == ["https://i.ytimg.com/vi/v1/maxres1.jpg",
                                                        "https://i.ytimg.com/vi/v1/maxres3.jpg"]
    assert "«Gus's Origin Story!»" in albums[0]["text"] and writer.bot.validate_post(albums[0]) == []
    assert writer.art_albums(2, albums) == []  # Тот же мультфильм второй раз не берём.


def test_one_news_per_article(no_network):
    news = {**NEWS, "kind": "news"}
    added = writer.build_all([news, {**news, "text": "<b>ДУБЛЬ</b>"}], set(), {}, posts=[])
    assert len(added) == 1
    assert writer.build_all([news], set(), {}, posts=added) == []
