"""解析 → 规划 → 调 API → 保存 WAV 的主流程。"""

from __future__ import annotations

import copy
import os
import random
import re
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Optional, Tuple

from .client import ApiError, TTSClient, TTSError
from .log import get_log_path
from .manifest import Manifest, write_report_csv
from .naming import (
    build_output_path,
    choose_existing_safe_path,
    choose_output_path,
    document_sha256,
    sanitize_project,
    state_path,
)
from .parser import Job, ParseError, ParsedDoc, parse_document
from .server import ServerError, ServerHandle, ensure_server, stop_server
from .settings import Settings, SettingsError, is_within
from .voices import (
    Voice,
    VoiceError,
    VoiceRegistry,
    load_registry,
    normalize_lang,
    validate_lang,
)
from .wavcheck import WavError, inspect_wav_bytes, write_wav_atomic


class RunnerError(Exception):
    def __init__(self, message: str, exit_code: int = 2):
        super().__init__(message)
        self.exit_code = exit_code


ProgressCallback = Callable[[Dict[str, Any]], None]


def _emit(progress: Optional[ProgressCallback], event: Dict[str, Any]) -> None:
    """把进度事件交给界面；界面出错不能影响生成主流程。"""
    if progress is None:
        return
    try:
        progress(event)
    except Exception:  # noqa: BLE001
        pass


@dataclass
class RunOptions:
    doc: Path
    project: Optional[str] = None
    out_root: Optional[Path] = None
    dry_run: bool = False
    limit: Optional[int] = None
    start: int = 1
    overwrite: bool = False
    resume: bool = True
    fail_fast: bool = False
    order: str = "voice"
    only_voice: Optional[str] = None
    default_voice: Optional[str] = None
    server_url: Optional[str] = None
    no_server: bool = False
    keep_server: bool = False
    skip_gpu_check: bool = False


@dataclass
class PlannedJob:
    job: Job
    voice: Voice
    text_lang: str
    output: Path


@dataclass
class Plan:
    doc: ParsedDoc
    project: str
    jobs: List[PlannedJob]
    settings: Settings


@dataclass
class PreparedJob:
    planned: PlannedJob
    output: Path
    skip: bool = False
    owned: bool = False


def _format_unknown(unknown: List[Tuple[int, str]], registry: VoiceRegistry) -> str:
    lines = ["以下角色没有在 voices.yaml 中配置："]
    for line_no, speaker in unknown:
        lines.append("  第 %d 行：%s" % (line_no, speaker))
    lines.append("可用角色：%s" % "、".join(registry.voices.keys()))
    return "\n".join(lines)


def _run_settings(settings: Settings, out_root: Optional[Path]) -> Settings:
    if not out_root:
        return settings
    result = copy.deepcopy(settings)
    path = Path(os.path.abspath(str(out_root)))
    if is_within(path, settings.install_root):
        raise RunnerError("--out-root 不能位于只读安装目录内：%s" % path)
    result.data["output"]["root"] = str(path)
    return result


