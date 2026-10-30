# -*- coding: utf-8 -*-
"""Windows 文件拖放（不依赖第三方 TkDND 包）。

Tk 本身不接收 ``WM_DROPFILES``。这里用 ctypes 给 Tk 顶层窗口挂一个
WndProc 子类：只截获 ``WM_DROPFILES``，其余消息原样转给 Tk 的窗口过程。
拖放路径通过 ``root.after`` 回到 Tk 主线程，避免在窗口过程里操作控件。

非 Windows 平台、或子类化失败时返回 ``None``；界面仍可用“浏览…”选择文件。
"""

from __future__ import annotations

import ctypes
import sys
from ctypes import wintypes
from pathlib import Path
from typing import Callable, List, Optional

WM_DROPFILES = 0x0233
GWLP_WNDPROC = -4
GA_ROOT = 2
LRESULT = ctypes.c_ssize_t
WNDPROC = ctypes.WINFUNCTYPE(
    LRESULT, wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM
)


class FileDropTarget:
    def __init__(self, root, callback: Callable[[List[Path]], None]) -> None:
        self.root = root
        self.callback = callback
        self.hwnd = 0
        self._old_proc = None
        self._proc = None
        self._enabled = False
        if sys.platform != "win32":
            return
        try:
            self._enable()
        except Exception:
            self.disable()

    def _enable(self) -> None:
        user32 = ctypes.WinDLL("user32", use_last_error=True)
        shell32 = ctypes.WinDLL("shell32", use_last_error=True)

        user32.GetAncestor.argtypes = [wintypes.HWND, wintypes.UINT]
        user32.GetAncestor.restype = wintypes.HWND
        hwnd = user32.GetAncestor(self.root.winfo_id(), GA_ROOT) or self.root.winfo_id()
        self.hwnd = hwnd

        self._proc = WNDPROC(self._wndproc)
        set_long = getattr(user32, "SetWindowLongPtrW", None) or user32.SetWindowLongW
        set_long.argtypes = [wintypes.HWND, ctypes.c_int, ctypes.c_void_p]
        set_long.restype = ctypes.c_void_p
        old = set_long(hwnd, GWLP_WNDPROC, ctypes.cast(self._proc, ctypes.c_void_p))
        if not old:
            error = ctypes.get_last_error()
            raise OSError("SetWindowLongPtrW 失败：%s" % error)
        self._old_proc = old

        user32.CallWindowProcW.argtypes = [
            ctypes.c_void_p,
            wintypes.HWND,
            wintypes.UINT,
            wintypes.WPARAM,
            wintypes.LPARAM,
        ]
        user32.CallWindowProcW.restype = LRESULT
        self._call_proc = user32.CallWindowProcW

        shell32.DragAcceptFiles.argtypes = [wintypes.HWND, wintypes.BOOL]
        shell32.DragAcceptFiles.restype = None
        shell32.DragAcceptFiles(hwnd, True)
        shell32.DragQueryFileW.argtypes = [
            wintypes.HANDLE,
            wintypes.UINT,
            wintypes.LPWSTR,
            wintypes.UINT,
        ]
        shell32.DragQueryFileW.restype = wintypes.UINT
        shell32.DragFinish.argtypes = [wintypes.HANDLE]
        shell32.DragFinish.restype = None
        self._drag_query = shell32.DragQueryFileW
        self._drag_finish = shell32.DragFinish
        self._enabled = True

    def _wndproc(self, hwnd, msg, wparam, lparam):  # noqa: ANN001
        if msg == WM_DROPFILES:
            try:
                self._read_drop(wparam)
            except Exception:
                pass
            return 0
        if self._old_proc:
            return self._call_proc(self._old_proc, hwnd, msg, wparam, lparam)
        return 0

    def _read_drop(self, hdrop_value) -> None:
        hdrop = wintypes.HANDLE(hdrop_value)
        count = int(self._drag_query(hdrop, 0xFFFFFFFF, None, 0))
        paths: List[Path] = []
        for index in range(count):
            length = int(self._drag_query(hdrop, index, None, 0))
            buffer = ctypes.create_unicode_buffer(length + 1)
            self._drag_query(hdrop, index, buffer, length + 1)
            if buffer.value:
                paths.append(Path(buffer.value))
        self._drag_finish(hdrop)
        if paths:
            # 窗口过程里不能直接碰 Tk；排到主线程空闲时执行。
            self.root.after(0, self._deliver, paths)

    def _deliver(self, paths: List[Path]) -> None:
        try:
            self.callback(paths)
        except Exception:
            pass

    def disable(self) -> None:
        if not self._enabled:
            return
        try:
            shell32 = ctypes.WinDLL("shell32", use_last_error=True)
            shell32.DragAcceptFiles(self.hwnd, False)
        except Exception:
            pass
        try:
            if self.hwnd and self._old_proc:
                user32 = ctypes.WinDLL("user32", use_last_error=True)
                set_long = getattr(user32, "SetWindowLongPtrW", None) or user32.SetWindowLongW
                set_long.argtypes = [wintypes.HWND, ctypes.c_int, ctypes.c_void_p]
                set_long.restype = ctypes.c_void_p
                set_long(self.hwnd, GWLP_WNDPROC, self._old_proc)
        except Exception:
            pass
        self._enabled = False


def enable_file_drop(root, callback: Callable[[List[Path]], None]) -> Optional[FileDropTarget]:
    target = FileDropTarget(root, callback)
    return target if target._enabled else None
