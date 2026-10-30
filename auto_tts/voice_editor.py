# -*- coding: utf-8 -*-
"""音色添加、扫描与配置写入。

这个模块只负责“编辑 voices.yaml”，不启动 GPU、不修改安装目录。
交互式提问放在 gptsovits_auto.py 的 voices add 命令里，这里提供可测试的
发现/校验/写入逻辑。
"""
from __future__ import annotations

import os
import re
import shutil
import tempfile
import time
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

import yaml

from .settings import Settings, SettingsError, is_within
from .voices import (
    ModelIndex,
    Voice,
    VoiceError,
    VoiceRegistry,
    detect_version,
    infer_version_from_path,
    load_registry,
    normalize_lang,
    read_voices_data,
    scan_presets,
)


# 训练权重里常见的基础模型/声码器，不应当被当成“可添加的音色”。
_BASE_MODEL_RE = re.compile(
    r"(^|[\\/])(s1|s2|s2g|s2gv4|vocoder|pretrained|gsv-v4|chinese-hubert|"
    r"chinese-roberta|bert|hubert)",
    re.IGNORECASE,
)
_AUDIO_EXTS = (".wav", ".mp3", ".flac", ".m4a", ".ogg", ".aac")


@dataclass
class VoiceDraft:
    """等待写入 voices.yaml 的一条音色定义。"""

    key: str
    preset: Optional[str] = None
    gpt: Optional[Path] = None
    sovits: Optional[Path] = None
    version: Optional[str] = None
    ref_audio: Optional[Path] = None
    aux_refs: List[Path] = field(default_factory=list)
    prompt_text: Optional[str] = None
    prompt_lang: Optional[str] = None
    text_lang: Optional[str] = None
    output_dir: Optional[str] = None
    aliases: List[str] = field(default_factory=list)
    speed_factor: Optional[float] = None
    seed: Optional[int] = None
    aux_ref: Optional[bool] = None

    def __post_init__(self) -> None:
        """CLI 传 Path，GUI 传 str；统一成 Path，避免 plan_voice 里出错。"""
        for name in ("gpt", "sovits", "ref_audio"):
            value = getattr(self, name)
            if value is None:
                continue
            text = str(value).strip()
            setattr(self, name, Path(text) if text else None)
        refs: List[Path] = []
        for item in self.aux_refs:
            text = str(item).strip()
            if text:
                refs.append(Path(text))
        self.aux_refs = refs

    def clean_key(self) -> str:
        return str(self.key or "").strip()

    def definition(self) -> Dict[str, Any]:
        data: Dict[str, Any] = {}
        if self.preset:
            data["preset"] = str(self.preset).strip()
        if self.gpt:
            data["gpt"] = str(self.gpt)
        if self.sovits:
            data["sovits"] = str(self.sovits)
        if self.version:
            data["version"] = str(self.version)
        if self.ref_audio:
            data["ref_audio"] = str(self.ref_audio)
        if self.aux_refs:
            data["aux_ref_audio_paths"] = [str(p) for p in self.aux_refs]
        if self.aux_ref is not None:
            data["aux_ref"] = bool(self.aux_ref)
        if self.prompt_text is not None:
            data["prompt_text"] = str(self.prompt_text)
        if self.prompt_lang is not None:
            data["prompt_lang"] = str(self.prompt_lang)
        if self.text_lang is not None:
            data["text_lang"] = str(self.text_lang)
        output_dir = str(self.output_dir or "").strip()
        if not output_dir:
            output_dir = re.sub(r"[（(].*$", "", self.clean_key()).strip() or self.clean_key()
        data["output_dir"] = output_dir
        aliases = []
        for alias in self.aliases:
            text = str(alias).strip()
            if text and text != self.clean_key() and text not in aliases:
                aliases.append(text)
        if aliases:
            data["aliases"] = aliases
        if self.speed_factor is not None:
            data["speed_factor"] = float(self.speed_factor)
        if self.seed is not None:
            data["seed"] = int(self.seed)
        return data


