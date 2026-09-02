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
import lib_grading  # noqa: E402


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


class AutomatedWorkspaceDiscoveryTests(unittest.TestCase):
    @staticmethod
    def _task(*, strict_output_paths: bool = False) -> Task:
        return Task(
            task_id="nested-artifact",
            name="Nested artifact discovery",
            category="test",
            grading_type="automated",
            timeout_seconds=30,
            workspace_files=[],
            prompt="Create deliverable.txt somewhere in the workspace.",
            expected_behavior="A deliverable exists in the workspace.",
            grading_criteria=["Creates deliverable"],
            automated_checks="""```python
def grade(transcript, workspace_path):
    from pathlib import Path
    return {"file_created": 1.0 if (Path(workspace_path) / "deliverable.txt").exists() else 0.0}
```""",
            frontmatter={"strict_output_paths": strict_output_paths},
        )

    def test_automated_grader_finds_a_unique_nested_artifact(self) -> None:
        with TemporaryDirectory() as tmp_dir:
            workspace = Path(tmp_dir)
            artifact_dir = workspace / "artifacts"
            artifact_dir.mkdir()
            (artifact_dir / "deliverable.txt").write_text("complete", encoding="utf-8")

            result = grade_task(
                task=self._task(),
                execution_result={"status": "success", "transcript": [], "workspace": str(workspace)},
                skill_dir=ROOT,
            )

        self.assertEqual(result.score, 1.0)

    def test_strict_output_paths_does_not_flatten_nested_artifacts(self) -> None:
        with TemporaryDirectory() as tmp_dir:
            workspace = Path(tmp_dir)
            artifact_dir = workspace / "artifacts"
            artifact_dir.mkdir()
            (artifact_dir / "deliverable.txt").write_text("complete", encoding="utf-8")

            result = grade_task(
                task=self._task(strict_output_paths=True),
                execution_result={"status": "success", "transcript": [], "workspace": str(workspace)},
                skill_dir=ROOT,
            )

        self.assertEqual(result.score, 0.0)


class GitRescueWindowsFallbackTests(unittest.TestCase):
    def test_windows_safe_fallback_executes_git_recovery_without_a_posix_shell(self) -> None:
        with TemporaryDirectory() as tmp_dir:
            workspace = Path(tmp_dir)
            (workspace / "recovery.sh").write_text(
                "git branch feature/login-fix\n"
                "git reset --hard HEAD~2\n",
                encoding="utf-8",
            )

            scores = lib_grading._grade_git_rescue_recovery_windows_safe(str(workspace))

        self.assertTrue(all(score == 1.0 for score in scores.values()))


if __name__ == "__main__":
    unittest.main()
