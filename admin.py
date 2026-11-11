#!/usr/bin/env python3
"""Админ-бот канала: очередь постов из Telegram и модерация чата обсуждений.

python3 admin.py --until-post   отвечать админам и следить за чатом, пока не подойдёт время поста

Работает внутри звена цепочки post.yml, пока звено ждёт следующего поста, поэтому
отвечает почти всегда (паузы на несколько секунд между звеньями). Админы канала пишут
боту в личку:
  📋 Очередь          сколько постов ждут, когда следующий, кнопки 👁 🚀 ✏️ 🗑 у каждого поста
  ⏰ Следующий пост    время следующей публикации
  любой текст, фото, видео или GIF   новый пост: опубликовать сейчас, следующим или в конец очереди
В чате обсуждений бот ищет нарушения (реклама, мошенничество, 18+, политика, травля, личные
данные, флуд; мат не трогает) и присылает владельцу канала сообщение с кнопками.
"""
import hashlib
import html
import json
import os
import re
import subprocess
import sys
import time

import bot

ROOT = bot.ROOT
SYNC = os.environ.get("ADMIN_GIT_SYNC", "1") == "1"
MENU = {"keyboard": [[{"text": "📋 Очередь"}, {"text": "📜 Вся очередь"}],
                     [{"text": "🤖 Автопосты"}, {"text": "⏰ Следующий пост"}], [{"text": "❓ Помощь"}]],
        "resize_keyboard": True}
HELP = (
    "<b>Управление каналом</b> (доступно только владельцу канала)\n\n"
    "📜 <b>Вся очередь</b>: весь список одним сообщением, вместе с автопостами.\n"
    "🤖 <b>Автопосты</b>: мемы, видео и новости, которые бот нашёл сам; их можно посмотреть и удалить.\n"
    "📋 <b>Очередь</b>: сколько постов ждут и кнопки у каждого:\n"
    "👁 посмотреть пост целиком, 🚀 опубликовать сейчас, ✏️ заменить текст или медиа, 🗑 удалить.\n\n"
    "➕ <b>Новый пост</b>: просто пришли сюда текст, фото, видео или GIF с подписью. "
    "Форматирование (жирный, курсив, цитата, ссылки) сохранится. Потом выбери: опубликовать сейчас, "
    "поставить следующим или в конец очереди.\n\n"
    "🛡 <b>Чат</b>: если в комментариях нарушение (реклама, мошенничество, 18+, политика, травля, "
    "личные данные, флуд), я пришлю его сюда с кнопками «Удалить» и «Забанить». Мат не трогаю.\n\n"
    "Чтобы я видел чат обсуждений, добавь меня туда администратором с правом удалять сообщения и банить."
)
PAGE = 10

# ---------- Модерация (без мата: его владелец разрешил) ----------