@dataclass(frozen=True)
class ModelEntry:
    path: Path
    kind: str  # "gpt" / "sovits"
    version: Optional[str]
    source: str

    def short(self, settings: Settings) -> str:
        return short_path(self.path, settings)


@dataclass
class ModelPair:
    key: str
    version: Optional[str]
    gpt: ModelEntry
    sovits: ModelEntry
    configured: List[str] = field(default_factory=list)

    @property
    def label(self) -> str:
        return " + ".join(p.stem for p in (self.gpt.path, self.sovits.path))


@dataclass
class ModelPack:
    root: Path
    models: List[ModelEntry]
    refs: List[Path]
    prompt_hint: str = ""
    weight_files: List[Path] = field(default_factory=list)


@dataclass
class ModelCatalog:
    entries: List[ModelEntry]
    pairs: List[ModelPair]
    unpaired: List[ModelEntry]
    packs: List[ModelPack]
    ambiguous: List[Tuple[str, List[ModelEntry]]] = field(default_factory=list)


@dataclass
class PresetInfo:
    name: str
    paths: List[Path]
    configured: List[str] = field(default_factory=list)


def short_path(path: Path, settings: Settings, limit: Optional[int] = None) -> str:
    """尽量显示相对路径，方便在控制台里阅读。"""
    resolved = Path(os.path.abspath(str(path)))
    roots: Sequence[Tuple[Path, str]] = [
        (settings.workspace, ""),
        (settings.install_root, "<install>/"),
    ]
    for root in settings.extra_model_roots:
        roots.append((root, root.name + "/"))
    text = str(resolved)
    for root, prefix in roots:
        try:
            if is_within(resolved, root):
                rel = os.path.relpath(str(resolved), str(root))
                text = prefix + rel.replace("\\", "/")
                break
        except ValueError:
            continue
    if limit and len(text) > limit:
        return text[: max(4, limit - 3)] + "..."
    return text


def resolve_user_path(
    value: Any,
    settings: Settings,
    label: str,
    must_exist: bool = True,
    expected_ext: Optional[Sequence[str]] = None,
) -> Path:
    """解析用户在 Windows 上输入/拖入的路径（允许带引号）。"""
    text = str(value or "").strip()
    if len(text) >= 2 and text[0] == text[-1] and text[0] in ("'", '"'):
        text = text[1:-1].strip()
    if not text:
        raise VoiceError("%s 不能为空" % label)
    path = Path(os.path.expandvars(text)).expanduser()
    candidates: List[Path] = []
    if path.is_absolute():
        candidates.append(path)
    else:
        candidates.extend(
            [
                Path.cwd() / path,
                settings.workspace / path,
                settings.install_root / path,
            ]
        )
    allowed_ext = (
        [expected_ext]
        if isinstance(expected_ext, str)
        else list(expected_ext or [])
    )
    for candidate in candidates:
        if candidate.exists() and candidate.is_file():
            if allowed_ext and candidate.suffix.lower() not in {
                str(ext).lower() for ext in allowed_ext
            }:
                raise VoiceError(
                    "%s 扩展名应为 %s：%s"
                    % (label, "/".join(allowed_ext), candidate)
                )
            return Path(os.path.abspath(str(candidate)))
    if not must_exist:
        target = candidates[0] if candidates else path
        return Path(os.path.abspath(str(target)))
    raise VoiceError("%s 文件不存在：%s" % (label, text))


def resolve_output_dir(value: Any, key: str) -> str:
    text = str(value or "").strip()
    if text:
        return text
    return re.sub(r"[（(].*$", "", key).strip() or key


def validate_key(key: str) -> str:
    text = str(key or "").strip()
    if not text:
        raise VoiceError("角色名不能为空")
    if len(text) > 60:
        raise VoiceError("角色名太长（最多 60 个字符）：%s" % text)
    if any(ch in text for ch in ("\r", "\n", "\t", ":", "：", "|")):
        raise VoiceError("角色名不能包含换行、冒号或竖线：%s" % text)
    return text


def _is_base_model(path: Path) -> bool:
    return bool(_BASE_MODEL_RE.search(str(path)))


