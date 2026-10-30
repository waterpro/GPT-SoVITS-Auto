# -*- coding: utf-8 -*-
"""Gptsovit Auto 的 Tkinter 图形界面。

界面只负责收集参数和展示进度；真正的解析、切模型、调 API、写文件仍然走
runner.py 和 voice_editor.py，命令行与 GUI 行为一致。
"""

from __future__ import annotations

import logging
import os
import queue
import threading
import traceback
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk
from typing import Any, Dict, List, Optional

from . import __version__
from .bootstrap import WORKSPACE_ROOT, ensure_initial_files, update_settings_file
from .client import TTSClient
from .log import get_log_path, setup_logger
from .naming import sanitize_project
from .runner import Plan, RunOptions, RunnerError, build_plan, run
from .server import gpu_free_mb
from .settings import Settings, load_settings
from .ui_theme import (
    BG,
    CARD,
    DANGER,
    FONT,
    LOG_BG,
    LOG_FG,
    MUTED,
    SUCCESS,
    TEXT,
    apply_theme,
    make_card,
    open_path,
    section_header,
    set_dpi_awareness,
)
from .ui_voices import VoicesTab
from .voice_editor import short_path
from .voices import VoiceRegistry, load_registry


class _QueueHandler(logging.Handler):
    """把日志送到 Tk 主线程，不能在工作线程里直接操作控件。"""

    def __init__(self, events: "queue.Queue[Dict[str, Any]]") -> None:
        super().__init__()
        self.events = events

    def emit(self, record: logging.LogRecord) -> None:
        try:
            message = self.format(record)
        except Exception:  # noqa: BLE001
            message = record.getMessage()
        self.events.put({"type": "log", "message": message})


def _entry_path(value: str) -> Path:
    path = Path(str(value).strip().strip('"')).expanduser()
    if not path.is_absolute():
        path = WORKSPACE_ROOT / path
    return path


