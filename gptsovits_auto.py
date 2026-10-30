# -*- coding: utf-8 -*-
"""GPT-SoVITS 批量语音生成命令行入口。

只读取 GPT-SoVITS 安装目录；所有配置、日志、状态和缓存都写在工作区。
"""
from __future__ import annotations

import argparse
import json
import os
import re
import socket
import sys
import traceback
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

# 便携版 Python 带有 python39._pth，处于 isolated 模式，不会自动把脚本目录
# 放进 sys.path；在导入本工作区包之前手动补上。
_SCRIPT_DIR = Path(__file__).resolve().parent
if str(_SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPT_DIR))

from auto_tts import __version__
from auto_tts.bootstrap import ensure_initial_files, update_settings_file
from auto_tts.client import ApiError, TTSClient
from auto_tts.log import setup_logger
from auto_tts.naming import sanitize_project
from auto_tts.parser import ParseError
from auto_tts.runner import RunOptions, RunnerError, run
from auto_tts.server import (
    ServerError,
    ServerHandle,
    ensure_server,
    gpu_free_mb,
    server_status,
    stop_server,
)
from auto_tts.settings import (
    WORKSPACE_ROOT,
    Settings,
    SettingsError,
    load_settings,
)
from auto_tts.voices import VERSIONS, VoiceError, load_registry
from auto_tts.voice_editor import (
    ModelCatalog,
    ModelPair,
    PresetInfo,
    VoiceDraft,
    discover_models,
    discover_presets,
    ensure_draft_defaults,
    format_voice_summary,
    plan_voice,
    remove_voice,
    resolve_output_dir,
    resolve_user_path,
    short_path,
    validate_key,
    write_voice,
)


EXIT_CONFIG = 2
EXIT_SERVER = 3


def _logger(settings: Settings, project: str):
    return setup_logger(settings, sanitize_project(project or "run"))


def _client(settings: Settings) -> TTSClient:
    return TTSClient(
        host=settings.api_host,
        port=settings.api_port,
        request_timeout=int(settings.api.get("request_timeout", 300)),
        switch_timeout=int(settings.api.get("switch_timeout", 240)),
    )


def _port_open(host: str, port: int, timeout: float = 0.8) -> bool:
    connect_host = "127.0.0.1" if host in ("0.0.0.0", "::") else host
    try:
        with socket.create_connection((connect_host, int(port)), timeout=timeout):
            return True
    except OSError:
        return False


def _dir_writable(path: Path) -> bool:
    probe = path
    while not probe.exists() and probe.parent != probe:
        probe = probe.parent
    return probe.exists() and os.access(str(probe), os.W_OK)


def cmd_init(args: argparse.Namespace) -> int:
    written = ensure_initial_files(WORKSPACE_ROOT, force=bool(args.force))
    install = Path(args.install).expanduser() if getattr(args, "install", None) else None
    output = Path(args.output).expanduser() if getattr(args, "output", None) else None
    models = [Path(p).expanduser() for p in (getattr(args, "models", None) or [])]
    if install is not None:
        if not install.exists():
            raise SettingsError("安装目录不存在：%s" % install)
        if not (install / "api_v2.py").exists():
            raise SettingsError("该目录下没有 api_v2.py，不是 GPT-SoVITS 安装目录：%s" % install)
    if install is not None or output is not None or models:
        update_settings_file(
            WORKSPACE_ROOT,
            install_root=install,
            output_root=output,
            extra_model_roots=models or None,
        )
        written.append(WORKSPACE_ROOT / "config" / "settings.yaml")
    settings = load_settings()
    settings.ensure_dirs()
    registry = load_registry(settings)
    print("工作区：%s" % WORKSPACE_ROOT)
    if written:
        print("已创建/更新：")
        for path in written:
            print("  %s" % path)
    else:
        print("默认文件已存在，未覆盖。")
    print("安装目录：%s" % settings.install_root)
    print("输出目录：%s" % settings.output_root)
    print("音色数量：%d" % len(registry.list()))
    for voice in registry.list():
        print("  - %s" % voice.summary())
    print("")
    print("下一步：")
    print('  run.bat parse --doc examples\\example.md')
    print('  run.bat run --doc examples\\example.md --dry-run')
    print('  run.bat run --doc "你的文档.md" --project "项目名"')
    return 0