def _model_version(path: Path) -> Optional[str]:
    if path.suffix.lower() == ".pth":
        try:
            return detect_version(path)
        except VoiceError:
            return infer_version_from_path(path)
    return infer_version_from_path(path)


def _model_source(path: Path, settings: Settings) -> str:
    if is_within(path, settings.install_root):
        return "install"
    for root in settings.extra_model_roots:
        if is_within(path, root):
            return "extra"
    return "other"


def pair_key(stem: str) -> str:
    """把 dania-e15 / dania_e16_s2192 归一成 dania，用于配对。"""
    text = unicodedata.normalize("NFKC", str(stem or "")).lower()
    text = re.sub(r"\.(ckpt|pth)$", "", text)
    text = re.sub(r"(?i)[-_]?(gpt|sovits|model)$", "", text)
    text = re.sub(r"(?i)[-_]?e\d+", "", text)
    text = re.sub(r"(?i)[-_]?s\d+", "", text)
    text = re.sub(r"(?i)[-_]?l\d+", "", text)
    text = re.sub(r"(?i)[-_]?(v2pp|v2proplus|v2pro|v3|v4)", "", text)
    text = re.sub(r"[\s._-]+", "_", text).strip("_")
    return text or unicodedata.normalize("NFKC", str(stem or "")).lower()


def _display_stem(entry: ModelEntry) -> str:
    return re.sub(
        r"(?i)[-_](?:e\d+|s\d+|l\d+)(?=$|[-_])",
        "",
        entry.path.stem,
    )


def _dedupe_entries(entries: List[ModelEntry]) -> List[ModelEntry]:
    """模型包和安装目录常有同名拷贝，优先保留安装目录中的那一份。"""
    unique: Dict[Tuple[str, str, int], ModelEntry] = {}
    order: List[Tuple[str, str, int]] = []
    for entry in entries:
        try:
            size = entry.path.stat().st_size
        except OSError:
            size = -1
        key = (entry.kind, entry.path.name.lower(), size)
        previous = unique.get(key)
        if previous is None:
            unique[key] = entry
            order.append(key)
        elif previous.source != "install" and entry.source == "install":
            unique[key] = entry
    return [unique[key] for key in order]


def discover_models(settings: Settings, registry: Optional[VoiceRegistry] = None) -> ModelCatalog:
    index = ModelIndex(settings)
    entries: List[ModelEntry] = []
    for path in index.entries():
        if not path.is_file() or path.suffix.lower() not in (".ckpt", ".pth"):
            continue
        if _is_base_model(path):
            continue
        kind = "gpt" if path.suffix.lower() == ".ckpt" else "sovits"
        entries.append(
            ModelEntry(
                path=path,
                kind=kind,
                version=_model_version(path),
                source=_model_source(path, settings),
            )
        )
    entries = _dedupe_entries(entries)

    groups: Dict[str, Dict[str, List[ModelEntry]]] = {}
    for entry in entries:
        key = pair_key(entry.path.stem)
        groups.setdefault(key, {"gpt": [], "sovits": []})[entry.kind].append(entry)

    pairs: List[ModelPair] = []
    unpaired: List[ModelEntry] = []
    ambiguous: List[Tuple[str, List[ModelEntry]]] = []
    for key, group in sorted(groups.items(), key=lambda item: item[0]):
        gpts = group["gpt"]
        sovits = group["sovits"]
        if len(gpts) == 1 and len(sovits) == 1:
            gpt, sov = gpts[0], sovits[0]
            versions = {v for v in (gpt.version, sov.version) if v}
            version = next(iter(versions)) if len(versions) == 1 else None
            pairs.append(ModelPair(key=key, version=version, gpt=gpt, sovits=sov))
        else:
            unpaired.extend(gpts)
            unpaired.extend(sovits)
            if gpts or sovits:
                ambiguous.append((key, gpts + sovits))

    packs = _discover_packs(settings, entries)
    catalog = ModelCatalog(
        entries=entries,
        pairs=pairs,
        unpaired=unpaired,
        packs=packs,
        ambiguous=ambiguous,
    )
    if registry is not None:
        mark_configured(catalog, registry)
    return catalog


