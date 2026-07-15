from __future__ import annotations

import sys
import unittest
from pathlib import Path
from types import SimpleNamespace


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_DIR = ROOT / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from benchmark import (  # noqa: E402
    _compute_category_scores,
    _compute_efficiency_summary,
    _compute_score_totals,
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
