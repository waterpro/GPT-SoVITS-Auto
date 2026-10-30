"""日志与终端输出。"""

from __future__ import annotations

import logging
import sys
from datetime import datetime
from pathlib import Path
from typing import Optional

from .naming import sanitize_project
from .settings import Settings


def _timestamp() -> str:
    return datetime.now().strftime("%Y%m%d-%H%M%S")


def setup_logger(
    settings: Settings,
    project: str,
    verbose: bool = True,
    name: str = "auto_tts",
) -> logging.Logger:
    logger = logging.getLogger(name)
    logger.setLevel(logging.DEBUG)
    logger.propagate = False
    for handler in list(logger.handlers):
        logger.removeHandler(handler)
        try:
            handler.close()
        except Exception:  # noqa: BLE001
            pass

    log_path = settings.logs_dir / ("run-%s-%s.log" % (sanitize_project(project), _timestamp()))
    settings.check_writable(log_path, "日志")
    log_path.parent.mkdir(parents=True, exist_ok=True)
    file_handler = logging.FileHandler(str(log_path), encoding="utf-8")
    file_handler.setLevel(logging.DEBUG)
    file_handler.setFormatter(
        logging.Formatter("%(asctime)s %(levelname)s %(message)s", "%Y-%m-%d %H:%M:%S")
    )
    logger.addHandler(file_handler)

    # pythonw.exe 下 sys.stdout/sys.stderr 可能是 None，此时只写文件日志。
    stream_target = sys.stdout or sys.stderr
    if stream_target is not None:
        stream = logging.StreamHandler(stream=stream_target)
        stream.setLevel(logging.INFO if verbose else logging.WARNING)
        stream.setFormatter(logging.Formatter("%(message)s"))
        logger.addHandler(stream)
    logger.autotts_log_path = str(log_path)  # type: ignore[attr-defined]
    return logger


def get_log_path(logger: logging.Logger) -> Optional[str]:
    return getattr(logger, "autotts_log_path", None)


def tail_file(path: Path, lines: int = 40) -> str:
    try:
        data = Path(path).read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return ""
    return "\n".join(data[-lines:])


def format_exception(exc: BaseException) -> str:
    return "%s: %s" % (type(exc).__name__, exc)
