"""api_v2.py 的 HTTP 客户端。"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, Optional, Tuple

import requests


class ApiError(Exception):
    """API 请求失败。"""


class TTSError(ApiError):
    """TTS 合成失败。"""


class TTSClient:
    def __init__(
        self,
        host: str = "127.0.0.1",
        port: int = 9880,
        request_timeout: int = 300,
        switch_timeout: int = 240,
        base_url: Optional[str] = None,
    ):
        if base_url:
            base_url = base_url.rstrip("/")
        else:
            connect_host = "127.0.0.1" if host in ("0.0.0.0", "::") else host
            base_url = "http://%s:%d" % (connect_host, int(port))
        self.base_url = base_url
        self.request_timeout = int(request_timeout)
        self.switch_timeout = int(switch_timeout)
        self.session = requests.Session()
        self.session.trust_env = False

    def _url(self, path: str) -> str:
        return self.base_url + path

    def close(self) -> None:
        self.session.close()

    def health(self, timeout: float = 2.0) -> bool:
        try:
            response = self.session.get(
                self._url("/openapi.json"),
                timeout=(min(timeout, 3.0), timeout),
            )
        except requests.RequestException:
            return False
        return response.status_code == 200

    def _error_text(self, response: requests.Response) -> str:
        try:
            data = response.json()
        except ValueError:
            text = (response.text or "").strip()
            return text[:800] or "HTTP %d" % response.status_code
        if isinstance(data, dict):
            message = str(data.get("message") or "").strip()
            exception = str(data.get("Exception") or data.get("exception") or "").strip()
            if message and exception:
                return "%s：%s" % (message, exception)
            return message or exception or str(data)[:800]
        return str(data)[:800]

    def _raise_for(self, response: requests.Response, action: str) -> None:
        if response.status_code != 200:
            raise ApiError(
                "%s失败（HTTP %d）：%s"
                % (action, response.status_code, self._error_text(response))
            )

    def _get(self, path: str, params: Dict[str, Any], action: str, timeout: int) -> str:
        try:
            response = self.session.get(
                self._url(path),
                params=params,
                timeout=(10, max(10, int(timeout))),
            )
        except requests.RequestException as exc:
            raise ApiError("%s请求失败：%s" % (action, exc)) from exc
        self._raise_for(response, action)
        return response.text

    def set_refer_audio(self, path: Path) -> str:
        return self._get(
            "/set_refer_audio",
            {"refer_audio_path": str(Path(path).resolve())},
            "设置参考音频",
            self.switch_timeout,
        )

    def set_gpt_weights(self, path: Path) -> str:
        return self._get(
            "/set_gpt_weights",
            {"weights_path": str(Path(path).resolve())},
            "切换 GPT 权重",
            self.switch_timeout,
        )

    def set_sovits_weights(self, path: Path) -> str:
        return self._get(
            "/set_sovits_weights",
            {"weights_path": str(Path(path).resolve())},
            "切换 SoVITS 权重",
            self.switch_timeout,
        )

    def tts(self, payload: Dict[str, Any], timeout: Optional[int] = None) -> bytes:
        url = self._url("/tts")
        try:
            response = self.session.post(
                url,
                json=payload,
                timeout=(10, max(30, int(timeout or self.request_timeout))),
            )
        except requests.RequestException as exc:
            raise TTSError("合成请求失败：%s" % exc) from exc
        if response.status_code != 200:
            raise TTSError(
                "合成失败（HTTP %d）：%s"
                % (response.status_code, self._error_text(response))
            )
        content_type = (response.headers.get("content-type") or "").lower()
        data = response.content
        if "application/json" in content_type:
            raise TTSError("服务返回 JSON 而非音频：%s" % self._error_text(response))
        if not data.startswith(b"RIFF"):
            raise TTSError(
                "服务返回的不是 WAV（开头：%r，HTTP %d）"
                % (data[:16], response.status_code)
            )
        return data

    def stop(self) -> None:
        try:
            self.session.get(
                self._url("/control"),
                params={"command": "exit"},
                timeout=(3, 5),
            )
        except requests.RequestException:
            pass

    def restart(self) -> None:
        self._get("/control", {"command": "restart"}, "重启服务", 15)
