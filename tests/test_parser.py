# -*- coding: utf-8 -*-
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from auto_tts.parser import ParseError, parse_document  # noqa: E402


SPEAKERS = {"A", "B", "C", "D"}


def _is_speaker(name: str) -> bool:
    return name in SPEAKERS or name.replace("(", "（").replace(")", "）") in SPEAKERS


def parse_text(text: str, **kwargs):
    handle = tempfile.NamedTemporaryFile(
        mode="w", suffix=".md", encoding="utf-8", delete=False
    )
    try:
        handle.write(text)
        handle.close()
        return parse_document(Path(handle.name), is_speaker=_is_speaker, **kwargs)
    finally:
        try:
            Path(handle.name).unlink()
        except OSError:
            pass


class ParserTests(unittest.TestCase):
    def test_colon_variants(self):
        doc = parse_text("A：你好。\nB: 再见。\n")
        self.assertEqual([job.speaker for job in doc.jobs], ["A", "B"])
        self.assertEqual([job.text for job in doc.jobs], ["你好。", "再见。"])
        self.assertEqual([job.id for job in doc.jobs], ["0001", "0002"])

    def test_bracket_and_explicit_id(self):
        doc = parse_text("【A】你好。\n[5931] B：再见。\n")
        self.assertEqual(doc.jobs[0].speaker, "A")
        self.assertEqual(doc.jobs[1].id, "5931")
        self.assertEqual(doc.jobs[1].speaker, "B")

    def test_timecode(self):
        doc = parse_text("[04:25] C：好。\n[09:02-07:43] D：嗯。\n")
        self.assertEqual(doc.jobs[0].id, "0425")
        self.assertEqual(doc.jobs[1].id, "0902_0743")

    def test_pipe_without_leading_bar(self):
        doc = parse_text("A|你好。\n[0007]B|再见。\n")
        self.assertEqual(len(doc.jobs), 2)
        self.assertEqual(doc.jobs[0].speaker, "A")
        self.assertEqual(doc.jobs[0].text, "你好。")
        self.assertEqual(doc.jobs[1].id, "0007")
        self.assertEqual(doc.jobs[1].speaker, "B")

    def test_markdown_table(self):
        doc = parse_text(
            "| 角色 | 台词 |\n"
            "| --- | --- |\n"
            "| A | 你好。 |\n"
            "| B | 再见。 |\n"
        )
        self.assertEqual([job.speaker for job in doc.jobs], ["A", "B"])

    def test_heading_context_and_list_quote(self):
        doc = parse_text(
            "## A\n"
            "- 你好。\n"
            "> 再见。\n"
            "## B\n"
            "1. 走吧。\n"
        )
        self.assertEqual([job.speaker for job in doc.jobs], ["A", "A", "B"])
        self.assertEqual([job.text for job in doc.jobs], ["你好。", "再见。", "走吧。"])

    def test_frontmatter(self):
        doc = parse_text(
            "---\n"
            "project: 测试项目\n"
            "default_voice: A\n"
            "text_lang: ja\n"
            "---\n"
            "你好。\n"
        )
        self.assertEqual(doc.meta.project, "测试项目")
        self.assertEqual(doc.meta.default_voice, "A")
        self.assertEqual(doc.meta.text_lang, "ja")
        self.assertEqual(doc.jobs[0].speaker, "A")

    def test_comments_and_code_fence(self):
        doc = parse_text(
            "// 注释\n"
            "```\n"
            "A：代码里的内容\n"
            "```\n"
            "A：正文。\n"
        )
        self.assertEqual(len(doc.jobs), 1)
        self.assertEqual(doc.jobs[0].text, "正文。")

    def test_markdown_cleanup_and_emotion_tag(self):
        doc = parse_text("A：**【开心】** 你好 [链接](https://example.com)。\n")
        self.assertEqual(doc.jobs[0].text, "你好 链接。")

    def test_unknown_speaker(self):
        with self.assertRaises(ParseError) as ctx:
            parse_text("未知角色：你好。\n")
        self.assertIn("第 1 行", str(ctx.exception))

    def test_empty_text(self):
        with self.assertRaises(ParseError):
            parse_text("A：\n")

    def test_overlong_text(self):
        with self.assertRaises(ParseError):
            parse_text("A：你好。\n", max_text_length=2)

    def test_explicit_id_wins_over_timecode(self):
        doc = parse_text("[5931] [04:25] A：你好。\n")
        self.assertEqual(doc.jobs[0].id, "5931")

    def test_heading_that_is_not_speaker_clears_context(self):
        with self.assertRaises(ParseError):
            parse_text("## A\n你好。\n## 场景说明\n再见。\n")


if __name__ == "__main__":
    unittest.main()
