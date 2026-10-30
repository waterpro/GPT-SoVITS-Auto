# -*- coding: utf-8 -*-
from __future__ import annotations

import io
import sys
import unittest
import wave
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from auto_tts.settings import load_settings  # noqa: E402
from auto_tts.wavcheck import WavError, inspect_wav_bytes, inspect_wav_file, write_wav_atomic  # noqa: E402


def make_wav(seconds: float = 0.1, rate: int = 32000) -> bytes:
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(rate)
        handle.writeframes(b"\x00\x00" * int(rate * seconds))
    return buffer.getvalue()


class WavTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.settings = load_settings()
        cls.settings.ensure_dirs()

    def test_inspect_valid(self):
        info = inspect_wav_bytes(make_wav(0.1))
        self.assertEqual(info.framerate, 32000)
        self.assertAlmostEqual(info.duration, 0.1, places=2)

    def test_inspect_invalid(self):
        with self.assertRaises(WavError):
            inspect_wav_bytes(b"not a wav")

    def test_write_atomic(self):
        target = self.settings.workspace / "tmp" / "wav-test.wav"
        try:
            info = write_wav_atomic(target, make_wav(0.2), self.settings)
            self.assertTrue(target.exists())
            self.assertAlmostEqual(info.duration, 0.2, places=2)
            self.assertEqual(inspect_wav_file(target).framerate, 32000)
        finally:
            target.unlink(missing_ok=True)


if __name__ == "__main__":
    unittest.main()