class GenerateTab(ttk.Frame):
    def __init__(self, parent: tk.Misc, app: "App") -> None:
        super().__init__(parent)
        self.app = app
        self.settings: Optional[Settings] = None
        self.registry: Optional[VoiceRegistry] = None
        self.plan: Optional[Plan] = None
        self._plan_key: Optional[tuple] = None
        self._row_numbers: Dict[int, int] = {}
        self._build()

    def _build(self) -> None:
        self.columnconfigure(0, weight=1)
        self.rowconfigure(2, weight=3, minsize=250)
        self.rowconfigure(3, weight=2, minsize=120)

        source = make_card(self, padding=14)
        source.grid(row=0, column=0, sticky="ew", padx=4, pady=(4, 8))
        source.columnconfigure(1, weight=1)

        head = section_header(source, "1. 选择台本文档", "支持 .md / .txt；一行一句台词。")
        head.grid(row=0, column=0, columnspan=3, sticky="ew", pady=(0, 8))

        ttk.Label(source, text="文档", style="Card.TLabel").grid(row=1, column=0, sticky="w", pady=4)
        self.doc_var = tk.StringVar()
        self.doc_entry = ttk.Entry(source, textvariable=self.doc_var)
        self.doc_entry.grid(row=1, column=1, sticky="ew", pady=4)
        self.doc_entry.bind("<Return>", lambda _event: self.preview(silent=False))
        self.doc_entry.bind("<FocusOut>", lambda _event: self._maybe_preview())
        doc_buttons = tk.Frame(source, bg=CARD)
        doc_buttons.grid(row=1, column=2, sticky="e", padx=(8, 0))
        ttk.Button(doc_buttons, text="浏览…", style="Small.TButton", command=self.browse_doc).pack(side="left")
        ttk.Button(doc_buttons, text="载入示例", style="Small.TButton", command=self.load_example).pack(
            side="left", padx=(6, 0)
        )

        ttk.Label(source, text="项目名", style="Card.TLabel").grid(row=2, column=0, sticky="w", pady=4)
        self.project_var = tk.StringVar()
        ttk.Entry(source, textvariable=self.project_var).grid(row=2, column=1, sticky="ew", pady=4)
        ttk.Label(source, text="留空使用文档文件名；输出到“角色/项目”。", style="CardHint.TLabel").grid(
            row=2, column=2, sticky="w", padx=(8, 0)
        )

        ttk.Label(source, text="输出目录", style="Card.TLabel").grid(row=3, column=0, sticky="w", pady=4)
        self.out_var = tk.StringVar()
        ttk.Entry(source, textvariable=self.out_var).grid(row=3, column=1, sticky="ew", pady=4)
        out_buttons = tk.Frame(source, bg=CARD)
        out_buttons.grid(row=3, column=2, sticky="e", padx=(8, 0))
        ttk.Button(out_buttons, text="浏览…", style="Small.TButton", command=self.browse_output).pack(side="left")
        ttk.Button(out_buttons, text="打开", style="Small.TButton", command=self.open_output).pack(
            side="left", padx=(6, 0)
        )

        actions = make_card(self, padding=12)
        actions.grid(row=1, column=0, sticky="ew", padx=4, pady=(0, 8))
        actions.columnconfigure(0, weight=1)

        options = tk.Frame(actions, bg=CARD)
        options.grid(row=0, column=0, sticky="w")
        self.resume_var = tk.BooleanVar(value=True)
        self.overwrite_var = tk.BooleanVar(value=False)
        self.failfast_var = tk.BooleanVar(value=False)
        tk.Checkbutton(
            options,
            text="跳过已完成",
            variable=self.resume_var,
            bg=CARD,
            fg=TEXT,
            activebackground=CARD,
            activeforeground=TEXT,
            selectcolor="#ffffff",
            font=(FONT, 9),
        ).pack(side="left")
        tk.Checkbutton(
            options,
            text="覆盖重生成",
            variable=self.overwrite_var,
            bg=CARD,
            fg=TEXT,
            activebackground=CARD,
            activeforeground=TEXT,
            selectcolor="#ffffff",
            font=(FONT, 9),
        ).pack(side="left", padx=(14, 0))
        tk.Checkbutton(
            options,
            text="遇错停止",
            variable=self.failfast_var,
            bg=CARD,
            fg=TEXT,
            activebackground=CARD,
            activeforeground=TEXT,
            selectcolor="#ffffff",
            font=(FONT, 9),
        ).pack(side="left", padx=(14, 0))

        main_buttons = tk.Frame(actions, bg=CARD)
        main_buttons.grid(row=0, column=1, sticky="e")
        self.stop_btn = ttk.Button(main_buttons, text="停止", style="Danger.TButton", command=self.stop)
        self.stop_btn.pack(side="right", padx=(8, 0))
        self.start_btn = ttk.Button(
            main_buttons, text="开始生成", style="Primary.TButton", command=self.start
        )
        self.start_btn.pack(side="right")

        progress_row = tk.Frame(actions, bg=CARD)
        progress_row.grid(row=1, column=0, columnspan=2, sticky="ew", pady=(10, 0))
        progress_row.columnconfigure(0, weight=1)
        self.progress = ttk.Progressbar(progress_row, mode="determinate", maximum=1, value=0)
        self.progress.grid(row=0, column=0, sticky="ew")
        self.count_var = tk.StringVar(value="等待解析")
        tk.Label(
            progress_row,
            textvariable=self.count_var,
            bg=CARD,
            fg=MUTED,
            font=(FONT, 9),
            width=18,
            anchor="e",
        ).grid(row=0, column=1, sticky="e", padx=(10, 0))

        preview = make_card(self, padding=10)
        preview.grid(row=2, column=0, sticky="nsew", padx=4, pady=(0, 8))
        preview.columnconfigure(0, weight=1)
        preview.rowconfigure(1, weight=1)
        preview_head = tk.Frame(preview, bg=CARD)
        preview_head.grid(row=0, column=0, columnspan=2, sticky="ew", pady=(0, 6))
        preview_head.columnconfigure(1, weight=1)
        tk.Label(preview_head, text="2. 预览台词", bg=CARD, fg=TEXT, font=(FONT, 11, "bold")).grid(
            row=0, column=0, sticky="w"
        )
        self.parse_label = tk.StringVar(value="选择文档后会自动解析；也可以点“解析预览”。")
        tk.Label(
            preview_head,
            textvariable=self.parse_label,
            bg=CARD,
            fg=MUTED,
            font=(FONT, 9),
            anchor="w",
        ).grid(row=0, column=1, sticky="w", padx=(12, 0))
        ttk.Button(
            preview_head, text="解析预览", style="Small.TButton", command=self.preview
        ).grid(row=0, column=2, sticky="e")

        columns = ("number", "line", "id", "speaker", "voice", "text", "status", "output")
        self.tree = ttk.Treeview(preview, columns=columns, show="headings", selectmode="browse", height=4)
        settings_map = {
            "number": ("#", 45),
            "line": ("行", 50),
            "id": ("ID", 70),
            "speaker": ("角色", 90),
            "voice": ("音色", 100),
            "text": ("台词", 310),
            "status": ("状态", 80),
            "output": ("输出文件", 260),
        }
        for column, (title, width) in settings_map.items():
            self.tree.heading(column, text=title)
            self.tree.column(column, width=width, minwidth=40, anchor="w", stretch=(column in ("text", "output")))
        self.tree.grid(row=1, column=0, sticky="nsew")
        yscroll = ttk.Scrollbar(preview, orient="vertical", command=self.tree.yview)
        yscroll.grid(row=1, column=1, sticky="ns")
        xscroll = ttk.Scrollbar(preview, orient="horizontal", command=self.tree.xview)
        xscroll.grid(row=2, column=0, sticky="ew")
        self.tree.configure(yscrollcommand=yscroll.set, xscrollcommand=xscroll.set)
        self.tree.tag_configure("run", foreground="#2563eb")
        self.tree.tag_configure("done", foreground=SUCCESS)
        self.tree.tag_configure("error", foreground=DANGER)

        log_card = make_card(self, padding=10)
        log_card.grid(row=3, column=0, sticky="nsew", padx=4, pady=(0, 4))
        log_card.columnconfigure(0, weight=1)
        log_card.rowconfigure(1, weight=1)
        log_head = tk.Frame(log_card, bg=CARD)
        log_head.grid(row=0, column=0, sticky="ew", pady=(0, 5))
        log_head.columnconfigure(0, weight=1)
        tk.Label(log_head, text="运行日志", bg=CARD, fg=TEXT, font=(FONT, 10, "bold")).grid(
            row=0, column=0, sticky="w"
        )
        self.log_path_label = tk.StringVar(value="")
        tk.Label(
            log_head,
            textvariable=self.log_path_label,
            bg=CARD,
            fg=MUTED,
            font=(FONT, 8),
            anchor="e",
        ).grid(row=0, column=1, sticky="e")
        self.log_text = tk.Text(
            log_card,
            height=5,
            wrap="none",
            bg=LOG_BG,
            fg=LOG_FG,
            insertbackground=LOG_FG,
            relief="flat",
            font=("Consolas", 9),
            padx=8,
            pady=6,
            state="disabled",
        )
        self.log_text.grid(row=1, column=0, sticky="nsew")
        log_scroll = ttk.Scrollbar(log_card, orient="vertical", command=self.log_text.yview)
        log_scroll.grid(row=1, column=1, sticky="ns")
        self.log_text.configure(yscrollcommand=log_scroll.set)

    # ------------------------------------------------------------------ 环境
    def set_context(self, settings: Optional[Settings], registry: Optional[VoiceRegistry]) -> None:
        self.settings = settings
        self.registry = registry
        if settings is not None:
            if not self.out_var.get().strip():
                self.out_var.set(str(settings.output_root))
            self.start_btn.configure(state="normal")
        else:
            self.start_btn.configure(state="disabled")
        self.stop_btn.configure(state="disabled")

    # ------------------------------------------------------------------ 文档
    def browse_doc(self) -> None:
        path = filedialog.askopenfilename(
            title="选择台本文档",
            filetypes=[("台本文档", "*.md *.txt *.markdown"), ("所有文件", "*.*")],
            parent=self,
        )
        if path:
            self.doc_var.set(path)
            self.preview()

    def load_example(self) -> None:
        path = WORKSPACE_ROOT / "examples" / "example.md"
        if not path.exists():
            try:
                ensure_initial_files()
            except Exception as exc:  # noqa: BLE001
                messagebox.showerror("载入示例失败", str(exc), parent=self)
                return
        if not path.exists():
            messagebox.showwarning("没有示例", "找不到 examples/example.md。", parent=self)
            return
        self.doc_var.set(str(path))
        self.preview()

    def browse_output(self) -> None:
        path = filedialog.askdirectory(title="选择输出根目录", parent=self)
        if path:
            self.out_var.set(path)

    def _doc_path(self) -> Path:
        return _entry_path(self.doc_var.get())

    def _out_path(self) -> Optional[Path]:
        raw = self.out_var.get().strip()
        return _entry_path(raw) if raw else None

    def _plan_key_for(self, doc: Path, project: str, out: Optional[Path]) -> tuple:
        try:
            stat = doc.stat()
            return (str(doc.resolve()), stat.st_mtime_ns, stat.st_size, project, str(out or ""))
        except OSError:
            return (str(doc), 0, 0, project, str(out or ""))

    def _maybe_preview(self) -> None:
        raw = self.doc_var.get().strip()
        if not raw:
            return
        doc = self._doc_path()
        if not doc.exists():
            return
        key = self._plan_key_for(doc, self.project_var.get().strip(), self._out_path())
        if key != self._plan_key:
            self.preview(silent=True)

    # ------------------------------------------------------------------ 解析
    def preview(self, silent: bool = False) -> bool:
        if self.settings is None or self.registry is None:
            if not silent:
                messagebox.showwarning("环境未就绪", "请先在“设置”页配置 GPT-SoVITS 安装目录。", parent=self)
            return False
        raw = self.doc_var.get().strip()
        if not raw:
            if not silent:
                messagebox.showwarning("缺少文档", "请先选择 .md 或 .txt 台本文档。", parent=self)
            return False
        doc = self._doc_path()
        if not doc.exists():
            if not silent:
                messagebox.showerror("文档不存在", "找不到文档：\n%s" % doc, parent=self)
            self.parse_label.set("文档不存在")
            return False
        project = self.project_var.get().strip()
        out = self._out_path()
        options = RunOptions(
            doc=doc,
            project=project or None,
            out_root=out,
            dry_run=True,
            start=1,
            order="voice",
        )
        try:
            plan = build_plan(self.settings, self.registry, options)
        except RunnerError as exc:
            if not silent:
                messagebox.showerror("解析失败", str(exc), parent=self)
            self.parse_label.set("解析失败")
            self._clear_plan()
            return False
        except Exception as exc:  # noqa: BLE001
            if not silent:
                messagebox.showerror("解析失败", "%s: %s" % (type(exc).__name__, exc), parent=self)
            self.parse_label.set("解析失败")
            self._clear_plan()
            return False

        self.plan = plan
        self.project_var.set(plan.project)
        self.out_var.set(str(plan.settings.output_root))
        self._plan_key = self._plan_key_for(doc, plan.project, plan.settings.output_root)
        self._fill_tree(plan)
        voices = sorted({item.voice.key for item in plan.jobs})
        self.parse_label.set(
            "共 %d 句；%d 个音色：%s" % (len(plan.jobs), len(voices), "、".join(voices))
        )
        self.count_var.set("共 %d 句" % len(plan.jobs))
        self.progress.configure(maximum=max(1, len(plan.jobs)), value=0)
        self.app.set_status("已解析 %d 句台词，可以开始生成。" % len(plan.jobs))
        return True

    def _clear_plan(self) -> None:
        self.plan = None
        self._plan_key = None
        for item in self.tree.get_children():
            self.tree.delete(item)
        self.count_var.set("等待解析")
        self.progress.configure(maximum=1, value=0)

    def _fill_tree(self, plan: Plan) -> None:
        for item in self.tree.get_children():
            self.tree.delete(item)
        self._row_numbers = {}
        for number, item in enumerate(plan.jobs, start=1):
            index = item.job.index
            self._row_numbers[index] = number
            text = item.job.text
            if len(text) > 46:
                text = text[:43] + "..."
            output = short_path(item.output, plan.settings, limit=52)
            self.tree.insert(
                "",
                "end",
                iid=str(index),
                values=(number, item.job.line_no, item.job.id, item.job.speaker, item.voice.key, text, "待生成", output),
            )
        # Treeview 插入后可能自动滚到末尾，预览应从第一句开始。
        self.tree.yview_moveto(0)

    def _set_row_status(self, index: Any, status: str, tag: str = "", output: Optional[str] = None) -> None:
        try:
            iid = str(index)
        except Exception:  # noqa: BLE001
            return
        if not self.tree.exists(iid):
            return
        values = list(self.tree.item(iid, "values"))
        if len(values) >= 7:
            values[6] = status
        if output and len(values) >= 8:
            if self.settings is not None:
                values[7] = short_path(Path(output), self.settings, limit=52)
            else:
                values[7] = output
        self.tree.item(iid, values=values, tags=(tag,) if tag else ())
        if status in ("生成中", "已完成", "失败"):
            self.tree.see(iid)

    # ------------------------------------------------------------------ 生成
    def _run_options(self, dry_run: bool = False) -> RunOptions:
        return RunOptions(
            doc=self._doc_path(),
            project=self.project_var.get().strip() or None,
            out_root=self._out_path(),
            dry_run=dry_run,
            start=1,
            overwrite=bool(self.overwrite_var.get()),
            resume=bool(self.resume_var.get()),
            fail_fast=bool(self.failfast_var.get()),
            order="voice",
        )

    def start(self) -> None:
        if self.app.worker is not None and self.app.worker.is_alive():
            return
        doc = self._doc_path()
        key = self._plan_key_for(doc, self.project_var.get().strip(), self._out_path())
        if self.plan is None or key != self._plan_key:
            if not self.preview():
                return
        if self.plan is None:
            return
        self.app.start_generation(self._run_options(dry_run=False))

    def stop(self) -> None:
        if self.app.cancel_event is not None:
            self.app.cancel_event.set()
            self.app.set_status("正在停止：当前这一句完成后会停下……")
            self.stop_btn.configure(state="disabled")

    def open_output(self) -> None:
        out = self._out_path() or (self.settings.output_root if self.settings else None)
        if out is None:
            messagebox.showinfo("输出目录", "还没有设置输出目录。", parent=self)
            return
        project = self.project_var.get().strip()
        target = out / sanitize_project(project) if project else out
        if not target.exists():
            target = out
        if not target.exists():
            messagebox.showinfo("输出目录", "目录还不存在，生成后会自动创建：\n%s" % target, parent=self)
            return
        try:
            open_path(target)
        except Exception as exc:  # noqa: BLE001
            messagebox.showerror("打开失败", str(exc), parent=self)

    # ------------------------------------------------------------------ 进度
    def on_started(self, log_path: Optional[str]) -> None:
        self.start_btn.configure(state="disabled")
        self.stop_btn.configure(state="normal")
        self.progress.configure(value=0)
        self.count_var.set("启动服务中…")
        self.log_path_label.set(("日志：" + log_path) if log_path else "")
        self._set_all_status("待生成")

    def _set_all_status(self, status: str) -> None:
        for iid in self.tree.get_children():
            values = list(self.tree.item(iid, "values"))
            if len(values) >= 7:
                values[6] = status
            self.tree.item(iid, values=values, tags=())

    def on_finished(self, info: Dict[str, Any]) -> None:
        self.start_btn.configure(state="normal")
        self.stop_btn.configure(state="disabled")
        total = int(info.get("done", 0)) + int(info.get("failed", 0)) + int(info.get("skipped", 0)) + int(
            info.get("not_run", 0)
        )
        if total:
            self.progress.configure(value=min(total, int(self.progress.cget("maximum"))))
        self.count_var.set(
            "完成 %s / 失败 %s / 跳过 %s"
            % (info.get("done", 0), info.get("failed", 0), info.get("skipped", 0))
        )

    def on_cancelled(self, info: Dict[str, Any]) -> None:
        self.start_btn.configure(state="normal")
        self.stop_btn.configure(state="disabled")
        self.count_var.set("已停止：完成 %s，失败 %s" % (info.get("done", 0), info.get("failed", 0)))

    def on_finish_message(self, info: Dict[str, Any]) -> None:
        code = int(info.get("code", 1))
        done = int(info.get("done", 0))
        failed = int(info.get("failed", 0))
        skipped = int(info.get("skipped", 0))
        not_run = int(info.get("not_run", 0))
        message = info.get("message") or ""
        detail = "成功 %d，失败 %d，跳过 %d，未执行 %d。" % (done, failed, skipped, not_run)
        if message:
            detail += "\n\n%s" % message
        if info.get("report"):
            detail += "\n\n报告：%s" % info["report"]
        if code == 0:
            messagebox.showinfo("生成完成", detail, parent=self)
        elif code == 1:
            messagebox.showwarning("生成结束（有失败）", detail, parent=self)
        elif code == 3:
            messagebox.showerror("服务启动失败", detail, parent=self)

    def append_log(self, message: str) -> None:
        self.log_text.configure(state="normal")
        self.log_text.insert("end", message.rstrip() + "\n")
        lines = int(self.log_text.index("end-1c").split(".")[0])
        if lines > 2000:
            self.log_text.delete("1.0", "200.0")
        self.log_text.see("end")
        self.log_text.configure(state="disabled")