def build_plan(
    settings: Settings,
    registry: VoiceRegistry,
    options: RunOptions,
) -> Plan:
    doc_path = Path(os.path.abspath(str(options.doc)))
    if not doc_path.exists():
        raise RunnerError("文档不存在：%s" % doc_path)
    run_settings = _run_settings(settings, options.out_root)
    if options.default_voice and not registry.resolve(options.default_voice):
        raise RunnerError("--default-voice 找不到角色：%s" % options.default_voice)
    try:
        parsed = parse_document(
            doc_path,
            is_speaker=registry.is_speaker,
            default_voice=options.default_voice,
            max_text_length=int(settings.defaults.get("max_text_length", 600)),
        )
    except ParseError as exc:
        raise RunnerError(str(exc), 2) from exc

    project = options.project or parsed.meta.project or doc_path.stem
    if not parsed.jobs:
        raise RunnerError("文档里没有解析到任何台词：%s" % doc_path)

    unknown: List[Tuple[int, str]] = []
    planned: List[PlannedJob] = []
    for job in parsed.jobs:
        voice = registry.resolve(job.speaker)
        if voice is None:
            unknown.append((job.line_no, job.speaker))
            continue
        text_lang = voice.text_lang
        if parsed.meta.text_lang and not voice.text_lang_explicit:
            try:
                text_lang = normalize_lang(parsed.meta.text_lang, voice.text_lang)
                validate_lang(voice.version, text_lang, "text_lang")
            except VoiceError as exc:
                raise RunnerError("第 %d 行：%s" % (job.line_no, exc)) from exc
        try:
            output = build_output_path(
                run_settings,
                voice.output_dir,
                project,
                job.id,
                job.text,
            )
        except SettingsError as exc:
            raise RunnerError(str(exc)) from exc
        planned.append(
            PlannedJob(job=job, voice=voice, text_lang=text_lang, output=output)
        )

    if unknown:
        raise RunnerError(_format_unknown(unknown, registry))
    if options.only_voice:
        target = registry.resolve(options.only_voice)
        if target is None:
            raise RunnerError("--only-voice 找不到角色：%s" % options.only_voice)
        planned = [item for item in planned if item.voice.key == target.key]
        if not planned:
            raise RunnerError("--only-voice %s 在文档里没有台词" % options.only_voice)

    if options.order not in ("doc", "voice"):
        raise RunnerError("--order 只能是 doc 或 voice")
    if options.order == "voice":
        planned.sort(key=lambda item: (item.voice.key, item.job.index))

    # 同一文档内先做一次重名规避，dry-run 也能看到最终名称。
    used: set = set()
    for item in planned:
        item.output = choose_output_path(item.output, used)
        used.add(item.output)

    if options.start < 1:
        raise RunnerError("--start 从 1 开始")
    if options.start > len(planned):
        raise RunnerError(
            "--start %d 超过可生成条数 %d" % (options.start, len(planned))
        )
    selected = planned[options.start - 1 :]
    if options.limit is not None:
        if options.limit < 1:
            raise RunnerError("--limit 必须大于 0")
        selected = selected[: options.limit]
    if not selected:
        raise RunnerError("筛选后没有需要生成的台词")

    return Plan(doc=parsed, project=project, jobs=selected, settings=run_settings)


def _print_dry_run(plan: Plan) -> None:
    print("文档：%s" % plan.doc.path)
    print("项目：%s" % plan.project)
    print("台词：%d 条" % len(plan.jobs))
    if plan.doc.warnings:
        for warning in plan.doc.warnings:
            print("警告：%s" % warning)
    print("")
    for number, item in enumerate(plan.jobs, start=1):
        print(
            "[%d/%d] 行 %d  %s  %s → %s (%s)  %s"
            % (
                number,
                len(plan.jobs),
                item.job.line_no,
                item.job.id,
                item.job.speaker,
                item.voice.key,
                item.voice.version,
                item.output,
            )
        )
        print("      台词：%s" % item.job.text)


def _prepare_outputs(
    plan: Plan,
    manifest: Manifest,
    resume: bool,
    overwrite: bool,
) -> List[PreparedJob]:
    prepared: List[PreparedJob] = []
    used: set = set()
    for item in plan.jobs:
        base = item.output
        entry = manifest.entry(base)
        owned = bool(entry and entry.get("text_hash") == item.job.text_hash)
        if resume and not overwrite and manifest.is_done(base, item.job.text_hash):
            prepared.append(PreparedJob(item, base, skip=True, owned=True))
            used.add(base)
            continue
        candidate = choose_existing_safe_path(base, owned=owned, overwrite=overwrite)
        candidate = choose_output_path(candidate, used)
        used.add(candidate)
        prepared.append(PreparedJob(item, candidate, skip=False, owned=owned))
    return prepared


def _payload(item: PlannedJob, settings: Settings) -> Dict[str, Any]:
    defaults = settings.defaults
    voice = item.voice
    return {
        "text": item.job.text,
        "text_lang": item.text_lang,
        "ref_audio_path": str(voice.ref_audio),
        "aux_ref_audio_paths": [str(path) for path in voice.aux_refs],
        "prompt_text": voice.prompt_text,
        "prompt_lang": voice.prompt_lang,
        "top_k": int(defaults.get("top_k", 15)),
        "top_p": float(defaults.get("top_p", 1.0)),
        "temperature": float(defaults.get("temperature", 1.0)),
        "text_split_method": str(defaults.get("text_split_method", "cut5")),
        "batch_size": int(defaults.get("batch_size", 1)),
        "batch_threshold": 0.75,
        "split_bucket": True,
        "speed_factor": float(voice.speed_factor),
        "fragment_interval": float(defaults.get("fragment_interval", 0.15)),
        "seed": int(voice.seed),
        "media_type": "wav",
        "streaming_mode": False,
        "parallel_infer": bool(defaults.get("parallel_infer", True)),
        "repetition_penalty": float(defaults.get("repetition_penalty", 1.35)),
        "sample_steps": int(defaults.get("sample_steps", 32)),
        "super_sampling": bool(defaults.get("super_sampling", False)),
    }


