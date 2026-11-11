"""Таблица баланса: строки без подтверждения статьёй выбрасываются."""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import balance  # noqa: E402
import bot  # noqa: E402

ARTICLE = "<html><script>var x=999</script><p>Shelly: Health increased from 3600 to 3800.</p>" \
          "<p>Super damage reduced from 320 to 300. Reload 1.5s</p></html>"


def test_page_text_skips_scripts():
    text = balance.page_text(ARTICLE)
    assert "999" not in text and "3600" in text


def test_rows_must_be_in_article():
    text = balance.page_text(ARTICLE)
    rows = [{"what": "Здоровье", "before": "3600", "after": "3800", "tone": "good"},
            {"what": "Урон супера", "before": "320", "after": "300", "tone": "bad"},
            {"what": "Скорость", "before": "720", "after": "770", "tone": "good"}]
    assert [r["what"] for r in balance.verified_rows(rows, text)] == ["Здоровье", "Урон супера"]


def test_full_post(tmp_path, monkeypatch):
    (tmp_path / "images").mkdir()
    (tmp_path / "tools").symlink_to(ROOT / "tools")
    monkeypatch.setattr(bot, "ROOT", tmp_path)
    monkeypatch.setattr(balance, "release_notes", lambda config: ("https://supercell.com/x/release-notes/", balance.page_text(ARTICLE)))
    answer = {"brawler": "Shelly", "brawler_ru": "Шелли",
              "rows": [{"what": "Здоровье", "before": "3600", "after": "3800", "tone": "good"},
                       {"what": "Урон супера", "before": "320", "after": "300", "tone": "bad"},
                       {"what": "Выдумка", "before": "1", "after": "12345", "tone": "good"}],
              "text": "<b>⚖️ БАЛАНС: ШЕЛЛИ</b>\n<i>Изменения.</i>\n\n🏆 <b>Вердикт:</b> <i>ок.</i>\n\n#BrawlStars"}
    reply = {"candidates": [{"content": {"parts": [{"text": json.dumps(answer, ensure_ascii=False)}]}}]}
    post = balance.balance_post({}, lambda prompt: reply, tmp_path, "2026-11-11-2100")
    assert post["source_name"] == "Supercell" and (tmp_path / post["image"]).is_file()
    assert not list((tmp_path / "images").glob("*.json"))