class SettingsTab(ttk.Frame):
    def __init__(self, parent: tk.Misc, app: "App") -> None:
        super().__init__(parent)
        self.app = app
        self.settings: Optional[Settings] = None
        self._build()

    def _build(self) -> None:
        self.columnconfigure(0, weight=1)
        card = make_card(self, padding=18)
        card.grid(row=0, column=0, sticky="ew", padx=8, pady=8)
        card.columnconfigure(1, weight=1)

        head = section_header(card, "环境设置", "第一次使用只需要选对 GPT-SoVITS 安装目录和输出目录。")
        head.grid(row=0, column=0, columnspan=3, sticky="ew", pady=(0, 12))

        ttk.Label(card, text="GPT-SoVITS 安装目录", style="Card.TLabel").grid(row=1, column=0, sticky="w", pady=6)
        self.install_var = tk.StringVar()
        ttk.Entry(card, textvariable=self.install_var).grid(row=1, column=1, sticky="ew", pady=6)
        ttk.Button(card, text="浏览…", style="Small.TButton", command=self._browse_install).grid(
            row=1, column=2, sticky="w", padx=(8, 0)
        )
        ttk.Label(
            card,
            text="必须包含 api_v2.py、runtime\\python.exe、GPT_weights* 和 presets。只读，不会被修改。",
            style="CardHint.TLabel",
        ).grid(row=2, column=1, columnspan=2, sticky="w")

        ttk.Label(card, text="输出根目录", style="Card.TLabel").grid(row=3, column=0, sticky="w", pady=6)
        self.output_var = tk.StringVar()
        ttk.Entry(card, textvariable=self.output_var).grid(row=3, column=1, sticky="ew", pady=6)
        ttk.Button(card, text="浏览…", style="Small.TButton", command=self._browse_output).grid(
            row=3, column=2, sticky="w", padx=(8, 0)
        )
        ttk.Label(
            card,
            text="音频会保存为“输出根目录\\角色\\项目\\编号_台词.wav”。",
            style="CardHint.TLabel",
        ).grid(row=4, column=1, columnspan=2, sticky="w")

        ttk.Label(card, text="额外模型包目录", style="Card.TLabel").grid(row=5, column=0, sticky="w", pady=6)
        self.models_var = tk.StringVar()
        ttk.Entry(card, textvariable=self.models_var).grid(row=5, column=1, sticky="ew", pady=6)
        ttk.Button(card, text="浏览…", style="Small.TButton", command=self._browse_models).grid(
            row=5, column=2, sticky="w", padx=(8, 0)
        )
        ttk.Label(
            card,
            text="放下载的 GPT/SoVITS 模型包的父目录；可以留空。",
            style="CardHint.TLabel",
        ).grid(row=6, column=1, columnspan=2, sticky="w")

        self.summary_var = tk.StringVar(value="")
        tk.Label(
            card,
            textvariable=self.summary_var,
            bg=CARD,
            fg=MUTED,
            font=(FONT, 9),
            justify="left",
            anchor="w",
            wraplength=760,
        ).grid(row=7, column=0, columnspan=3, sticky="w", pady=(12, 0))

        buttons = tk.Frame(card, bg=CARD)
        buttons.grid(row=8, column=0, columnspan=3, sticky="e", pady=(14, 0))
        ttk.Button(buttons, text="环境检测", style="Small.TButton", command=self.app.check_environment).pack(
            side="left", padx=(0, 8)
        )
        ttk.Button(buttons, text="打开工作区", style="Small.TButton", command=lambda: open_path(WORKSPACE_ROOT)).pack(
            side="left", padx=(0, 8)
        )
        ttk.Button(buttons, text="保存并重新检测", style="Primary.TButton", command=self.save).pack(side="left")

        note = make_card(self, padding=14)
        note.grid(row=1, column=0, sticky="ew", padx=8, pady=(0, 8))
        tk.Label(
            note,
            text="说明：本程序不会修改 GPT-SoVITS 安装目录。模型切换通过官方 api_v2.py 完成；"
            "配置文件、日志、音色库都保存在本工作区。",
            bg=CARD,
            fg=MUTED,
            font=(FONT, 9),
            justify="left",
            wraplength=820,
            anchor="w",
        ).pack(fill="x")

    def set_context(self, settings: Optional[Settings]) -> None:
        self.settings = settings
        if settings is None:
            self.summary_var.set("环境未就绪：请选择 GPT-SoVITS 安装目录后保存。")
            return
        self.install_var.set(str(settings.install_root))
        self.output_var.set(str(settings.output_root))
        self.models_var.set(str(settings.extra_model_roots[0]) if settings.extra_model_roots else "")
        self.summary_var.set(
            "当前：安装目录 %s\n输出目录 %s\n额外模型根目录 %s"
            % (
                settings.install_root,
                settings.output_root,
                settings.extra_model_roots or "（无）",
            )
        )

    def _browse_install(self) -> None:
        path = filedialog.askdirectory(title="选择 GPT-SoVITS 安装目录", parent=self)
        if not path:
            return
        if not (Path(path) / "api_v2.py").exists():
            messagebox.showwarning(
                "目录不正确",
                "这个目录里没有 api_v2.py。\n请选择 GPT-SoVITS 的根目录（和 webui.py 同级）。",
                parent=self,
            )
            return
        self.install_var.set(path)

    def _browse_output(self) -> None:
        path = filedialog.askdirectory(title="选择输出根目录", parent=self)
        if path:
            self.output_var.set(path)

    def _browse_models(self) -> None:
        path = filedialog.askdirectory(title="选择额外模型包目录", parent=self)
        if path:
            self.models_var.set(path)

    def save(self) -> None:
        install = self.install_var.get().strip()
        output = self.output_var.get().strip()
        models = self.models_var.get().strip()
        if not install:
            messagebox.showwarning("缺少安装目录", "请选择 GPT-SoVITS 安装目录。", parent=self)
            return
        if not output:
            messagebox.showwarning("缺少输出目录", "请选择输出根目录。", parent=self)
            return
        install_path = Path(install).expanduser()
        if not install_path.is_absolute():
            install_path = WORKSPACE_ROOT / install_path
        if not (install_path / "api_v2.py").is_file():
            messagebox.showwarning(
                "安装目录不正确",
                "这个目录里没有 api_v2.py。\n请选择 GPT-SoVITS 根目录（和 webui.py 同级）。",
                parent=self,
            )
            return
        try:
            path = update_settings_file(
                WORKSPACE_ROOT,
                install_root=Path(install),
                output_root=Path(output),
                extra_model_roots=[Path(models)] if models else [],
            )
        except Exception as exc:  # noqa: BLE001
            messagebox.showerror("保存设置失败", str(exc), parent=self)
            return
        # 重新读取并验证；失败时保留原设置页内容，方便修正。
        try:
            settings = load_settings(WORKSPACE_ROOT / "config" / "settings.yaml")
            load_registry(settings)
        except Exception as exc:  # noqa: BLE001
            messagebox.showerror("设置已保存，但检测失败", "%s\n\n设置文件：%s" % (exc, path), parent=self)
            return
        self.app.reload_environment()
        messagebox.showinfo(
            "保存成功",
            "设置已保存并生效。\n\n安装目录：%s\n输出目录：%s\n\n旧设置备份在 config\\backups。"
            % (settings.install_root, settings.output_root),
            parent=self,
        )


