"""Проверки админ-бота: разметка, модерация и управление очередью (Telegram и git подменены)."""
import json
import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ["ADMIN_GIT_SYNC"] = "0"

import admin  # noqa: E402
import bot  # noqa: E402

OWNER, CHAT = 111, -1009999


def test_to_html_keeps_formatting_and_escapes():
    text = "Жирный & курсив 😀 ссылка"
    entities = [{"type": "bold", "offset": 0, "length": 6},
                {"type": "italic", "offset": 9, "length": 6},
                {"type": "text_link", "offset": 19, "length": 6, "url": "https://e.com/?a=1&b=2"}]
    assert admin.to_html(text, entities) == (
        '<b>Жирный</b> &amp; <i>курсив</i> 😀 <a href="https://e.com/?a=1&amp;b=2">ссылка</a>')
    nested = admin.to_html("цитата жирная", [{"type": "blockquote", "offset": 0, "length": 13},
                                             {"type": "bold", "offset": 7, "length": 6}])
    assert nested == "<blockquote>цитата <b>жирная</b></blockquote>"
    assert bot.inspect_markup(nested)[0] == []


@pytest.mark.parametrize("text, reason", [
    ("Заходи на t.me/mychannel", "реклама"),
    ("раздача бесплатных гемов тут", "мошенничество"),
    ("продам акк с 50к кубков", "продажа аккаунтов"),
    ("скачай приватку с читами", "читы"),
    ("путин и выборы", "политика"),
    ("ты даун, сдохни", "оскорбление"),
    ("звони +7 912 345-67-89", "личные данные"),
])
def test_violations(text, reason):
    assert any(reason in found for found in admin.violations(text))


@pytest.mark.parametrize("text", ["Блин, опять слил катку, капец", "Шелли имба, ставлю лайк",
                                  "Где фармить кубки?", "Читаю патчноут", "Классный пост 🔥"])
def test_normal_chat_is_fine(text):
    assert admin.violations(text) == []


def test_mat_is_not_a_violation():
    assert admin.violations("бля, опять проиграл, сука") == []


def test_post_from_message():
    photo = {"photo": [{"file_id": "small"}, {"file_id": "big"}], "caption": "Подпись",
             "caption_entities": [{"type": "bold", "offset": 0, "length": 7}]}
    assert admin.post_from_message(photo) == {"type": "photo", "image": "tg:big", "text": "<b>Подпись</b>"}
    video = admin.post_from_message({"video": {"file_id": "v"}, "caption": "Смотри"})
    assert video == {"type": "video", "video": "tg:v", "text": "Смотри"}
    gif = admin.post_from_message({"animation": {"file_id": "g"}})
    assert gif["animation"] is True and bot.validate_post(gif) == []


class FakeTelegram:
    def __init__(self):
        self.calls = []

    def __call__(self, token, method, payload):
        self.calls.append((method, payload))
        if method == "getChatAdministrators":
            return {"result": [{"status": "creator", "user": {"id": OWNER}},
                               {"status": "administrator", "user": {"id": 5, "is_bot": True}}]}
        if method == "getChat":
            return {"result": {"linked_chat_id": CHAT}}
        return {"ok": True, "result": {"message_id": 1}}

    def sent(self, method):
        return [payload for name, payload in self.calls if name == method]


@pytest.fixture
def bot_env(tmp_path, monkeypatch):
    for name in ("QUEUE", "STATE", "INBOX", "SEEN", "CONFIG"):
        monkeypatch.setattr(bot, name, tmp_path / getattr(bot, name).name)
    posts = [{"type": "text", "text": "старый"}, {"type": "text", "text": "<b>Первый</b>"},
             {"type": "text", "text": "<b>Второй</b>"}]
    bot.QUEUE.write_text(json.dumps(posts, ensure_ascii=False), encoding="utf-8")
    bot.write_json(bot.STATE, {"next": 1, "next_at": 1794210000})
    fake = FakeTelegram()
    monkeypatch.setattr(bot, "call", fake)
    robot = admin.Admin("token", "@channel")
    robot.setup()
    return robot, fake


