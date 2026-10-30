# -*- coding: utf-8 -*-
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from auto_tts.naming import (  # noqa: E402
    build_output_path,
    choose_existing_safe_path,
    choose_output_path,
    sanitize_component,
    sanitize_id,
    sanitize_project,
    sanitize_text,
    state_path,
)
from auto_tts.settings import load_settings  # noqa: E402


class NamingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.settings = load_settings()
        cls.settings.ensure_dirs()

    def test_sanitize_component(self):
        cleaned = sanitize_component('a<>:"/\\|?*b')
        for char in '<>:"/\\|?*':
            self.assertNotIn(char, cleaned)
        self.assertEqual(sanitize_component("  name.  "), "name")
        self.assertTrue(sanitize_component("CON").startswith("_"))
        self.assertEqual(sanitize_component(""), "_")

    def test_sanitize_text_keeps_fullwidth_punctuation(self):
        text = sanitize_text("你好，世界？")
        self.assertIn("，", text)
        self.assertIn("？", text)
        self.assertNotIn("?", text)
        self.assertNotIn(":", text)

    def test_sanitize_id_and_project(self):
        self.assertEqual(sanitize_id("5931"), "5931")
        self.assertEqual(sanitize_id("a_b-c"), "a_b-c")
        self.assertNotIn("/", sanitize_id("a b/c"))
        self.assertNotIn(" ", sanitize_id("a b/c"))
        project = sanitize_project("../evil/name")
        self.assertNotIn("/", project)
        self.assertNotIn("\\", project)

    def test_build_output_path(self):
        path = build_output_path(
            self.settings, "银狼", "测试项目", "0001", "你好。"
        )
        self.assertTrue(str(path).startswith(str(self.settings.output_root)))
        self.assertEqual(path.suffix, ".wav")
        self.assertIn("0001_", path.name)

    def test_choose_output_path_suffix(self):
        base = self.settings.workspace / "tmp" / "a.wav"
        used = set()
        first = choose_output_path(base, used)
        used.add(first)
        second = choose_output_path(base, used)
        self.assertNotEqual(first, second)
        self.assertTrue(second.name.endswith("_2.wav"))

    def test_choose_existing_safe_path_never_overwrites_unowned(self):
        path = self.settings.workspace / "tmp" / "owned-test.wav"
        path.write_bytes(b"x")
        try:
            chosen = choose_existing_safe_path(path, owned=False, overwrite=True)
            self.assertNotEqual(chosen, path)
            self.assertTrue(chosen.name.endswith("_2.wav"))
            owned = choose_existing_safe_path(path, owned=True, overwrite=False)
            self.assertEqual(owned, path)
        finally:
            path.unlink(missing_ok=True)

    def test_state_path(self):
        path = state_path(self.settings, "测试", "a" * 64)
        self.assertIn(str(self.settings.state_dir), str(path))
        self.assertTrue(path.name.endswith(".json"))


if __name__ == "__main__":
    unittest.main()
