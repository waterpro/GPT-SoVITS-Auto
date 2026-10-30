"""音色注册表：预设、权重、参考音频与别名解析。"""

from __future__ import annotations

import json
import os
import re
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

import yaml

from .settings import Settings, SettingsError


class VoiceError(Exception):
    """音色配置错误。"""


VERSIONS = ("v1", "v2", "v3", "v4", "v2Pro", "v2ProPlus")
_V1_LANGS = {"auto", "en", "zh", "ja", "all_zh", "all_ja"}
_V2_LANGS = {
    "auto",
    "auto_yue",
    "en",
    "zh",
    "ja",
    "yue",
    "ko",
    "all_zh",
    "all_ja",
    "all_yue",
    "all_ko",
}
_LANG_NAMES = {
    "中文": "zh",
    "汉语": "zh",
    "国语": "zh",
    "普通话": "zh",
    "日文": "ja",
    "日语": "ja",
    "英文": "en",
    "英语": "en",
    "韩文": "ko",
    "韩语": "ko",
    "粤语": "yue",
    "广东话": "yue",
    "自动": "auto",
    "chinese": "zh",
    "japanese": "ja",
    "english": "en",
    "korean": "ko",
    "cantonese": "yue",
    "zh-cn": "zh",
    "zh_cn": "zh",
    "jp": "ja",
}
_HEADER_VERSION = {
    b"00": "v1",
    b"01": "v2",
    b"02": "v3",
    b"03": "v3",
    b"04": "v4",
    b"05": "v2Pro",
    b"06": "v2ProPlus",
}


def normalize_lang(value: Any, default: str = "zh") -> str:
    text = str(value or "").strip()
    if not text:
        return default
    lowered = text.lower()
    if lowered in _LANG_NAMES:
        return _LANG_NAMES[lowered]
    if text in _LANG_NAMES:
        return _LANG_NAMES[text]
    if lowered in _V2_LANGS:
        return lowered
    raise VoiceError("不支持的语言：%s（可用：%s）" % (text, ", ".join(sorted(_V2_LANGS))))


def version_langs(version: str) -> set:
    return set(_V1_LANGS if version == "v1" else _V2_LANGS)


def validate_lang(version: str, lang: str, field_name: str) -> None:
    allowed = version_langs(version)
    if lang not in allowed:
        raise VoiceError(
            "%s=%s 与模型版本 %s 不兼容（可用：%s）"
            % (field_name, lang, version, ", ".join(sorted(allowed)))
        )


def normalize_name(value: str) -> str:
    text = unicodedata.normalize("NFKC", str(value or ""))
    text = text.strip().lstrip("@").strip()
    text = re.sub(r"[*`#]", "", text)
    text = re.sub(r"\s+", "", text)
    return text.lower()


def infer_version_from_path(path: Path) -> Optional[str]:
    # 只看文件名和它附近的 2 层目录，避免把安装目录名里的 “v2pro”
    # 误当成权重版本（例如 ...\GPT-SoVITS-v2pro-...\GPT_weights\x.ckpt）。
    parts = list(reversed(Path(path).parts))[:3]
    for part in parts:
        lowered = part.lower()
        if "v2proplus" in lowered:
            return "v2ProPlus"
        if "v2pro" in lowered:
            return "v2Pro"
        if re.search(r"(^|_)v4($|_)", lowered):
            return "v4"
        if re.search(r"(^|_)v3($|_)", lowered):
            return "v3"
        if re.search(r"(^|_)v2($|_)", lowered):
            return "v2"
        if re.search(r"(^|_)v1($|_)", lowered):
            return "v1"
    return None


def detect_version(sovits_path: Path, explicit: Optional[str] = None) -> str:
    if explicit:
        explicit = str(explicit).strip()
        if explicit not in VERSIONS:
            raise VoiceError("未知模型版本：%s" % explicit)
    try:
        with Path(sovits_path).open("rb") as fh:
            head = fh.read(2)
    except OSError as exc:
        raise VoiceError("无法读取 SoVITS 权重 %s：%s" % (sovits_path, exc)) from exc
    detected = _HEADER_VERSION.get(head)
    if detected is None:
        detected = infer_version_from_path(Path(sovits_path))
    if explicit and detected and explicit != detected:
        raise VoiceError(
            "SoVITS 文件头/路径显示版本 %s，但配置写了 %s：%s"
            % (detected, explicit, sovits_path)
        )
    version = explicit or detected
    if version not in VERSIONS:
        raise VoiceError(
            "无法判断模型版本，请在 voices.yaml 中写 version: %s（%s）"
            % ("/".join(VERSIONS), sovits_path)
        )
    return version


