"""Проверки бота на локальной заглушке Telegram."""
import json
import os
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import bot


class Fake(ThreadingHTTPServer):
    def __init__(self):
        super().__init__(("127.0.0.1", 0), Handler)
        self.calls, self.fail = [], 0


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def do_POST(self):
        raw = self.rfile.read(int(self.headers["content-length"]))
        method = self.path.rsplit("/", 1)[1]
        if self.server.fail:
            self.server.fail -= 1
            data, code = b'{"ok": false, "description": "Bad Request: broken"}', 400
        else:
            self.server.calls.append((method, self.headers["content-type"], raw))
            data, code = b'{"ok": true, "result": {"message_id": 1}}', 200
        self.send_response(code)
        self.send_header("content-length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


@pytest.fixture
def run(tmp_path):
    server = Fake()
    threading.Thread(target=server.serve_forever, daemon=True).start()
    (tmp_path / "images").mkdir()
    (tmp_path / "images" / "a.png").write_bytes(b"\x89PNG-test")
    (tmp_path / "videos").mkdir()
    (tmp_path / "videos" / "a.mp4").write_bytes(b"MP4-test")
    (tmp_path / "bot.py").write_text((ROOT / "bot.py").read_text(encoding="utf-8"), encoding="utf-8")

    def go(posts, state=0, arguments=("--now",), config=None, inbox=None):
        (tmp_path / "queue.json").write_text(json.dumps(posts), encoding="utf-8")
        (tmp_path / "state.json").write_text(json.dumps({"next": state}))
        (tmp_path / "config.json").write_text(json.dumps(config or {"slots": []}))
        (tmp_path / "inbox.json").write_text(json.dumps(inbox or []))
        env = {
            **os.environ,
            "TELEGRAM_BOT_TOKEN": "secret-token",
            "TELEGRAM_CHANNEL": "@channel",
            "TELEGRAM_API_BASE": f"http://127.0.0.1:{server.server_port}",
        }
        env["SKIP_FETCH"] = "1"
        env.pop("POST_NOW", None)
        done = subprocess.run(
            [sys.executable, "bot.py", *arguments], cwd=tmp_path, env=env, capture_output=True, text=True
        )
        return done, json.loads((tmp_path / "state.json").read_text())["next"]

    yield go, server
    server.shutdown()


def test_local_image_is_uploaded_with_footer(run):
    go, server = run
    done, state = go([{"type": "photo", "image": "images/a.png", "text": "<b>Заголовок</b>", "source": "https://e.com/?a=1&b=2"}])
    assert done.returncode == 0 and state == 1
    method, kind, raw = server.calls[0]
    text = raw.decode("utf-8", "replace")
    assert method == "sendPhoto" and kind.startswith("multipart/form-data")
    assert "PNG-test" in text and "<b>Заголовок</b>" in text
    assert "a=1&amp;b=2" in text and 'name="parse_mode"' in text
    assert "Источник: e.com" in text and "reply_markup" in text and "t.me/share" in text
    assert "неофициальный" not in text


def test_local_video_is_uploaded(run):
    go, server = run
    done, state = go([{"type": "video", "video": "videos/a.mp4", "text": "<b>Видео</b>", "source": "https://e.com/a"}])
    assert done.returncode == 0 and state == 1
    method, kind, raw = server.calls[0]
    text = raw.decode("utf-8", "replace")
    assert method == "sendVideo" and kind.startswith("multipart/form-data")
    assert "MP4-test" in text and 'name="video"' in text and 'name="supports_streaming"' in text


def test_video_link_shows_player_above_text(run):
    go, server = run
    url = "https://www.youtube.com/watch?v=abc"
    go([{"type": "text", "text": "Смотри", "video_url": url, "source": url, "source_name": "YouTube"}])
    preview = json.loads(server.calls[0][2])["link_preview_options"]
    assert preview == {"url": url, "prefer_large_media": True, "show_above_text": True}


def test_url_image_uses_json(run):
    go, server = run
    go([{"type": "photo", "image": "https://e.com/a.png", "text": "x"}])
    payload = json.loads(server.calls[0][2])
    assert payload["photo"] == "https://e.com/a.png" and payload["parse_mode"] == "HTML"


def test_poll_with_image_sends_both(run):
    go, server = run
    go([{"type": "poll", "image": "images/a.png", "text": "Голосуем", "question": "Да?", "options": ["Да", "Нет"]}])
    assert [call[0] for call in server.calls] == ["sendPhoto", "sendPoll"]


def test_plain_poll_and_text(run):
    go, server = run
    go([{"type": "poll", "question": "Да?", "options": ["Да", "Нет"]}, {"type": "text", "text": "Привет"}])
    assert server.calls[0][0] == "sendPoll"
    go([{"type": "poll", "question": "Да?", "options": ["Да", "Нет"]}, {"type": "text", "text": "Привет"}], 1)
    payload = json.loads(server.calls[1][2])
    assert server.calls[1][0] == "sendMessage" and payload["link_preview_options"] == {"is_disabled": True}


def test_source_name_and_button_in_text_post(run):
    go, server = run
    go([{"type": "text", "text": "Привет", "source": "https://e.com/a", "source_name": "Supercell"}])
    payload = json.loads(server.calls[0][2])
    assert payload["text"].endswith('<a href="https://e.com/a">Источник: Supercell</a>')
    assert payload["reply_markup"] == bot.SHARE_BUTTON


def test_post_without_source_has_no_footer(run):
    go, server = run
    go([{"type": "text", "text": "Привет"}])
    assert json.loads(server.calls[0][2])["text"] == "Привет"


def test_no_slot_means_no_post(run):
    go, server = run
    done, state = go([{"type": "text", "text": "Привет"}], arguments=())
    assert done.returncode == 0 and state == 0 and server.calls == []
    assert "нет слота" in done.stdout


def slot_config(offset_seconds):
    """Конфиг с одним слотом, который наступает через offset_seconds."""
    moment = time.gmtime(time.time() + offset_seconds + 60)
    return {"timezone_offset_hours": 0, "slots": [time.strftime("%H:%M", moment)]}


def test_posts_in_slot_and_remembers_it(run, tmp_path):
    go, server = run
    config = slot_config(-120)
    done, state = go([{"type": "text", "text": "Привет"}], arguments=(), config=config)
    assert done.returncode == 0 and state == 1
    saved = json.loads((tmp_path / "state.json").read_text())
    assert saved["last_slot"].endswith(config["slots"][0])


MSK = {"timezone_offset_hours": 3, "slots": ["09:00", "10:00"], "meme_slots": ["10:00"]}
NINE = 1794204000  # 2026-11-09 09:00 по Москве


@pytest.mark.parametrize(
    "shift, expected",
    [(-300, "2026-11-09 09:00"), (-10, "2026-11-09 09:00"), (0, "2026-11-09 09:00"),
     (19 * 60, "2026-11-09 09:00"), (21 * 60, None), (-7 * 60, None), (3600 - 60, "2026-11-09 10:00")],
)
def test_due_slot(shift, expected):
    slot = bot.due_slot(MSK, {"next": 0}, NINE + shift)
    assert (slot and slot[1]) == expected
    if slot:
        assert slot[0] in (NINE, NINE + 3600)


def test_slot_is_used_once():
    assert bot.due_slot(MSK, {"next": 0, "last_slot": "2026-11-09 09:00"}, NINE + 60) is None


def test_choose_order():
    meme = {"kind": "meme", "id": "m"}
    news = {"kind": "news", "id": "n"}
    posts = [{"type": "text", "text": "x"}]
    state = {"next": 0}
    assert bot.choose(posts, state, [news, meme], MSK, "10:00", "d", False) == ("meme", meme)
    assert bot.choose(posts, state, [news, meme], MSK, "09:00", "d", False) == ("news", news)
    assert bot.choose(posts, {"next": 0, "news_today": {"d": 4}}, [news], MSK, "09:00", "d", False) == ("queue", 0)
    assert bot.choose(posts, {"next": 1}, [meme], MSK, "09:00", "d", False) == ("meme", meme)
    assert bot.choose(posts, {"next": 1}, [], MSK, "09:00", "d", False) is None


def test_inbox_news_goes_first(run):
    go, server = run
    inbox = [{"kind": "news", "id": "v1", "added": int(time.time()), "post": {"type": "text", "text": "Новость"}}]
    done, state = go([{"type": "text", "text": "Из очереди"}], inbox=inbox)
    assert state == 0 and json.loads(server.calls[0][2])["text"] == "Новость"


def test_fallback_image_after_url_failure(run):
    go, server = run
    server.fail = 1
    post = {"type": "photo", "image": "https://e.com/x.jpg", "fallback_image": "images/a.png", "text": "x"}
    done, state = go([post])
    assert done.returncode == 0 and state == 1
    assert server.calls[0][1].startswith("multipart/form-data")


def test_failed_send_keeps_position_and_hides_token(run):
    go, server = run
    server.fail = 1
    done, state = go([{"type": "text", "text": "Привет"}])
    assert done.returncode != 0 and state == 0
    assert "secret-token" not in done.stdout + done.stderr


def test_empty_queue_is_ok(run):
    go, _ = run
    done, state = go([{"type": "text", "text": "Старый"}], 1)
    assert done.returncode == 0 and "закончилась" in done.stdout and state == 1


def test_broken_old_post_does_not_stop_queue(run):
    go, server = run
    done, state = go([{"type": "photo", "image": "images/deleted.png"}, {"type": "text", "text": "Новый"}], 1)
    assert done.returncode == 0 and state == 2


@pytest.mark.parametrize(
    "post, message",
    [
        ({"type": "photo", "image": "images/none.png", "text": "x"}, "не найден"),
        ({"type": "photo", "image": "../secret.png", "text": "x"}, "не найден"),
        ({"type": "photo", "text": "x"}, "нет картинки"),
        ({"type": "text", "text": "<b>x"}, "не закрыт"),
        ({"type": "text", "text": "a & b"}, "&amp;"),
        ({"type": "text", "text": "2 < 3"}, "&lt;"),
        ({"type": "text", "text": "<div>x</div>"}, "не поддерживается"),
        ({"type": "photo", "image": "https://e.com/a.png", "text": "я" * 1025}, "длиннее 1024"),
        ({"type": "poll", "question": "Да?", "options": ["Да"]}, "от 2 до 10"),
        ({"type": "audio"}, "неизвестный"),
        ({"type": "text", "text": "x", "video_url": "https://e.com/a"}, "source должен совпадать"),
        ({"type": "video", "video": "videos/none.mp4", "text": "x"}, "не найдено"),
        ({"type": "video", "text": "x"}, "не найдено"),
    ],
)
def test_validation(post, message):
    assert message in "; ".join(bot.validate_post(post))


def test_html_tags_do_not_count_towards_limit():
    errors, visible = bot.inspect_markup('<b>abc</b> <a href="https://e.com">d</a>')
    assert errors == [] and visible == 5


def test_real_queue_is_valid():
    posts = json.loads(bot.QUEUE.read_text(encoding="utf-8"))
    bot.validate(posts, bot.load_state()["next"])
