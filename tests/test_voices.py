# -*- coding: utf-8 -*-
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from auto_tts.settings import load_settings  # noqa: E402
from auto_tts.voices import (  # noqa: E402
    VERSIONS,
    detect_version,
    load_registry,
    normalize_lang,
    normalize_name,
)


class VoicesTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.settings = load_settings()
        cls.registry = load_registry(cls.settings)

    def test_empty_registry_is_valid(self):
        directory = Path(
            tempfile.mkdtemp(prefix="empty-voices-", dir=str(self.settings.workspace / "tmp"))
        )
        try:
            target = directory / "voices.yaml"
            target.write_text("voices: {}\n", encoding="utf-8")
            registry = load_registry(self.settings, target)
            self.assertEqual(registry.list(), [])
            self.assertIsNone(registry.resolve("A"))
        finally:
            import shutil

            shutil.rmtree(str(directory), ignore_errors=True)

    def test_voice_references_exist(self):
        for voice in self.registry.list():
            self.assertTrue(voice.ref_audio.exists(), voice.key)
            self.assertTrue(voice.gpt.exists(), voice.key)
            self.assertTrue(voice.sovits.exists(), voice.key)
            self.assertIn(voice.prompt_lang, ("zh", "ja", "en", "ko", "yue"))
            self.assertIn(voice.text_lang, ("zh", "ja", "en", "ko", "yue", "auto"))

    def test_configured_aliases_resolve(self):
        for voice in self.registry.list():
            for alias in voice.aliases:
                self.assertEqual(self.registry.resolve(alias).key, voice.key)

    def test_versions(self):
        paths = []
        for root in self.settings.install_root.glob("SoVITS_weights*"):
            paths.extend(root.glob("*.pth"))
        if not paths:
            self.skipTest("安装目录没有 SoVITS 权重")
        version = detect_version(paths[0])
        self.assertIn(version, VERSIONS)

    def test_normalization(self):
        self.assertEqual(normalize_lang("中文"), "zh")
        self.assertEqual(normalize_lang("日文"), "ja")
        self.assertEqual(normalize_name("A（开心）"), "a(开心)")


if __name__ == "__main__":
    unittest.main()
