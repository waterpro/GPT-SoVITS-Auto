"""断点续跑清单与运行报告。"""

from __future__ import annotations

import csv
import json
import os
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from .settings import Settings
from .wavcheck import inspect_wav_file


class ManifestError(Exception):
    """清单读写错误。"""


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _key(path: Path) -> str:
    return os.path.normcase(os.path.abspath(str(path)))


class Manifest:
    VERSION = 1

    def __init__(
        self,
        settings: Settings,
        path: Path,
        doc_path: Path,
        project: str,
        doc_sha256: str,
    ):
        self.settings = settings
        self.path = settings.check_writable(path, "运行清单")
        self.data: Dict[str, Any] = {
            "version": self.VERSION,
            "doc": str(Path(doc_path).resolve()),
            "doc_sha256": doc_sha256,
            "project": project,
            "created": _now(),
            "updated": _now(),
            "entries": {},
        }

    @classmethod
    def load_or_create(
        cls,
        settings: Settings,
        path: Path,
        doc_path: Path,
        project: str,
        doc_sha256: str,
    ) -> "Manifest":
        manifest = cls(settings, path, doc_path, project, doc_sha256)
        if not manifest.path.exists():
            return manifest
        try:
            with manifest.path.open("r", encoding="utf-8-sig") as fh:
                loaded = json.load(fh)
            if not isinstance(loaded, dict) or not isinstance(loaded.get("entries"), dict):
                raise ValueError("缺少 entries 映射")
            if int(loaded.get("version", 0)) != cls.VERSION:
                raise ValueError("清单版本不支持：%s" % loaded.get("version"))
            manifest.data = loaded
            manifest.data["doc"] = str(Path(doc_path).resolve())
            manifest.data["doc_sha256"] = doc_sha256
            manifest.data["project"] = project
            manifest.data["updated"] = _now()
        except Exception as exc:  # noqa: BLE001
            backup = manifest.path.with_name(
                manifest.path.name + ".corrupt-" + datetime.now().strftime("%Y%m%d-%H%M%S")
            )
            try:
                os.replace(str(manifest.path), str(backup))
            except OSError:
                pass
            manifest.data["warning"] = "旧清单损坏，已备份到 %s：%s" % (backup, exc)
        return manifest

    def entry(self, output: Path) -> Optional[Dict[str, Any]]:
        entry = self.data["entries"].get(_key(output))
        return entry if isinstance(entry, dict) else None

    def is_done(self, output: Path, text_hash: str) -> bool:
        entry = self.entry(output)
        if not entry or entry.get("status") != "done":
            return False
        if entry.get("text_hash") != text_hash:
            return False
        if not Path(output).exists():
            return False
        return inspect_wav_file(Path(output)) is not None

    def start(self, output: Path, info: Dict[str, Any]) -> None:
        self.data["entries"][_key(output)] = {
            "status": "running",
            "output": str(Path(output).resolve()),
            "started_at": _now(),
            "finished_at": "",
            "duration": 0.0,
            "error": "",
            **info,
        }
        self.data["updated"] = _now()
        self.save()

    def done(self, output: Path, duration: float) -> None:
        entry = self.data["entries"].get(_key(output))
        if entry is None:
            raise ManifestError("清单里没有条目：%s" % output)
        entry["status"] = "done"
        entry["duration"] = round(float(duration), 3)
        entry["finished_at"] = _now()
        entry["error"] = ""
        self.data["updated"] = _now()
        self.save()

    def failed(self, output: Path, error: str) -> None:
        entry = self.data["entries"].get(_key(output))
        if entry is None:
            raise ManifestError("清单里没有条目：%s" % output)
        entry["status"] = "failed"
        entry["finished_at"] = _now()
        entry["error"] = str(error)[:2000]
        self.data["updated"] = _now()
        self.save()

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd, temp_name = tempfile.mkstemp(
            prefix=self.path.stem + "-", suffix=".tmp", dir=str(self.path.parent)
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as fh:
                json.dump(self.data, fh, ensure_ascii=False, indent=2)
                fh.write("\n")
                fh.flush()
                os.fsync(fh.fileno())
            os.replace(temp_name, self.path)
        except Exception:
            try:
                os.unlink(temp_name)
            except OSError:
                pass
            raise

    def counts(self) -> Dict[str, int]:
        result = {"done": 0, "failed": 0, "running": 0, "skipped": 0}
        for entry in self.data["entries"].values():
            status = str(entry.get("status") or "unknown")
            result[status] = result.get(status, 0) + 1
        return result


_REPORT_FIELDS = [
    "index",
    "line_no",
    "id",
    "speaker",
    "voice",
    "text",
    "output",
    "duration",
    "status",
    "error",
    "started_at",
    "finished_at",
]


def write_report_csv(
    settings: Settings,
    path: Path,
    entries: Dict[str, Dict[str, Any]],
) -> Path:
    path = settings.check_writable(path, "运行报告")
    path.parent.mkdir(parents=True, exist_ok=True)
    rows: List[Dict[str, Any]] = []
    for entry in entries.values():
        row = {name: entry.get(name, "") for name in _REPORT_FIELDS}
        rows.append(row)
    rows.sort(key=lambda row: (int(row.get("index") or 0), str(row.get("output") or "")))
    with path.open("w", encoding="utf-8-sig", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=_REPORT_FIELDS, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow(row)
    return path
