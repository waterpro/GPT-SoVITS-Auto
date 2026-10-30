# -*- coding: utf-8 -*-
"""随机多录命名、通用示例和 agent skill 的回归测试。"""

import tempfile
import unittest
from pathlib import Path

from auto_tts.runner import _next_random_index, random_take_path
from auto_tts.settings import load_settings


class RandomTakePathTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.settings = load_settings()
        cls.settings.ensure_dirs()

    def test_random_take_path_uses_marker_suffix(self):
        with tempfile.TemporaryDirectory(dir=str(self.settings.workspace / "tmp")) as tmp:
            base = Path(tmp) / "0001_测试台词.wav"
            path = random_take_path(base, 1, self.settings)
            self.assertEqual(path.name, "0001_测试台词_随机01.wav")
            self.assertEqual(path.parent, base.parent)

    def test_next_random_index_continues_after_existing_files(self):
        with tempfile.TemporaryDirectory(dir=str(self.settings.workspace / "tmp")) as tmp:
            base = Path(tmp) / "0001_测试台词.wav"
            (Path(tmp) / "0001_测试台词_随机01.wav").write_bytes(b"x")
            (Path(tmp) / "0001_测试台词_随机02.wav").write_bytes(b"x")
            self.assertEqual(_next_random_index(base), 3)

    def test_random_take_path_stays_under_max_path(self):
        with tempfile.TemporaryDirectory(dir=str(self.settings.workspace / "tmp")) as tmp:
            base = Path(tmp) / ("长" * 200 + ".wav")
            path = random_take_path(base, 1, self.settings)
            self.assertLessEqual(len(str(path)), int(self.settings.output["max_path_chars"]))
            self.assertTrue(path.name.endswith("_随机01.wav"))


class SkillAndExampleTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.workspace = Path(__file__).resolve().parent.parent

    def test_skill_has_frontmatter_and_generic_examples(self):
        text = (self.workspace / "SKILL.md").read_text(encoding="utf-8")
        self.assertTrue(text.startswith("---"))
        self.assertIn("name: gptsovits-auto-script-format", text)
        self.assertIn("A：雨好像停了", text)
        self.assertIn("[0001] A", text)
        self.assertIn("ID|角色|台词", text)

    def test_example_uses_only_generic_speakers_and_plain_scene(self):
        text = (self.workspace / "examples" / "example.md").read_text(encoding="utf-8")
        self.assertIn("project: 雨后的公交站", text)
        for speaker in ("A", "B", "C", "D"):
            self.assertIn(speaker + "：", text)


if __name__ == "__main__":
    unittest.main()
