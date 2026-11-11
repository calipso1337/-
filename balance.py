"""Пост-таблица изменений баланса одного бойца по официальному патчноуту Supercell.

python3 writer.py --publish balance

Gemini получает текст статьи и только раскладывает его по строкам. Каждое число из таблицы
скрипт проверяет по тексту статьи: строки, которых в статье нет, выбрасываются.
"""
import json
import re
import subprocess
import sys
from html.parser import HTMLParser

import bot
import sources

PROMPT = """Ниже текст официальных патчноутов Brawl Stars с сайта Supercell. Выбери ОДНОГО бойца,
у которого больше всего изменений баланса с конкретными числами, и разложи их по строкам.
Бери числа ТОЛЬКО из текста, ничего не придумывай и не пересчитывай.

Ответь только JSON без пояснений:
{{"brawler": "имя бойца как в тексте", "brawler_ru": "имя по-русски, как в русской версии игры",
  "rows": [{{"what": "что изменилось по-русски, до 28 символов", "before": "число как в тексте",
            "after": "число как в тексте", "tone": "good если усиление, bad если ослабление"}}],
  "text": "пост по-русски в HTML Telegram (теги b, i, blockquote): заголовок <b>⚖️ БАЛАНС: ИМЯ</b>,
           подводка в <i>, 1-2 цитаты с сутью изменений, строка 🏆 <b>Вердикт:</b> <i>...</i>,
           вопрос «Станешь играть за него? Ставь 🔥 или 🗿» и хештеги #BrawlStars #баланс.
           Не длиннее 700 символов, символы & и < пиши как &amp; и &lt;."}}

Текст статьи:
{article}"""


class Text(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts, self.skip = [], 0

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style", "noscript"):
            self.skip += 1

    def handle_endtag(self, tag):
        if tag in ("script", "style", "noscript") and self.skip:
            self.skip -= 1

    def handle_data(self, data):
        if not self.skip and data.strip():
            self.parts.append(data.strip())


def page_text(page):
    parser = Text()
    parser.feed(page.decode("utf-8", "replace") if isinstance(page, bytes) else page)
    return " ".join(parser.parts)


def release_notes(config):
    """Адрес и текст последней статьи с патчноутом или изменениями баланса."""
    blog = config["supercell_blog"]["url"]
    links = sources.parse_blog_links(sources.get(blog), blog)
    for link in links:
        if re.search(r"release-notes|balance|patch", link):
            return link, page_text(sources.get(link))
    raise ValueError("в блоге Supercell не нашлось патчноута")


def numbers(value):
    return re.findall(r"\d+(?:[.,]\d+)?", str(value))


def verified_rows(rows, article):
    """Строки, все числа которых есть в статье. Остальное Gemini мог придумать."""
    found = set(numbers(article))
    good = []
    for row in rows:
        values = numbers(row.get("before")) + numbers(row.get("after"))
        if values and all(value in found for value in values) and str(row.get("what", "")).strip():
            good.append(row)
        else:
            print(f"Строка не подтверждена статьёй, пропускаю: {row}")
    return good


def table_data(answer, rows, date):
    sections = []
    for tone, name in (("good", "Усиления"), ("bad", "Ослабления"), ("neutral", "Изменения")):
        part = [[str(r["what"])[:28], str(r["before"])[:18], str(r["after"])[:18]]
                for r in rows if (r.get("tone") if r.get("tone") in ("good", "bad") else "neutral") == tone]
        if part:
            sections.append({"name": name, "tone": tone, "rows": part})
    name = answer.get("brawler_ru") or answer.get("brawler")
    return {"title": f"Баланс: {name}", "subtitle": f"Официальный патчноут Supercell · {date}",
            "columns": ["Что", "Было", "Стало"], "sections": sections}


def balance_post(config, ask, root, stamp):
    link, article = release_notes(config)
    print(f"Патчноут: {link}")
    reply = ask(PROMPT.format(article=article[:30000]))
    candidate = (reply.get("candidates") or [{}])[0]
    raw = "".join(part.get("text", "") for part in candidate.get("content", {}).get("parts", []))
    match = re.search(r"\{.*\}", raw, re.DOTALL)
    if not match:
        raise ValueError(f"Gemini не вернул JSON: {raw[:300]}")
    answer = json.loads(match.group(0))
    rows = verified_rows(answer.get("rows", []), article)
    if len(rows) < 2:
        raise ValueError("слишком мало подтверждённых изменений баланса для таблицы")
    data = root / "images" / f"{stamp}-balance.json"
    image = root / "images" / f"{stamp}-balance.png"
    data.write_text(json.dumps(table_data(answer, rows, stamp[:10]), ensure_ascii=False), encoding="utf-8")
    done = subprocess.run([sys.executable, str(root / "tools" / "make_table.py"), str(image), str(data)],
                          capture_output=True, text=True)
    if done.returncode:
        raise ValueError(f"таблица не нарисовалась: {done.stderr.strip()[-300:]}")
    data.unlink()
    post = {"type": "photo", "image": f"images/{image.name}", "text": answer.get("text", "").strip(),
            "source": link, "source_name": "Supercell", "cover_kind": "update"}
    errors = bot.validate_post(post)
    if errors:
        raise ValueError("пост с таблицей не прошёл проверку: " + "; ".join(errors))
    return post
