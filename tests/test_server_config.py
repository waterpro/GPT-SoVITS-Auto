# -*- coding: utf-8 -*-
from __future__ import annotations

import hashlib
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from auto_tts.server import build_local_config  # noqa: E402
from auto_tts.settings import is_within, load_settings  # noqa: E402
from auto_tts.voice_editor import discover_presets  # noqa: E402
from auto_tts.voices import load_registry  # noqa: E402


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


class ServerConfigTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.settings = load_settings()
        cls.settings.ensure_dirs()
        registry = load_registry(cls.settings)
        if registry.list():
            cls.voice = registry.list()[0]
            return

        presets = discover_presets(cls.settings, registry)
        if not presets:
            raise unittest.SkipTest("没有可用音色或预设")

        cls.tmpdir = Path(
            tempfile.mkdtemp(
                prefix="server-config-",
                dir=str(cls.settings.workspace / "tmp"),
            )
        )
        cls.addClassCleanup(shutil.rmtree, str(cls.tmpdir), True)
        target = cls.tmpdir / "voices.yaml"
        data = {
            "voices": {
                "A": {
                    "preset": presets[0].name,
                    "output_dir": "A",
                    "text_lang": "zh",
                }
            }
        }
        target.write_text(
            yaml.safe_dump(data, allow_unicode=True, sort_keys=False),
            encoding="utf-8",
        )
        cls.voice = load_registry(cls.settings, target).list()[0]

    def test_build_local_config_is_workspace_only(self):
        before = sha256(self.settings.install_config)
        path = build_local_config(self.settings, self.voice)
        after = sha256(self.settings.install_config)
        self.assertEqual(before, after)
        self.assertEqual(path, self.settings.config_path)
        self.assertTrue(is_within(path, self.settings.workspace))
        self.assertFalse(is_within(path, self.settings.install_root))
        with path.open("r", encoding="utf-8") as fh:
            data = yaml.safe_load(fh)
        custom = data["custom"]
        self.assertEqual(custom["version"], self.voice.version)
        self.assertEqual(Path(custom["t2s_weights_path"]), self.voice.gpt)
        self.assertEqual(Path(custom["vits_weights_path"]), self.voice.sovits)
        self.assertTrue(Path(custom["bert_base_path"]).is_absolute())
        self.assertTrue(Path(custom["cnhuhbert_base_path"]).is_absolute())


if __name__ == "__main__":
    unittest.main()
