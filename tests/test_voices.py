# -*- coding: utf-8 -*-
from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from auto_tts.settings import load_settings  # noqa: E402
from auto_tts.voices import (  # noqa: E402
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

    def test_seeded_voices(self):
        self.assertGreaterEqual(len(self.registry.list()), 7)
        self.assertEqual(self.registry.resolve("千早爱音").key, "爱音")
        self.assertEqual(self.registry.resolve("Anon").key, "爱音")
        self.assertEqual(self.registry.resolve("高松灯").key, "灯")
        self.assertEqual(self.registry.resolve("Tomorin").key, "灯")
        self.assertEqual(self.registry.resolve("银狼开心").key, "银狼（开心）")
        self.assertEqual(self.registry.resolve("银狼（开心）").key, "银狼（开心）")
        self.assertEqual(self.registry.resolve("白祥").key, "祥子")
        self.assertEqual(self.registry.resolve("Funina").key, "芙宁娜")
        self.assertIsNone(self.registry.resolve("不存在的角色"))

    def test_voice_references_exist(self):
        for voice in self.registry.list():
            self.assertTrue(voice.ref_audio.exists(), voice.key)
            self.assertTrue(voice.gpt.exists(), voice.key)
            self.assertTrue(voice.sovits.exists(), voice.key)
            self.assertIn(voice.prompt_lang, ("zh", "ja", "en", "ko", "yue"))
            self.assertIn(voice.text_lang, ("zh", "ja", "en", "ko", "yue", "auto"))

    def test_versions(self):
        sovits = self.settings.install_root / "SoVITS_weights_v2ProPlus" / "银狼_e10_s120.pth"
        self.assertEqual(detect_version(sovits), "v2ProPlus")
        v4 = self.settings.install_root / "SoVITS_weights_v4" / "夕_e10_s210_l32.pth"
        self.assertEqual(detect_version(v4), "v4")

    def test_normalization(self):
        self.assertEqual(normalize_lang("中文"), "zh")
        self.assertEqual(normalize_lang("日文"), "ja")
        self.assertEqual(normalize_name("银狼（开心）"), "银狼(开心)")


if __name__ == "__main__":
    unittest.main()
