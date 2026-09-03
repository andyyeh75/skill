"""Regression coverage for llama.cpp streaming measurement accounting."""
from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_DIR = ROOT / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import measure_llamacpp_context_streaming as measurement  # noqa: E402


class _Response:
    def __init__(self, lines: list[bytes]) -> None:
        self.lines = lines

    def __enter__(self) -> "_Response":
        return self

    def __exit__(self, *_args: object) -> None:
        return None

    def __iter__(self):
        return iter(self.lines)


class StreamingMeasurementTests(unittest.TestCase):
    def test_tps_uses_llamacpp_complete_decode_timing(self) -> None:
        events = [
            {"choices": [{"delta": {"role": "assistant", "content": None}}]},
            {"choices": [{"delta": {"content": "Hello"}}]},
            {
                "choices": [],
                "usage": {"prompt_tokens": 10, "completion_tokens": 2},
                "timings": {
                    "predicted_n": 2,
                    "predicted_ms": 200.0,
                    "predicted_per_second": 10.0,
                },
            },
        ]
        lines = [f"data: {json.dumps(event)}\n".encode() for event in events]
        lines.append(b"data: [DONE]\n")
        with patch.object(measurement.urllib.request, "urlopen", return_value=_Response(lines)), patch.object(
            measurement.time,
            "perf_counter_ns",
            side_effect=[0, 50_000_000, 100_000_000, 300_000_000, 350_000_000],
        ):
            result, _ = measurement.post_stream("http://example.test", {}, 1, None, 200)

        self.assertEqual(result["tps"], 10.0)
        self.assertEqual(result["decode_ms"], 200.0)
        self.assertEqual(result["client_post_first_content_ms"], 250.0)

    def test_warmup_failure_does_not_increment_measured_failure_count(self) -> None:
        success = {
            "input_tokens": 10,
            "output_tokens": 2,
            "ttft_ms": 1.0,
            "decode_ms": 200.0,
            "duration_ms": 201.0,
            "client_post_first_content_ms": 150.0,
            "tps": 10.0,
        }
        outcomes = [RuntimeError("warmup failure"), (success, []), (success, []), (success, [])]
        with TemporaryDirectory() as tmp_dir, patch.object(
            measurement, "post_stream", side_effect=outcomes
        ), patch.object(
            sys,
            "argv",
            [
                "measure",
                "--output-dir",
                tmp_dir,
                "--model",
                "test-model",
                "--backend-args",
                "test",
                "--ctx-size",
                "1000",
                "--long-input-words",
                "2",
                "--max-tokens",
                "2",
                "--runs",
                "1",
            ],
        ):
            measurement.main()
            output = json.loads(
                (Path(tmp_dir) / "bench-sycl-qwen36-35b-context.json").read_text()
            )

        short = output["models"][0]["results"][0]["scenarios"][0]
        self.assertEqual(short["failed_runs"], 0)
        self.assertEqual(short["warmup_failed_runs"], 1)
        self.assertEqual(len(short["failures"]["warmup"]), 1)
