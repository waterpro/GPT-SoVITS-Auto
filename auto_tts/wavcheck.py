"""WAV 校验与原子写入。"""

from __future__ import annotations

import io
import os
import tempfile
import wave
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from .settings import Settings


class WavError(Exception):
    """音频数据不是有效的 WAV。"""


@dataclass
class WavInfo:
    channels: int
    sample_width: int
    framerate: int
    frames: int
    duration: float

    def summary(self) -> str:
        return "%.2fs %dHz %dch" % (self.duration, self.framerate, self.channels)


def inspect_wav_bytes(data: bytes) -> WavInfo:
    if not data or len(data) < 44:
        raise WavError("音频数据太短（%d 字节）" % len(data))
    if data[:4] != b"RIFF" or data[8:12] != b"WAVE":
        raise WavError("返回数据不是 RIFF/WAVE（开头：%r）" % data[:16])
    try:
        with wave.open(io.BytesIO(data), "rb") as wav:
            channels = wav.getnchannels()
            sample_width = wav.getsampwidth()
            framerate = wav.getframerate()
            frames = wav.getnframes()
    except (wave.Error, EOFError) as exc:
        raise WavError("WAV 解析失败：%s" % exc) from exc
    if framerate <= 0 or frames <= 0:
        raise WavError("WAV 没有有效音频帧")
    duration = frames / float(framerate)
    if duration < 0.05:
        raise WavError("音频过短：%.3fs" % duration)
    if duration > 1800:
        raise WavError("音频异常长：%.1fs" % duration)
    return WavInfo(
        channels=channels,
        sample_width=sample_width,
        framerate=framerate,
        frames=frames,
        duration=duration,
    )


def inspect_wav_file(path: Path) -> Optional[WavInfo]:
    try:
        return inspect_wav_bytes(Path(path).read_bytes())
    except (OSError, WavError):
        return None


def write_wav_atomic(path: Path, data: bytes, settings: Settings) -> WavInfo:
    """先写同目录临时文件，校验通过后原子替换。"""
    settings.check_writable(path, "写入 WAV")
    info = inspect_wav_bytes(data)
    path = Path(os.path.abspath(str(path)))
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(
        prefix=path.stem + "-", suffix=".part", dir=str(path.parent)
    )
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(data)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(temp_name, path)
    except Exception:
        try:
            os.unlink(temp_name)
        except OSError:
            pass
        raise
    return info