def cmd_check(args: argparse.Namespace) -> int:
    settings = load_settings()
    settings.ensure_dirs()
    registry = load_registry(settings)
    print("工作区：%s" % WORKSPACE_ROOT)
    print("安装目录：%s" % settings.install_root)
    print("运行时：%s" % settings.runtime_python)
    print("API 脚本：%s" % settings.api_script)
    print("安装配置（只读）：%s" % settings.install_config)
    print("工作区配置（可写）：%s" % settings.config_path)
    print("音色配置：%s" % settings.voices_path)
    print("输出目录：%s（%s）" % (settings.output_root, "可写" if _dir_writable(settings.output_root) else "不可写"))
    print("音色数量：%d" % len(registry.list()))
    for voice in registry.list():
        print("  - %s" % voice.summary())
    for root in settings.extra_model_roots:
        print("额外模型目录：%s（%s）" % (root, "存在" if root.exists() else "不存在"))

    free = gpu_free_mb()
    if free is None:
        print("GPU 空闲显存：无法读取 nvidia-smi")
    else:
        minimum = int(settings.defaults.get("min_free_vram_mb", 4000))
        print(
            "GPU 空闲显存：%d MiB（安全阈值 %d MiB）%s"
            % (free, minimum, "" if free >= minimum else "  ⚠ 低于阈值")
        )

    client = _client(settings)
    try:
        healthy = client.health(timeout=2.0)
        port_open = _port_open(settings.api_host, settings.api_port)
        print("API 服务：%s" % ("已运行且健康" if healthy else "未运行（生成时会自动启动）"))
        if port_open and not healthy:
            print("  ⚠ 端口 %d 已被占用，但健康检查失败。" % settings.api_port)
    finally:
        client.close()

    ok = (
        settings.install_root.exists()
        and settings.runtime_python.exists()
        and settings.api_script.exists()
        and settings.install_config.exists()
        and _dir_writable(settings.output_root)
    )
    if not ok:
        print("检查失败：有必需路径不可用。")
        return EXIT_CONFIG
    print("检查通过。")
    return 0


def _add_plan_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--doc", required=True, help="md/txt 文档路径")
    parser.add_argument("--project", default=None, help="项目名；默认取 frontmatter 或文档文件名")
    parser.add_argument("--out-root", default=None, help="覆盖输出根目录")
    parser.add_argument("--order", choices=("doc", "voice"), default=None, help="生成顺序")
    parser.add_argument("--only-voice", default=None, help="只生成指定角色")
    parser.add_argument("--default-voice", default=None, help="无角色行的默认角色")
    parser.add_argument("--start", type=int, default=1, help="从第几条开始（1 起）")
    parser.add_argument("--limit", type=int, default=None, help="最多生成多少条")


def cmd_parse(args: argparse.Namespace) -> int:
    settings = load_settings()
    settings.ensure_dirs()
    project = args.project or Path(args.doc).stem
    logger = _logger(settings, project)
    options = RunOptions(
        doc=Path(args.doc),
        project=args.project,
        out_root=Path(args.out_root) if args.out_root else None,
        dry_run=True,
        limit=args.limit,
        start=args.start,
        order=args.order or str(settings.defaults.get("order", "voice")),
        only_voice=args.only_voice,
        default_voice=args.default_voice,
    )
    return run(settings, options, logger)


def cmd_run(args: argparse.Namespace) -> int:
    settings = load_settings()
    settings.ensure_dirs()
    project = args.project or Path(args.doc).stem
    logger = _logger(settings, project)
    logger.info("工作区：%s", WORKSPACE_ROOT)
    logger.info("安装目录（只读）：%s", settings.install_root)
    options = RunOptions(
        doc=Path(args.doc),
        project=args.project,
        out_root=Path(args.out_root) if args.out_root else None,
        dry_run=bool(args.dry_run),
        limit=args.limit,
        start=args.start,
        overwrite=bool(args.overwrite),
        resume=not bool(args.no_resume),
        fail_fast=bool(args.fail_fast),
        order=args.order or str(settings.defaults.get("order", "voice")),
        only_voice=args.only_voice,
        default_voice=args.default_voice,
        server_url=args.server_url,
        no_server=bool(args.no_server),
        keep_server=bool(args.keep_server),
        skip_gpu_check=bool(args.skip_gpu_check),
    )
    return run(settings, options, logger)