class HelpTab(ttk.Frame):
    def __init__(self, parent: tk.Misc, app: "App") -> None:
        super().__init__(parent)
        self.app = app
        self._build()

    def _build(self) -> None:
        self.columnconfigure(0, weight=1)
        self.rowconfigure(0, weight=1)
        card = make_card(self, padding=16)
        card.grid(row=0, column=0, sticky="nsew", padx=8, pady=8)
        card.columnconfigure(0, weight=1)
        card.rowconfigure(1, weight=1)
        head = section_header(card, "使用帮助", "三步就能生成一批语音。")
        head.grid(row=0, column=0, sticky="ew", pady=(0, 10))
        text = tk.Text(
            card,
            wrap="word",
            bg="#f8fafc",
            fg=TEXT,
            relief="flat",
            font=(FONT, 10),
            padx=14,
            pady=12,
        )
        text.grid(row=1, column=0, sticky="nsew")
        scroll = ttk.Scrollbar(card, orient="vertical", command=text.yview)
        scroll.grid(row=1, column=1, sticky="ns")
        text.configure(yscrollcommand=scroll.set)
        content = """一、第一次使用
1. 打开“设置”，选择 GPT-SoVITS 安装目录（包含 api_v2.py 和 runtime 文件夹）。
2. 选择输出根目录；默认是工作区旁边的“语音output”。
3. 点“保存并重新检测”。检测通过后，顶部会显示绿色状态。

二、生成语音
1. 在“生成语音”页选择 .md 或 .txt 台本文档。点“载入示例”可以先试跑。
2. 程序会自动解析；也可以点“解析预览”。表格里能看到每一句的角色、音色和输出文件。
3. 点“开始生成”。第一次会启动 API 并加载模型，需要等几十秒。
4. 生成中可以点“停止”；当前这一句结束后会安全停下。
5. 完成后点“打开输出目录”查看 wav。

三、文档格式
支持这些写法（角色名必须在“音色库”里）：
    银狼：今天天气不错。
    [5931] 银狼：要不要一起去星穹列车？
    【爱音】这怎么活得下去啊啊啊啊。
    灯|想和大家一起，一直走下去。
    [04:25] 银狼：时间码会变成文件名前缀 0425。
也支持 Markdown 标题、列表、引用，以及：
    ---
    project: 项目名
    default_voice: 银狼
    ---
角色后面可以带情绪，例如 银狼（开心）：…… ，前提是音色库里有“银狼（开心）”。

四、添加音色
“音色库”页可以：
- 从预设添加：直接选 GPT-SoVITS presets 里现成的音色，最省事。
- 手动添加：选 GPT/SoVITS 权重和一段参考音频，填写参考台词。
参考台词必须和参考音频里真正说出的内容一致。添加后会自动备份 voices.yaml。

五、注意
- GPT-SoVITS 安装目录是只读的，程序不会修改里面的文件。
- 批量生成时显卡要同时容纳 WebUI 和 API；显存不足时先关闭 WebUI 的推理页。
- 详细说明见工作区 README.md 和 README.en.md。
"""
        text.insert("1.0", content)
        text.configure(state="disabled")


