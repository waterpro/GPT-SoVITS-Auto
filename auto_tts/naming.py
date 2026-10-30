"""文件名、目录名与输出路径处理。"""

from __future__ import annotations

import hashlib
import os
import re
from pathlib import Path
from typing import Iterable, Optional, Sequence

from .settings import Settings, SettingsError


_FORBIDDEN = re.compile(r'[<>:"/\\|?*\x00-\x1f\x7f]')
_SPACE = re.compile(r"\s+")
_RESERVED = {
    "con",
    "prn",
    "aux",
    "nul",
    *(f"com{i}" for i in range(1, 10)),
    *(f"lpt{i}" for i in range(1, 10)),
}


def sanitize_component(value: str, max_chars: int = 80, default: str = "_") -> str:
    """清理单个路径组件（不能包含路径分隔符）。"""
    text = str(value or "").strip()
    text = _FORBIDDEN.sub("_", text)
    text = _SPACE.sub(" ", text).strip()
    text = text.rstrip(" .")
    if text in ("", ".", ".."):
        text = default
    # Windows 保留名（含带扩展名形式）
    stem = text.split(".", 1)[0].lower()
    if stem in _RESERVED:
        text = "_" + text
    if len(text) > max_chars:
        text = text[:max_chars].rstrip(" .")
    return text or default


def sanitize_voice_dir(value: str) -> str:
    return sanitize_component(value, max_chars=60, default="未命名角色")


def sanitize_project(value: str) -> str:
    return sanitize_component(value, max_chars=80, default="未命名项目")


def sanitize_id(value: str) -> str:
    text = str(value or "").strip()
    text = re.sub(r"[^0-9A-Za-z_-]", "", text)
    if not text:
        text = "0001"
    return text[:30]


def sanitize_text(value: str, max_chars: int = 80) -> str:
    """清理文件名中的台词部分。保留中文全角标点。"""
    text = str(value or "")
    text = text.replace("\r", " ").replace("\n", " ").replace("\t", " ")
    text = _FORBIDDEN.sub("_", text)
    text = _SPACE.sub(" ", text).strip()
    text = text.strip(". ")
    if len(text) > max_chars:
        text = text[:max_chars].rstrip(". ")
    return text or "_"


def _format_parts(template: str, values: dict, fallback: Sequence[str]) -> list:
    try:
        rendered = template.format(**values)
    except (KeyError, ValueError):
        rendered = "/".join(fallback)
    parts = [p for p in re.split(r"[\\/]+", rendered) if p not in ("", ".", "..")]
    return parts or list(fallback)


def build_output_path(
    settings: Settings,
    voice_dir: str,
    project: str,
    line_id: str,
    text: str,
    filename_template: Optional[str] = None,
    layout: Optional[str] = None,
) -> Path:
    """构造并校验输出 WAV 路径。"""
    output = settings.output
    filename_template = filename_template or str(output.get("filename") or "{id}_{text}")
    layout = layout or str(output.get("layout") or "{voice_dir}/{project}")
    max_chars = int(output.get("max_text_chars", 80))
    max_path = int(output.get("max_path_chars", 230))

    voice_dir = sanitize_voice_dir(voice_dir)
    project = sanitize_project(project)
    line_id = sanitize_id(line_id)
    text_part = sanitize_text(text, max_chars)

    dir_parts = _format_parts(
        layout,
        {"voice_dir": voice_dir, "project": project},
        [voice_dir, project],
    )
    dir_parts = [sanitize_component(p, max_chars=60) for p in dir_parts]

    while True:
        filename = filename_template.format(id=line_id, text=text_part)
        filename = sanitize_component(filename, max_chars=200, default=line_id)
        if not filename.lower().endswith(".wav"):
            filename += ".wav"
        path = settings.output_root.joinpath(*dir_parts, filename)
        path = Path(os.path.abspath(str(path)))
        settings.check_writable(path, "输出路径")
        if len(str(path)) <= max_path or len(text_part) <= 4:
            return path
        text_part = text_part[: max(4, len(text_part) - 8)].rstrip(". ")


def number_path(path: Path, number: int) -> Path:
    if number <= 1:
        return path
    return path.with_name("%s_%d%s" % (path.stem, number, path.suffix))


def choose_output_path(path: Path, used: Iterable[Path]) -> Path:
    """文档内同名时稳定地追加 _2/_3。"""
    used_set = {str(Path(p).resolve()).lower() for p in used}
    if str(path.resolve()).lower() not in used_set:
        return path
    for n in range(2, 1000):
        candidate = number_path(path, n)
        if str(candidate.resolve()).lower() not in used_set:
            return candidate
    raise SettingsError("同名输出过多，无法生成唯一文件名：%s" % path)


def choose_existing_safe_path(
    path: Path,
    owned: bool,
    overwrite: bool,
) -> Path:
    """磁盘上已有文件时：自己的旧文件可覆盖；别人的文件绝不覆盖。"""
    if not path.exists():
        return path
    if owned:
        # 清单确认是本任务生成的文件，允许覆盖或重试。
        return path
    # 不属于本任务的文件绝不覆盖，即使传入 overwrite=True。
    for n in range(2, 1000):
        candidate = number_path(path, n)
        if not candidate.exists():
            return candidate
    raise SettingsError("输出文件重名过多：%s" % path)


def document_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def text_sha256(text: str) -> str:
    return hashlib.sha256(str(text).encode("utf-8")).hexdigest()


def state_path(settings: Settings, project: str, doc_sha256: str) -> Path:
    safe = sanitize_project(project)
    return settings.state_dir / ("%s-%s.json" % (safe, doc_sha256[:8]))