def cmd_voices_list(args: argparse.Namespace) -> int:
    settings = load_settings()
    registry = load_registry(settings)
    for voice in registry.list():
        print(voice.summary())
        if voice.aliases:
            print("    aliases: %s" % "、".join(voice.aliases))
        print("    prompt_text: %s" % voice.prompt_text)
        print("    ref_audio: %s" % voice.ref_audio)
    return 0


def _ask_text(
    prompt: str,
    default: Optional[str] = None,
    allow_empty: bool = False,
) -> str:
    suffix = ""
    if default not in (None, ""):
        suffix = " [%s]" % default
    while True:
        raw = input("%s%s：" % (prompt, suffix)).strip()
        if raw:
            return raw
        if default is not None:
            return str(default)
        if allow_empty:
            return ""
        print("  不能为空，请重新输入。")


def _ask_yes(prompt: str, default: bool = True) -> bool:
    suffix = " [Y/n]" if default else " [y/N]"
    while True:
        raw = input("%s%s：" % (prompt, suffix)).strip().lower()
        if not raw:
            return default
        if raw in ("y", "yes", "是", "好", "确认", "1"):
            return True
        if raw in ("n", "no", "否", "不", "取消", "0"):
            return False
        print("  请输入 y 或 n。")


def _split_aliases(value: str) -> List[str]:
    return [item.strip() for item in re.split(r"[,，、;；]", value or "") if item.strip()]


def _ask_path(
    prompt: str,
    settings: Settings,
    label: str,
    default: Optional[str] = None,
    expected_ext: Optional[Sequence[str]] = None,
) -> Path:
    while True:
        raw = _ask_text(prompt, default=default)
        try:
            return resolve_user_path(
                raw,
                settings,
                label,
                expected_ext=expected_ext,
            )
        except VoiceError as exc:
            print("  %s" % exc)


def _preset_metadata(preset: PresetInfo) -> Dict[str, Any]:
    if not preset.paths:
        return {}
    path = Path(preset.paths[0]) / "metadata.json"
    try:
        with path.open("r", encoding="utf-8-sig") as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _print_preset_info(settings: Settings, preset: PresetInfo) -> None:
    directory = Path(preset.paths[0]) if preset.paths else Path("")
    metadata = _preset_metadata(preset)
    print("  预设: %s" % preset.name)
    print("  目录: %s" % short_path(directory, settings))
    print("  主参考: %s" % metadata.get("main_filename", "（未找到 metadata.json）"))
    aux = metadata.get("aux_filenames") or []
    print("  辅助参考: %s" % ("、".join(str(x) for x in aux) if aux else "无"))
    print("  参考文本: %s" % metadata.get("prompt_text", ""))
    print("  参考语言: %s" % metadata.get("prompt_language", ""))
    if preset.configured:
        print("  已配置为: %s" % "、".join(preset.configured))


def _find_preset(presets: Sequence[PresetInfo], value: str) -> PresetInfo:
    text = str(value or "").strip()
    for preset in presets:
        if preset.name == text:
            return preset
    raise VoiceError(
        "找不到预设“%s”。先运行 run.bat voices scan 查看可用预设。" % text
    )


def _choose_preset(presets: Sequence[PresetInfo]) -> Optional[PresetInfo]:
    if not presets:
        print("  没有发现预设；请改用手动权重模式。")
        return None
    print("  可用预设：")
    for index, preset in enumerate(presets, 1):
        flag = "（已配置：%s）" % "、".join(preset.configured) if preset.configured else ""
        print("    %2d. %s %s" % (index, preset.name, flag))
    raw = _ask_text("输入编号或预设名（0=改用手动权重）", default="1")
    if raw.strip() == "0":
        return None
    if raw.strip().isdigit():
        index = int(raw.strip())
        if not 1 <= index <= len(presets):
            raise VoiceError("预设编号超出范围：%s" % raw)
        return presets[index - 1]
    return _find_preset(presets, raw)


def _choose_pair(catalog: ModelCatalog) -> Optional[ModelPair]:
    if not catalog.pairs:
        print("  没有发现成对的 GPT/SoVITS 权重。")
        return None
    print("  可用权重对：")
    for index, pair in enumerate(catalog.pairs, 1):
        flag = "（已配置：%s）" % "、".join(pair.configured) if pair.configured else ""
        print(
            "    %2d. [%s] %s %s"
            % (index, pair.version or "?", pair.label, flag)
        )
    raw = _ask_text("输入编号（0=手动粘贴权重路径）", default="1")
    if raw.strip() == "0":
        return None
    if not raw.strip().isdigit():
        raise VoiceError("请输入数字编号：%s" % raw)
    index = int(raw.strip())
    if not 1 <= index <= len(catalog.pairs):
        raise VoiceError("权重编号超出范围：%s" % raw)
    return catalog.pairs[index - 1]