RULES = [
    ("реклама или ссылка", r"(https?://|t\.me/|telegram\.me/|www\.|\b\w+\.(ru|com|net|org|io|xyz|me)\b|"
                           r"подпис\w* на (мой|наш)|переход\w* (в|по) (профил|ссылк)|пиши в лс|@\w+bot\b)"),
    ("мошенничество, «бесплатные гемы»", r"(бесплатн\w* гем|гем\w* бесплатно|free gems?|раздач\w* гем|халяв\w* гем|"
                                        r"взлом\w*|накрут\w*|генератор гем|промокод\w* на гем)"),
    ("продажа аккаунтов", r"(прода\w* акк|куплю акк|покупаю акк|обмен\w* акк|акк\w* за \d+|продам акк)"),
    ("читы и приватные серверы", r"(\bчиты?\b|\bчитам\w*|приватк\w*|приватн\w* сервер\w*|\bnulls\b|мод на гем)"),
    ("контент 18+", r"(порн\w*|\bсекс\w*|интим\w*|\b18\+|\bnudes?\b|onlyfans|онлифанс)"),
    ("политика", r"(путин\w*|зеленск\w*|навальн\w*|\bсво\b|\bвойн[аеуы]\b|политик\w*|хохл\w*|москал\w*|"
                 r"\bвыбор(ы|ах|ов)\b)"),
    ("оскорбление, травля или угроза", r"(убей себя|убейся|сдохни|\bkys\b|выпились|повесься|самоубий\w*|"
                                       r"\bдаун\w*|аутист\w*|дебил\w*|\bурод\w*|\bчмо\b|ничтожеств\w*|нищеброд\w*|"
                                       r"\bлох\w*\b|чурк\w*|\bхач\w*|\bжид\w*|нигг\w*|nigg\w*|пидор\w*|пидр\w*|"
                                       r"найду тебя|убью тебя)"),
    ("азарт или наркотики", r"(казино|\bставк\w* на\b|1xbet|букмекер\w*|наркот\w*|закладк\w*|\bснюс\w*)"),
    ("личные данные (телефон)", r"((\+7|\b8)[\s\-]?\(?\d{3}\)?[\s\-]?\d{3}[\s\-]?\d{2}[\s\-]?\d{2}\b)"),
]
RULES = [(name, re.compile(pattern, re.IGNORECASE)) for name, pattern in RULES]
FLOOD = (6, 30)  # Шесть сообщений за 30 секунд от одного человека это флуд.


def violations(text):
    return [name for name, pattern in RULES if pattern.search(text or "")]


# ---------- Разметка Telegram -> HTML ----------

TAGS = {"bold": "b", "italic": "i", "underline": "u", "strikethrough": "s", "code": "code", "pre": "code",
        "spoiler": "tg-spoiler", "blockquote": "blockquote", "expandable_blockquote": "blockquote"}


def to_html(text, entities):
    """Текст и entities из сообщения Telegram -> HTML для parse_mode=HTML (смещения в UTF-16)."""
    if not text:
        return ""
    units = text.encode("utf-16-le")
    opens, closes = {}, {}
    for entity in entities or []:
        kind = entity.get("type")
        if kind == "text_link":
            start_tag, end_tag = f'<a href="{html.escape(entity["url"], quote=True)}">', "</a>"
        elif kind in TAGS:
            start_tag, end_tag = f"<{TAGS[kind]}>", f"</{TAGS[kind]}>"
        else:
            continue
        start, end = entity["offset"], entity["offset"] + entity["length"]
        opens.setdefault(start, []).append((end, start_tag))
        closes.setdefault(end, []).append((start, end_tag))
    out = []
    for position in range(len(units) // 2 + 1):
        for _, tag in sorted(closes.get(position, []), key=lambda item: -item[0]):
            out.append(tag)
        for _, tag in sorted(opens.get(position, []), key=lambda item: -item[0]):
            out.append(tag)
        if position < len(units) // 2:
            out.append(units[position * 2: position * 2 + 2])
    # Склеиваем символы обратно (суррогатные пары собираются при декодировании).
    result, chunk = [], bytearray()
    for part in out:
        if isinstance(part, bytes):
            chunk += part
        else:
            if chunk:
                result.append(html.escape(chunk.decode("utf-16-le"), quote=False))
                chunk = bytearray()
            result.append(part)
    if chunk:
        result.append(html.escape(chunk.decode("utf-16-le"), quote=False))
    return "".join(result)


def post_from_message(message):
    """Пост для очереди из сообщения админа или None."""
    caption = to_html(message.get("caption") or message.get("text") or "",
                      message.get("caption_entities") or message.get("entities"))
    if message.get("photo"):
        return {"type": "photo", "image": "tg:" + message["photo"][-1]["file_id"], "text": caption}
    if message.get("video"):
        return {"type": "video", "video": "tg:" + message["video"]["file_id"], "text": caption}
    if message.get("animation"):
        return {"type": "video", "video": "tg:" + message["animation"]["file_id"], "animation": True, "text": caption}
    if message.get("text"):
        return {"type": "text", "text": caption}
    return None


# ---------- Очередь ----------


def pid(post):
    return hashlib.sha1(json.dumps(post, sort_keys=True, ensure_ascii=False).encode()).hexdigest()[:10]


def title(post):
    text = re.sub(r"<[^>]+>", "", (post.get("text") or post.get("question") or "").split("\n")[0])
    icon = {"photo": "🖼", "video": "🎬", "poll": "📊", "album": "🎨"}.get(post.get("type"), "📝")
    return f"{icon} {html.unescape(text).strip()[:45] or 'без текста'}"


def git(*args):
    return subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True)


