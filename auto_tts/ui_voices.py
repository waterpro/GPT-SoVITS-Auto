# -*- coding: utf-8 -*-
"""音色库页：查看、添加、删除用于生成的角色音色。"""

from __future__ import annotations

import json
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk
from typing import Any, Dict, List, Optional

from .ui_theme import (
    CARD,
    FONT,
    MUTED,
    TEXT,
    center_window,
    make_card,
    section_header,
)
from .voice_editor import (
    PresetInfo,
    VoiceDraft,
    discover_presets,
    ensure_draft_defaults,
    resolve_user_path,
    short_path,
    write_voice,
    remove_voice,
)
from .voices import Voice, VoiceError, detect_version

_LANG_OPTIONS = [
    ("跟随预设（推荐）", None),
    ("中文", "zh"),
    ("日文", "ja"),
    ("英文", "en"),
    ("韩文", "ko"),
    ("粤语", "yue"),
    ("自动检测", "auto"),
]
_MANUAL_LANG_OPTIONS = [
    ("中文", "zh"),
    ("日文", "ja"),
    ("英文", "en"),
    ("韩文", "ko"),
    ("粤语", "yue"),
    ("自动检测", "auto"),
]
_LANG_LABELS = {code: label for label, code in _LANG_OPTIONS if code}
_LANG_LABELS.update({"all_zh": "中文（整段）", "all_ja": "日文（整段）", "all_yue": "粤语（整段）", "all_ko": "韩文（整段）"})


def _split_aliases(value: str) -> List[str]:
    result: List[str] = []
    for part in value.replace("，", ",").replace("、", ",").replace("；", ";").replace(";", ",").split(","):
        item = part.strip()
        if item and item not in result:
            result.append(item)
    return result


def _lang_text(code: str) -> str:
    return _LANG_LABELS.get(code, code)


def _preset_meta(preset: PresetInfo) -> Dict[str, Any]:
    path = Path(preset.paths[0]) / "metadata.json"
    try:
        with path.open("r", encoding="utf-8") as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _short(value: Any, limit: int = 58) -> str:
    text = str(value or "")
    return text if len(text) <= limit else text[: limit - 3] + "..."