def _interactive_draft(
    args: argparse.Namespace,
    settings: Settings,
    registry: Any,
) -> VoiceDraft:
    presets = discover_presets(settings, registry)
    catalog = discover_models(settings, registry)
    key = args.key or _ask_text("角色名（文档里的说话人，例如 A）")
    draft = VoiceDraft(key=validate_key(key))

    preset: Optional[PresetInfo] = None
    if args.preset:
        preset = _find_preset(presets, args.preset)
    elif not (args.gpt or args.sovits):
        mode = _ask_text(
            "添加方式：1=使用现成预设 2=手动选择权重",
            default="1",
        )
        if mode.strip() not in ("2", "2=手动", "手动", "manual"):
            preset = _choose_preset(presets)

    if preset is not None:
        _print_preset_info(settings, preset)
        draft.preset = preset.name
        print("  将继承预设的模型、参考音频、参考文本和参考语言。")
        draft.output_dir = args.output_dir or _ask_text(
            "输出文件夹",
            default=resolve_output_dir(None, draft.clean_key()),
        )
        draft.aliases = list(args.alias or [])
        if not draft.aliases:
            draft.aliases = _split_aliases(
                _ask_text("别名（文档里的其他叫法，逗号分隔，可留空）", allow_empty=True)
            )
        if args.text_lang:
            draft.text_lang = args.text_lang
        else:
            raw = _ask_text(
                "台词语言（留空继承预设；常用 zh/ja/en，可留空）",
                default="",
                allow_empty=True,
            )
            if raw:
                draft.text_lang = raw
        if args.ref_audio:
            draft.ref_audio = resolve_user_path(
                args.ref_audio, settings, "参考音频", expected_ext=(".wav", ".mp3", ".flac")
            )
        if args.prompt_text is not None:
            draft.prompt_text = args.prompt_text
        if args.prompt_lang:
            draft.prompt_lang = args.prompt_lang
        if args.speed is not None:
            draft.speed_factor = args.speed
        if args.seed is not None:
            draft.seed = args.seed
        return draft

    print("  手动模式：需要 GPT(.ckpt) + SoVITS(.pth) + 参考音频 + 参考文本。")
    pair: Optional[ModelPair] = None
    if args.gpt and args.sovits:
        draft.gpt = resolve_user_path(
            args.gpt, settings, "GPT 权重", expected_ext=(".ckpt",)
        )
        draft.sovits = resolve_user_path(
            args.sovits, settings, "SoVITS 权重", expected_ext=(".pth",)
        )
    else:
        pair = _choose_pair(catalog)
        if pair is not None:
            draft.gpt = pair.gpt.path
            draft.sovits = pair.sovits.path
            draft.version = pair.version
        else:
            draft.gpt = _ask_path("GPT 权重 .ckpt 路径", settings, "GPT 权重", expected_ext=(".ckpt",))
            draft.sovits = _ask_path(
                "SoVITS 权重 .pth 路径",
                settings,
                "SoVITS 权重",
                expected_ext=(".pth",),
            )
    if args.ref_audio:
        draft.ref_audio = resolve_user_path(
            args.ref_audio, settings, "参考音频", expected_ext=(".wav", ".mp3", ".flac")
        )
    else:
        draft.ref_audio = _ask_path(
            "参考音频路径（可把文件拖进窗口）",
            settings,
            "参考音频",
            expected_ext=(".wav", ".mp3", ".flac", ".m4a", ".ogg", ".aac"),
        )
    if args.prompt_text is not None:
        draft.prompt_text = args.prompt_text
    else:
        draft.prompt_text = _ask_text("参考文本（必须和参考音频内容一致）")
    draft.prompt_lang = args.prompt_lang or _ask_text(
        "参考音频语言（zh/ja/en/ko/yue）",
        default=str(settings.defaults.get("text_lang", "zh")),
    )
    draft.text_lang = args.text_lang or _ask_text(
        "台词语言（zh/ja/en/ko/yue）",
        default=str(settings.defaults.get("text_lang", "zh")),
    )
    draft.output_dir = args.output_dir or _ask_text(
        "输出文件夹",
        default=resolve_output_dir(None, draft.clean_key()),
    )
    draft.aliases = list(args.alias or [])
    if not draft.aliases:
        draft.aliases = _split_aliases(
            _ask_text("别名（文档里的其他叫法，逗号分隔，可留空）", allow_empty=True)
        )
    if args.aux_ref:
        draft.aux_refs = [
            resolve_user_path(
                item, settings, "辅助参考音频", expected_ext=(".wav", ".mp3", ".flac")
            )
            for item in args.aux_ref
        ]
    else:
        raw_aux = _ask_text(
            "辅助参考音频（可选，多个用分号分隔，直接回车跳过）",
            default="",
            allow_empty=True,
        )
        if raw_aux:
            draft.aux_refs = [
                resolve_user_path(
                    item.strip(), settings, "辅助参考音频", expected_ext=(".wav", ".mp3", ".flac")
                )
                for item in raw_aux.split(";")
                if item.strip()
            ]
    if args.speed is not None:
        draft.speed_factor = args.speed
    if args.seed is not None:
        draft.seed = args.seed
    return draft


