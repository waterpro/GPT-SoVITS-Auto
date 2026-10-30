"""启动、复用和停止 api_v2.py 服务。"""

from __future__ import annotations

import json
import os
import socket
import subprocess
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional

import yaml

from .client import TTSClient
from .log import tail_file
from .settings import Settings


class ServerError(Exception):
    """API 服务启动或管理失败。"""


@dataclass
class ServerHandle:
    base_url: str
    started_by_us: bool = False
    external: bool = False
    fresh: bool = False
    keep: bool = False
    pid: Optional[int] = None
    process: Optional[subprocess.Popen] = None
    log_path: Optional[Path] = None
    config_path: Optional[Path] = None
    started_at: str = ""

    @property
    def managed_by_us(self) -> bool:
        return self.started_by_us and not self.external


def _timestamp() -> str:
    return datetime.now().strftime("%Y%m%d-%H%M%S")


def marker_path(settings: Settings) -> Path:
    return settings.state_dir / "api-server.json"


def _read_marker(settings: Settings) -> Optional[Dict[str, Any]]:
    path = marker_path(settings)
    if not path.exists():
        return None
    try:
        with path.open("r", encoding="utf-8-sig") as fh:
            data = json.load(fh)
    except Exception:  # noqa: BLE001
        return None
    return data if isinstance(data, dict) else None


def _write_marker(settings: Settings, data: Dict[str, Any]) -> None:
    path = settings.check_writable(marker_path(settings), "服务标记")
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + ".tmp")
    with temp.open("w", encoding="utf-8", newline="\n") as fh:
        json.dump(data, fh, ensure_ascii=False, indent=2)
        fh.write("\n")
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(str(temp), str(path))


def _remove_marker(settings: Settings, pid: Optional[int] = None) -> None:
    path = marker_path(settings)
    if not path.exists():
        return
    if pid is not None:
        marker = _read_marker(settings)
        if marker and int(marker.get("pid") or -1) != int(pid):
            return
    try:
        path.unlink()
    except OSError:
        pass


def _port_open(host: str, port: int, timeout: float = 0.7) -> bool:
    connect_host = "127.0.0.1" if host in ("0.0.0.0", "::") else host
    try:
        with socket.create_connection((connect_host, int(port)), timeout=timeout):
            return True
    except OSError:
        return False


