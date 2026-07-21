from __future__ import annotations

import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_DIR = ROOT / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from lib_grading import (  # noqa: E402
    _combine_grades,
    _compute_cache_key,
    _normalize_judge_response,
    _parse_judge_text,
    _parse_judge_response,
    _read_workspace_files,
    grade_task,
    GradeResult,
)
from lib_tasks import Task  # noqa: E402


class JudgeNormalizationTests(unittest.TestCase):
    def test_normalize_judge_response_averages_summed_total_when_breakdown_is_unit_scale(
        self,
    ) -> None:
        parsed = {
            "scores": {
                "coverage": 0.75,
                "synthesis": 0.75,
                "structure": 0.75,
                "tone": 0.8,
                "conciseness": 0.8,
            },
            "total": 3.85,
            "notes": "Summed by mistake",
        }

        normalized = _normalize_judge_response(parsed)

        self.assertAlmostEqual(normalized["total"], 0.77)

    def test_hybrid_score_uses_normalized_judge_total(self) -> None:
        auto = GradeResult(
            task_id="task_email_triage",
            score=0.7062937062937062,
            max_score=1.0,
            grading_type="automated",
            breakdown={},
            notes="",
        )
        judge = GradeResult(
            task_id="task_email_triage",
            score=0.87,
            max_score=1.0,
            grading_type="llm_judge",
            breakdown={},
            notes="",
        )

        class _Task:
            task_id = "task_email_triage"
            grading_weights = {"automated": 0.4, "llm_judge": 0.6}

        combined = _combine_grades(_Task(), auto, judge)

        self.assertAlmostEqual(combined.score, 0.8045174825174824)

    def test_parse_judge_response_prefers_latest_assistant_json_over_embedded_tool_json(
        self,
    ) -> None:
        transcript = [
            {
                "type": "message",
                "message": {
                    "role": "assistant",
                    "content": [
                        {
                            "type": "text",
                            "text": (
                                'Tool: web_search({"count": 10, "query": "WWDC 2025"})\n'
                                'Result: {"query": "WWDC 2025", "count": 10}'
                            ),
                        }
                    ],
                },
            },
            {
                "type": "message",
                "message": {
                    "role": "assistant",
                    "content": [{"type": "text", "text": "NO_REPLY"}],
                },
            },
            {
                "type": "message",
                "message": {
                    "role": "assistant",
                    "content": [
                        {
                            "type": "text",
                            "text": (
                                '{"scores": {"accuracy": 0.75, "completeness": 1.0}, '
                                '"total": 0.875, "notes": "Final judgment"}'
                            ),
                        }
                    ],
                },
            },
        ]

        parsed = _parse_judge_response(transcript)

        self.assertEqual(parsed["scores"]["accuracy"], 0.75)
        self.assertEqual(parsed["scores"]["completeness"], 1.0)
        self.assertEqual(parsed["total"], 0.875)

    def test_parse_judge_response_ignores_waiting_messages_before_final_json(self) -> None:
        transcript = [
            {
                "type": "message",
                "message": {
                    "role": "assistant",
                    "content": [
                        {"type": "text", "text": "Waiting for remaining parts (6-7)."}
                    ],
                },
            },
            {
                "type": "message",
                "message": {
                    "role": "assistant",
                    "content": [
                        {
                            "type": "text",
                            "text": (
                                '{"scores": {"clarity": 0.75, "accuracy": 0.85}, '
                                '"total": 0.8, "notes": "Looks good"}'
                            ),
                        }
                    ],
                },
            },
        ]

        parsed = _parse_judge_response(transcript)

        self.assertEqual(parsed["scores"]["clarity"], 0.75)
        self.assertEqual(parsed["total"], 0.8)

    def test_parse_judge_text_repairs_literal_newlines_inside_json_string_values(self) -> None:
        raw_text = (
            '{"scores": {"clarity": 0.9, "completion": 1.\n0}, "total": 0.9, '
            '"notes": "First line\nSecond line"}'
        )

        parsed = _parse_judge_text(raw_text)

        self.assertEqual(parsed["scores"], {"clarity": 0.9, "completion": 1.0})
        self.assertEqual(parsed["total"], 0.9)
        self.assertEqual(parsed["notes"], "First line\nSecond line")