def _draft_from_args(
    args: argparse.Namespace,
    settings: Settings,
    registry: Any,
) -> VoiceDraft:
    if not args.key:
        raise VoiceError("非交互模式必须提供 --key")
    draft = VoiceDraft(key=validate_key(args.key))
    if args.preset:
        presets = discover_presets(settings, registry)
        preset = _find_preset(presets, args.preset)
        draft.preset = preset.name
    else:
        if not args.gpt or not args.sovits:
            raise VoiceError("手动音色必须同时提供 --gpt 和 --sovits")
        if not args.ref_audio:
            raise VoiceError("手动音色必须提供 --ref-audio")
        if not args.prompt_text:
            raise VoiceError("手动音色必须提供 --prompt-text（参考音频的逐字文本）")
        draft.gpt = resolve_user_path(
            args.gpt, settings, "GPT 权重", expected_ext=(".ckpt",)
        )
        draft.sovits = resolve_user_path(
            args.sovits, settings, "SoVITS 权重", expected_ext=(".pth",)
        )
        draft.version = args.version
        draft.ref_audio = resolve_user_path(
            args.ref_audio, settings, "参考音频", expected_ext=(".wav", ".mp3", ".flac")
        )
        draft.prompt_text = args.prompt_text
        draft.prompt_lang = args.prompt_lang
        draft.text_lang = args.text_lang
    if args.ref_audio and draft.ref_audio is None:
        draft.ref_audio = resolve_user_path(
            args.ref_audio, settings, "参考音频", expected_ext=(".wav", ".mp3", ".flac")
        )
    if args.prompt_text is not None:
        draft.prompt_text = args.prompt_text
    if args.prompt_lang:
        draft.prompt_lang = args.prompt_lang
    if args.text_lang:
        draft.text_lang = args.text_lang
    if args.version and not args.preset:
        draft.version = args.version
    if args.aux_ref:
        draft.aux_refs = [
            resolve_user_path(
                item, settings, "辅助参考音频", expected_ext=(".wav", ".mp3", ".flac")
            )
            for item in args.aux_ref
        ]
    draft.output_dir = args.output_dir
    draft.aliases = list(args.alias or [])
    draft.speed_factor = args.speed
    draft.seed = args.seed
    return draft