def mark_configured(catalog: ModelCatalog, registry: VoiceRegistry) -> None:
    by_gpt: Dict[str, List[str]] = {}
    by_sovits: Dict[str, List[str]] = {}
    for voice in registry.list():
        by_gpt.setdefault(os.path.normcase(os.path.abspath(str(voice.gpt))), []).append(voice.key)
        by_sovits.setdefault(
            os.path.normcase(os.path.abspath(str(voice.sovits))), []
        ).append(voice.key)
    for pair in catalog.pairs:
        left = set(by_gpt.get(os.path.normcase(str(pair.gpt.path)), []))
        right = set(by_sovits.get(os.path.normcase(str(pair.sovits.path)), []))
        pair.configured = sorted(left & right)


def discover_presets(settings: Settings, registry: Optional[VoiceRegistry] = None) -> List[PresetInfo]:
    index = scan_presets(settings)
    configured: Dict[str, List[str]] = {}
    if registry is not None:
        for voice in registry.list():
            if voice.preset:
                configured.setdefault(voice.preset, []).append(voice.key)
    return [
        PresetInfo(name=name, paths=paths, configured=sorted(configured.get(name, [])))
        for name, paths in sorted(index.items())
    ]


def _read_text(path: Path) -> str:
    for encoding in ("utf-8-sig", "utf-8", "gb18030"):
        try:
            return path.read_text(encoding=encoding)
        except UnicodeDecodeError:
            continue
        except OSError:
            return ""
    return ""


def _prompt_hint(pack_root: Path) -> str:
    """从模型包的 README/asr.txt 里找一个可能的参考文本，仅作提示。"""
    candidates: List[Path] = []
    for pattern in ("asr.txt", "README.md", "readme.md", "README.txt", "readme.txt"):
        candidates.extend(sorted(pack_root.rglob(pattern)))
    for path in candidates[:10]:
        text = _read_text(path)
        for raw in text.splitlines():
            line = raw.strip().strip("`>*- \t")
            if not line or line.startswith(("#", "|", "```", "http")):
                continue
            lowered = line.lower()
            if any(token in lowered for token in ("prompt", "参考文本", "台词", "文本：")):
                for sep in ("：", ":"):
                    if sep in line:
                        value = line.split(sep, 1)[1].strip().strip("“”\"'` ")
                        if 2 <= len(value) <= 300:
                            return value
            if "\u4e00" <= line[0] <= "\u9fff" or "\u3040" <= line[0] <= "\u30ff":
                if 4 <= len(line) <= 200:
                    return line
    return ""


def _discover_packs(settings: Settings, entries: List[ModelEntry]) -> List[ModelPack]:
    packs: List[ModelPack] = []
    for extra_root in settings.extra_model_roots:
        if not extra_root.exists() or not extra_root.is_dir():
            continue
        roots: List[Path] = []
        direct_weights = any(extra_root.glob("*.ckpt")) or any(extra_root.glob("*.pth"))
        if direct_weights:
            roots.append(extra_root)
        else:
            for child in sorted(extra_root.iterdir()):
                if not child.is_dir():
                    continue
                if any(child.rglob("*.ckpt")) or any(child.rglob("*.pth")):
                    roots.append(child)
        for root in roots:
            weight_files = [
                path
                for path in root.rglob("*")
                if path.is_file()
                and path.suffix.lower() in (".ckpt", ".pth")
                and not _is_base_model(path)
            ]
            if not weight_files:
                continue
            models = [
                entry
                for entry in entries
                if is_within(entry.path, root)
            ]
            refs: List[Path] = []
            for path in sorted(root.rglob("*")):
                if path.is_file() and path.suffix.lower() in _AUDIO_EXTS:
                    if _is_base_model(path):
                        continue
                    refs.append(path)
                    if len(refs) >= 12:
                        break
            packs.append(
                ModelPack(
                    root=root,
                    models=models,
                    refs=refs,
                    prompt_hint=_prompt_hint(root),
                    weight_files=weight_files,
                )
            )
    return packs