class VoicesTab(ttk.Frame):
    def __init__(self, parent: tk.Misc, app: Any) -> None:
        super().__init__(parent)
        self.app = app
        self.settings = None
        self.registry = None
        self.presets: List[PresetInfo] = []
        self._build()

    def _build(self) -> None:
        self.columnconfigure(0, weight=1)
        self.rowconfigure(1, weight=1)

        head = make_card(self, padding=14)
        head.grid(row=0, column=0, sticky="ew", padx=4, pady=(4, 8))
        head.columnconfigure(0, weight=1)
        left = tk.Frame(head, bg=CARD)
        left.grid(row=0, column=0, sticky="w")
        tk.Label(left, text="音色库", bg=CARD, fg=TEXT, font=(FONT, 14, "bold")).pack(anchor="w")
        self.summary_label = tk.Label(
            left,
            text="正在读取……",
            bg=CARD,
            fg=MUTED,
            font=(FONT, 9),
            anchor="w",
            justify="left",
        )
        self.summary_label.pack(anchor="w", pady=(3, 0))

        buttons = tk.Frame(head, bg=CARD)
        buttons.grid(row=0, column=1, sticky="e")
        self.btn_refresh = ttk.Button(buttons, text="刷新", style="Small.TButton", command=self.refresh)
        self.btn_refresh.pack(side="left", padx=3)
        self.btn_view = ttk.Button(buttons, text="查看详情", style="Small.TButton", command=self.view_selected)
        self.btn_view.pack(side="left", padx=3)
        self.btn_preset = ttk.Button(buttons, text="从预设添加", style="Small.TButton", command=self.add_preset)
        self.btn_preset.pack(side="left", padx=3)
        self.btn_manual = ttk.Button(buttons, text="手动添加", style="Small.TButton", command=self.add_manual)
        self.btn_manual.pack(side="left", padx=3)
        self.btn_remove = ttk.Button(buttons, text="删除", style="Small.TButton", command=self.remove_selected)
        self.btn_remove.pack(side="left", padx=(10, 0))

        card = make_card(self, padding=10)
        card.grid(row=1, column=0, sticky="nsew", padx=4, pady=(0, 4))
        card.columnconfigure(0, weight=1)
        card.rowconfigure(1, weight=1)

        hint = tk.Frame(card, bg=CARD)
        hint.grid(row=0, column=0, sticky="ew", pady=(0, 8))
        tk.Label(
            hint,
            text="文档里的角色名必须在这里出现，或写成它的别名。双击一行可以查看详情。",
            bg=CARD,
            fg=MUTED,
            font=(FONT, 9),
            anchor="w",
        ).pack(side="left")

        columns = ("key", "preset", "version", "ref", "lang", "output", "aliases")
        self.tree = ttk.Treeview(card, columns=columns, show="headings", selectmode="browse")
        headings = {
            "key": ("角色名", 130),
            "preset": ("预设", 130),
            "version": ("版本", 86),
            "ref": ("参考音频", 250),
            "lang": ("台词语言", 80),
            "output": ("输出目录", 90),
            "aliases": ("别名", 180),
        }
        for column, (title, width) in headings.items():
            self.tree.heading(column, text=title)
            self.tree.column(column, width=width, minwidth=60, anchor="w", stretch=(column in ("ref", "aliases")))
        self.tree.grid(row=1, column=0, sticky="nsew")
        scroll = ttk.Scrollbar(card, orient="vertical", command=self.tree.yview)
        scroll.grid(row=1, column=1, sticky="ns")
        self.tree.configure(yscrollcommand=scroll.set)
        self.tree.bind("<Double-1>", lambda _event: self.view_selected())

        self.empty_label = tk.Label(
            card,
            text="",
            bg=CARD,
            fg=MUTED,
            font=(FONT, 10),
            justify="left",
        )

    def set_context(self, settings: Any, registry: Any) -> None:
        self.settings = settings
        self.registry = registry
        self.presets = []
        if settings is not None and registry is not None:
            try:
                self.presets = discover_presets(settings, registry)
            except Exception:  # noqa: BLE001
                self.presets = []
        self.refresh()

    def refresh(self) -> None:
        for item in self.tree.get_children():
            self.tree.delete(item)
        self.empty_label.grid_forget()

        if self.settings is None or self.registry is None:
            self.summary_label.configure(text="环境未就绪：请先在“设置”页选择 GPT-SoVITS 安装目录。")
            self._set_buttons(False)
            self.empty_label.configure(text="还没有可用的音色配置。\n先完成设置，再添加角色音色。")
            self.empty_label.grid(row=1, column=0, sticky="n", pady=30)
            return

        voices = sorted(self.registry.list(), key=lambda item: item.key)
        configured = sum(1 for voice in voices if voice.enabled)
        self.summary_label.configure(
            text="已配置 %d 个角色音色；可用预设 %d 个。添加后立即生效，原文件会自动备份。"
            % (configured, len(self.presets))
        )
        self._set_buttons(True)
        if not voices:
            self.empty_label.configure(
                text="还没有角色音色。\n点击“从预设添加”，或“手动添加”一个 GPT/SoVITS 模型。"
            )
            self.empty_label.grid(row=1, column=0, sticky="n", pady=30)
            return

        for voice in voices:
            ref_name = Path(voice.ref_audio).name if voice.ref_audio else "—"
            if len(ref_name) > 44:
                ref_name = ref_name[:41] + "..."
            self.tree.insert(
                "",
                "end",
                iid=voice.key,
                values=(
                    voice.key,
                    voice.preset or "手动模型",
                    voice.version or "自动",
                    ref_name,
                    _lang_text(voice.text_lang),
                    voice.output_dir,
                    ", ".join(voice.aliases) if voice.aliases else "—",
                ),
            )

    def _set_buttons(self, enabled: bool) -> None:
        state = "normal" if enabled else "disabled"
        for button in (self.btn_refresh, self.btn_view, self.btn_preset, self.btn_manual, self.btn_remove):
            button.configure(state=state)

    def _selected(self) -> Optional[Voice]:
        selection = self.tree.selection()
        if not selection or self.registry is None:
            return None
        try:
            return self.registry.get(selection[0])
        except Exception:  # noqa: BLE001
            return None

    def view_selected(self) -> None:
        voice = self._selected()
        if voice is None:
            messagebox.showinfo("查看音色", "请先选择一个音色。", parent=self)
            return
        text = (
            "角色名：%s\n"
            "别名：%s\n"
            "来源：%s\n"
            "GPT 模型：%s\n"
            "SoVITS 模型：%s\n"
            "版本：%s\n"
            "参考音频：%s\n"
            "辅助参考：%d 个\n"
            "参考台词：%s\n"
            "参考语言：%s\n"
            "台词语言：%s\n"
            "输出目录：%s\n"
        ) % (
            voice.key,
            ", ".join(voice.aliases) or "—",
            voice.preset or "手动模型",
            short_path(voice.gpt, self.settings, limit=100),
            short_path(voice.sovits, self.settings, limit=100),
            voice.version or "自动",
            short_path(voice.ref_audio, self.settings, limit=100) if voice.ref_audio else "—",
            len(voice.aux_refs),
            voice.prompt_text or "—",
            _lang_text(voice.prompt_lang),
            _lang_text(voice.text_lang),
            voice.output_dir,
        )
        self._show_text("音色详情 - %s" % voice.key, text)

    def _show_text(self, title: str, text: str) -> None:
        window = tk.Toplevel(self)
        window.title(title)
        window.configure(bg=CARD)
        window.transient(self.app)
        window.grab_set()
        center_window(window, 760, 520)
        frame = make_card(window, padding=18)
        frame.pack(fill="both", expand=True, padx=12, pady=12)
        header = section_header(frame, title, "参考台词必须和参考音频的内容一致。")
        header.pack(fill="x", pady=(0, 10))
        box = tk.Text(frame, wrap="word", bg="#f8fafc", fg=TEXT, relief="flat", font=(FONT, 10), padx=10, pady=10)
        box.pack(fill="both", expand=True)
        box.insert("1.0", text)
        box.configure(state="disabled")
        ttk.Button(frame, text="关闭", command=window.destroy).pack(anchor="e", pady=(10, 0))

    def add_preset(self) -> None:
        if self.settings is None:
            return
        if not self.presets:
            messagebox.showwarning("没有预设", "没有在 GPT-SoVITS 的 presets 目录里找到可用预设。", parent=self)
            return
        AddPresetDialog(self, self.settings, self.registry, self.presets)

    def add_manual(self) -> None:
        if self.settings is None:
            return
        AddManualDialog(self, self.settings, self.registry)

    def remove_selected(self) -> None:
        voice = self._selected()
        if voice is None:
            messagebox.showinfo("删除音色", "请先选择一个音色。", parent=self)
            return
        if not messagebox.askyesno(
            "确认删除",
            "确定要从 voices.yaml 删除“%s”吗？\n\n音频文件不会删除，原配置会自动备份。" % voice.key,
            parent=self,
        ):
            return
        try:
            _data, backup = remove_voice(self.settings, voice.key)
        except (VoiceError, OSError) as exc:
            messagebox.showerror("删除失败", str(exc), parent=self)
            return
        self.app.reload_environment()
        self.refresh()
        messagebox.showinfo("已删除", "已删除“%s”。\n备份：%s" % (voice.key, backup), parent=self)