def cmd_voices_scan(args: argparse.Namespace) -> int:
    settings = load_settings()
    registry = load_registry(settings)
    catalog = discover_models(settings, registry)
    presets = discover_presets(settings, registry)
    if args.json:
        payload = {
            "workspace": str(settings.workspace),
            "install": str(settings.install_root),
            "voices": [
                {
                    "key": voice.key,
                    "preset": voice.preset,
                    "gpt": str(voice.gpt),
                    "sovits": str(voice.sovits),
                    "version": voice.version,
                }
                for voice in registry.list()
            ],
            "presets": [
                {
                    "name": preset.name,
                    "directory": str(preset.paths[0]) if preset.paths else "",
                    "configured": preset.configured,
                }
                for preset in presets
            ],
            "pairs": [
                {
                    "key": pair.key,
                    "version": pair.version,
                    "gpt": str(pair.gpt.path),
                    "sovits": str(pair.sovits.path),
                    "configured": pair.configured,
                }
                for pair in catalog.pairs
            ],
            "unpaired": [
                {"kind": entry.kind, "path": str(entry.path), "version": entry.version}
                for entry in catalog.unpaired
            ],
            "packs": [
                {
                    "root": str(pack.root),
                    "weights": len(pack.weight_files),
                    "refs": len(pack.refs),
                    "prompt_hint": pack.prompt_hint,
                }
                for pack in catalog.packs
            ],
        }
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 0

    print("工作区：%s" % settings.workspace)
    print("安装目录：%s" % settings.install_root)
    print("")
    print("已配置音色（%d）：" % len(registry.list()))
    for voice in registry.list():
        print("  - %s ← %s [%s]" % (voice.key, voice.preset or "手动", voice.version))
    print("")
    print("预设（%d）：" % len(presets))
    for preset in presets:
        flag = " 已配置: %s" % "、".join(preset.configured) if preset.configured else ""
        print("  - %s%s" % (preset.name, flag))
    print("")
    print("权重对（%d）：" % len(catalog.pairs))
    for pair in catalog.pairs:
        flag = " 已配置: %s" % "、".join(pair.configured) if pair.configured else ""
        print(
            "  - [%s] %s%s"
            % (pair.version or "?", pair.label, flag)
        )
    if catalog.unpaired:
        print("")
        print("未配对权重（%d），请检查是否为单独的基础模型：", len(catalog.unpaired))
        for entry in catalog.unpaired:
            print("  - [%s] %s" % (entry.kind, short_path(entry.path, settings)))
    if catalog.packs:
        print("")
        print("模型包（%d）：" % len(catalog.packs))
        for pack in catalog.packs:
            print(
                "  - %s：%d 个权重，%d 个参考音频"
                % (short_path(pack.root, settings), len(pack.weight_files), len(pack.refs))
            )
            if pack.prompt_hint:
                print("    参考文本提示：%s" % pack.prompt_hint[:120])
    return 0


def cmd_voices_add(args: argparse.Namespace) -> int:
    settings = load_settings()
    settings.ensure_dirs()
    registry = load_registry(settings)
    if args.yes:
        draft = _draft_from_args(args, settings, registry)
        ensure_draft_defaults(settings, draft)
        data, text, voice, backup = write_voice(
            settings,
            draft,
            force=bool(args.force),
            dry_run=bool(args.dry_run),
        )
        print(format_voice_summary(voice))
        if args.dry_run:
            print("")
            print(text)
            print("（--dry-run：未写入）")
        else:
            print("")
            print("已写入：%s" % settings.voices_path)
            if backup:
                print("备份：%s" % backup)
        return 0

    draft = _interactive_draft(args, settings, registry)
    ensure_draft_defaults(settings, draft)
    data, text, voice = plan_voice(
        settings,
        draft,
        force=bool(args.force),
    )
    print("")
    print(format_voice_summary(voice))
    if args.dry_run:
        print("")
        print(text)
        print("（--dry-run：未写入）")
        return 0
    if not _ask_yes("确认写入 voices.yaml？", True):
        print("已取消。")
        return 0
    data, text, voice, backup = write_voice(
        settings,
        draft,
        force=bool(args.force),
    )
    print("")
    print("已添加音色：%s" % voice.key)
    print("配置：%s" % settings.voices_path)
    if backup:
        print("备份：%s" % backup)
    return 0


def cmd_voices_remove(args: argparse.Namespace) -> int:
    settings = load_settings()
    settings.ensure_dirs()
    if not args.dry_run and not args.yes:
        if not _ask_yes("确认从 voices.yaml 删除音色“%s”？" % args.key, False):
            print("已取消。")
            return 0
    data, text, backup = remove_voice(
        settings,
        args.key,
        dry_run=bool(args.dry_run),
    )
    if args.dry_run:
        print(text)
        print("（--dry-run：未写入）")
    else:
        print("已删除音色：%s" % args.key)
        if backup:
            print("备份：%s" % backup)
    return 0


