"""Мемы-моменты из нарезок: поиск, выбор и пост (yt-dlp и Gemini подменены)."""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import bot  # noqa: E402
import moments  # noqa: E402

VIDEO = {"id": "abc", "duration": 600.0, "channel_id": "UCx", "channel": "Мемный канал",
         "title": "Смешные моменты бравл старс #5"}


def test_filters():
    assert moments.good_compilation(VIDEO, set())
    assert not moments.good_compilation(VIDEO, {"abc"})
    assert not moments.good_compilation({**VIDEO, "duration": 30}, set())
    assert not moments.good_compilation({**VIDEO, "title": "Brawl Stars memes compilation"}, set())


def test_post_has_timecode_player_and_author():
    post = moments.moment_post(VIDEO, 95, "Когда тиммейт & Шелли")
    assert post["video_url"] == "https://www.youtube.com/watch?v=abc&t=95s" == post["source"]
    assert "смотри с 1:35" in post["text"] and "Мемный канал" in post["text"] and "&amp;" in post["text"]
    assert bot.validate_post(post) == []


def test_find_uses_replay_peak(monkeypatch):
    monkeypatch.setattr(moments, "search", lambda query, limit=15: [VIDEO])
    monkeypatch.setattr(moments, "replay_peak", lambda video_id, duration: 200)
    asked = {}

    def ask(video_id, start, end, models):
        asked.update(start=start, end=end)
        return {"ok": True, "start": 212, "end": 230, "title": "Эпичный фейл"}
    monkeypatch.setattr(moments, "ask_video", ask)
    used = set()
    posts = moments.find_moments(2, used, ["m"])
    assert len(posts) == 1 and asked == {"start": 170, "end": 260} and used == {"abc"}
    assert "t=212s" in posts[0]["video_url"]


def test_rejects_bad_answers(monkeypatch):
    monkeypatch.setattr(moments, "search", lambda query, limit=15: [VIDEO])
    monkeypatch.setattr(moments, "replay_peak", lambda video_id, duration: None)
    for answer in ({"ok": False}, {"ok": True, "start": 9999, "title": "x"}, None):
        monkeypatch.setattr(moments, "ask_video", lambda *a, answer=answer: answer)
        assert moments.find_moments(1, set(), ["m"]) == []