class AddPresetDialog(tk.Toplevel):
    def __init__(self, parent: VoicesTab, settings: Any, registry: Any, presets: List[PresetInfo]) -> None:
        super().__init__(parent)
        self.parent_tab = parent
        self.settings = settings
        self.registry = registry
        self.presets = presets
        self.title("从预设添加音色")
        self.configure(bg=CARD)
        self.transient(parent.winfo_toplevel())
        self.grab_set()
        self.resizable(False, False)
        center_window(self, 700, 600)
        self._build()

    def _build(self) -> None:
        card = make_card(self, padding=18)
        card.pack(fill="both", expand=True, padx=12, pady=12)
        card.columnconfigure(1, weight=1)

        header = section_header(card, "从预设添加", "预设里已经包含模型、参考音频和参考台词。")
        header.grid(row=0, column=0, columnspan=3, sticky="ew", pady=(0, 12))

        ttk.Label(card, text="选择预设", style="Card.TLabel").grid(row=1, column=0, sticky="w", pady=6)
        self.preset_var = tk.StringVar(value=self.presets[0].name)
        self.combo = ttk.Combobox(
            card,
            textvariable=self.preset_var,
            values=[item.name for item in self.presets],
            state="readonly",
            width=42,
        )
        self.combo.grid(row=1, column=1, columnspan=2, sticky="ew", pady=6)
        self.combo.bind("<<ComboboxSelected>>", lambda _event: self._on_preset_change())

        ttk.Label(card, text="角色名", style="Card.TLabel").grid(row=2, column=0, sticky="w", pady=6)
        self.key_var = tk.StringVar(value=self.presets[0].name)
        ttk.Entry(card, textvariable=self.key_var).grid(row=2, column=1, columnspan=2, sticky="ew", pady=6)

        ttk.Label(card, text="输出目录", style="Card.TLabel").grid(row=3, column=0, sticky="w", pady=6)
        self.output_var = tk.StringVar()
        ttk.Entry(card, textvariable=self.output_var).grid(row=3, column=1, columnspan=2, sticky="ew", pady=6)
        ttk.Label(card, text="留空时使用角色名；例如填“银狼”。", style="CardHint.TLabel").grid(
            row=4, column=1, columnspan=2, sticky="w"
        )

        ttk.Label(card, text="别名", style="Card.TLabel").grid(row=5, column=0, sticky="w", pady=6)
        self.alias_var = tk.StringVar()
        ttk.Entry(card, textvariable=self.alias_var).grid(row=5, column=1, columnspan=2, sticky="ew", pady=6)
        ttk.Label(card, text="用逗号分隔；文档里写任意一个都能匹配。", style="CardHint.TLabel").grid(
            row=6, column=1, columnspan=2, sticky="w"
        )

        ttk.Label(card, text="台词语言", style="Card.TLabel").grid(row=7, column=0, sticky="w", pady=6)
        self.lang_var = tk.StringVar(value=_LANG_OPTIONS[0][0])
        ttk.Combobox(
            card,
            textvariable=self.lang_var,
            values=[item[0] for item in _LANG_OPTIONS],
            state="readonly",
            width=22,
        ).grid(row=7, column=1, sticky="w", pady=6)

        ttk.Label(card, text="语速", style="Card.TLabel").grid(row=8, column=0, sticky="w", pady=6)
        self.speed_var = tk.StringVar()
        ttk.Entry(card, textvariable=self.speed_var, width=10).grid(row=8, column=1, sticky="w", pady=6)
        ttk.Label(card, text="留空使用默认值；范围 0.5–2.0。", style="CardHint.TLabel").grid(
            row=8, column=2, sticky="w"
        )

        info = tk.Frame(card, bg="#f8fafc", highlightbackground="#e2e8f0", highlightthickness=1)
        info.grid(row=9, column=0, columnspan=3, sticky="ew", pady=(12, 4))
        info.columnconfigure(0, weight=1)
        self.info_label = tk.Label(
            info,
            text="",
            bg="#f8fafc",
            fg=TEXT,
            font=(FONT, 9),
            justify="left",
            anchor="w",
            wraplength=600,
            padx=12,
            pady=10,
        )
        self.info_label.grid(row=0, column=0, sticky="ew")
        self.configured_label = tk.Label(
            info,
            text="",
            bg="#f8fafc",
            fg="#b45309",
            font=(FONT, 9, "bold"),
            justify="left",
            anchor="w",
            wraplength=600,
            padx=12,
            pady=0,
        )
        self.configured_label.grid(row=1, column=0, sticky="ew")

        footer = tk.Frame(card, bg=CARD)
        footer.grid(row=10, column=0, columnspan=3, sticky="e", pady=(14, 0))
        ttk.Button(footer, text="取消", command=self.destroy).pack(side="right")
        ttk.Button(footer, text="保存并校验", style="Primary.TButton", command=self._save).pack(
            side="right", padx=(0, 8)
        )

        self._on_preset_change()

    def _current_preset(self) -> PresetInfo:
        for item in self.presets:
            if item.name == self.preset_var.get():
                return item
        return self.presets[0]

    def _on_preset_change(self) -> None:
        preset = self._current_preset()
        current_key = self.key_var.get().strip()
        if not current_key or current_key in {item.name for item in self.presets}:
            self.key_var.set(preset.name)
        meta = _preset_meta(preset)
        main = meta.get("main_filename", "（缺少参考音频）")
        aux = meta.get("aux_filenames") or []
        prompt = meta.get("prompt_text", "（缺少参考台词）")
        prompt_lang = meta.get("prompt_language", "—")
        model_text = "—"
        if preset.paths:
            parts = preset.paths[0].parts
            if len(parts) >= 3:
                model_text = "%s / %s" % (parts[-3], parts[-2])
        self.info_label.configure(
            text="模型：%s\n参考音频：%s\n辅助音频：%d 个\n参考台词：%s\n参考语言：%s"
            % (
                model_text,
                main,
                len(aux),
                _short(prompt, 150),
                prompt_lang,
            )
        )
        if preset.configured:
            self.configured_label.configure(text="这个预设已配置为：%s（仍可另建一个角色名）" % ", ".join(preset.configured))
        else:
            self.configured_label.configure(text="")

    def _save(self) -> None:
        key = self.key_var.get().strip()
        if not key:
            messagebox.showwarning("缺少角色名", "请填写角色名。", parent=self)
            return
        lang_code = None
        for label, code in _LANG_OPTIONS:
            if label == self.lang_var.get():
                lang_code = code
                break
        speed: Optional[float] = None
        raw_speed = self.speed_var.get().strip()
        if raw_speed:
            try:
                speed = float(raw_speed)
            except ValueError:
                messagebox.showwarning("语速无效", "语速必须是数字，例如 1.05。", parent=self)
                return
            if not 0.5 <= speed <= 2.0:
                messagebox.showwarning("语速超出范围", "语速需要在 0.5 到 2.0 之间。", parent=self)
                return
        draft = VoiceDraft(
            key=key,
            preset=self.preset_var.get(),
            output_dir=self.output_var.get().strip() or None,
            aliases=_split_aliases(self.alias_var.get()),
            text_lang=lang_code,
            speed_factor=speed,
        )
        ensure_draft_defaults(self.settings, draft)
        self._commit(draft)

    def _commit(self, draft: VoiceDraft) -> None:
        try:
            _data, _text, voice, backup = write_voice(self.settings, draft)
        except VoiceError as exc:
            message = str(exc)
            if "已存在" in message or "exists" in message.lower():
                if not messagebox.askyesno("角色名重复", "%s\n\n要覆盖它吗？" % message, parent=self):
                    return
                try:
                    _data, _text, voice, backup = write_voice(self.settings, draft, force=True)
                except (VoiceError, OSError) as inner:
                    messagebox.showerror("保存失败", str(inner), parent=self)
                    return
            else:
                messagebox.showerror("保存失败", message, parent=self)
                return
        except OSError as exc:
            messagebox.showerror("保存失败", str(exc), parent=self)
            return
        self.parent_tab.app.reload_environment()
        self.parent_tab.refresh()
        self.destroy()
        messagebox.showinfo(
            "已添加",
            "已添加音色“%s”。\n备份：%s\n\n现在可以在文档里写这个角色名了。" % (voice.key, backup),
            parent=self.parent_tab,
        )