def refresh():
    """Свежая очередь с GitHub (её могли поменять бот, Gemini или другой админ)."""
    if SYNC:
        git("fetch", "-q", "origin", "main")
        git("reset", "-q", "--hard", "origin/main")


def mutate(change, message, extra=()):
    """Меняет очередь и состояние на свежей версии и сразу сохраняет в GitHub.

    change(posts, state) возвращает результат или None, если менять нечего."""
    for _ in range(4):
        refresh()
        posts = json.loads(bot.QUEUE.read_text(encoding="utf-8"))
        state = bot.load_state()
        result = change(posts, state)
        if result is None:
            return None
        bot.QUEUE.write_text(json.dumps(posts, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        bot.write_json(bot.STATE, state)
        if not SYNC:
            return result
        git("add", "queue.json", "state.json", *extra)
        git("commit", "-q", "-m", message)
        if git("push", "-q", "origin", "HEAD:main").returncode == 0:
            return result
        time.sleep(3)
    raise RuntimeError("не удалось сохранить изменения в GitHub")


def find(posts, state, post_id):
    for index in range(state["next"], len(posts)):
        if pid(posts[index]) == post_id:
            return index
    return None


class Admin:
    def __init__(self, token, channel):
        self.token, self.channel = token, channel
        self.admins, self.owner, self.chat = set(), None, None
        self.pending = {}  # chat_id -> ("edit", post_id) | ("draft", post)
        self.drafts = {}
        self.recent = {}
        self.config = bot.load_config()

    def api(self, method, **payload):
        return bot.call(self.token, method, payload).get("result")

    def say(self, chat, text, keyboard=None, **extra):
        payload = {"chat_id": chat, "text": text, "parse_mode": "HTML",
                   "link_preview_options": {"is_disabled": True}, **extra}
        if keyboard:
            payload["reply_markup"] = keyboard
        return self.api("sendMessage", **payload)

    def setup(self):
        """Кто админ канала и какой чат у него для комментариев."""
        members = self.api("getChatAdministrators", chat_id=self.channel) or []
        self.owner = next((m["user"]["id"] for m in members if m.get("status") == "creator"), None)
        # Управлять ботом может только владелец канала.
        self.admins = {self.owner} if self.owner else set()
        info = self.api("getChat", chat_id=self.channel) or {}
        self.chat = info.get("linked_chat_id")
        me = self.api("getMe") or {}
        self.username = me.get("username", "")
        print(f"Бот @{self.username}, владелец: {self.owner}, чат обсуждений: {self.chat or 'нет'}")

    def hello(self):
        """Пробует написать владельцу первым. Telegram разрешает это, только если владелец уже нажимал «Старт»."""
        self.setup()
        hook = self.api("getWebhookInfo") or {}
        if hook.get("url"):
            print("У бота был webhook, он мешал получать сообщения: удаляю")
            self.api("deleteWebhook")
        try:
            self.say(self.owner, "👋 Привет! Я бот канала. Управление очередью и модерация здесь.\n\n" + HELP, MENU)
            print("Владельцу отправлено приветствие")
        except RuntimeError as error:
            print(f"Написать первым не получилось ({error}). Открой ссылку и нажми «Старт»: "
                  f"https://t.me/{self.username}?start=admin")

    # ----- очередь -----

    def queue_view(self, chat, page=0):
        refresh()
        posts = json.loads(bot.QUEUE.read_text(encoding="utf-8"))
        state = bot.load_state()
        waiting = posts[state["next"]:]
        moment = bot.next_post_at(self.config, state, time.time())
        head = (f"📋 <b>В очереди {len(waiting)} постов</b>\n"
                f"⏰ Следующий пост: {bot.clock(self.config, moment):%d.%m в %H:%M}\n"
                "Ещё бот сам добавляет видео, мемы и новости из источников.")
        if not waiting:
            return self.say(chat, head + "\n\nОчередь пуста: пришли мне пост или подожди, Gemini допишет ночью.")
        rows, lines = [], []
        start = page * PAGE
        for number, post in enumerate(waiting[start:start + PAGE], start + 1):
            lines.append(f"{number}. {html.escape(title(post), quote=False)}")
            key = pid(post)
            rows.append([{"text": f"{number} 👁", "callback_data": f"v:{key}"},
                         {"text": "🚀", "callback_data": f"p:{key}"},
                         {"text": "✏️", "callback_data": f"e:{key}"},
                         {"text": "🗑", "callback_data": f"d:{key}"}])
        nav = []
        if page:
            nav.append({"text": "⬅️ Назад", "callback_data": f"q:{page - 1}"})
        if start + PAGE < len(waiting):
            nav.append({"text": "Дальше ➡️", "callback_data": f"q:{page + 1}"})
        if nav:
            rows.append(nav)
        return self.say(chat, head + "\n\n" + "\n".join(lines), {"inline_keyboard": rows})

    def full_list(self, chat):
        refresh()
        posts, state = json.loads(bot.QUEUE.read_text(encoding="utf-8")), bot.load_state()
        inbox = bot.read_json(bot.INBOX, [])
        moment = bot.next_post_at(self.config, state, time.time())
        lines = [f"📜 <b>Вся очередь</b>, следующий пост {bot.clock(self.config, moment):%d.%m в %H:%M}", "",
                 f"<b>Посты ({len(posts) - state['next']}):</b>"]
        lines += [f"{n}. {html.escape(title(p), quote=False)}" for n, p in enumerate(posts[state["next"]:], 1)]
        names = {"meme": "😂 мем", "news": "📰 новость/видео"}
        lines += ["", f"<b>Автопосты ({len(inbox)}), выходят в свои слоты (мем каждым "
                      f"{self.config.get('meme_every', 3)}-м постом, новости сразу):</b>"]
        lines += [f"• {names.get(i['kind'], i['kind'])}: {html.escape(title(i['post']), quote=False)}" for i in inbox]
        if not inbox:
            lines.append("пока нет")
        text, chunk = "\n".join(lines), ""
        for line in text.split("\n"):
            if len(chunk) + len(line) > 3800:
                self.say(chat, chunk)
                chunk = ""
            chunk += line + "\n"
        return self.say(chat, chunk)

    def auto_view(self, chat):
        refresh()
        inbox = bot.read_json(bot.INBOX, [])
        if not inbox:
            return self.say(chat, "🤖 Автопостов сейчас нет: бот ищет мемы, видео и новости перед каждым постом.")
        rows, lines = [], []
        for number, item in enumerate(inbox[:20], 1):
            lines.append(f"{number}. {html.escape(title(item['post']), quote=False)}")
            key = pid(item["post"])
            rows.append([{"text": f"{number} 👁", "callback_data": f"iv:{key}"},
                         {"text": "🗑", "callback_data": f"id:{key}"}])
        return self.say(chat, f"🤖 <b>Автопосты: {len(inbox)}</b>\n\n" + "\n".join(lines), {"inline_keyboard": rows})

    def on_callback(self, query):
        chat = query["message"]["chat"]["id"]
        action, _, key = query.get("data", "").partition(":")
        self.api("answerCallbackQuery", callback_query_id=query["id"])
        if action == "q":
            return self.queue_view(chat, int(key))
        if action == "iv":
            refresh()
            item = next((i for i in bot.read_json(bot.INBOX, []) if pid(i["post"]) == key), None)
            return bot.publish(self.token, chat, item["post"]) if item else self.say(chat, "Этого автопоста уже нет.")
        if action == "id":
            def drop_auto(posts, state):
                inbox = bot.read_json(bot.INBOX, [])
                kept = [i for i in inbox if pid(i["post"]) != key]
                if len(kept) == len(inbox):
                    return None
                bot.write_json(bot.INBOX, kept)
                return True
            done = mutate(drop_auto, "Админ удалил автопост", extra=("inbox.json",))
            return self.say(chat, "🗑 Автопост удалён." if done else "Этого автопоста уже нет.")
        if action == "v":
            refresh()
            posts, state = json.loads(bot.QUEUE.read_text(encoding="utf-8")), bot.load_state()
            index = find(posts, state, key)
            if index is None:
                return self.say(chat, "Этого поста уже нет в очереди.")
            return bot.publish(self.token, chat, posts[index])
        if action == "p":
            return self.publish_from_queue(chat, key)
        if action == "e":
            self.pending[chat] = ("edit", key)
            return self.say(chat, "✏️ Пришли новый текст поста (форматирование сохранится) или новое фото/видео "
                                  "с подписью. /cancel чтобы отменить.")
        if action == "d":
            return self.say(chat, "Точно удалить пост из очереди?", {"inline_keyboard": [[
                {"text": "🗑 Да, удалить", "callback_data": f"D:{key}"},
                {"text": "Отмена", "callback_data": "x:"}]]})
        if action == "D":
            def drop(posts, state):
                index = find(posts, state, key)
                return None if index is None else posts.pop(index)
            removed = mutate(drop, "Админ удалил пост из очереди")
            return self.say(chat, f"🗑 Удалён: {html.escape(title(removed), quote=False)}" if removed
                            else "Этого поста уже нет в очереди.")
        if action in ("n", "s", "a"):
            post = self.drafts.pop(key, None)
            if not post:
                return self.say(chat, "Черновик устарел, пришли пост ещё раз.")
            if action == "n":
                bot.publish(self.token, self.channel, post)
                return self.say(chat, "🚀 Опубликовано в канале.")
            where = "следующим" if action == "s" else "в конец очереди"

            def add(posts, state):
                if action == "s":
                    posts.insert(state["next"], post)
                else:
                    posts.append(post)
                return len(posts) - state["next"]
            total = mutate(add, f"Админ добавил пост {where}")
            return self.say(chat, f"✅ Добавлен {where}. Теперь в очереди {total} постов.")
        if action == "x":
            self.drafts.pop(key, None)
            return self.say(chat, "Отменено.")
        if action in ("md", "mb", "mk"):
            return self.on_moderation(query, action, key)
        return None

    def publish_from_queue(self, chat, key):
        holder = {}

        def take(posts, state):
            index = find(posts, state, key)
            if index is None:
                return None
            holder["post"] = posts.pop(index)
            return True
        if not mutate(take, "Админ опубликовал пост вне очереди"):
            return self.say(chat, "Этого поста уже нет в очереди.")
        try:
            bot.publish(self.token, self.channel, holder["post"])
        except Exception:
            # Не получилось: возвращаем пост на место, чтобы он не потерялся.
            mutate(lambda posts, state: posts.insert(state["next"], holder["post"]) or True,
                   "Возврат поста после ошибки публикации")
            raise
        return self.say(chat, "🚀 Опубликовано в канале.")

    def on_private(self, message):
        chat, text = message["chat"]["id"], (message.get("text") or "").strip()
        if message.get("from", {}).get("id") not in self.admins:
            return self.say(chat, "Это служебный бот канала t.me/All_About_Brawl. Подписывайся! 🙂")
        if text.startswith("/start") or text in ("/help", "❓ Помощь"):
            return self.say(chat, HELP, MENU)
        if text in ("/queue", "📋 Очередь"):
            return self.queue_view(chat)
        if text in ("/all", "📜 Вся очередь"):
            return self.full_list(chat)
        if text in ("/auto", "🤖 Автопосты"):
            return self.auto_view(chat)
        if text in ("/next", "⏰ Следующий пост"):
            state = bot.load_state()
            moment = bot.next_post_at(self.config, state, time.time())
            return self.say(chat, f"⏰ Следующий пост: {bot.clock(self.config, moment):%d.%m в %H:%M} (Москва).")
        if text == "/cancel":
            self.pending.pop(chat, None)
            return self.say(chat, "Отменено.", MENU)
        post = post_from_message(message)
        if not post:
            return self.say(chat, "Такой тип сообщения я пока не умею добавлять. Пришли текст, фото, видео или GIF.")
        errors = bot.validate_post(post)
        if errors:
            return self.say(chat, "Не получится: " + "; ".join(errors))
        pending = self.pending.pop(chat, None)
        if pending and pending[0] == "edit":
            key = pending[1]

            def replace(posts, state):
                index = find(posts, state, key)
                if index is None:
                    return None
                old = posts[index]
                if post["type"] == "text" and old.get("type") in ("photo", "video", "poll"):
                    old["text"] = post["text"]  # Новый текст, медиа прежнее.
                else:
                    posts[index] = {**post, **{k: old[k] for k in ("source", "source_name") if k in old}}
                return posts[index]
            new = mutate(replace, "Админ изменил пост в очереди")
            return self.say(chat, f"✏️ Готово: {html.escape(title(new), quote=False)}" if new
                            else "Этого поста уже нет в очереди.")
        key = pid(post)
        self.drafts[key] = post
        return self.say(chat, "Что сделать с этим постом?", {"inline_keyboard": [
            [{"text": "🚀 Опубликовать сейчас", "callback_data": f"n:{key}"}],
            [{"text": "⏭ Следующим в очереди", "callback_data": f"s:{key}"},
             {"text": "📥 В конец очереди", "callback_data": f"a:{key}"}],
            [{"text": "❌ Отмена", "callback_data": f"x:{key}"}]]})

    # ----- модерация -----

    def on_group(self, message):
        sender = message.get("from") or {}
        if message.get("is_automatic_forward") or message.get("sender_chat") or sender.get("is_bot"):
            return None  # Пересылка поста из канала или сообщение от имени канала.
        if sender.get("id") in self.admins:
            return None
        reasons = violations((message.get("text") or "") + " " + (message.get("caption") or ""))
        now = time.time()
        times = [t for t in self.recent.get(sender.get("id"), []) if now - t < FLOOD[1]] + [now]
        self.recent[sender.get("id")] = times
        if len(times) == FLOOD[0]:
            reasons.append("флуд")
        if not reasons or not self.owner:
            return None
        chat, msg = message["chat"]["id"], message["message_id"]
        name = html.escape(" ".join(filter(None, [sender.get("first_name"), sender.get("last_name")])) or "без имени",
                           quote=False)
        login = f" (@{sender['username']})" if sender.get("username") else ""
        link = f"https://t.me/c/{str(chat).removeprefix('-100')}/{msg}"
        body = html.escape((message.get("text") or message.get("caption") or "[медиа без текста]")[:700], quote=False)
        text = (f"⚠️ <b>Нарушение в чате:</b> {', '.join(reasons)}\n"
                f"👤 {name}{login}, id {sender.get('id')}\n\n<blockquote>{body}</blockquote>\n"
                f'<a href="{link}">Открыть сообщение</a>')
        key = f"{chat}:{msg}:{sender.get('id')}"
        return self.say(self.owner, text, {"inline_keyboard": [[
            {"text": "🗑 Удалить", "callback_data": f"md:{key}"},
            {"text": "🚫 Удалить и забанить", "callback_data": f"mb:{key}"}],
            [{"text": "✅ Оставить", "callback_data": f"mk:{key}"}]]})

    def on_moderation(self, query, action, key):
        if query.get("from", {}).get("id") not in self.admins:
            return None
        chat, msg, user = key.split(":")
        result = "✅ Оставлено"
        try:
            if action in ("md", "mb"):
                self.api("deleteMessage", chat_id=int(chat), message_id=int(msg))
                result = "🗑 Удалено"
            if action == "mb":
                self.api("banChatMember", chat_id=int(chat), user_id=int(user))
                result = "🚫 Удалено, автор забанен"
        except RuntimeError as error:
            result = f"Не получилось: {error}. Проверь, что я админ чата с правом удалять и банить."
        message = query["message"]
        return self.api("editMessageText", chat_id=message["chat"]["id"], message_id=message["message_id"],
                        text=f"{message.get('text', '')}\n\n{result}")

    # ----- цикл -----

    def handle(self, update):
        if "callback_query" in update:
            return self.on_callback(update["callback_query"])
        message = update.get("message") or update.get("edited_message")
        if not message:
            return None
        kind = message["chat"].get("type")
        if kind == "private" and "message" in update:
            return self.on_private(message)
        if kind in ("group", "supergroup") and (not self.chat or message["chat"]["id"] == self.chat):
            return self.on_group(message)
        return None

    def run(self, until):
        self.setup()
        offset = None
        while time.time() < until:
            wait = int(min(25, max(1, until - time.time())))
            try:
                updates = bot.call(self.token, "getUpdates", {
                    "offset": offset, "timeout": wait,
                    "allowed_updates": ["message", "edited_message", "callback_query"]}).get("result", [])
            except RuntimeError as error:
                print(f"getUpdates: {error}")
                time.sleep(5)
                continue
            for update in updates:
                offset = update["update_id"] + 1
                try:
                    self.handle(update)
                except Exception as error:  # noqa: BLE001 - одна ошибка не должна останавливать бота
                    print(f"Ошибка обработки: {type(error).__name__}: {error}")
                    chat = ((update.get("message") or (update.get("callback_query") or {}).get("message") or {})
                            .get("chat", {}).get("id"))
                    if chat and chat > 0:
                        try:
                            self.say(chat, f"Что-то пошло не так: {html.escape(str(error), quote=False)}")
                        except RuntimeError:
                            pass
        if offset:
            bot.call(self.token, "getUpdates", {"offset": offset, "timeout": 0})  # Подтверждаем прочитанное.


def main():
    token, channel = os.environ.get("TELEGRAM_BOT_TOKEN"), os.environ.get("TELEGRAM_CHANNEL")
    config, state, now = bot.load_config(), bot.load_state(), time.time()
    until = now + bot.sleep_until_post(config, state, now)
    if not token or not channel:
        print("Нет TELEGRAM_BOT_TOKEN: просто жду")
        time.sleep(max(until - now, 0))
        return
    try:
        Admin(token, channel).run(until)
    except Exception as error:  # noqa: BLE001 - пост важнее админки: дождёмся его в любом случае
        print(f"Админ-бот остановился: {type(error).__name__}: {error}")
        time.sleep(max(until - time.time(), 0))


if __name__ == "__main__" and "--until-post" in sys.argv:
    main()
elif __name__ == "__main__" and "--preview-test" in sys.argv:
    # Какая ссылка на YouTube даёт плеер прямо в Telegram: присылаем владельцу четыре варианта.
    robot = Admin(os.environ["TELEGRAM_BOT_TOKEN"], os.environ["TELEGRAM_CHANNEL"])
    robot.setup()
    short = sys.argv[sys.argv.index("--preview-test") + 1]
    for number, url in enumerate([f"https://www.youtube.com/shorts/{short}", f"https://youtu.be/{short}",
                                  f"https://www.youtube.com/watch?v={short}", f"https://m.youtube.com/shorts/{short}"], 1):
        robot.api("sendMessage", chat_id=robot.owner, text=f"Вариант {number}: {url}",
                  link_preview_options={"url": url, "prefer_large_media": True, "show_above_text": True})
    print("Отправлено 4 варианта")
elif __name__ == "__main__" and "--hello" in sys.argv:
    Admin(os.environ["TELEGRAM_BOT_TOKEN"], os.environ["TELEGRAM_CHANNEL"]).hello()