def cmd_server_start(args: argparse.Namespace) -> int:
    settings = load_settings()
    settings.ensure_dirs()
    registry = load_registry(settings)
    voices = registry.list()
    if not voices:
        raise ServerError("voices.yaml 中没有可用的音色")
    logger = _logger(settings, "server")
    client = _client(settings)
    try:
        handle = ensure_server(
            settings,
            voices[0],
            client,
            server_url=None,
            no_server=False,
            keep_server=True,
            skip_gpu_check=bool(args.skip_gpu_check),
            logger=logger,
        )
    finally:
        client.close()
    print("API 服务：%s" % handle.base_url)
    print("PID：%s" % handle.pid)
    print("日志：%s" % handle.log_path)
    print("状态：%s" % ("本次新启动" if handle.fresh else "复用已有服务"))
    return 0


def cmd_server_stop(args: argparse.Namespace) -> int:
    settings = load_settings()
    settings.ensure_dirs()
    logger = _logger(settings, "server")
    client = _client(settings)
    try:
        info = server_status(settings, client, logger)
        marker = info.get("marker")
        if not marker:
            if info.get("healthy"):
                print("API 服务在运行，但不是本工作区启动的；不会停止它。")
                return EXIT_CONFIG
            print("API 服务未运行。")
            return 0
        handle = ServerHandle(
            base_url=client.base_url,
            started_by_us=True,
            keep=False,
            pid=int(marker.get("pid") or 0) or None,
            log_path=Path(marker["log"]) if marker.get("log") else None,
            config_path=Path(marker["config"]) if marker.get("config") else None,
            started_at=str(marker.get("started_at") or ""),
        )
        stop_server(settings, handle, client, logger)
        if client.health(timeout=2.0):
            print("停止失败：API 仍在响应。")
            return 1
        print("API 服务已停止。")
        return 0
    finally:
        client.close()