class AddManualDialog(tk.Toplevel):
    def __init__(self, parent: VoicesTab, settings: Any, registry: Any) -> None:
        super().__init__(parent)
        self.parent_tab = parent
        self.settings = settings
        self.registry = registry
        self.title("手动添加音色")
        self.configure(bg=CARD)
        self.transient(parent.winfo_toplevel())
        self.grab_set()
        self.resizable(False, False)
        center_window(self, 820, 650)
        self._build()

    def _build(self) -> None:
        card = make_card(self, padding=18)
        card.pack(fill="both", expand=True, padx=12, pady=12)
        card.columnconfigure(1, weight=1)

        header = section_header(card, "手动添加音色", "选择 GPT/SoVITS 权重，再选一段参考音频并填写参考台词。")
        header.grid(row=0, column=0, columnspan=3, sticky="ew", pady=(0, 10))

        ttk.Label(card, text="角色名", style="Card.TLabel").grid(row=1, column=0, sticky="w", pady=5)
        self.key_var = tk.StringVar()
        ttk.Entry(card, textvariable=self.key_var).grid(row=1, column=1, columnspan=2, sticky="ew", pady=5)

        ttk.Label(card, text="GPT 权重", style="Card.TLabel").grid(row=2, column=0, sticky="w", pady=5)
        self.gpt_var = tk.StringVar()
        ttk.Entry(card, textvariable=self.gpt_var).grid(row=2, column=1, sticky="ew", pady=5)
        ttk.Button(card, text="浏览…", style="Small.TButton", command=self._browse_gpt).grid(
            row=2, column=2, sticky="w", padx=(8, 0)
        )

        ttk.Label(card, text="SoVITS 权重", style="Card.TLabel").grid(row=3, column=0, sticky="w", pady=5)
        self.sovits_var = tk.StringVar()
        ttk.Entry(card, textvariable=self.sovits_var).grid(row=3, column=1, sticky="ew", pady=5)
        ttk.Button(card, text="浏览…", style="Small.TButton", command=self._browse_sovits).grid(
            row=3, column=2, sticky="w", padx=(8, 0)
        )

        ttk.Label(card, text="版本", style="Card.TLabel").grid(row=4, column=0, sticky="w", pady=5)
        self.version_var = tk.StringVar(value="选择 SoVITS 权重后自动识别")
        ttk.Label(card, textvariable=self.version_var, style="CardHint.TLabel").grid(
            row=4, column=1, columnspan=2, sticky="w", pady=5
        )

        ttk.Label(card, text="参考音频", style="Card.TLabel").grid(row=5, column=0, sticky="w", pady=5)
        self.ref_var = tk.StringVar()
        ttk.Entry(card, textvariable=self.ref_var).grid(row=5, column=1, sticky="ew", pady=5)
        ttk.Button(card, text="浏览…", style="Small.TButton", command=self._browse_ref).grid(
            row=5, column=2, sticky="w", padx=(8, 0)
        )

        ttk.Label(card, text="参考台词", style="Card.TLabel").grid(row=6, column=0, sticky="nw", pady=5)
        self.prompt_text = tk.Text(
            card,
            height=4,
            wrap="word",
            font=(FONT, 10),
            bg="#ffffff",
            fg=TEXT,
            relief="solid",
            bd=1,
            padx=8,
            pady=6,
        )
        self.prompt_text.grid(row=6, column=1, columnspan=2, sticky="ew", pady=5)
        ttk.Label(
            card,
            text="必须和参考音频里说出的内容完全一致。带情绪标签的音频，建议只填标签后真正说出的部分。",
            style="CardHint.TLabel",
            wraplength=700,
        ).grid(row=7, column=1, columnspan=2, sticky="w")

        ttk.Label(card, text="参考语言", style="Card.TLabel").grid(row=8, column=0, sticky="w", pady=5)
        self.prompt_lang_var = tk.StringVar(value="中文")
        ttk.Combobox(
            card,
            textvariable=self.prompt_lang_var,
            values=[item[0] for item in _MANUAL_LANG_OPTIONS],
            state="readonly",
            width=20,
        ).grid(row=8, column=1, sticky="w", pady=5)

        ttk.Label(card, text="台词语言", style="Card.TLabel").grid(row=9, column=0, sticky="w", pady=5)
        self.text_lang_var = tk.StringVar(value="中文")
        ttk.Combobox(
            card,
            textvariable=self.text_lang_var,
            values=[item[0] for item in _MANUAL_LANG_OPTIONS],
            state="readonly",
            width=20,
        ).grid(row=9, column=1, sticky="w", pady=5)

        ttk.Label(card, text="输出目录", style="Card.TLabel").grid(row=10, column=0, sticky="w", pady=5)
        self.output_var = tk.StringVar()
        ttk.Entry(card, textvariable=self.output_var).grid(row=10, column=1, columnspan=2, sticky="ew", pady=5)
        ttk.Label(card, text="留空时使用角色名。", style="CardHint.TLabel").grid(
            row=11, column=1, columnspan=2, sticky="w"
        )

        ttk.Label(card, text="别名", style="Card.TLabel").grid(row=12, column=0, sticky="w", pady=5)
        self.alias_var = tk.StringVar()
        ttk.Entry(card, textvariable=self.alias_var).grid(row=12, column=1, columnspan=2, sticky="ew", pady=5)
        ttk.Label(card, text="用逗号分隔；文档里写任意一个都能匹配。", style="CardHint.TLabel").grid(
            row=13, column=1, columnspan=2, sticky="w"
        )

        footer = tk.Frame(card, bg=CARD)
        footer.grid(row=14, column=0, columnspan=3, sticky="e", pady=(14, 0))
        ttk.Button(footer, text="取消", command=self.destroy).pack(side="right")
        ttk.Button(footer, text="保存并校验", style="Primary.TButton", command=self._save).pack(
            side="right", padx=(0, 8)
        )

    def _browse_gpt(self) -> None:
        path = filedialog.askopenfilename(
            title="选择 GPT 权重（.ckpt）",
            filetypes=[("GPT 权重", "*.ckpt"), ("所有文件", "*.*")],
            parent=self,
        )
        if path:
            self.gpt_var.set(path)
            if not self.key_var.get().strip():
                self.key_var.set(Path(path).stem)

    def _browse_sovits(self) -> None:
        path = filedialog.askopenfilename(
            title="选择 SoVITS 权重（.pth）",
            filetypes=[("SoVITS 权重", "*.pth"), ("所有文件", "*.*")],
            parent=self,
        )
        if path:
            self.sovits_var.set(path)
            self._refresh_version()
            if not self.key_var.get().strip():
                self.key_var.set(Path(path).stem)

    def _browse_ref(self) -> None:
        path = filedialog.askopenfilename(
            title="选择参考音频",
            filetypes=[
                ("音频", "*.wav *.mp3 *.flac *.m4a *.ogg *.aac"),
                ("所有文件", "*.*"),
            ],
            parent=self,
        )
        if path:
            self.ref_var.set(path)

    def _refresh_version(self) -> None:
        raw = self.sovits_var.get().strip()
        if not raw:
            self.version_var.set("选择 SoVITS 权重后自动识别")
            return
        try:
            path = Path(raw).expanduser()
            if not path.is_absolute():
                path = self.settings.workspace / path
            if path.is_file():
                self.version_var.set("自动识别：%s" % (detect_version(path) or "未知"))
            else:
                self.version_var.set("文件不存在，保存时会报错")
        except Exception:  # noqa: BLE001
            self.version_var.set("自动识别失败，保存时会再次校验")

    def _save(self) -> None:
        key = self.key_var.get().strip()
        if not key:
            messagebox.showwarning("缺少角色名", "请填写角色名。", parent=self)
            return
        try:
            gpt = resolve_user_path(self.gpt_var.get(), self.settings, "GPT 权重", expected_ext=".ckpt")
            sovits = resolve_user_path(self.sovits_var.get(), self.settings, "SoVITS 权重", expected_ext=".pth")
            ref = resolve_user_path(self.ref_var.get(), self.settings, "参考音频")
        except VoiceError as exc:
            messagebox.showerror("路径有误", str(exc), parent=self)
            return
        prompt = self.prompt_text.get("1.0", "end").strip()
        if not prompt:
            messagebox.showwarning("缺少参考台词", "请填写参考音频里说的话。", parent=self)
            return
        prompt_lang = self._lang_code(self.prompt_lang_var.get(), _MANUAL_LANG_OPTIONS)
        text_lang = self._lang_code(self.text_lang_var.get(), _MANUAL_LANG_OPTIONS)
        draft = VoiceDraft(
            key=key,
            gpt=str(gpt),
            sovits=str(sovits),
            ref_audio=str(ref),
            prompt_text=prompt,
            prompt_lang=prompt_lang,
            text_lang=text_lang,
            output_dir=self.output_var.get().strip() or None,
            aliases=_split_aliases(self.alias_var.get()),
        )
        ensure_draft_defaults(self.settings, draft)
        self._commit(draft)

    @staticmethod
    def _lang_code(label: str, options: List[tuple]) -> str:
        for text, code in options:
            if text == label:
                return code or "zh"
        return "zh"

    def _commit(self, draft: VoiceDraft) -> None:
        try:
            _data, _text, voice, backup = write_voice(self.settings, draft)
        except VoiceError as exc:
            message = str(exc)
            if "已存在" in message or "exists" in message.lower():
                if not messagebox.askyesno("角色名重复", "%s\n\n要覆盖它吗？" % message, parent=self):
                    return
                try:
                    _data, _text, voice, backup = write_voice(self.settings, draft, force=True)
                except (VoiceError, OSError) as inner:
                    messagebox.showerror("保存失败", str(inner), parent=self)
                    return
            else:
                messagebox.showerror("保存失败", message, parent=self)
                return
        except OSError as exc:
            messagebox.showerror("保存失败", str(exc), parent=self)
            return
        self.parent_tab.app.reload_environment()
        self.parent_tab.refresh()
        self.destroy()
        messagebox.showinfo(
            "已添加",
            "已添加音色“%s”（%s）。\n备份：%s\n\n现在可以在文档里写这个角色名了。"
            % (voice.key, voice.version, backup),
            parent=self.parent_tab,
        )
