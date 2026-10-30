# -*- coding: utf-8 -*-
"""Tkinter 界面的统一配色、字体和常用小部件。"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path
from typing import Optional

import tkinter as tk
from tkinter import ttk

BG = "#eef2f9"
CARD = "#ffffff"
TEXT = "#0f172a"
MUTED = "#64748b"
BORDER = "#d8e0ee"
ACCENT = "#2563eb"
ACCENT_DARK = "#1d4ed8"
SUCCESS = "#15803d"
WARN = "#b45309"
DANGER = "#b91c1c"
LOG_BG = "#0b1220"
LOG_FG = "#dbeafe"

FONT = "Microsoft YaHei UI"
MONO = "Consolas"


def apply_theme(root: tk.Tk) -> ttk.Style:
    root.configure(bg=BG)
    root.option_add("*Font", (FONT, 10))
    root.option_add("*TCombobox*Listbox.font", (FONT, 10))

    style = ttk.Style(root)
    try:
        style.theme_use("clam")
    except tk.TclError:
        pass

    style.configure(".", background=BG, foreground=TEXT, font=(FONT, 10))
    style.configure("TFrame", background=BG)
    style.configure("Card.TFrame", background=CARD)
    style.configure("Header.TFrame", background=CARD)
    style.configure("TLabel", background=BG, foreground=TEXT)
    style.configure("Card.TLabel", background=CARD, foreground=TEXT)
    style.configure("Muted.TLabel", background=CARD, foreground=MUTED, font=(FONT, 9))
    style.configure("Hint.TLabel", background=BG, foreground=MUTED, font=(FONT, 9))
    style.configure("CardHint.TLabel", background=CARD, foreground=MUTED, font=(FONT, 9))
    style.configure("Title.TLabel", background=CARD, foreground=TEXT, font=(FONT, 20, "bold"))
    style.configure("Subtitle.TLabel", background=CARD, foreground=MUTED, font=(FONT, 10))
    style.configure("Section.TLabel", background=CARD, foreground=TEXT, font=(FONT, 12, "bold"))
    style.configure("Metric.TLabel", background=CARD, foreground=TEXT, font=(FONT, 13, "bold"))
    style.configure("Ok.TLabel", background=CARD, foreground=SUCCESS, font=(FONT, 9, "bold"))
    style.configure("Warn.TLabel", background=CARD, foreground=WARN, font=(FONT, 9, "bold"))
    style.configure("Error.TLabel", background=CARD, foreground=DANGER, font=(FONT, 9, "bold"))
    style.configure("Log.TLabel", background=BG, foreground=MUTED, font=(FONT, 9))

    style.configure("TButton", padding=(12, 7), font=(FONT, 10))
    style.configure("Small.TButton", padding=(8, 4), font=(FONT, 9))
    style.configure("Primary.TButton", padding=(18, 10), font=(FONT, 11, "bold"))
    style.configure("Danger.TButton", padding=(10, 6), font=(FONT, 9))
    style.map(
        "Primary.TButton",
        background=[("active", ACCENT_DARK), ("!disabled", ACCENT)],
        foreground=[("!disabled", "#ffffff"), ("disabled", "#dbeafe")],
    )
    style.map(
        "Danger.TButton",
        background=[("active", "#991b1b"), ("!disabled", DANGER)],
        foreground=[("!disabled", "#ffffff")],
    )
    style.configure("TNotebook", background=BG, borderwidth=0)
    style.configure(
        "TNotebook.Tab",
        background="#e3eaf6",
        foreground=MUTED,
        padding=(20, 10),
        font=(FONT, 10, "bold"),
    )
    style.map(
        "TNotebook.Tab",
        background=[("selected", CARD)],
        foreground=[("selected", ACCENT)],
    )

    style.configure(
        "Treeview",
        background=CARD,
        fieldbackground=CARD,
        foreground=TEXT,
        rowheight=30,
        borderwidth=0,
        font=(FONT, 9),
    )
    style.configure(
        "Treeview.Heading",
        background="#f1f5f9",
        foreground=MUTED,
        relief="flat",
        font=(FONT, 9, "bold"),
        padding=(8, 7),
    )
    style.map("Treeview", background=[("selected", "#dbeafe")], foreground=[("selected", TEXT)])
    style.configure("TEntry", padding=6)
    style.configure("TCombobox", padding=5)
    style.configure("TSpinbox", padding=5)
    style.configure(
        "Horizontal.TProgressbar",
        troughcolor="#e2e8f0",
        background=ACCENT,
        lightcolor=ACCENT,
        darkcolor=ACCENT,
        thickness=10,
    )
    style.configure("TLabelframe", background=CARD, bordercolor=BORDER, relief="solid")
    style.configure("TLabelframe.Label", background=CARD, foreground=TEXT, font=(FONT, 10, "bold"))
    return style


def make_card(parent: tk.Misc, padding: int = 16) -> tk.Frame:
    frame = tk.Frame(
        parent,
        bg=CARD,
        bd=0,
        highlightbackground=BORDER,
        highlightcolor=BORDER,
        highlightthickness=1,
        padx=padding,
        pady=padding,
    )
    return frame


def section_header(parent: tk.Misc, title: str, hint: Optional[str] = None) -> tk.Frame:
    frame = tk.Frame(parent, bg=CARD)
    tk.Label(
        frame,
        text=title,
        bg=CARD,
        fg=TEXT,
        font=(FONT, 12, "bold"),
        anchor="w",
    ).pack(side="left")
    if hint:
        tk.Label(
            frame,
            text=hint,
            bg=CARD,
            fg=MUTED,
            font=(FONT, 9),
            anchor="w",
        ).pack(side="left", padx=(10, 0))
    return frame


def center_window(window: tk.Misc, width: int, height: int) -> None:
    window.update_idletasks()
    screen_w = window.winfo_screenwidth()
    screen_h = window.winfo_screenheight()
    # 小屏幕/远程桌面下不要超出工作区。
    width = max(480, min(width, screen_w - 60))
    height = max(360, min(height, screen_h - 100))
    x = max(0, (screen_w - width) // 2)
    y = max(0, (screen_h - height) // 2 - 20)
    window.geometry("%dx%d+%d+%d" % (width, height, x, y))


def open_path(path: Path) -> None:
    value = str(path)
    if os.name == "nt":
        os.startfile(value)  # type: ignore[attr-defined]
        return
    subprocess.Popen(["xdg-open", value])


def set_dpi_awareness() -> None:
    if os.name != "nt":
        return
    try:
        import ctypes

        ctypes.windll.shcore.SetProcessDpiAwareness(1)  # type: ignore[attr-defined]
    except Exception:  # noqa: BLE001
        pass


def readable_path(path: Path, limit: int = 80) -> str:
    text = str(path)
    if len(text) <= limit:
        return text
    keep = max(10, limit - 3)
    head = keep // 2
    return text[:head] + "..." + text[-(keep - head):]