class WorkspaceFilesForJudgeTests(unittest.TestCase):
    def test_read_workspace_files_preserves_full_text_file_content(self) -> None:
        with TemporaryDirectory() as tmp_dir:
            workspace = Path(tmp_dir)
            long_content = "A" * 3000 + "TAIL_MARKER"
            (workspace / "report.md").write_text(long_content, encoding="utf-8")

            content = _read_workspace_files(str(workspace))

        self.assertIn("### File: report.md", content)
        self.assertIn("TAIL_MARKER", content)
        self.assertIn(long_content, content)

    def test_video_transcript_task_uses_only_final_judge_evidence(self) -> None:
        with TemporaryDirectory() as tmp_dir:
            workspace = Path(tmp_dir)
            (workspace / "transcript.txt").write_text("clean transcript", encoding="utf-8")
            (workspace / "video_summary.md").write_text("structured summary", encoding="utf-8")
            (workspace / "video.info.json").write_text(
                '{"large": "raw downloader metadata"}', encoding="utf-8"
            )
            (workspace / "video.en.vtt").write_text("raw subtitles", encoding="utf-8")

            content = _read_workspace_files(
                str(workspace),
                task_id="task_video_transcript_extraction",
                evidence_aware=True,
            )

        self.assertIn("### File: transcript.txt", content)
        self.assertIn("clean transcript", content)
        self.assertIn("### File: video_summary.md", content)
        self.assertIn("structured summary", content)
        self.assertNotIn("video.info.json", content)
        self.assertNotIn("raw downloader metadata", content)
        self.assertNotIn("video.en.vtt", content)
        self.assertNotIn("raw subtitles", content)

    def test_video_transcript_task_keeps_full_evidence_before_context_failure(self) -> None:
        with TemporaryDirectory() as tmp_dir:
            workspace = Path(tmp_dir)
            (workspace / "transcript.txt").write_text("clean transcript", encoding="utf-8")
            (workspace / "video_summary.md").write_text("structured summary", encoding="utf-8")
            (workspace / "video.info.json").write_text("raw metadata", encoding="utf-8")

            content = _read_workspace_files(
                str(workspace), task_id="task_video_transcript_extraction"
            )

        self.assertIn("video.info.json", content)
        self.assertIn("raw metadata", content)

    def test_compute_cache_key_changes_when_workspace_content_changes(self) -> None:
        first_key = _compute_cache_key(
            "task_report",
            "same transcript",
            "same rubric",
            "same model",
            "workspace version one",
        )
        second_key = _compute_cache_key(
            "task_report",
            "same transcript",
            "same rubric",
            "same model",
            "workspace version two",
        )

        self.assertNotEqual(first_key, second_key)


class JudgeRetryTests(unittest.TestCase):
    def test_copilot_context_overflow_retries_with_task_evidence_allowlist(self) -> None:
        task = Task(
            task_id="task_video_transcript_extraction",
            name="Video Transcript Extraction and Summary",
            category="coding",
            grading_type="llm_judge",
            timeout_seconds=300,
            workspace_files=[],
            prompt="Create transcript.txt and video_summary.md.",
            expected_behavior="Create both deliverables.",
            grading_criteria=["Creates the transcript and summary"],
            llm_judge_rubric="Grade the transcript and summary.",
        )
        context_failure = {
            "status": "error",
            "text": "",
            "error": (
                "copilot exit 1: 400 prompt token count of 1488815 "
                "exceeds the limit of 272000"
            ),
        }
        judge_success = {
            "status": "success",
            "text": (
                '{"scores": {"completion": 1.0}, "total": 1.0, '
                '"notes": "Complete"}'
            ),
        }

        with TemporaryDirectory() as tmp_dir:
            workspace = Path(tmp_dir)
            (workspace / "transcript.txt").write_text("clean transcript", encoding="utf-8")
            (workspace / "video_summary.md").write_text("structured summary", encoding="utf-8")
            (workspace / "video.info.json").write_text("raw metadata", encoding="utf-8")
            execution_result = {
                "status": "success",
                "transcript": [],
                "workspace": str(workspace),
            }

            with patch(
                "lib_grading.call_judge_api",
                side_effect=[context_failure, judge_success],
            ) as call:
                result = grade_task(
                    task=task,
                    execution_result=execution_result,
                    skill_dir=ROOT,
                    judge_model="copilot:gpt-5.4-mini",
                    judge_backend="api",
                )

        self.assertEqual(call.call_count, 2)
        full_prompt = call.call_args_list[0].kwargs["prompt"]
        reduced_prompt = call.call_args_list[1].kwargs["prompt"]
        self.assertIn("raw metadata", full_prompt)
        self.assertNotIn("raw metadata", reduced_prompt)
        self.assertIn("clean transcript", reduced_prompt)
        self.assertIn("structured summary", reduced_prompt)
        self.assertEqual(result.score, 1.0)

    def test_quota_exhaustion_is_not_retried(self) -> None:
        task = Task(
            task_id="quota-exhaustion-smoke",
            name="Quota exhaustion smoke",
            category="test",
            grading_type="llm_judge",
            timeout_seconds=30,
            workspace_files=[],
            prompt="Respond with a greeting.",
            expected_behavior="A greeting.",
            grading_criteria=["Produces a greeting"],
        )
        execution_result = {"status": "success", "transcript": [], "workspace": ""}
        quota_failure = {
            "status": "quota_exceeded",
            "text": "",
            "error": "copilot exit 1: You have exceeded your monthly quota",
        }

        with patch("lib_grading.call_judge_api", return_value=quota_failure) as call, patch(
            "lib_grading.time.sleep"
        ) as sleep:
            result = grade_task(
                task=task,
                execution_result=execution_result,
                skill_dir=ROOT,
                judge_model="copilot:auto",
                judge_backend="api",
            )

        self.assertEqual(call.call_count, 1)
        sleep.assert_not_called()
        self.assertEqual(result.score, 0.0)
        self.assertIn("no parseable response", result.notes)


if __name__ == "__main__":
    unittest.main()
