# -*- coding: utf-8 -*-
"""Gptsovit Auto GUI 入口。

双击 run_ui.bat 时由 pythonw.exe 启动；没有控制台，所以启动异常会写入
logs/ui-crash.log 并尽量弹出一个错误对话框。
"""

from __future__ import annotations

import sys
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _write_crash(detail: str) -> Path:
    logs = ROOT / "logs"
    try:
        logs.mkdir(parents=True, exist_ok=True)
        target = logs / "ui-crash.log"
        with target.open("a", encoding="utf-8") as handle:
            handle.write("\n" + detail + "\n")
        return target
    except Exception:  # noqa: BLE001
        return logs / "ui-crash.log"


def _show_crash(detail: str) -> None:
    try:
        import tkinter as tk
        from tkinter import messagebox

        root = tk.Tk()
        root.withdraw()
        messagebox.showerror(
            "Gptsovit Auto 启动失败",
            "程序没有成功启动。\n\n"
            "错误信息已写入 logs\\ui-crash.log。\n\n"
            + detail[-1500:],
        )
        root.destroy()
    except Exception:  # noqa: BLE001
        pass


def main() -> int:
    try:
        from auto_tts.ui import App

        app = App()
        app.mainloop()
        return 0
    except Exception:  # noqa: BLE001
        detail = traceback.format_exc()
        _write_crash(detail)
        _show_crash(detail)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
