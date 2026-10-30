# -*- coding: utf-8 -*-
"""voices add 的发现、校验、写入和回滚测试。"""
from __future__ import annotations

import shutil
import tempfile
import unittest
from pathlib import Path

import yaml

from auto_tts.bootstrap import update_settings_file
from auto_tts.settings import load_settings
from auto_tts.voice_editor import (
    VoiceDraft,
    discover_models,
    discover_presets,
    ensure_draft_defaults,
    pair_key,
    plan_voice,
    remove_voice,
    write_voice,
)
from auto_tts.voices import VoiceError, load_registry


class VoiceEditorTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.settings = load_settings()
        cls.registry = load_registry(cls.settings)

    def setUp(self):
        self.tmp = Path(
            tempfile.mkdtemp(
                prefix="voice-editor-",
                dir=str(self.settings.workspace / "tmp"),
            )
        )
        self.target = self.tmp / "voices.yaml"
        shutil.copy2(str(self.settings.voices_path), str(self.target))

    def tearDown(self):
        shutil.rmtree(str(self.tmp), ignore_errors=True)

    def test_pair_key_normalizes_versions_and_training_suffixes(self):
        self.assertEqual(pair_key("dania-e15"), pair_key("dania_e16_s2192"))
        self.assertEqual(pair_key("银狼-e10"), pair_key("银狼_e10_s120"))
        self.assertEqual(
            pair_key("MyGO_千早爱音_v2pp"),
            pair_key("MyGO_千早爱音_v2pp"),
        )
        self.assertEqual(pair_key("夕-e10"), pair_key("夕_e10_s210_l32"))
        self.assertEqual(
            pair_key("枫原万叶_ZH-e10"),
            pair_key("枫原万叶_ZH_e10_s960_l32"),
        )

    def test_discovery_has_pairs_presets_and_packs(self):
        catalog = discover_models(self.settings, self.registry)
        presets = discover_presets(self.settings, self.registry)
        self.assertGreaterEqual(len(catalog.pairs), 20)
        self.assertEqual(len(catalog.unpaired), 0)
        self.assertGreaterEqual(len(presets), 7)
        self.assertGreaterEqual(len(catalog.packs), 5)
        by_key = {pair.key: pair for pair in catalog.pairs}
        self.assertIn("dania", by_key)
        self.assertEqual(by_key["dania"].version, "v2ProPlus")
        self.assertIn("银狼", by_key[pair_key("银狼-e10")].configured)

    def test_plan_write_and_remove_preset_voice(self):
        draft = VoiceDraft(key="测试音色", preset="银狼中立")
        ensure_draft_defaults(self.settings, draft)
        data, text, voice = plan_voice(self.settings, draft, target=self.target)
        self.assertIn("测试音色", data["voices"])
        self.assertEqual(voice.preset, "银狼中立")
        self.assertNotIn("测试音色", self.target.read_text(encoding="utf-8"))

        data, text, voice, backup = write_voice(
            self.settings, draft, target=self.target
        )
        self.assertTrue(backup and backup.exists())
        saved = yaml.safe_load(self.target.read_text(encoding="utf-8"))
        self.assertIn("测试音色", saved["voices"])

        data, text, backup = remove_voice(
            self.settings, "测试音色", target=self.target
        )
        self.assertTrue(backup and backup.exists())
        saved = yaml.safe_load(self.target.read_text(encoding="utf-8"))
        self.assertNotIn("测试音色", saved["voices"])

    def test_duplicate_key_needs_force(self):
        draft = VoiceDraft(key="银狼", preset="银狼中立")
        ensure_draft_defaults(self.settings, draft)
        with self.assertRaises(VoiceError):
            plan_voice(self.settings, draft, target=self.target)
        data, text, voice = plan_voice(
            self.settings, draft, target=self.target, force=True
        )
        self.assertIn("银狼", data["voices"])
        self.assertEqual(voice.key, "银狼")

    def test_manual_voice_with_pack_paths(self):
        if not self.settings.extra_model_roots:
            self.skipTest("未配置额外模型目录")
        root = self.settings.extra_model_roots[0] / "达尼亚"
        gpt = root / "dania-e15.ckpt"
        sovits = root / "dania_e16_s2192.pth"
        ref = root / "output.wav_0009342720_0009558400.wav"
        if not (gpt.exists() and sovits.exists() and ref.exists()):
            self.skipTest("达尼亚模型包不存在")
        draft = VoiceDraft(
            key="达尼亚测试",
            gpt=gpt,
            sovits=sovits,
            ref_audio=ref,
            prompt_text="怎么啊？如果有你在也不放心，那就干脆给我也装个限制器或者炸弹喽。",
            prompt_lang="zh",
            text_lang="zh",
        )
        ensure_draft_defaults(self.settings, draft)
        data, text, voice = plan_voice(self.settings, draft, target=self.target)
        self.assertEqual(voice.version, "v2ProPlus")
        self.assertEqual(voice.prompt_text, draft.prompt_text)
        self.assertIn("达尼亚测试", data["voices"])


class PortableSettingsTests(unittest.TestCase):
    def test_update_settings_keeps_relative_paths(self):
        settings = load_settings()
        workspace = Path(
            tempfile.mkdtemp(prefix="settings-", dir=str(settings.workspace / "tmp"))
        )
        try:
            path = update_settings_file(
                workspace,
                install_root=settings.install_root,
                output_root=settings.output_root,
                extra_model_roots=settings.extra_model_roots,
            )
            data = yaml.safe_load(path.read_text(encoding="utf-8"))
            self.assertNotEqual(data["install_root"], str(settings.install_root))
            self.assertIn("..", data["install_root"])
            self.assertIn("..", data["output"]["root"])
            self.assertTrue(data["extra_model_roots"][0].startswith(".."))
        finally:
            shutil.rmtree(str(workspace), ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