def _entry_info(item: PreparedJob) -> Dict[str, Any]:
    return {
        "index": item.planned.job.index,
        "line_no": item.planned.job.line_no,
        "id": item.planned.job.id,
        "speaker": item.planned.job.speaker,
        "voice": item.planned.voice.key,
        "text": item.planned.job.text,
        "text_hash": item.planned.job.text_hash,
        "preset": item.planned.voice.preset or "",
        "gpt": str(item.planned.voice.gpt),
        "sovits": str(item.planned.voice.sovits),
        "ref_audio": str(item.planned.voice.ref_audio),
    }


def _next_random_index(base: Path) -> int:
    """同一句已有随机录音时，从下一个编号继续，保证文件名连续。"""
    pattern = re.compile(re.escape(base.stem) + r"_随机(\d+)$")
    used = []
    try:
        for path in base.parent.glob(base.stem + "_随机*" + base.suffix):
            match = pattern.match(path.stem)
            if match:
                used.append(int(match.group(1)))
    except OSError:
        return 1
    return (max(used) + 1) if used else 1


def random_take_path(base: Path, take: int, settings: Settings) -> Path:
    suffix = "_随机%02d%s" % (take, base.suffix)
    max_path = int(settings.output.get("max_path_chars", 230))
    room = max_path - len(str(base.parent)) - len(suffix) - 1
    if room < 8:
        raise RunnerError("随机录音文件名太长，无法保存：%s" % base)
    stem = base.stem[:room]
    return base.parent / (stem + suffix)


def generate_random_takes(
    settings: Settings,
    item: PlannedJob,
    count: int,
    logger: Any,
    progress: Optional[ProgressCallback] = None,
    cancel: Optional[Any] = None,
    no_server: bool = False,
    keep_server: bool = False,
    skip_gpu_check: bool = False,
    server_url: Optional[str] = None,
) -> int:
    """对同一句用不同随机种子生成多条音频，文件名带 _随机NN。

    这是预览区的辅助功能：不写项目 manifest，避免影响正常断点续跑。
    编号从已有文件的下一号开始，所以多次点击会继续紧挨着排列。
    """
    count = int(count)
    if count < 1 or count > 9:
        raise RunnerError("随机条数必须在 1 到 9 之间")
    base = Path(item.output)
    start = _next_random_index(base)
    try:
        plan = [random_take_path(base, start + offset, settings) for offset in range(count)]
    except RunnerError:
        raise
    except OSError as exc:
        raise RunnerError("无法计算随机录音文件名：%s" % exc) from exc

    settings.ensure_dirs()
    client = TTSClient(
        host=settings.api_host,
        port=settings.api_port,
        request_timeout=int(settings.api.get("request_timeout", 300)),
        switch_timeout=int(settings.api.get("switch_timeout", 240)),
        base_url=server_url,
    )
    server: Optional[ServerHandle] = None
    current: Optional[Tuple[str, str, str, str]] = None
    done = failed = 0
    interrupted = False
    code = 0
    error_message = ""
    try:
        if cancel is not None and cancel.is_set():
            interrupted = True
        else:
            try:
                server = ensure_server(
                    settings,
                    item.voice,
                    client,
                    server_url=server_url,
                    no_server=no_server,
                    keep_server=settings.defaults.get("keep_server", False) or keep_server,
                    skip_gpu_check=skip_gpu_check,
                    logger=logger,
                )
                if server.fresh:
                    current = _voice_signature(item.voice)
                current = _ensure_voice(client, current, item.voice, logger)
            except (ServerError, ApiError) as exc:
                error_message = str(exc)
                logger.error("随机录音启动失败：%s", exc)
                code = 3 if isinstance(exc, ServerError) else 1
            if not error_message:
                for offset, output in enumerate(plan, start=1):
                    if cancel is not None and cancel.is_set():
                        interrupted = True
                        break
                    take = start + offset - 1
                    seed = random.SystemRandom().randint(1, 2147483647)
                    logger.info(
                        "随机录音 %d/%d：%s（种子 %d）", offset, count, output.name, seed
                    )
                    _emit(
                        progress,
                        {
                            "type": "take_start",
                            "index": item.job.index,
                            "take": take,
                            "batch": offset,
                            "count": count,
                            "seed": seed,
                            "output": str(output),
                        },
                    )
                    try:
                        payload = _payload(item, settings)
                        payload["seed"] = seed
                        data = client.tts(payload)
                        info = write_wav_atomic(output, data, settings)
                        done += 1
                        logger.info("  随机完成 %s（%s）", output, info.summary())
                        _emit(
                            progress,
                            {
                                "type": "take_done",
                                "index": item.job.index,
                                "take": take,
                                "batch": offset,
                                "count": count,
                                "seed": seed,
                                "duration": info.duration,
                                "output": str(output),
                            },
                        )
                    except (TTSError, WavError, SettingsError, OSError) as exc:
                        failed += 1
                        error_message = str(exc)
                        logger.error("  随机失败：%s", error_message)
                        _emit(
                            progress,
                            {
                                "type": "take_error",
                                "index": item.job.index,
                                "take": take,
                                "batch": offset,
                                "count": count,
                                "output": str(output),
                                "message": error_message,
                            },
                        )
                        if _is_oom(error_message):
                            break
                if failed and not error_message:
                    code = 1
    except KeyboardInterrupt:
        interrupted = True
        logger.warning("收到中断，正在停止随机录音……")
    finally:
        try:
            stop_server(settings, server, client, logger)
        except Exception as exc:  # noqa: BLE001
            logger.warning("停止服务时出错：%s", exc)
        client.close()

    if interrupted:
        _emit(
            progress,
            {
                "type": "takes_cancelled",
                "index": item.job.index,
                "done": done,
                "failed": failed,
                "count": count,
            },
        )
        return 130
    if failed and code == 0:
        code = 1
    _emit(
        progress,
        {
            "type": "takes_finish",
            "index": item.job.index,
            "code": code,
            "done": done,
            "failed": failed,
            "count": count,
            "message": error_message,
            "log": get_log_path(logger) or "",
        },
    )
    return code


