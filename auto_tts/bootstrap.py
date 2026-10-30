# -*- coding: utf-8 -*-
"""工作区初始化：默认设置、音色映射与示例文档。"""
from __future__ import annotations

import copy
import os
import shutil
import time
from pathlib import Path
from typing import Dict, Iterable, List, Optional

import yaml

from .settings import DEFAULT_SETTINGS, WORKSPACE_ROOT


DEFAULT_VOICES: Dict[str, object] = {
    "voices": {
        "银狼": {
            "preset": "银狼中立",
            "output_dir": "银狼",
            "text_lang": "zh",
            "aliases": ["银狼中立", "银狼（中立）"],
        },
        "银狼（开心）": {
            "preset": "银狼开心",
            "output_dir": "银狼",
            "text_lang": "zh",
            "aliases": ["银狼开心"],
        },
        "爱音": {
            "preset": "爱音1",
            "output_dir": "爱音",
            "text_lang": "zh",
            "aliases": ["千早爱音", "Anon", "爱音1"],
        },
        "灯": {
            "preset": "灯1",
            "output_dir": "高松灯",
            "text_lang": "zh",
            "aliases": ["高松灯", "Tomorin", "偷摸零", "灯1"],
        },
        "灯2": {
            "preset": "灯2",
            "output_dir": "高松灯",
            "text_lang": "zh",
            "aliases": ["高松灯2"],
        },
        "祥子": {
            "preset": "白祥1",
            "output_dir": "祥子",
            "text_lang": "zh",
            "aliases": ["白祥", "丰川祥子", "白祥1"],
        },
        "芙宁娜": {
            "preset": "芙宁娜（骄傲激动）",
            "output_dir": "芙宁娜",
            "text_lang": "zh",
            "aliases": ["Funina", "芙宁娜（骄傲激动）"],
        },
    }
}


EXAMPLE_MD = """---
project: 示例项目
---

## 银狼
银狼：今天天气不错，要不要一起去星穹列车？
银狼（开心）：虽然我们的立场不同，但在这样一场宇宙级的盛会上，就先放下成见吧。

## 爱音
[0001] 爱音：这怎么活得下去啊啊啊啊。

## 高松灯
[04:25] 灯：想和大家一起，一直走下去。
"""


def _write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as fh:
        fh.write(text)


def _portable_path(value: Path, workspace: Path) -> str:
    """尽量写成相对工作区的路径，换机器/换目录时仍然可用。"""
    resolved = Path(os.path.abspath(str(Path(value).expanduser())))
    try:
        rel = os.path.relpath(str(resolved), str(workspace))
    except ValueError:
        return str(resolved)
    return rel or "."


def update_settings_file(
    workspace: Path = WORKSPACE_ROOT,
    install_root: Optional[Path] = None,
    output_root: Optional[Path] = None,
    extra_model_roots: Optional[Iterable[Path]] = None,
    backup: bool = True,
) -> Path:
    """更新 config/settings.yaml；只改传入的字段，其他配置保留。"""
    workspace = Path(os.path.abspath(str(workspace)))
    path = workspace / "config" / "settings.yaml"
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        try:
            with path.open("r", encoding="utf-8-sig") as fh:
                data = yaml.safe_load(fh) or {}
        except Exception as exc:  # noqa: BLE001
            raise ValueError("读取设置失败 %s：%s" % (path, exc)) from exc
        if not isinstance(data, dict):
            raise ValueError("设置文件顶层必须是 YAML 映射：%s" % path)
    else:
        data = copy.deepcopy(DEFAULT_SETTINGS)

    if install_root is not None:
        data["install_root"] = _portable_path(Path(install_root), workspace)
    if output_root is not None:
        data.setdefault("output", {})
        data["output"]["root"] = _portable_path(Path(output_root), workspace)
    if extra_model_roots is not None:
        data["extra_model_roots"] = [
            _portable_path(Path(p), workspace) for p in extra_model_roots
        ]

    if backup and path.exists():
        backup_dir = path.parent / "backups"
        backup_dir.mkdir(parents=True, exist_ok=True)
        stamp = time.strftime("%Y%m%d-%H%M%S")
        target = backup_dir / ("settings-%s.yaml" % stamp)
        suffix = 1
        while target.exists():
            suffix += 1
            target = backup_dir / ("settings-%s-%d.yaml" % (stamp, suffix))
        shutil.copy2(str(path), str(target))

    _write_text(
        path,
        yaml.safe_dump(data, allow_unicode=True, sort_keys=False),
    )
    return path


def ensure_initial_files(
    workspace: Path = WORKSPACE_ROOT,
    force: bool = False,
) -> List[Path]:
    """创建目录与缺失的默认文件；force 会重写音色和示例（不重写设置）。"""
    written: List[Path] = []
    for name in ("config", "voices", "examples", "state", "logs", "cache", "tmp"):
        (workspace / name).mkdir(parents=True, exist_ok=True)
    (workspace / "cache" / "pycache").mkdir(parents=True, exist_ok=True)

    settings_path = workspace / "config" / "settings.yaml"
    if not settings_path.exists():
        _write_text(
            settings_path,
            yaml.safe_dump(DEFAULT_SETTINGS, allow_unicode=True, sort_keys=False),
        )
        written.append(settings_path)

    voices_path = workspace / "voices" / "voices.yaml"
    if force or not voices_path.exists():
        _write_text(
            voices_path,
            yaml.safe_dump(DEFAULT_VOICES, allow_unicode=True, sort_keys=False),
        )
        written.append(voices_path)

    example_path = workspace / "examples" / "example.md"
    if force or not example_path.exists():
        _write_text(example_path, EXAMPLE_MD)
        written.append(example_path)
    return written