def _yaml_text(data: Dict[str, Any]) -> str:
    header = "# 由 Gptsovit Auto 的 voices add/remove 维护；可以手动编辑。\n"
    return header + yaml.safe_dump(
        data,
        allow_unicode=True,
        sort_keys=False,
        default_flow_style=False,
        width=120,
    )


def _validate_voices_data(
    settings: Settings,
    data: Dict[str, Any],
    target: Path,
) -> VoiceRegistry:
    target.parent.mkdir(parents=True, exist_ok=True)
    settings.check_writable(target.parent / ".voices-validate.yaml", "校验音色配置")
    fd, name = tempfile.mkstemp(
        prefix=".voices-validate-",
        suffix=".yaml",
        dir=str(target.parent),
    )
    os.close(fd)
    temp_path = Path(name)
    try:
        temp_path.write_text(_yaml_text(data), encoding="utf-8")
        return load_registry(settings, temp_path)
    finally:
        try:
            temp_path.unlink()
        except OSError:
            pass


def _backup(target: Path, settings: Settings) -> Optional[Path]:
    if not target.exists():
        return None
    backup_dir = target.parent / "backups"
    backup_dir.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    backup = backup_dir / ("%s-%s.yaml" % (target.stem, stamp))
    suffix = 1
    while backup.exists():
        suffix += 1
        backup = backup_dir / ("%s-%s-%d.yaml" % (target.stem, stamp, suffix))
    settings.check_writable(backup, "备份音色配置")
    shutil.copy2(str(target), str(backup))
    backups = sorted(backup_dir.glob("%s-*.yaml" % target.stem))
    for old in backups[:-20]:
        try:
            old.unlink()
        except OSError:
            pass
    return backup


def _atomic_write(target: Path, text: str, settings: Settings) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    settings.check_writable(target, "写入音色配置")
    fd, name = tempfile.mkstemp(
        prefix=".voices-write-",
        suffix=".yaml",
        dir=str(target.parent),
    )
    os.close(fd)
    temp_path = Path(name)
    try:
        with temp_path.open("w", encoding="utf-8", newline="\n") as fh:
            fh.write(text)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(str(temp_path), str(target))
    finally:
        try:
            temp_path.unlink()
        except OSError:
            pass


def plan_voice(
    settings: Settings,
    draft: VoiceDraft,
    target: Optional[Path] = None,
    force: bool = False,
) -> Tuple[Dict[str, Any], str, Voice]:
    """校验并返回（完整 YAML 数据, YAML 文本, 新音色对象），不写文件。"""
    key = validate_key(draft.clean_key())
    draft.key = key
    target = Path(os.path.abspath(str(target or settings.voices_path)))
    settings.check_writable(target, "写入音色配置")
    if target.exists():
        data = read_voices_data(target)
    else:
        data = {"voices": {}}
    definitions = data.get("voices")
    if not isinstance(definitions, dict):
        raise VoiceError("音色配置缺少 voices: 映射：%s" % target)
    if key in definitions and not force:
        raise VoiceError("音色 %s 已存在；要覆盖请加 --force" % key)

    if not draft.preset and (not draft.gpt or not draft.sovits):
        raise VoiceError("手动添加音色时必须同时提供 GPT(.ckpt) 和 SoVITS(.pth)")
    if draft.gpt and not draft.gpt.is_file():
        raise VoiceError("GPT 权重不存在：%s" % draft.gpt)
    if draft.sovits and not draft.sovits.is_file():
        raise VoiceError("SoVITS 权重不存在：%s" % draft.sovits)
    if draft.ref_audio and not draft.ref_audio.is_file():
        raise VoiceError("参考音频不存在：%s" % draft.ref_audio)
    for aux in draft.aux_refs:
        if not aux.is_file():
            raise VoiceError("辅助参考音频不存在：%s" % aux)
    if draft.speed_factor is not None and not (0.1 < float(draft.speed_factor) <= 4.0):
        raise VoiceError("语速必须在 0.1 到 4.0 之间")
    if draft.prompt_lang is not None:
        draft.prompt_lang = normalize_lang(draft.prompt_lang, "zh")
    if draft.text_lang is not None:
        draft.text_lang = normalize_lang(draft.text_lang, "zh")
    definitions[key] = draft.definition()
    registry = _validate_voices_data(settings, data, target)
    voice = registry.get(key)
    if voice is None:
        raise VoiceError("音色校验失败：%s" % key)
    return data, _yaml_text(data), voice