def _resolve_existing_file(value: Any, voices_dir: Path, settings: Settings, label: str) -> Path:
    text = str(value or "").strip()
    if not text:
        raise VoiceError("%s 不能为空" % label)
    path = Path(os.path.expandvars(text)).expanduser()
    candidates: List[Path] = []
    if path.is_absolute():
        candidates.append(path)
    else:
        candidates.extend(
            [
                voices_dir / path,
                settings.install_root / path,
                settings.workspace / path,
            ]
        )
    for candidate in candidates:
        if candidate.exists() and candidate.is_file():
            return Path(os.path.abspath(str(candidate)))
    raise VoiceError("%s 文件不存在：%s" % (label, text))


@dataclass
class Voice:
    key: str
    aliases: List[str]
    preset: Optional[str]
    gpt: Path
    sovits: Path
    version: str
    ref_audio: Path
    aux_refs: List[Path]
    prompt_text: str
    prompt_lang: str
    text_lang: str
    text_lang_explicit: bool
    output_dir: str
    speed_factor: float
    seed: int
    enabled: bool = True
    definition: Dict[str, Any] = field(default_factory=dict)

    def summary(self) -> str:
        preset = self.preset or "-"
        return (
            "%s | %s | %s | ref=%s | prompt=%s | text=%s | out=%s"
            % (
                self.key,
                preset,
                self.version,
                self.ref_audio,
                self.prompt_lang,
                self.text_lang,
                self.output_dir,
            )
        )


class VoiceRegistry:
    def __init__(
        self,
        voices: Dict[str, Voice],
        alias_map: Dict[str, str],
        preset_index: Dict[str, List[Path]],
        settings: Settings,
    ):
        self.voices = voices
        self.alias_map = alias_map
        self.preset_index = preset_index
        self.settings = settings

    def get(self, key: str) -> Optional[Voice]:
        return self.voices.get(key)

    def resolve(self, name: str) -> Optional[Voice]:
        normalized = normalize_name(name)
        if not normalized:
            return None
        key = self.alias_map.get(normalized)
        if key:
            return self.voices.get(key)
        # 允许“角色(情绪)”写成“角色（情绪）”或“角色 情绪”
        match = re.match(r"^(.+?)[\(\[](..+?)[\)\]]$", normalized)
        if match:
            base, variant = match.group(1), match.group(2)
            for candidate in (
                "%s(%s)" % (base, variant),
                "%s%s" % (base, variant),
                "%s %s" % (base, variant),
                "%s-%s" % (base, variant),
            ):
                found = self.alias_map.get(candidate)
                if found:
                    return self.voices.get(found)
        return None

    def is_speaker(self, name: str) -> bool:
        return self.resolve(name) is not None

    def list(self) -> List[Voice]:
        return list(self.voices.values())

    def preset_paths(self, preset: str) -> List[Path]:
        return list(self.preset_index.get(str(preset).strip(), []))


