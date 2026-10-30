"""工作区配置加载与只读路径保护。"""

from __future__ import annotations

import copy
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

import yaml


WORKSPACE_ROOT = Path(__file__).resolve().parent.parent


class SettingsError(Exception):
    """配置错误。"""


def _default_install_root() -> str:
    """优先使用环境变量，其次使用工作区旁边的 GPT-SoVITS 目录。

    相对路径由 Settings 按工作区解析，因此整个目录可以改名/搬家；
    发布开源版时也不会把作者机器上的绝对路径写进代码。
    """
    env = os.environ.get("GPT_SOVITS_HOME", "").strip()
    if env:
        return env
    parent = WORKSPACE_ROOT.parent
    for name in ("GPT-SoVITS-v2pro-20250604-nvidia50", "GPT-SoVITS"):
        candidate = parent / name
        if (candidate / "api_v2.py").exists():
            return r"..\%s" % name
    return r"..\GPT-SoVITS"


DEFAULT_SETTINGS: Dict[str, Any] = {
    "install_root": _default_install_root(),
    "extra_model_roots": [r"..\GPT-SoVITS models"],
    "api": {
        "host": "127.0.0.1",
        "port": 9880,
        "startup_timeout": 240,
        "switch_timeout": 240,
        "request_timeout": 300,
    },
    "output": {
        "root": r"..\语音output",
        "layout": "{voice_dir}/{project}",
        "filename": "{id}_{text}",
        "max_text_chars": 80,
        "max_path_chars": 230,
    },
    "defaults": {
        "text_lang": "zh",
        "text_split_method": "cut5",
        "speed_factor": 1.05,
        "fragment_interval": 0.15,
        "top_k": 15,
        "top_p": 1.0,
        "temperature": 1.0,
        "repetition_penalty": 1.35,
        "seed": -1,
        "batch_size": 1,
        "parallel_infer": True,
        "sample_steps": 32,
        "super_sampling": False,
        "aux_ref": True,
        "resume": True,
        "overwrite": False,
        "min_free_vram_mb": 4000,
        "order": "voice",
        "max_text_length": 600,
    },
}


def _deep_merge(base: Dict[str, Any], override: Dict[str, Any]) -> Dict[str, Any]:
    result = copy.deepcopy(base)
    for key, value in (override or {}).items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = copy.deepcopy(value)
    return result


def _abs_path(value: Any, base: Path) -> Path:
    path = Path(os.path.expandvars(str(value))).expanduser()
    if not path.is_absolute():
        path = base / path
    return Path(os.path.abspath(str(path)))


def is_within(child: Path, parent: Path) -> bool:
    """判断 child 是否在 parent 目录内（大小写不敏感，Windows 友好）。"""
    try:
        child_s = os.path.normcase(os.path.abspath(str(child)))
        parent_s = os.path.normcase(os.path.abspath(str(parent)))
        return os.path.commonpath([child_s, parent_s]) == parent_s
    except ValueError:
        # 不同盘符
        return False


