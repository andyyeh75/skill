from __future__ import annotations

import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_DIR = ROOT / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from benchmark import (  # noqa: E402
    _build_agent_id,
    _compute_category_scores,
    _compute_efficiency_summary,
    _compute_score_totals,
    _exceeded_score_cutoff,
)


def _grading(mean: float, max_score: float) -> dict:
    return {
        "runs": [{"score": mean, "max_score": max_score}],
        "mean": mean,
        "std": 0.0,
        "min": mean,
        "max": mean,
    }


class BenchmarkScoringTests(unittest.TestCase):
    @patch("benchmark.os.getpid", return_value=4242)
    def test_agent_id_defaults_to_run_and_process_suffix(self, _getpid) -> None:
        agent_id = _build_agent_id("test-model", "0017", "")

        self.assertEqual(agent_id, "bench-test-model-0017-4242")

    @patch("benchmark.os.getpid", return_value=4242)
    def test_agent_id_retains_explicit_suffix_override(self, _getpid) -> None:
        agent_id = _build_agent_id("test-model", "0017", "launch@worker")

        self.assertEqual(agent_id, "bench-test-model-launch-worker")

    def test_score_cutoff_respects_threshold_and_hard_timeout(self) -> None:
        cutoff_seconds = 10.0
        cases = (
            ("just below cutoff", {"execution_time": 9.99}, False),
            ("at cutoff", {"execution_time": 10.0}, True),
            ("hard timeout", {"execution_time": 1.0, "hard_timeout_exceeded": True}, True),
        )

        for name, result, expected in cases:
            with self.subTest(name=name):
                self.assertEqual(
                    _exceeded_score_cutoff(result, cutoff_seconds), expected
                )

    def test_score_totals_exclude_skipped_grades(self) -> None:
        totals = _compute_score_totals(
            {
                "graded": _grading(0.75, 1.0),
                "skipped": _grading(0.0, 0.0),
            }
        )

        self.assertEqual(totals, (0.75, 1.0))

    def test_category_scores_exclude_skipped_tasks(self) -> None:
        task_entries = [
            {"task_id": "graded", "grading": _grading(0.75, 1.0)},
            {"task_id": "skipped", "grading": _grading(0.0, 0.0)},
        ]
        tasks_by_id = {
            "graded": SimpleNamespace(category="analysis"),
            "skipped": SimpleNamespace(category="analysis"),
        }

        result = _compute_category_scores(task_entries, tasks_by_id)

        self.assertEqual(
            result,
            {
                "ANALYSIS": {
                    "score": 0.75,
                    "max_score": 1.0,
                    "pct": 75.0,
                    "task_count": 1,
                }
            },
        )

    def test_efficiency_marks_no_judge_scores_unavailable(self) -> None:
        entries = [
            {
                "task_id": "skipped",
                "usage": {"total_tokens": 100},
                "execution_time": 1.0,
            }
        ]
        summary = _compute_efficiency_summary(
            entries,
            {"skipped": _grading(0.0, 0.0)},
        )

        self.assertIsNone(summary["score_per_1k_tokens"])
        self.assertIsNone(summary["score_per_dollar"])
        self.assertIsNone(summary["per_task"][0]["score"])


if __name__ == "__main__":
    unittest.main()