class ModelIndex:
    """安装目录与额外模型根目录的权重索引。"""

    def __init__(self, settings: Settings):
        self.settings = settings
        self._index: Dict[str, List[Path]] = {}
        self._entries: List[Path] = []
        self._built = False

    def _add(self, path: Path) -> None:
        stem = path.stem.lower()
        resolved = Path(os.path.abspath(str(path)))
        self._index.setdefault(stem, []).append(resolved)
        self._entries.append(resolved)

    def build(self) -> None:
        if self._built:
            return
        roots: List[Path] = []
        if self.settings.install_root.exists():
            for pattern in ("GPT_weights*", "SoVITS_weights*"):
                roots.extend(sorted(self.settings.install_root.glob(pattern)))
        roots.extend(self.settings.extra_model_roots)
        seen = set()
        for root in roots:
            if not root.exists() or not root.is_dir():
                continue
            for ext in ("*.ckpt", "*.pth"):
                for path in root.rglob(ext):
                    if not path.is_file():
                        continue
                    key = str(path).lower()
                    if key in seen:
                        continue
                    seen.add(key)
                    self._add(path)
        self._built = True

    def entries(self) -> List[Path]:
        """返回所有扫描到的权重文件（安装目录 + extra_model_roots）。"""
        self.build()
        return list(self._entries)

    def find(self, value: str, expected_ext: str) -> Path:
        text = str(value or "").strip()
        if not text:
            raise VoiceError("权重路径不能为空")
        path = Path(os.path.expandvars(text)).expanduser()
        if path.is_absolute():
            if path.exists() and path.is_file():
                return Path(os.path.abspath(str(path)))
            raise VoiceError("权重文件不存在：%s" % text)
        # 明确写成了相对路径
        for base in (self.settings.workspace, self.settings.install_root):
            candidate = base / path
            if candidate.exists() and candidate.is_file():
                return Path(os.path.abspath(str(candidate)))
        if len(path.parts) > 1:
            raise VoiceError("权重文件不存在：%s" % text)
        stem = path.stem if path.suffix else text
        self.build()
        matches = list(self._index.get(stem.lower(), []))
        matches = [p for p in matches if p.suffix.lower() == expected_ext.lower()]
        if not matches:
            raise VoiceError(
                "找不到权重 %s（在安装目录权重目录或 extra_model_roots 中）" % text
            )
        if len(matches) > 1:
            # 同名时优先安装目录
            install_matches = [
                p for p in matches if p.is_relative_to(self.settings.install_root)
            ]
            if len(install_matches) == 1:
                return install_matches[0]
            raise VoiceError(
                "权重名 %s 有多个候选，请写绝对路径：\n%s"
                % (stem, "\n".join(str(p) for p in matches))
            )
        return matches[0]


def _load_yaml(path: Path) -> Dict[str, Any]:
    try:
        with Path(path).open("r", encoding="utf-8-sig") as fh:
            data = yaml.safe_load(fh) or {}
    except Exception as exc:  # noqa: BLE001
        raise VoiceError("读取音色配置失败 %s：%s" % (path, exc)) from exc
    if not isinstance(data, dict):
        raise VoiceError("音色配置顶层必须是 YAML 映射：%s" % path)
    return data


def read_voices_data(path: Path) -> Dict[str, Any]:
    """读取 voices.yaml 并校验顶层 voices 映射。"""
    data = _load_yaml(path)
    definitions = data.get("voices")
    if not isinstance(definitions, dict):
        raise VoiceError("音色配置缺少 voices: 映射：%s" % path)
    return data


def scan_presets(settings: Settings) -> Dict[str, List[Path]]:
    index: Dict[str, List[Path]] = {}
    root = settings.presets_root
    if not root.exists():
        raise VoiceError("预设目录不存在：%s" % root)
    for metadata in root.glob("*/*/*/metadata.json"):
        preset_dir = metadata.parent
        name = preset_dir.name
        index.setdefault(name, []).append(preset_dir)
    return index


def _select_preset(preset: str, index: Dict[str, List[Path]]) -> Path:
    spec = str(preset or "").strip()
    if not spec:
        raise VoiceError("preset 不能为空")
    if "/" in spec or "\\" in spec:
        parts = [p for p in re.split(r"[\\/]+", spec) if p]
        matches = [
            p for p in index.get(parts[-1], []) if [x.name for x in p.parts[-len(parts) :]] == parts
        ]
        if len(matches) == 1:
            return matches[0]
        raise VoiceError("找不到限定预设：%s" % spec)
    matches = index.get(spec, [])
    if not matches:
        available = "、".join(sorted(index)) or "（无）"
        raise VoiceError("找不到预设 %s；现有预设：%s" % (spec, available))
    if len(matches) > 1:
        raise VoiceError(
            "预设名 %s 有多个，请写成“GPT目录/SoVITS目录/预设名”：\n%s"
            % (spec, "\n".join(str(p) for p in matches))
        )
    return matches[0]


