"""Проверки подбора официальных фонов."""
import sys
from pathlib import Path

from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import art


def test_letterboxed_shorts_frame(tmp_path):
    shorts = Image.new("RGB", (1280, 720), (0, 0, 0))
    ImageDraw.Draw(shorts).rectangle((440, 0, 840, 720), fill=(200, 120, 40))
    shorts.save(tmp_path / "s.jpg")
    full = Image.new("RGB", (1280, 720), (60, 140, 230))
    full.save(tmp_path / "f.jpg")
    assert art.letterboxed(tmp_path / "s.jpg") and not art.letterboxed(tmp_path / "f.jpg")


def test_pick_prefers_topic_then_rotates():
    index = [{"id": "a", "title": "Frogs Brawl Talk", "path": "a"},
             {"id": "b", "title": "Ranked season", "path": "b"},
             {"id": "c", "title": "Hypercharge", "path": "c"}]
    used = []
    assert art.pick("Громожабы и frogs", index, used) == "a"
    picks = {art.pick("что-то другое", index, used) for _ in range(2)}
    assert picks == {"b", "c"}
    assert art.pick("", [], used) is None