def _is_oom(error: str) -> bool:
    lowered = error.lower()
    return any(
        token in lowered
        for token in (
            "out of memory",
            "cuda oom",
            "allocation on device",
            "cublas_status_alloc_failed",
            "cuda error",
        )
    )


def _voice_signature(voice: Voice) -> Tuple[str, str, str, str]:
    return (
        voice.version,
        str(voice.gpt),
        str(voice.sovits),
        str(voice.ref_audio),
    )


def _ensure_voice(
    client: TTSClient,
    current: Optional[Tuple[str, str, str, str]],
    voice: Voice,
    logger: Any,
) -> Tuple[str, str, str, str]:
    signature = _voice_signature(voice)
    if current == signature:
        return signature
    logger.info(
        "切换音色：%s（%s）\n  GPT   : %s\n  SoVITS: %s\n  参考  : %s",
        voice.key,
        voice.version,
        voice.gpt,
        voice.sovits,
        voice.ref_audio,
    )
    # 必须 SoVITS 先于 GPT：init_t2s_weights 会用 configs.version 取 mute token。
    client.set_sovits_weights(voice.sovits)
    client.set_gpt_weights(voice.gpt)
    client.set_refer_audio(voice.ref_audio)
    return signature


def _groups(prepared: List[PreparedJob]) -> List[List[PreparedJob]]:
    groups: List[List[PreparedJob]] = []
    index: Dict[str, int] = {}
    for item in prepared:
        key = item.planned.voice.key
        if key not in index:
            index[key] = len(groups)
            groups.append([])
        groups[index[key]].append(item)
    return groups


def _report_rows(
    prepared: List[PreparedJob],
    manifest: Manifest,
) -> Dict[str, Dict[str, Any]]:
    rows: Dict[str, Dict[str, Any]] = {}
    for item in prepared:
        entry = manifest.entry(item.output)
        if entry:
            rows[str(item.output)] = dict(entry)
            continue
        rows[str(item.output)] = {
            **_entry_info(item),
            "output": str(item.output),
            "status": "skipped" if item.skip else "not_run",
            "duration": 0.0,
            "error": "" if item.skip else "未执行",
            "started_at": "",
            "finished_at": "",
        }
    return rows


