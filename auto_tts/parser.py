"""Markdown / 纯文本文档解析。"""

from __future__ import annotations

import hashlib
import html
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Dict, List, Optional, Sequence, Tuple

import yaml


class ParseError(Exception):
    def __init__(self, message: str, line_no: Optional[int] = None):
        self.line_no = line_no
        prefix = "第 %d 行：" % line_no if line_no else ""
        super().__init__(prefix + message)


@dataclass
class DocMeta:
    project: Optional[str] = None
    default_voice: Optional[str] = None
    text_lang: Optional[str] = None


@dataclass
class Job:
    index: int
    line_no: int
    id: str
    speaker: str
    text: str
    raw: str
    text_hash: str = ""

    def finalize(self) -> None:
        self.text_hash = hashlib.sha256(self.text.encode("utf-8")).hexdigest()


@dataclass
class ParsedDoc:
    path: Path
    meta: DocMeta
    jobs: List[Job]
    warnings: List[str] = field(default_factory=list)
    encoding: str = "utf-8"


_TIME_RANGE_RE = re.compile(
    r"^\s*\[?(\d{1,2}):(\d{2})(?::(\d{2}))?\s*[-–—~至]\s*"
    r"(\d{1,2}):(\d{2})(?::(\d{2}))?\]?\s*"
)
_TIME_SINGLE_RE = re.compile(r"^\s*\[?(\d{1,2}):(\d{2})(?::(\d{2}))?\]?\s*")
_HEADING_RE = re.compile(r"^(#{1,6})\s+(.+?)\s*#*\s*$")
_LIST_PREFIX_RE = re.compile(r"^\s*(?:>\s?|[-*+]\s+|\d+[.)]\s+)")
_TABLE_SEP_RE = re.compile(r"^:?-{3,}:?$")
_HTML_COMMENT_RE = re.compile(r"<!--.*?-->", re.S)
_IMAGE_RE = re.compile(r"!\[[^\]]*\]\([^)]*\)")
_LINK_RE = re.compile(r"\[([^\]]+)\]\([^)]*\)")
_EMPHASIS_RE = re.compile(r"(\*\*|__|~~|`)")
_HTML_TAG_RE = re.compile(r"<[^>]+>")
_ZERO_WIDTH_RE = re.compile(r"[\u200b-\u200f\u202a-\u202e\ufeff]")
_EMOTION_TAG_RE = re.compile(r"^[【\[（(]\s*([^】\]）)]{1,8})\s*[】\]）)]\s*")
_TABLE_HEADERS = {
    "id",
    "编号",
    "序号",
    "角色",
    "人物",
    "说话人",
    "台词",
    "对白",
    "文本",
    "内容",
    "speaker",
    "text",
    "line",
}
_SKIP_ROW = "\x00table-row"

_EMOTIONS = {
    "开心",
    "高兴",
    "快乐",
    "生气",
    "愤怒",
    "悲伤",
    "难过",
    "低落",
    "恐惧",
    "害怕",
    "惊讶",
    "吃惊",
    "平静",
    "正常",
    "兴奋",
    "激动",
    "骄傲",
    "慵懒",
    "担心",
    "无聊",
    "着急",
    "害羞",
    "哭",
    "笑",
    "嘲讽",
    "疑惑",
    "温柔",
    "冷酷",
}


def read_document(path: Path) -> Tuple[str, str]:
    data = Path(path).read_bytes()
    for encoding in ("utf-8-sig", "gb18030"):
        try:
            return data.decode(encoding), encoding
        except UnicodeDecodeError:
            continue
    raise ParseError("无法识别文件编码（请另存为 UTF-8）：%s" % path)