def write_voice(
    settings: Settings,
    draft: VoiceDraft,
    target: Optional[Path] = None,
    force: bool = False,
    dry_run: bool = False,
) -> Tuple[Dict[str, Any], str, Voice, Optional[Path]]:
    """校验并写入 voices.yaml；返回（数据, YAML, 音色, 备份路径）。"""
    target = Path(os.path.abspath(str(target or settings.voices_path)))
    data, text, voice = plan_voice(settings, draft, target=target, force=force)
    if dry_run:
        return data, text, voice, None
    backup = _backup(target, settings)
    _atomic_write(target, text, settings)
    return data, text, voice, backup


def remove_voice(
    settings: Settings,
    key: str,
    target: Optional[Path] = None,
    dry_run: bool = False,
) -> Tuple[Dict[str, Any], str, Optional[Path]]:
    key = str(key or "").strip()
    target = Path(os.path.abspath(str(target or settings.voices_path)))
    settings.check_writable(target, "写入音色配置")
    if not target.exists():
        raise VoiceError("音色配置文件不存在：%s" % target)
    data = read_voices_data(target)
    definitions = data.get("voices")
    if key not in definitions:
        raise VoiceError("没有这个音色：%s" % key)
    if len(definitions) <= 1:
        raise VoiceError("至少保留一个音色，不能删除最后一个")
    del definitions[key]
    registry = _validate_voices_data(settings, data, target)
    if registry.get(key) is not None:
        raise VoiceError("删除失败：%s" % key)
    text = _yaml_text(data)
    if dry_run:
        return data, text, None
    backup = _backup(target, settings)
    _atomic_write(target, text, settings)
    return data, text, backup


def format_voice_summary(voice: Voice) -> str:
    return (
        "角色名: %s\n"
        "预设: %s\n"
        "模型: %s\n"
        "      %s\n"
        "版本: %s\n"
        "参考音频: %s\n"
        "参考文本: %s\n"
        "参考语言: %s | 台词语言: %s\n"
        "输出目录: %s\n"
        "别名: %s"
        % (
            voice.key,
            voice.preset or "-",
            voice.gpt,
            voice.sovits,
            voice.version,
            voice.ref_audio,
            voice.prompt_text or "（空）",
            voice.prompt_lang,
            voice.text_lang,
            voice.output_dir,
            "、".join(voice.aliases) or "-",
        )
    )


def suggest_key(entry: ModelEntry) -> str:
    text = _display_stem(entry)
    text = re.sub(r"(?i)[-_](gpt|sovits|model)$", "", text)
    return text.strip(" _-") or entry.path.stem


def ensure_draft_defaults(settings: Settings, draft: VoiceDraft) -> VoiceDraft:
    """给非交互命令补上输出目录/语言等默认值。"""
    if not draft.output_dir:
        draft.output_dir = resolve_output_dir(None, draft.clean_key())
    # 预设音色默认继承 metadata 的 prompt_language/text_lang；手动音色才补默认值。
    if draft.preset is None:
        if draft.prompt_lang is None:
            draft.prompt_lang = str(settings.defaults.get("text_lang", "zh"))
        if draft.text_lang is None:
            draft.text_lang = str(settings.defaults.get("text_lang", "zh"))
    if draft.prompt_lang is not None:
        draft.prompt_lang = normalize_lang(draft.prompt_lang, "zh")
    if draft.text_lang is not None:
        draft.text_lang = normalize_lang(draft.text_lang, "zh")
    if draft.speed_factor is None and draft.preset is None:
        try:
            draft.speed_factor = float(settings.defaults.get("speed_factor", 1.0))
        except (TypeError, ValueError):
            draft.speed_factor = 1.0
    return draft