@dataclass
class Settings:
    data: Dict[str, Any]
    workspace: Path
    source_path: Path

    # ---- 路径属性 -------------------------------------------------
    @property
    def install_root(self) -> Path:
        return _abs_path(self.data["install_root"], self.workspace)

    @property
    def runtime_python(self) -> Path:
        return self.install_root / "runtime" / "python.exe"

    @property
    def api_script(self) -> Path:
        return self.install_root / "api_v2.py"

    @property
    def install_config(self) -> Path:
        return self.install_root / "GPT_SoVITS" / "configs" / "tts_infer.yaml"

    @property
    def install_config_dir(self) -> Path:
        return self.install_root / "GPT_SoVITS" / "configs"

    @property
    def presets_root(self) -> Path:
        return self.install_root / "presets"

    @property
    def config_path(self) -> Path:
        return self.workspace / "config" / "tts_infer.local.yaml"

    @property
    def voices_path(self) -> Path:
        return self.workspace / "voices" / "voices.yaml"

    @property
    def state_dir(self) -> Path:
        return self.workspace / "state"

    @property
    def logs_dir(self) -> Path:
        return self.workspace / "logs"

    @property
    def cache_dir(self) -> Path:
        return self.workspace / "cache" / "pycache"

    @property
    def output_root(self) -> Path:
        return _abs_path(self.data["output"]["root"], self.workspace)

    @property
    def api_host(self) -> str:
        return str(self.data["api"]["host"])

    @property
    def api_port(self) -> int:
        return int(self.data["api"]["port"])

    @property
    def api(self) -> Dict[str, Any]:
        return self.data["api"]

    @property
    def output(self) -> Dict[str, Any]:
        return self.data["output"]

    @property
    def defaults(self) -> Dict[str, Any]:
        return self.data["defaults"]

    @property
    def extra_model_roots(self) -> List[Path]:
        return [_abs_path(p, self.workspace) for p in self.data.get("extra_model_roots", [])]

    def get(self, dotted: str, default: Any = None) -> Any:
        node: Any = self.data
        for part in dotted.split("."):
            if not isinstance(node, dict) or part not in node:
                return default
            node = node[part]
        return node

    # ---- 生命周期 -------------------------------------------------
    def ensure_dirs(self) -> None:
        for path in (
            self.workspace / "config",
            self.workspace / "voices",
            self.workspace / "examples",
            self.state_dir,
            self.logs_dir,
            self.cache_dir,
            self.workspace / "tmp",
        ):
            path.mkdir(parents=True, exist_ok=True)

    def validate(self) -> None:
        missing = []
        for label, path in (
            ("安装目录", self.install_root),
            ("runtime\\python.exe", self.runtime_python),
            ("api_v2.py", self.api_script),
            ("GPT_SoVITS\\configs", self.install_config_dir),
            ("presets 目录", self.presets_root),
        ):
            if not path.exists():
                missing.append("%s: %s" % (label, path))
        if missing:
            raise SettingsError("缺少必要路径：\n" + "\n".join(missing))

        if is_within(self.config_path, self.install_root):
            raise SettingsError(
                "配置文件不能放在只读安装目录内：%s" % self.config_path
            )
        if is_within(self.output_root, self.install_root):
            raise SettingsError(
                "输出目录不能放在只读安装目录内：%s" % self.output_root
            )
        if is_within(self.workspace, self.install_root):
            raise SettingsError(
                "工作区不能放在只读安装目录内：%s" % self.workspace
            )
        if self.source_path.exists() is False:
            raise SettingsError("配置文件不存在：%s" % self.source_path)

    # ---- 写入保护 -------------------------------------------------
    def allowed_write_roots(self) -> List[Path]:
        return [self.workspace, self.output_root]

    def check_writable(self, path: Path, purpose: str = "写入") -> Path:
        """所有写操作必须先经过这里，安装目录一律拒绝。"""
        resolved = Path(os.path.abspath(str(path)))
        if is_within(resolved, self.install_root):
            raise SettingsError(
                "拒绝%s：目标位于只读安装目录内：%s" % (purpose, resolved)
            )
        if not any(is_within(resolved, root) for root in self.allowed_write_roots()):
            raise SettingsError(
                "拒绝%s：目标不在允许的工作区/输出目录内：%s" % (purpose, resolved)
            )
        return resolved


def load_settings(
    path: Optional[Path] = None,
    workspace: Optional[Path] = None,
) -> Settings:
    workspace = Path(os.path.abspath(str(workspace or WORKSPACE_ROOT)))
    source = Path(path) if path is not None else workspace / "config" / "settings.yaml"
    source = Path(os.path.abspath(str(source)))
    if not source.exists():
        raise SettingsError("配置文件不存在：%s（先运行 run.bat init）" % source)
    try:
        with source.open("r", encoding="utf-8-sig") as fh:
            raw = yaml.safe_load(fh) or {}
    except Exception as exc:  # noqa: BLE001 - 统一转成配置错误
        raise SettingsError("读取配置失败 %s：%s" % (source, exc)) from exc
    if not isinstance(raw, dict):
        raise SettingsError("配置文件顶层必须是 YAML 映射：%s" % source)

    data = _deep_merge(DEFAULT_SETTINGS, raw)
    settings = Settings(data=data, workspace=workspace, source_path=source)
    settings.validate()
    return settings


def format_exception(exc: BaseException) -> str:
    return "%s: %s" % (type(exc).__name__, exc)