def cmd_server_status(args: argparse.Namespace) -> int:
    settings = load_settings()
    settings.ensure_dirs()
    logger = _logger(settings, "server")
    client = _client(settings)
    try:
        info = server_status(settings, client, logger)
    finally:
        client.close()
    return 0 if info.get("healthy") else 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="gptsovits_auto.py",
        description="GPT-SoVITS 批量语音生成（只读安装目录）",
    )
    parser.add_argument("--version", action="version", version="%(prog)s " + __version__)
    parser.add_argument("--debug", action="store_true", help="异常时打印完整堆栈")
    sub = parser.add_subparsers(dest="command", required=True)

    init = sub.add_parser("init", help="初始化工作区默认配置")
    init.add_argument("--force", action="store_true", help="覆盖 voices.yaml 和示例文档")
    init.add_argument("--install", default=None, help="GPT-SoVITS 安装目录（目录需包含 api_v2.py）")
    init.add_argument("--output", default=None, help="默认输出根目录")
    init.add_argument(
        "--models",
        action="append",
        default=None,
        help="额外模型包根目录；可重复使用",
    )
    init.set_defaults(func=cmd_init)

    check = sub.add_parser("check", help="检查路径、音色、显存和 API 状态")
    check.set_defaults(func=cmd_check)

    voices = sub.add_parser("voices", help="音色相关命令")
    voices_sub = voices.add_subparsers(dest="voices_command", required=True)
    voices_list = voices_sub.add_parser("list", help="列出可用音色")
    voices_list.set_defaults(func=cmd_voices_list)

    voices_scan = voices_sub.add_parser("scan", help="扫描安装目录和模型包里的权重/预设")
    voices_scan.add_argument("--json", action="store_true", help="输出 JSON，便于脚本调用")
    voices_scan.set_defaults(func=cmd_voices_scan)

    voices_add = voices_sub.add_parser("add", help="添加音色（交互向导；--yes 非交互）")
    voices_add.add_argument("--key", default=None, help="角色名（文档里的说话人）")
    voices_add.add_argument("--preset", default=None, help="预设名；不填则手动选权重")
    voices_add.add_argument("--gpt", default=None, help="GPT .ckpt 路径")
    voices_add.add_argument("--sovits", default=None, help="SoVITS .pth 路径")
    voices_add.add_argument("--ref-audio", default=None, help="参考音频路径")
    voices_add.add_argument(
        "--aux-ref",
        action="append",
        default=None,
        help="辅助参考音频；可重复使用",
    )
    voices_add.add_argument("--prompt-text", default=None, help="参考音频的逐字文本")
    voices_add.add_argument("--prompt-lang", default=None, help="参考音频语言 zh/ja/en/ko/yue")
    voices_add.add_argument("--text-lang", default=None, help="台词语言 zh/ja/en/ko/yue")
    voices_add.add_argument("--output-dir", default=None, help="输出子目录，默认取角色名")
    voices_add.add_argument("--alias", action="append", default=None, help="别名；可重复使用")
    voices_add.add_argument("--version", choices=VERSIONS, default=None, help="手动指定模型版本")
    voices_add.add_argument("--speed", type=float, default=None, help="语速倍率，默认 1.05")
    voices_add.add_argument("--seed", type=int, default=None, help="随机种子")
    voices_add.add_argument("--yes", action="store_true", help="非交互模式，必须提供完整参数")
    voices_add.add_argument("--force", action="store_true", help="覆盖同名音色")
    voices_add.add_argument("--dry-run", action="store_true", help="只校验/预览，不写入")
    voices_add.set_defaults(func=cmd_voices_add)

    voices_remove = voices_sub.add_parser("remove", help="删除音色（会备份 voices.yaml）")
    voices_remove.add_argument("--key", required=True, help="要删除的角色名")
    voices_remove.add_argument("--yes", action="store_true", help="不询问直接删除")
    voices_remove.add_argument("--dry-run", action="store_true", help="只预览，不写入")
    voices_remove.set_defaults(func=cmd_voices_remove)

    parse = sub.add_parser("parse", help="只解析文档并预览计划，不生成")
    _add_plan_args(parse)
    parse.set_defaults(func=cmd_parse)

    runp = sub.add_parser("run", help="解析文档并批量生成语音")
    _add_plan_args(runp)
    runp.add_argument("--dry-run", action="store_true", help="只预览，不启动 API")
    runp.add_argument("--overwrite", action="store_true", help="重新生成已有音频")
    runp.add_argument("--no-resume", action="store_true", help="关闭断点续跑")
    runp.add_argument("--fail-fast", action="store_true", help="遇到第一条失败就停止")
    runp.add_argument("--server-url", default=None, help="复用外部 API 服务，例如 http://127.0.0.1:9880")
    runp.add_argument("--no-server", action="store_true", help="只复用现有服务，不自动启动")
    runp.add_argument("--keep-server", action="store_true", help="生成结束后保留 API 服务")
    runp.add_argument("--skip-gpu-check", action="store_true", help="跳过显存预检")
    runp.set_defaults(func=cmd_run)

    server = sub.add_parser("server", help="管理 API 服务")
    server_sub = server.add_subparsers(dest="server_command", required=True)
    server_start = server_sub.add_parser("start", help="启动 API 服务并保留")
    server_start.add_argument("--skip-gpu-check", action="store_true", help="跳过显存预检")
    server_start.set_defaults(func=cmd_server_start)
    server_stop = server_sub.add_parser("stop", help="停止本工作区启动的 API 服务")
    server_stop.set_defaults(func=cmd_server_stop)
    server_status_p = server_sub.add_parser("status", help="查看 API 服务状态")
    server_status_p.set_defaults(func=cmd_server_status)
    return parser


def main(argv: Optional[List[str]] = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        try:
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        except Exception:  # noqa: BLE001
            pass
    args = build_parser().parse_args(argv)
    try:
        return int(args.func(args))
    except RunnerError as exc:
        print("错误：%s" % exc)
        return int(getattr(exc, "exit_code", EXIT_CONFIG) or EXIT_CONFIG)
    except SettingsError as exc:
        print("错误：%s" % exc)
        print(
            '提示：如果 GPT-SoVITS 不在工作区旁边，先运行 '
            'run.bat init --install "你的 GPT-SoVITS 目录"'
        )
        return EXIT_CONFIG
    except (VoiceError, ParseError) as exc:
        print("错误：%s" % exc)
        return EXIT_CONFIG
    except ServerError as exc:
        print("错误：%s" % exc)
        return EXIT_SERVER
    except ApiError as exc:
        print("错误：API 请求失败：%s" % exc)
        return EXIT_SERVER
    except EOFError:
        print("输入结束，已取消。")
        return 130
    except KeyboardInterrupt:
        print("已中断。")
        return 130
    except Exception as exc:  # noqa: BLE001
        print("错误：%s" % exc)
        if getattr(args, "debug", False):
            traceback.print_exc()
        return 1


if __name__ == "__main__":
    sys.exit(main())