def queue():
    return json.loads(bot.QUEUE.read_text(encoding="utf-8"))


def private(text=None, **extra):
    return {"message": {"chat": {"id": OWNER, "type": "private"}, "from": {"id": OWNER}, "text": text, **extra}}


def press(data):
    return {"callback_query": {"id": "q", "data": data, "from": {"id": OWNER},
                               "message": {"chat": {"id": OWNER}, "message_id": 7, "text": "алерт"}}}


def test_queue_view_counts_and_buttons(bot_env):
    robot, fake = bot_env
    robot.handle(private("📋 Очередь"))
    message = fake.sent("sendMessage")[-1]
    assert "В очереди 2 постов" in message["text"] and "Первый" in message["text"]
    buttons = message["reply_markup"]["inline_keyboard"]
    assert [b["text"] for b in buttons[0]] == ["1 👁", "🚀", "✏️", "🗑"]


def test_delete_and_publish_now(bot_env):
    robot, fake = bot_env
    first, second = queue()[1], queue()[2]
    robot.handle(press(f"D:{admin.pid(first)}"))
    assert queue()[1:] == [second]
    robot.handle(press(f"p:{admin.pid(second)}"))
    assert queue()[1:] == []
    assert fake.sent("sendMessage")[-2]["chat_id"] == "@channel"  # Ушло в канал.


def test_edit_text_keeps_media(bot_env):
    robot, fake = bot_env
    target = queue()[1]
    robot.handle(press(f"e:{admin.pid(target)}"))
    robot.handle(private("Новый текст", entities=[{"type": "bold", "offset": 0, "length": 5}]))
    assert queue()[1]["text"] == "<b>Новый</b> текст"


def test_new_post_goes_next(bot_env):
    robot, fake = bot_env
    robot.handle(private(None, video={"file_id": "vid"}, caption="Ролик"))
    key = admin.pid({"type": "video", "video": "tg:vid", "text": "Ролик"})
    robot.handle(press(f"s:{key}"))
    assert queue()[1] == {"type": "video", "video": "tg:vid", "text": "Ролик"}


def test_strangers_cannot_manage(bot_env):
    robot, fake = bot_env
    robot.handle({"message": {"chat": {"id": 42, "type": "private"}, "from": {"id": 42}, "text": "📋 Очередь"}})
    assert "служебный бот" in fake.sent("sendMessage")[-1]["text"]


def group(text, user=77, **extra):
    return {"message": {"chat": {"id": CHAT, "type": "supergroup"}, "message_id": 5,
                        "from": {"id": user, "first_name": "Вася", "username": "vasya"}, "text": text, **extra}}


def test_moderation_alerts_owner(bot_env):
    robot, fake = bot_env
    robot.handle(group("Бесплатные гемы тут: t.me/scam"))
    alert = fake.sent("sendMessage")[-1]
    assert alert["chat_id"] == OWNER and "Нарушение" in alert["text"] and "@vasya" in alert["text"]
    robot.handle(press(f"mb:{CHAT}:5:77"))
    assert fake.sent("deleteMessage") and fake.sent("banChatMember")[0]["user_id"] == 77
    assert "забанен" in fake.sent("editMessageText")[-1]["text"]


def test_channel_forwards_and_clean_messages_are_ignored(bot_env):
    robot, fake = bot_env
    before = len(fake.sent("sendMessage"))
    robot.handle(group("Новый пост t.me/All_About_Brawl", is_automatic_forward=True))
    robot.handle(group("Крутой пост, спасибо!"))
    robot.handle(group("моя ссылка t.me/x", user=OWNER))  # Админам можно.
    assert len(fake.sent("sendMessage")) == before


def test_flood(bot_env):
    robot, fake = bot_env
    for _ in range(6):
        robot.handle(group("ааа"))
    assert "флуд" in fake.sent("sendMessage")[-1]["text"]