def _clean_name(value: str) -> str:
    text = _EMPHASIS_RE.sub("", str(value or ""))
    text = text.strip().strip("@").strip()
    text = _ZERO_WIDTH_RE.sub("", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def _compact_time(hour: str, minute: str, second: Optional[str]) -> str:
    if second is not None:
        return "%02d%02d%02d" % (int(hour), int(minute), int(second))
    return "%02d%02d" % (int(hour), int(minute))


def _strip_list_prefixes(line: str) -> str:
    text = line
    for _ in range(4):
        new = _LIST_PREFIX_RE.sub("", text, count=1)
        if new == text:
            break
        text = new
    return text


def clean_text(value: str) -> str:
    text = str(value or "")
    text = _HTML_COMMENT_RE.sub("", text)
    text = _IMAGE_RE.sub("", text)
    text = _LINK_RE.sub(r"\1", text)
    text = _HTML_TAG_RE.sub("", text)
    text = _EMPHASIS_RE.sub("", text)
    text = html.unescape(text)
    text = _ZERO_WIDTH_RE.sub("", text)
    text = re.sub(r"\s+", " ", text).strip()
    match = _EMOTION_TAG_RE.match(text)
    if match and match.group(1).strip() in _EMOTIONS:
        text = text[match.end() :].lstrip("：: ")
    return text.strip()


def _is_table_row(line: str) -> bool:
    stripped = line.strip()
    return stripped.count("|") >= 1


def _split_table(line: str) -> List[str]:
    return [cell.strip() for cell in line.strip().strip("|").split("|")]


def _looks_like_id(value: str) -> bool:
    text = str(value or "").strip().strip("[]【】")
    if not re.fullmatch(r"[0-9A-Za-z_-]{1,30}", text):
        return False
    return any(ch.isdigit() for ch in text)


def _parse_speaker_text(
    raw: str,
    current_speaker: Optional[str],
    default_voice: Optional[str],
) -> Tuple[Optional[str], str, str]:
    """返回 (显式 ID, 角色, 台词)。"""
    line = raw.strip()

    # Markdown 表格 / 竖线格式。竖线格式不带前导 |，但若同时有冒号，
    # 优先当作“角色：台词”（台词里可能含 |）。
    table_like = _is_table_row(line)
    if table_like and line.strip().startswith("|"):
        table_like = True
    elif table_like and (":" in line or "：" in line):
        table_like = False
    if table_like:
        cells = _split_table(line)
        if cells and all(_TABLE_SEP_RE.match(c) for c in cells if c):
            return None, _SKIP_ROW, ""
        lowered = {c.lower() for c in cells}
        if lowered & _TABLE_HEADERS:
            return None, _SKIP_ROW, ""
        if len(cells) >= 2:
            first = cells[0]
            if len(cells) >= 3 and _looks_like_id(first):
                return first.strip("[]【】"), _clean_name(cells[1]), "|".join(cells[2:]).strip()
            return None, _clean_name(first), "|".join(cells[1:]).strip()

    # 【角色】台词 / [角色]台词
    bracket = re.match(r"^[【\[]\s*([^】\]]{1,40})\s*[】\]]\s*[：:]?\s*(.+)$", line)
    if bracket:
        speaker = _clean_name(bracket.group(1))
        return None, speaker, bracket.group(2).strip()

    # 角色：台词（排除时间 12:30 之类；此时 timecode 已经先剥离）
    colon = re.match(r"^([^：:|]{1,40}?)\s*[：:]\s*(.+)$", line)
    if colon:
        speaker = _clean_name(colon.group(1))
        if speaker:
            return None, speaker, colon.group(2).strip()

    if current_speaker:
        return None, current_speaker, line
    if default_voice:
        return None, default_voice, line
    return None, "", ""


def _make_speaker_checker(
    is_speaker: Optional[Callable[[str], bool]],
) -> Callable[[str], bool]:
    if is_speaker is not None:
        return is_speaker

    def _accept(name: str) -> bool:
        return bool(_clean_name(name))

    return _accept


def parse_document(
    path: Path,
    is_speaker: Optional[Callable[[str], bool]] = None,
    default_voice: Optional[str] = None,
    max_text_length: int = 600,
) -> ParsedDoc:
    path = Path(path)
    if not path.exists():
        raise ParseError("文档不存在：%s" % path)
    text, encoding = read_document(path)
    lines = text.splitlines()
    meta = DocMeta()
    warnings: List[str] = []

    # YAML frontmatter
    start = 0
    if lines and lines[0].strip() == "---":
        for idx in range(1, len(lines)):
            if lines[idx].strip() == "---":
                block = "\n".join(lines[1:idx])
                try:
                    loaded = yaml.safe_load(block) or {}
                except Exception as exc:  # noqa: BLE001
                    warnings.append("frontmatter 解析失败：%s" % exc)
                    loaded = {}
                if isinstance(loaded, dict):
                    meta.project = str(loaded.get("project") or "").strip() or None
                    meta.default_voice = str(loaded.get("default_voice") or "").strip() or None
                    meta.text_lang = str(loaded.get("text_lang") or "").strip() or None
                start = idx + 1
                break

    checker = _make_speaker_checker(is_speaker)
    effective_default = meta.default_voice or default_voice
    jobs: List[Job] = []
    current_speaker: Optional[str] = None
    in_code = False
    in_comment = False
    job_index = 0

    for line_no, original in enumerate(lines, start=1):
        if line_no <= start:
            continue
        line = original.replace("\ufeff", "").rstrip("\r\n")
        stripped = line.strip()

        if "```" in stripped or stripped.startswith("~~~"):
            in_code = not in_code
            continue
        if in_code:
            continue

        # 跨行 HTML 注释
        if in_comment:
            if "-->" in line:
                line = line.split("-->", 1)[1]
                in_comment = False
            else:
                continue
        if "<!--" in line:
            if "-->" in line:
                line = _HTML_COMMENT_RE.sub("", line)
            else:
                line = line.split("<!--", 1)[0]
                in_comment = True

        if not line.strip():
            continue
        if stripped.startswith("//") or stripped.startswith("# "):
            # "# " 在标题之外通常是注释；真正的 Markdown 标题下面会先匹配
            if not _HEADING_RE.match(stripped):
                continue

        heading = _HEADING_RE.match(stripped)
        if heading:
            candidate = _clean_name(heading.group(2))
            if candidate.startswith("角色"):
                parts = re.split(r"[：:]", candidate, maxsplit=1)
                if len(parts) == 2:
                    candidate = _clean_name(parts[1])
            if checker(candidate):
                current_speaker = candidate
            else:
                current_speaker = None
            continue

        if stripped in ("---", "***", "___"):
            continue

        line = _strip_list_prefixes(line).strip()
        if not line:
            continue
        if stripped.startswith("|") and re.match(r"^\|?[\s:|-]+\|?$", stripped):
            continue

        explicit_id = ""
        time_id = ""

        match = _TIME_RANGE_RE.match(line)
        if match:
            start_id = _compact_time(match.group(1), match.group(2), match.group(3))
            end_id = _compact_time(match.group(4), match.group(5), match.group(6))
            time_id = "%s_%s" % (start_id, end_id)
            line = line[match.end() :].strip()
        else:
            match = _TIME_SINGLE_RE.match(line)
            if match:
                time_id = _compact_time(match.group(1), match.group(2), match.group(3))
                line = line[match.end() :].strip()

        id_match = re.match(r"^\[([0-9A-Za-z_-]{1,30})\]\s*(.+)$", line)
        if id_match and _looks_like_id(id_match.group(1)):
            explicit_id = id_match.group(1)
            line = id_match.group(2).strip()
            # 允许 "[5931] [04:25] 角色：台词" 这种编号 + 时间码组合。
            if not time_id:
                match = _TIME_RANGE_RE.match(line)
                if match:
                    start_id = _compact_time(
                        match.group(1), match.group(2), match.group(3)
                    )
                    end_id = _compact_time(
                        match.group(4), match.group(5), match.group(6)
                    )
                    time_id = "%s_%s" % (start_id, end_id)
                    line = line[match.end() :].strip()
                else:
                    match = _TIME_SINGLE_RE.match(line)
                    if match:
                        time_id = _compact_time(
                            match.group(1), match.group(2), match.group(3)
                        )
                        line = line[match.end() :].strip()

        if not line:
            continue

        parsed_id, speaker, raw_text = _parse_speaker_text(
            line, current_speaker, effective_default
        )
        if speaker == _SKIP_ROW:
            continue
        if not speaker:
            raise ParseError(
                "无法识别角色（请用“角色：台词”、[角色] 或 Markdown 标题）", line_no
            )
        if not checker(speaker):
            raise ParseError(
                "未知角色“%s”（请在 voices.yaml 中配置，或添加 aliases）" % speaker,
                line_no,
            )
        if parsed_id:
            explicit_id = parsed_id

        cleaned = clean_text(raw_text)
        if not cleaned:
            raise ParseError("台词为空", line_no)
        if len(cleaned) > max_text_length:
            raise ParseError(
                "台词过长（%d 字 > %d），请先拆行" % (len(cleaned), max_text_length),
                line_no,
            )

        job_index += 1
        line_id = explicit_id or time_id or ("%04d" % job_index)
        job = Job(
            index=job_index,
            line_no=line_no,
            id=line_id,
            speaker=speaker,
            text=cleaned,
            raw=original.strip(),
        )
        job.finalize()
        jobs.append(job)

    return ParsedDoc(path=path, meta=meta, jobs=jobs, warnings=warnings, encoding=encoding)