def gpu_free_mb() -> Optional[int]:
    try:
        result = subprocess.run(
            [
                "nvidia-smi",
                "--query-gpu=memory.free",
                "--format=csv,noheader,nounits",
            ],
            capture_output=True,
            text=True,
            timeout=15,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if result.returncode != 0:
        return None
    for line in result.stdout.splitlines():
        line = line.strip()
        if line.isdigit():
            return int(line)
    return None


def _absolute_install_path(settings: Settings, value: Any) -> Any:
    if not isinstance(value, str) or not value.strip():
        return value
    path = Path(os.path.expandvars(value))
    if path.is_absolute():
        return str(path)
    return str((settings.install_root / path).resolve())


def build_local_config(settings: Settings, first_voice: Any) -> Path:
    source = settings.install_config
    if not source.exists():
        raise ServerError("安装目录的 tts_infer.yaml 不存在：%s" % source)
    try:
        with source.open("r", encoding="utf-8-sig") as fh:
            data = yaml.safe_load(fh) or {}
    except Exception as exc:  # noqa: BLE001
        raise ServerError("读取安装配置失败 %s：%s" % (source, exc)) from exc
    if not isinstance(data, dict):
        raise ServerError("安装配置不是 YAML 映射：%s" % source)

    for section in data.values():
        if not isinstance(section, dict):
            continue
        for key in list(section.keys()):
            if key.endswith("_path"):
                section[key] = _absolute_install_path(settings, section[key])

    base = data.get(first_voice.version)
    if not isinstance(base, dict):
        base = data.get("v2") if isinstance(data.get("v2"), dict) else {}
    custom = dict(data.get("custom") or {})
    custom["version"] = first_voice.version
    custom["t2s_weights_path"] = str(first_voice.gpt)
    custom["vits_weights_path"] = str(first_voice.sovits)
    custom["bert_base_path"] = base.get(
        "bert_base_path", custom.get("bert_base_path", "")
    )
    custom["cnhuhbert_base_path"] = base.get(
        "cnhuhbert_base_path", custom.get("cnhuhbert_base_path", "")
    )
    custom["device"] = str(custom.get("device") or "cuda")
    custom["is_half"] = bool(custom.get("is_half", True))
    data["custom"] = custom

    config_path = settings.config_path
    settings.check_writable(config_path, "生成 API 配置")
    config_path.parent.mkdir(parents=True, exist_ok=True)
    with config_path.open("w", encoding="utf-8", newline="\n") as fh:
        yaml.safe_dump(data, fh, allow_unicode=True, sort_keys=False)
    return config_path


def _pid_alive(pid: int) -> bool:
    try:
        result = subprocess.run(
            ["tasklist", "/FI", "PID eq %d" % int(pid), "/FO", "CSV", "/NH"],
            capture_output=True,
            text=True,
            timeout=10,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return str(int(pid)) in (result.stdout or "")


def _tail_log(handle: ServerHandle, lines: int = 40) -> str:
    if handle.log_path and Path(handle.log_path).exists():
        return tail_file(Path(handle.log_path), lines)
    return ""


def _startup_env(settings: Settings) -> Dict[str, str]:
    env = os.environ.copy()
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    env["PYTHONPYCACHEPREFIX"] = str(settings.cache_dir)
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUTF8"] = "1"
    return env


def start_server(
    settings: Settings,
    first_voice: Any,
    client: TTSClient,
    skip_gpu_check: bool,
    keep_server: bool,
    logger: Any,
) -> ServerHandle:
    min_free = int(settings.defaults.get("min_free_vram_mb", 4000))
    if not skip_gpu_check:
        free = gpu_free_mb()
        if free is not None and free < min_free:
            raise ServerError(
                "GPU 空闲显存只有 %d MiB，低于安全阈值 %d MiB。\n"
                "请先关闭正在占用显存的 GPT-SoVITS WebUI（9872/9874），"
                "或加 --skip-gpu-check 强制继续。"
                % (free, min_free)
            )
        if free is None:
            logger.warning("无法读取 nvidia-smi，跳过显存预检。")

    config_path = build_local_config(settings, first_voice)
    log_path = settings.logs_dir / ("api-%s.log" % _timestamp())
    settings.check_writable(log_path, "API 日志")
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_file = log_path.open("a", encoding="utf-8", errors="replace")

    command = [
        str(settings.runtime_python),
        "-B",
        "-u",
        "-X",
        "pycache_prefix=" + str(settings.cache_dir),
        str(settings.api_script),
        "-a",
        settings.api_host,
        "-p",
        str(settings.api_port),
        "-c",
        str(config_path),
    ]
    creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0) | getattr(
        subprocess, "CREATE_NEW_PROCESS_GROUP", 0
    )
    logger.info("启动 API 服务：%s", " ".join(command))
    try:
        process = subprocess.Popen(
            command,
            cwd=str(settings.install_root),
            env=_startup_env(settings),
            stdout=log_file,
            stderr=subprocess.STDOUT,
            creationflags=creationflags,
        )
    except OSError as exc:
        log_file.close()
        raise ServerError("无法启动 api_v2.py：%s" % exc) from exc

    handle = ServerHandle(
        base_url=client.base_url,
        started_by_us=True,
        fresh=True,
        keep=bool(keep_server),
        pid=process.pid,
        process=process,
        log_path=log_path,
        config_path=config_path,
        started_at=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    )

    timeout = int(settings.api.get("startup_timeout", 240))
    deadline = time.monotonic() + timeout
    try:
        _write_marker(
            settings,
            {
                "pid": process.pid,
                "host": settings.api_host,
                "port": settings.api_port,
                "base_url": client.base_url,
                "config": str(config_path),
                "log": str(log_path),
                "started_at": handle.started_at,
                "keep_server": bool(keep_server),
            },
        )
        while time.monotonic() < deadline:
            if process.poll() is not None:
                raise ServerError(
                    "API 服务启动后立即退出（exit=%s）。日志：\n%s"
                    % (process.returncode, _tail_log(handle))
                )
            if client.health(timeout=2.0):
                logger.info("API 服务就绪：%s（PID %d）", client.base_url, process.pid)
                return handle
            time.sleep(1.0)
        raise ServerError(
            "等待 API 服务超时（%d 秒）。日志：\n%s"
            % (timeout, _tail_log(handle))
        )
    except Exception:
        stop_server(settings, handle, client, logger, force=True)
        raise
    finally:
        # 子进程已经继承了句柄，父进程可以关闭。
        try:
            log_file.close()
        except Exception:  # noqa: BLE001
            pass


def ensure_server(
    settings: Settings,
    first_voice: Any,
    client: TTSClient,
    server_url: Optional[str],
    no_server: bool,
    keep_server: bool,
    skip_gpu_check: bool,
    logger: Any,
) -> ServerHandle:
    if server_url:
        if not client.health(timeout=3.0):
            raise ServerError("无法连接 --server-url 指定的服务：%s" % server_url)
        logger.info("使用外部 API 服务：%s", client.base_url)
        return ServerHandle(
            base_url=client.base_url,
            started_by_us=False,
            external=True,
            keep=True,
        )

    healthy = client.health(timeout=3.0)
    marker = _read_marker(settings)
    if healthy:
        if marker and int(marker.get("port") or -1) == settings.api_port:
            logger.info("复用本工作区已运行的 API 服务：%s", client.base_url)
            return ServerHandle(
                base_url=client.base_url,
                started_by_us=True,
                keep=bool(keep_server or marker.get("keep_server")),
                pid=int(marker.get("pid") or 0) or None,
                log_path=Path(marker["log"]) if marker.get("log") else None,
                config_path=Path(marker["config"]) if marker.get("config") else None,
                started_at=str(marker.get("started_at") or ""),
            )
        if no_server:
            raise ServerError(
                "端口 %d 上已有 API 服务，但不是本工作区启动的，"
                "无法保证它使用工作区配置。请改用 --server-url 明确复用，"
                "或先停止该服务。" % settings.api_port
            )
        raise ServerError(
            "端口 %d 已被占用（%s）。如果是另一个 api_v2.py，请先关闭；"
            "如果确定要复用它，请用 --server-url %s。"
            % (settings.api_port, client.base_url, client.base_url)
        )

    if _port_open(settings.api_host, settings.api_port):
        raise ServerError(
            "端口 %d 已被其他程序占用，但健康检查未通过：%s"
            % (settings.api_port, client.base_url)
        )
    if no_server:
        raise ServerError(
            "--no-server 指定复用现有服务，但 %s 没有可用的 API。" % client.base_url
        )
    return start_server(
        settings,
        first_voice,
        client,
        skip_gpu_check=skip_gpu_check,
        keep_server=keep_server,
        logger=logger,
    )


def _terminate(handle: ServerHandle) -> None:
    if handle.process is not None:
        try:
            handle.process.terminate()
        except OSError:
            pass
        try:
            handle.process.wait(timeout=10)
            return
        except subprocess.TimeoutExpired:
            pass
    if handle.pid:
        subprocess.run(
            ["taskkill", "/PID", str(handle.pid), "/T", "/F"],
            capture_output=True,
            text=True,
            timeout=20,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )


def stop_server(
    settings: Settings,
    handle: Optional[ServerHandle],
    client: TTSClient,
    logger: Any,
    force: bool = False,
) -> None:
    if handle is None:
        return
    if handle.external:
        return
    if not handle.managed_by_us:
        return
    if handle.keep and not force:
        logger.info("按 --keep-server 保留 API 服务（PID %s）", handle.pid)
        return

    logger.info("停止 API 服务（PID %s）", handle.pid)
    try:
        client.stop()
    except Exception:  # noqa: BLE001
        pass
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        if handle.process is not None and handle.process.poll() is not None:
            break
        if handle.process is None and handle.pid and not _pid_alive(int(handle.pid)):
            break
        if not _port_open(settings.api_host, settings.api_port):
            break
        time.sleep(0.5)
    if (handle.process is not None and handle.process.poll() is None) or (
        handle.process is None and handle.pid and _pid_alive(int(handle.pid))
    ):
        _terminate(handle)
    _remove_marker(settings, handle.pid)
    if handle.log_path and Path(handle.log_path).exists():
        tail = tail_file(Path(handle.log_path), 12)
        if "Traceback" in tail or "Error" in tail:
            logger.warning("API 日志末尾：\n%s", tail)


def server_status(settings: Settings, client: TTSClient, logger: Any) -> Dict[str, Any]:
    marker = _read_marker(settings)
    healthy = client.health(timeout=3.0)
    free = gpu_free_mb()
    info = {
        "base_url": client.base_url,
        "healthy": healthy,
        "port_open": _port_open(settings.api_host, settings.api_port),
        "gpu_free_mb": free,
        "marker": marker,
    }
    logger.info("API 地址：%s", client.base_url)
    logger.info("健康检查：%s", "通过" if healthy else "失败")
    logger.info("端口：%s", "打开" if info["port_open"] else "关闭")
    logger.info("GPU 空闲显存：%s", ("%d MiB" % free) if free is not None else "未知")
    if marker:
        logger.info(
            "本工作区服务：PID %s，日志 %s",
            marker.get("pid"),
            marker.get("log"),
        )
    else:
        logger.info("没有本工作区启动的服务标记。")
    return info
