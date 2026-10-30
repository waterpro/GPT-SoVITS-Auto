# -*- coding: utf-8 -*-
from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from auto_tts.settings import (  # noqa: E402
    SettingsError,
    is_within,
    load_settings,
)


class SettingsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.settings = load_settings()

    def test_required_paths(self):
        self.assertTrue(self.settings.install_root.exists())
        self.assertTrue(self.settings.runtime_python.exists())
        self.assertTrue(self.settings.api_script.exists())
        self.assertTrue(self.settings.install_config.exists())

    def test_install_is_read_only(self):
        with self.assertRaises(SettingsError):
            self.settings.check_writable(self.settings.install_config, "测试")
        with self.assertRaises(SettingsError):
            self.settings.check_writable(self.settings.install_root / "new.txt", "测试")

    def test_workspace_writable(self):
        target = self.settings.workspace / "tmp" / "settings-test.txt"
        self.assertEqual(self.settings.check_writable(target, "测试"), target)

    def test_is_within(self):
        self.assertTrue(is_within(self.settings.install_root / "a", self.settings.install_root))
        self.assertFalse(is_within(self.settings.workspace, self.settings.install_root))


if __name__ == "__main__":
    unittest.main()