def _metadata(preset_dir: Path) -> Dict[str, Any]:
    path = preset_dir / "metadata.json"
    try:
        with path.open("r", encoding="utf-8-sig") as fh:
            data = json.load(fh)
    except Exception as exc:  # noqa: BLE001
        raise VoiceError("读取预设元数据失败 %s：%s" % (path, exc)) from exc
    if not isinstance(data, dict):
        raise VoiceError("预设元数据必须是 JSON 对象：%s" % path)
    return data


def _preset_models(
    settings: Settings,
    preset_dir: Path,
    model_index: ModelIndex,
    gpt_override: Any,
    sovits_override: Any,
) -> Tuple[Path, Path]:
    gpt_stem = preset_dir.parent.parent.name if len(preset_dir.parts) >= 3 else ""
    sovits_stem = preset_dir.parent.name
    if gpt_override:
        gpt = model_index.find(str(gpt_override), ".ckpt")
    else:
        if not gpt_stem:
            raise VoiceError("无法从预设路径推断 GPT 权重：%s" % preset_dir)
        gpt = model_index.find(gpt_stem, ".ckpt")
    if sovits_override:
        sovits = model_index.find(str(sovits_override), ".pth")
    else:
        sovits = model_index.find(sovits_stem, ".pth")
    return gpt, sovits


def _read_voice_entry(
    key: str,
    definition: Dict[str, Any],
    settings: Settings,
    voices_dir: Path,
    preset_index: Dict[str, List[Path]],
    model_index: ModelIndex,
    default_text_lang: str,
    default_aux: bool,
    default_speed: float,
    default_seed: int,
) -> Voice:
    if not isinstance(definition, dict):
        raise VoiceError("音色 %s 的配置必须是 YAML 映射" % key)
    if definition.get("enabled", True) is False:
        raise VoiceError("音色 %s 已禁用" % key)

    preset_name = str(definition.get("preset") or "").strip() or None
    preset_dir: Optional[Path] = None
    metadata: Dict[str, Any] = {}
    if preset_name:
        preset_dir = _select_preset(preset_name, preset_index)
        metadata = _metadata(preset_dir)

    gpt_value = definition.get("gpt")
    sovits_value = definition.get("sovits")
    if preset_dir:
        gpt, sovits = _preset_models(settings, preset_dir, model_index, gpt_value, sovits_value)
    else:
        if not gpt_value or not sovits_value:
            raise VoiceError("音色 %s 必须提供 preset，或同时提供 gpt 和 sovits" % key)
        gpt = model_index.find(str(gpt_value), ".ckpt")
        sovits = model_index.find(str(sovits_value), ".pth")

    version = detect_version(sovits, definition.get("version"))

    if definition.get("ref_audio"):
        ref_audio = _resolve_existing_file(
            definition["ref_audio"], voices_dir, settings, "音色 %s 的 ref_audio" % key
        )
    elif preset_dir and metadata.get("main_filename"):
        ref_audio = _resolve_existing_file(
            preset_dir / str(metadata["main_filename"]),
            voices_dir,
            settings,
            "音色 %s 的预设参考音频" % key,
        )
    else:
        raise VoiceError("音色 %s 缺少 ref_audio（预设也没有 main_filename）" % key)

    aux_enabled = bool(definition.get("aux_ref", default_aux))
    aux_refs: List[Path] = []
    if aux_enabled:
        aux_values: List[Any] = []
        if definition.get("aux_ref_audio_paths"):
            aux_values = list(definition["aux_ref_audio_paths"])
        elif definition.get("aux_refs"):
            aux_values = list(definition["aux_refs"])
        elif preset_dir and metadata.get("aux_filenames"):
            aux_values = [preset_dir / str(name) for name in metadata["aux_filenames"]]
        for item in aux_values:
            aux_refs.append(
                _resolve_existing_file(
                    item, voices_dir, settings, "音色 %s 的辅助参考音频" % key
                )
            )

    prompt_text = str(
        definition.get("prompt_text")
        if definition.get("prompt_text") is not None
        else metadata.get("prompt_text", "")
    ).strip()
    prompt_lang_raw = definition.get("prompt_lang")
    if prompt_lang_raw is None:
        prompt_lang_raw = metadata.get("prompt_language", "zh")
    prompt_lang = normalize_lang(prompt_lang_raw, "zh")
    if version in ("v3", "v4") and not prompt_text:
        raise VoiceError("音色 %s 是 %s，必须提供 prompt_text 参考文本" % (key, version))

    text_lang_explicit = "text_lang" in definition
    text_lang = normalize_lang(
        definition.get("text_lang", default_text_lang), default_text_lang
    )
    validate_lang(version, text_lang, "text_lang")
    validate_lang(version, prompt_lang, "prompt_lang")

    output_dir = str(definition.get("output_dir") or "").strip()
    if not output_dir:
        output_dir = re.sub(r"[（(].*$", "", key).strip() or key

    speed_factor = float(definition.get("speed_factor", default_speed))
    if speed_factor <= 0.1 or speed_factor > 4.0:
        raise VoiceError("音色 %s 的 speed_factor 超出范围：%s" % (key, speed_factor))
    seed = int(definition.get("seed", default_seed))

    aliases: List[str] = []
    raw_aliases = definition.get("aliases") or []
    if isinstance(raw_aliases, str):
        raw_aliases = [raw_aliases]
    for alias in raw_aliases:
        alias_text = str(alias).strip()
        if alias_text:
            aliases.append(alias_text)
    if key not in aliases:
        aliases.insert(0, key)

    return Voice(
        key=key,
        aliases=aliases,
        preset=preset_name,
        gpt=gpt,
        sovits=sovits,
        version=version,
        ref_audio=ref_audio,
        aux_refs=aux_refs,
        prompt_text=prompt_text,
        prompt_lang=prompt_lang,
        text_lang=text_lang,
        text_lang_explicit=text_lang_explicit,
        output_dir=output_dir,
        speed_factor=speed_factor,
        seed=seed,
        definition=definition,
    )