class App(tk.Tk):
    def __init__(self) -> None:
        set_dpi_awareness()
        super().__init__()
        self.title("Gptsovit Auto - 中文语音批量生成")
        screen_w = self.winfo_screenwidth()
        screen_h = self.winfo_screenheight()
        width = min(1240, max(1000, screen_w - 40))
        height = min(900, max(700, screen_h - 80))
        self.geometry("%dx%d" % (width, height))
        self.minsize(min(1000, width), min(700, height))
        apply_theme(self)

        self.settings_path = WORKSPACE_ROOT / "config" / "settings.yaml"
        self.settings: Optional[Settings] = None
        self.registry: Optional[VoiceRegistry] = None
        self.plan: Optional[Plan] = None
        self.worker: Optional[threading.Thread] = None
        self.cancel_event: Optional[threading.Event] = None
        self.events: "queue.Queue[Dict[str, Any]]" = queue.Queue()
        self._queue_handler: Optional[_QueueHandler] = None
        self._finish_info: Optional[Dict[str, Any]] = None
        self._closing = False
        self._close_pending = False
        self._close_ticks = 0
        self._build()
        self.protocol("WM_DELETE_WINDOW", self.on_close)
        self.after(80, self._poll_queue)
        self.after(120, self.initialize_environment)

    def _build(self) -> None:
        self.columnconfigure(0, weight=1)
        self.rowconfigure(1, weight=1)

        header = make_card(self, padding=14)
        header.grid(row=0, column=0, sticky="ew", padx=10, pady=(10, 6))
        header.columnconfigure(0, weight=1)
        left = tk.Frame(header, bg=CARD)
        left.grid(row=0, column=0, sticky="w")
        tk.Label(left, text="Gptsovit Auto", bg=CARD, fg=TEXT, font=(FONT, 18, "bold")).pack(anchor="w")
        tk.Label(
            left,
            text="选择台本文档，预览每一句，然后批量生成语音。",
            bg=CARD,
            fg=MUTED,
            font=(FONT, 9),
        ).pack(anchor="w", pady=(2, 0))

        right = tk.Frame(header, bg=CARD)
        right.grid(row=0, column=1, sticky="e")
        self.env_badge = tk.Label(
            right,
            text="正在检测环境…",
            bg="#fef3c7",
            fg="#92400e",
            font=(FONT, 9, "bold"),
            padx=10,
            pady=5,
        )
        self.env_badge.pack(side="left", padx=(0, 8))
        ttk.Button(right, text="重新检测", style="Small.TButton", command=self.reload_environment).pack(
            side="left", padx=3
        )
        ttk.Button(right, text="打开工作区", style="Small.TButton", command=lambda: open_path(WORKSPACE_ROOT)).pack(
            side="left", padx=3
        )

        self.notebook = ttk.Notebook(self)
        self.notebook.grid(row=1, column=0, sticky="nsew", padx=8)
        self.generate_tab = GenerateTab(self.notebook, self)
        self.voices_tab = VoicesTab(self.notebook, self)
        self.settings_tab = SettingsTab(self.notebook, self)
        self.help_tab = HelpTab(self.notebook, self)
        self.notebook.add(self.generate_tab, text="生成语音")
        self.notebook.add(self.voices_tab, text="音色库")
        self.notebook.add(self.settings_tab, text="设置")
        self.notebook.add(self.help_tab, text="帮助")

        status = tk.Frame(self, bg=BG)
        status.grid(row=2, column=0, sticky="ew", padx=14, pady=(2, 8))
        status.columnconfigure(0, weight=1)
        self.status_var = tk.StringVar(value="准备就绪")
        tk.Label(
            status,
            textvariable=self.status_var,
            bg=BG,
            fg=MUTED,
            font=(FONT, 9),
            anchor="w",
        ).grid(row=0, column=0, sticky="w")
        tk.Label(
            status,
            text="v%s" % __version__,
            bg=BG,
            fg=MUTED,
            font=(FONT, 8),
            anchor="e",
        ).grid(row=0, column=1, sticky="e")

    # ------------------------------------------------------------------ 环境
    def initialize_environment(self) -> None:
        if not self.settings_path.exists():
            try:
                ensure_initial_files()
            except Exception as exc:  # noqa: BLE001
                messagebox.showerror("初始化失败", str(exc), parent=self)
                self._set_env_bad("初始化失败")
                return
        if not self.reload_environment(show_error=False):
            self.notebook.select(self.settings_tab)
            messagebox.showwarning(
                "需要设置",
                "还不能开始生成。\n\n请在“设置”页选择 GPT-SoVITS 安装目录和输出目录，然后点“保存并重新检测”。",
                parent=self,
            )

    def reload_environment(self, show_error: bool = True) -> bool:
        try:
            settings = load_settings(self.settings_path)
            registry = load_registry(settings)
        except Exception as exc:  # noqa: BLE001
            self.settings = None
            self.registry = None
            self._set_env_bad("环境未就绪")
            self.generate_tab.set_context(None, None)
            self.voices_tab.set_context(None, None)
            self.settings_tab.set_context(None)
            self.set_status("环境检测失败：%s" % exc)
            if show_error:
                messagebox.showerror("环境检测失败", str(exc), parent=self)
            return False
        self.settings = settings
        self.registry = registry
        self._set_env_ok("环境正常 · %d 个音色" % len(registry.list()))
        self.generate_tab.set_context(settings, registry)
        self.voices_tab.set_context(settings, registry)
        self.settings_tab.set_context(settings)
        self.set_status(
            "环境正常：%s；输出到 %s" % (settings.install_root.name, settings.output_root)
        )
        return True

    def _set_env_ok(self, text: str) -> None:
        self.env_badge.configure(text=text, bg="#dcfce7", fg="#166534")

    def _set_env_bad(self, text: str) -> None:
        self.env_badge.configure(text=text, bg="#fee2e2", fg="#991b1b")

    def set_status(self, text: str) -> None:
        self.status_var.set(text)

    def check_environment(self) -> None:
        try:
            settings = load_settings(self.settings_path)
            registry = load_registry(settings)
        except Exception as exc:  # noqa: BLE001
            messagebox.showerror("环境检测失败", str(exc), parent=self)
            return
        free = gpu_free_mb()
        client = TTSClient(settings.api_host, settings.api_port)
        healthy = client.health(2.0)
        client.close()
        lines = [
            "安装目录：%s" % settings.install_root,
            "输出目录：%s" % settings.output_root,
            "音色数量：%d" % len(registry.list()),
            "GPU 空闲显存：%s" % (("%d MiB" % free) if free is not None else "未知"),
            "API 服务：%s" % ("运行中" if healthy else "未启动（生成时会自动启动）"),
        ]
        if free is not None and free < int(settings.defaults.get("min_free_vram_mb", 4000)):
            lines.append("提示：显存偏低，建议先关闭 WebUI 的推理页。")
        messagebox.showinfo("环境检测", "\n".join(lines), parent=self)

    # ------------------------------------------------------------------ 生成线程
    def start_generation(self, options: RunOptions) -> None:
        if self.settings is None or self.registry is None:
            messagebox.showwarning("环境未就绪", "请先在“设置”页完成配置。", parent=self)
            return
        if self.worker is not None and self.worker.is_alive():
            return
        project = options.project or Path(options.doc).stem
        try:
            logger = setup_logger(self.settings, project, verbose=True)
        except Exception as exc:  # noqa: BLE001
            messagebox.showerror("无法启动", "创建日志失败：\n%s" % exc, parent=self)
            return
        handler = _QueueHandler(self.events)
        handler.setLevel(logging.INFO)
        handler.setFormatter(logging.Formatter("%(asctime)s %(message)s", "%H:%M:%S"))
        logger.addHandler(handler)
        self._queue_handler = handler
        self._finish_info = None
        self.cancel_event = threading.Event()
        self.generate_tab.on_started(get_log_path(logger))
        self.generate_tab.append_log("开始生成：%s" % options.doc)
        self.set_status("正在启动 API 服务并加载模型……")
        self.worker = threading.Thread(
            target=self._run_worker,
            args=(self.settings, options, logger, self.cancel_event),
            name="gptsovit-ui-worker",
            daemon=True,
        )
        self.worker.start()

    def _run_worker(
        self,
        settings: Settings,
        options: RunOptions,
        logger: logging.Logger,
        cancel: threading.Event,
    ) -> None:
        code = 3
        try:
            code = run(settings, options, logger, progress=self.events.put, cancel=cancel)
        except Exception:  # noqa: BLE001
            detail = traceback.format_exc()
            logger.error("界面生成失败：\n%s", detail)
            self.events.put({"type": "fatal", "message": detail})
        finally:
            self.events.put({"type": "worker_end", "code": code})

    def _poll_queue(self) -> None:
        try:
            handled = 0
            while handled < 200:
                event = self.events.get_nowait()
                handled += 1
                try:
                    self._handle_event(event)
                except Exception:  # noqa: BLE001
                    detail = traceback.format_exc()
                    try:
                        self.generate_tab.append_log("界面事件错误：\n%s" % detail)
                    except Exception:  # noqa: BLE001
                        pass
                    self.set_status("界面事件错误，详见运行日志。")
        except queue.Empty:
            pass
        except tk.TclError:
            return
        try:
            self.after(80, self._poll_queue)
        except tk.TclError:
            pass

    def _handle_event(self, event: Dict[str, Any]) -> None:
        kind = event.get("type")
        tab = self.generate_tab
        if kind == "log":
            tab.append_log(str(event.get("message", "")))
            return
        if kind == "plan":
            total = int(event.get("total", 0))
            skipped = int(event.get("skipped", 0))
            tab.progress.configure(maximum=max(1, total), value=0)
            tab.count_var.set("共 %d 句，跳过 %d" % (total, skipped))
            self.set_status("开始生成，共 %d 句……" % total)
            return
        if kind == "voice":
            self.set_status(
                "正在使用音色：%s（%s）" % (event.get("voice", ""), event.get("version", ""))
            )
            return
        if kind == "voice_error":
            tab.append_log("音色切换失败：%s" % event.get("message", ""))
            self.set_status("音色切换失败：%s" % event.get("message", ""))
            return
        if kind == "job_start":
            index = event.get("index")
            tab._set_row_status(index, "生成中", "run")
            number = int(event.get("number", 0))
            total = int(event.get("total", 0))
            tab.progress.configure(value=max(0, number - 1))
            tab.count_var.set("正在生成 %d / %d" % (number, total))
            self.set_status("正在生成 %d/%d：%s" % (number, total, event.get("id", "")))
            return
        if kind == "job_done":
            tab._set_row_status(event.get("index"), "已完成", "done", event.get("output"))
            number = int(event.get("number", 0))
            total = int(event.get("total", 0))
            tab.progress.configure(value=number)
            tab.count_var.set("完成 %d / %d" % (number, total))
            return
        if kind == "job_error":
            tab._set_row_status(event.get("index"), "失败", "error", event.get("output"))
            tab.append_log("失败：%s" % event.get("message", ""))
            self.set_status("有台词生成失败：%s" % event.get("message", ""))
            return
        if kind == "skip":
            tab._set_row_status(event.get("index"), "已跳过")
            return
        if kind == "cancelled":
            tab.on_cancelled(event)
            self.set_status("已停止生成。")
            return
        if kind == "finish":
            self._finish_info = event
            tab.on_finished(event)
            tab.append_log(
                "结束：成功 %s，失败 %s，跳过 %s，未执行 %s"
                % (event.get("done", 0), event.get("failed", 0), event.get("skipped", 0), event.get("not_run", 0))
            )
            if event.get("message"):
                tab.append_log(str(event["message"]))
            if int(event.get("code", 0)) in (0, 1, 3):
                self.set_status("生成结束。")
                if not self._closing:
                    tab.on_finish_message(event)
            return
        if kind == "fatal":
            tab.append_log("界面异常：\n%s" % event.get("message", ""))
            messagebox.showerror("界面异常", str(event.get("message", ""))[-1200:], parent=self)
            self.set_status("界面异常，详见日志。")
            return
        if kind == "worker_end":
            self.worker = None
            self.cancel_event = None
            tab.start_btn.configure(state="normal")
            tab.stop_btn.configure(state="disabled")
            if self._closing and self._close_pending:
                self.destroy()
            return

    # ------------------------------------------------------------------ 关闭
    def on_close(self) -> None:
        if self.worker is not None and self.worker.is_alive():
            if not messagebox.askyesno(
                "正在生成",
                "还在生成语音。要停止并退出吗？\n\n当前这一句会先结束，然后程序会关闭。",
                parent=self,
            ):
                return
            self._close_pending = True
            self._closing = True
            if self.cancel_event is not None:
                self.cancel_event.set()
            self.set_status("正在停止并退出……")
            self._close_ticks = 0
            self.after(400, self._wait_close)
            return
        self.destroy()

    def _wait_close(self) -> None:
        if self.worker is None or not self.worker.is_alive():
            self.destroy()
            return
        self._close_ticks += 1
        if self._close_ticks >= 75:  # 约 30 秒
            self.destroy()
            return
        self.set_status("正在停止并退出……（%d）" % self._close_ticks)
        self.after(400, self._wait_close)


def main() -> int:
    app = App()
    app.mainloop()
    return 0
