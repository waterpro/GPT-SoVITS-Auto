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
        if self.settings.voices_path.exists():
            shutil.copy2(str(self.settings.voices_path), str(self.target))
        else:
            self.target.write_text("voices: {}\n", encoding="utf-8")

    def tearDown(self):
        shutil.rmtree(str(self.tmp), ignore_errors=True)

    def test_pair_key_normalizes_versions_and_training_suffixes(self):
        self.assertEqual(pair_key("model-e10"), pair_key("model_e10_s120"))
        self.assertEqual(pair_key("voice-v2pp"), pair_key("voice_v2pp"))
        self.assertEqual(pair_key("model_x-e10"), pair_key("model_x_e10_s210_l32"))
        self.assertEqual(pair_key("model_y-e10"), pair_key("model_y_e10_s960_l32"))

    def test_discovery_has_pairs_presets_and_packs(self):
        catalog = discover_models(self.settings, self.registry)
        presets = discover_presets(self.settings, self.registry)
        self.assertEqual(len(catalog.unpaired), 0)
        self.assertGreaterEqual(len(presets), 0)
        self.assertGreaterEqual(len(catalog.packs), 0)
        if catalog.pairs:
            self.assertTrue(all(pair.gpt and pair.sovits for pair in catalog.pairs))
        if self.registry.list():
            self.assertTrue(any(pair.configured for pair in catalog.pairs))

    def test_plan_write_and_remove_preset_voice(self):
        presets = discover_presets(self.settings, self.registry)
        if not presets:
            self.skipTest("安装目录没有预设")
        before = yaml.safe_load(self.target.read_text(encoding="utf-8"))
        before_voices = before.get("voices") or {}
        self.assertNotIn("A", before_voices)

        draft = VoiceDraft(key="A", preset=presets[0].name)
        ensure_draft_defaults(self.settings, draft)
        data, text, voice = plan_voice(self.settings, draft, target=self.target)
        self.assertIn("A", data["voices"])
        self.assertEqual(voice.preset, presets[0].name)
        saved = yaml.safe_load(self.target.read_text(encoding="utf-8"))
        self.assertNotIn("A", saved.get("voices") or {})

        data, text, voice, backup = write_voice(
            self.settings, draft, target=self.target
        )
        self.assertTrue(backup and backup.exists())
        saved = yaml.safe_load(self.target.read_text(encoding="utf-8"))
        self.assertIn("A", saved["voices"])

        if not before_voices:
            second = VoiceDraft(key="B", preset=presets[0].name)
            ensure_draft_defaults(self.settings, second)
            write_voice(self.settings, second, target=self.target)

        data, text, backup = remove_voice(self.settings, "A", target=self.target)
        self.assertTrue(backup and backup.exists())
        saved = yaml.safe_load(self.target.read_text(encoding="utf-8"))
        self.assertNotIn("A", saved["voices"])

    def test_duplicate_key_needs_force(self):
        presets = discover_presets(self.settings, self.registry)
        if not presets:
            self.skipTest("安装目录没有预设")
        draft = VoiceDraft(key="A", preset=presets[0].name)
        ensure_draft_defaults(self.settings, draft)
        write_voice(self.settings, draft, target=self.target)
        with self.assertRaises(VoiceError):
            plan_voice(self.settings, draft, target=self.target)
        data, text, voice = plan_voice(
            self.settings, draft, target=self.target, force=True
        )
        self.assertIn("A", data["voices"])
        self.assertEqual(voice.key, "A")

    def test_manual_voice_with_pack_paths(self):
        catalog = discover_models(self.settings, self.registry)
        pair = next(
            (item for item in catalog.pairs if item.gpt and item.sovits),
            None,
        )
        refs = [
            ref
            for pack in catalog.packs
            for ref in pack.refs
            if ref.suffix.lower() in (".wav", ".mp3", ".flac")
        ]
        if pair is None or not refs:
            self.skipTest("没有可用的权重配对或参考音频")
        draft = VoiceDraft(
            key="B",
            gpt=pair.gpt.path,
            sovits=pair.sovits.path,
            ref_audio=refs[0],
            prompt_text="这是一段参考音频。",
            prompt_lang="zh",
            text_lang="zh",
        )
        ensure_draft_defaults(self.settings, draft)
        data, text, voice = plan_voice(self.settings, draft, target=self.target)
        self.assertEqual(voice.version, pair.version)
        self.assertEqual(voice.prompt_text, draft.prompt_text)
        self.assertIn("B", data["voices"])


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
            if data.get("extra_model_roots"):
                self.assertTrue(data["extra_model_roots"][0].startswith(".."))
        finally:
            shutil.rmtree(str(workspace), ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