def load_registry(
    settings: Settings,
    voices_path: Optional[Path] = None,
) -> VoiceRegistry:
    path = Path(voices_path) if voices_path is not None else settings.voices_path
    path = Path(os.path.abspath(str(path)))
    if not path.exists():
        raise VoiceError("音色配置文件不存在：%s" % path)
    data = _load_yaml(path)
    definitions = data.get("voices")
    if not isinstance(definitions, dict):
        raise VoiceError("音色配置缺少 voices: 映射：%s" % path)

    preset_index = scan_presets(settings)
    if not definitions:
        # 本仓库不附带音色；首次使用时允许空库，由 `voices add` 添加。
        return VoiceRegistry({}, {}, preset_index, settings)

    model_index = ModelIndex(settings)
    voices_dir = path.parent
    default_text_lang = str(settings.defaults.get("text_lang", "zh"))
    default_aux = bool(settings.defaults.get("aux_ref", True))
    default_speed = float(settings.defaults.get("speed_factor", 1.0))
    default_seed = int(settings.defaults.get("seed", -1))

    voices: Dict[str, Voice] = {}
    alias_map: Dict[str, str] = {}
    conflicts: Dict[str, List[str]] = {}

    for key, definition in definitions.items():
        voice_key = str(key).strip()
        if not voice_key:
            raise VoiceError("voices 中存在空角色名")
        if voice_key in voices:
            raise VoiceError("音色角色名重复：%s" % voice_key)
        voice = _read_voice_entry(
            voice_key,
            definition,
            settings,
            voices_dir,
            preset_index,
            model_index,
            default_text_lang,
            default_aux,
            default_speed,
            default_seed,
        )
        voices[voice_key] = voice
        for alias in voice.aliases:
            normalized = normalize_name(alias)
            if not normalized:
                continue
            previous = alias_map.get(normalized)
            if previous and previous != voice_key:
                conflicts.setdefault(normalized, [previous]).append(voice_key)
                continue
            alias_map[normalized] = voice_key

    if conflicts:
        lines = [
            "%s -> %s" % (name, "、".join(sorted(set(keys))))
            for name, keys in sorted(conflicts.items())
        ]
        raise VoiceError("角色别名冲突，请改名或删除 aliases：\n" + "\n".join(lines))

    return VoiceRegistry(
        voices=voices,
        alias_map=alias_map,
        preset_index=preset_index,
        settings=settings,
    )