def run(
    settings: Settings,
    options: RunOptions,
    logger: Any,
    progress: Optional[ProgressCallback] = None,
    cancel: Optional[Any] = None,
) -> int:
    try:
        registry = load_registry(settings)
    except VoiceError as exc:
        raise RunnerError(str(exc), 2) from exc
    plan = build_plan(settings, registry, options)

    if options.dry_run:
        _print_dry_run(plan)
        _emit(progress, {"type": "dry_run", "total": len(plan.jobs), "project": plan.project})
        return 0

    settings.ensure_dirs()
    try:
        plan.settings.output_root.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise RunnerError("无法创建输出目录 %s：%s" % (plan.settings.output_root, exc)) from exc

    doc_sha = document_sha256(plan.doc.path)
    manifest = Manifest.load_or_create(
        plan.settings,
        state_path(plan.settings, plan.project, doc_sha),
        plan.doc.path,
        plan.project,
        doc_sha,
    )
    if manifest.data.get("warning"):
        logger.warning("%s", manifest.data["warning"])
    prepared = _prepare_outputs(
        plan,
        manifest,
        resume=bool(options.resume),
        overwrite=bool(options.overwrite),
    )
    active = [item for item in prepared if not item.skip]
    skipped = [item for item in prepared if item.skip]
    _emit(
        progress,
        {
            "type": "plan",
            "project": plan.project,
            "doc": str(plan.doc.path),
            "total": len(active),
            "skipped": len(skipped),
        },
    )
    if skipped:
        logger.info("断点续跑：跳过 %d 条已生成且校验通过的音频。", len(skipped))
        for item in skipped:
            _emit(
                progress,
                {
                    "type": "skip",
                    "index": item.planned.job.index,
                    "output": str(item.output),
                },
            )
    if not active:
        report_path = write_report_csv(
            plan.settings,
            plan.settings.logs_dir
            / ("%s-%s.csv" % (sanitize_project(plan.project), datetime.now().strftime("%Y%m%d-%H%M%S"))),
            _report_rows(prepared, manifest),
        )
        for item in skipped:
            logger.info("已存在：%s", item.output)
        logger.info("全部已完成，报告：%s", report_path)
        _emit(
            progress,
            {
                "type": "finish",
                "code": 0,
                "done": 0,
                "failed": 0,
                "skipped": len(skipped),
                "not_run": 0,
                "report": str(report_path),
                "message": "全部已完成",
            },
        )
        return 0

    if cancel is not None and cancel.is_set():
        _emit(
            progress,
            {
                "type": "cancelled",
                "done": 0,
                "failed": 0,
                "skipped": len(skipped),
                "not_run": len(active),
            },
        )
        return 130

    client = TTSClient(
        host=settings.api_host,
        port=settings.api_port,
        request_timeout=int(settings.api.get("request_timeout", 300)),
        switch_timeout=int(settings.api.get("switch_timeout", 240)),
        base_url=options.server_url,
    )
    server: Optional[ServerHandle] = None
    failed = 0
    not_run = 0
    done = 0
    interrupted = False
    abort_reason = ""
    current: Optional[Tuple[str, str, str, str]] = None
    total = len(active)

    try:
        try:
            server = ensure_server(
                settings,
                active[0].planned.voice,
                client,
                server_url=options.server_url,
                no_server=options.no_server,
                keep_server=settings.defaults.get("keep_server", False) or options.keep_server,
                skip_gpu_check=options.skip_gpu_check,
                logger=logger,
            )
        except ServerError as exc:
            logger.error("%s", exc)
            not_run = len(active)
            _emit(
                progress,
                {
                    "type": "finish",
                    "code": 3,
                    "done": 0,
                    "failed": 0,
                    "skipped": len(skipped),
                    "not_run": not_run,
                    "report": "",
                    "message": str(exc),
                },
            )
            return 3

        if server.fresh:
            current = _voice_signature(active[0].planned.voice)

        if options.order == "voice":
            batches = _groups(active)
        else:
            batches = [[item] for item in active]

        number = 0
        for batch in batches:
            if cancel is not None and cancel.is_set():
                interrupted = True
                break
            if abort_reason:
                not_run += len(batch)
                continue
            voice = batch[0].planned.voice
            try:
                _emit(
                    progress,
                    {
                        "type": "voice",
                        "voice": voice.key,
                        "preset": voice.preset or "",
                        "version": voice.version,
                    },
                )
                current = _ensure_voice(client, current, voice, logger)
            except ApiError as exc:
                error = "切换音色失败：%s" % exc
                logger.error("%s", error)
                _emit(
                    progress,
                    {
                        "type": "voice_error",
                        "voice": voice.key,
                        "message": error,
                    },
                )
                for item in batch:
                    manifest.start(item.output, _entry_info(item))
                    manifest.failed(item.output, error)
                    failed += 1
                if "out of memory" in str(exc).lower():
                    abort_reason = "CUDA 显存不足，已停止后续生成"
                elif options.fail_fast:
                    abort_reason = "fail-fast：切换音色失败"
                continue

            for item in batch:
                if cancel is not None and cancel.is_set():
                    interrupted = True
                    break
                number += 1
                if abort_reason:
                    not_run += 1
                    continue
                try:
                    manifest.start(item.output, _entry_info(item))
                except Exception as exc:  # noqa: BLE001
                    logger.error("写清单失败：%s", exc)
                    abort_reason = "写清单失败"
                    not_run += 1
                    _emit(
                        progress,
                        {
                            "type": "job_error",
                            "index": item.planned.job.index,
                            "number": number,
                            "total": total,
                            "id": item.planned.job.id,
                            "voice": voice.key,
                            "output": str(item.output),
                            "message": "写清单失败：%s" % exc,
                        },
                    )
                    continue
                logger.info(
                    "[%d/%d] %s（行 %d）→ %s",
                    number,
                    total,
                    item.planned.job.id,
                    item.planned.job.line_no,
                    item.output.name,
                )
                logger.debug("台词：%s", item.planned.job.text)
                _emit(
                    progress,
                    {
                        "type": "job_start",
                        "index": item.planned.job.index,
                        "number": number,
                        "total": total,
                        "id": item.planned.job.id,
                        "line": item.planned.job.line_no,
                        "voice": voice.key,
                        "output": str(item.output),
                    },
                )
                try:
                    data = client.tts(_payload(item.planned, plan.settings))
                    info = write_wav_atomic(item.output, data, plan.settings)
                    manifest.done(item.output, info.duration)
                    done += 1
                    logger.info(
                        "  完成 %s（%s）", item.output, info.summary()
                    )
                    _emit(
                        progress,
                        {
                            "type": "job_done",
                            "index": item.planned.job.index,
                            "number": number,
                            "total": total,
                            "duration": info.duration,
                            "output": str(item.output),
                        },
                    )
                except (TTSError, WavError, SettingsError, OSError) as exc:
                    error = str(exc)
                    logger.error("  失败：%s", error)
                    try:
                        manifest.failed(item.output, error)
                    except Exception:  # noqa: BLE001
                        pass
                    failed += 1
                    _emit(
                        progress,
                        {
                            "type": "job_error",
                            "index": item.planned.job.index,
                            "number": number,
                            "total": total,
                            "output": str(item.output),
                            "message": error,
                        },
                    )
                    if _is_oom(error):
                        abort_reason = "CUDA 显存不足，已停止后续生成。请关闭 WebUI 后重试。"
                    elif options.fail_fast:
                        abort_reason = "fail-fast：遇到失败即停止"
                    continue
            if interrupted:
                break
    except KeyboardInterrupt:
        interrupted = True
        logger.warning("收到中断，正在停止服务……")
    finally:
        try:
            stop_server(settings, server, client, logger)
        except Exception as exc:  # noqa: BLE001
            logger.warning("停止服务时出错：%s", exc)
        client.close()

    if interrupted:
        not_run = max(0, total - done - failed)
        logger.warning("已中断：完成 %d，失败 %d，未执行 %d。", done, failed, not_run)
        _emit(
            progress,
            {
                "type": "cancelled",
                "done": done,
                "failed": failed,
                "skipped": len(skipped),
                "not_run": not_run,
            },
        )
        return 130
    if abort_reason:
        logger.warning("%s", abort_reason)

    report_path = write_report_csv(
        plan.settings,
        plan.settings.logs_dir
        / ("%s-%s.csv" % (sanitize_project(plan.project), datetime.now().strftime("%Y%m%d-%H%M%S"))),
        _report_rows(prepared, manifest),
    )
    logger.info(
        "完成：%d，失败：%d，跳过：%d，未执行：%d",
        done,
        failed,
        len(skipped),
        not_run,
    )
    logger.info("清单：%s", manifest.path)
    logger.info("报告：%s", report_path)
    log_path = get_log_path(logger)
    if log_path:
        logger.info("日志：%s", log_path)
    code = 0 if failed == 0 and not_run == 0 else 1
    _emit(
        progress,
        {
            "type": "finish",
            "code": code,
            "done": done,
            "failed": failed,
            "skipped": len(skipped),
            "not_run": not_run,
            "report": str(report_path),
            "log": log_path or "",
            "message": abort_reason,
        },
    )
    return code
