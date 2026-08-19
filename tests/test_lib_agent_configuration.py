"""Tests for custom endpoint agent configuration and execution mode."""
from __future__ import annotations

import json
import subprocess
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_DIR = ROOT / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from lib_agent import ensure_agent_exists, execute_openclaw_task  # noqa: E402
from lib_tasks import Task  # noqa: E402


def _task() -> Task:
    return Task(
        task_id="custom-endpoint-smoke",
        name="Custom endpoint smoke test",
        category="test",
        grading_type="automated",
        timeout_seconds=30,
        workspace_files=[],
        prompt="Say hello.",
        expected_behavior="A greeting.",
        grading_criteria=[],
    )


class CustomEndpointAgentConfigurationTests(unittest.TestCase):
    def test_named_provider_uses_bare_model_as_the_default(self) -> None:
        with TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            workspace = root / "workspace"
            agent_store = root / "agent-store"
            home = root / "home"
            list_result = subprocess.CompletedProcess(
                ["openclaw", "agents", "list"], 0, stdout="", stderr=""
            )
            create_result = subprocess.CompletedProcess(
                ["openclaw", "agents", "add"], 0, stdout="", stderr=""
            )
            auth_result = subprocess.CompletedProcess(
                ["openclaw", "models", "auth", "paste-api-key"], 0, stdout="", stderr=""
            )

            with patch(
                "lib_agent.subprocess.run",
                side_effect=[list_result, create_result, auth_result],
            ) as run, patch(
                "lib_agent._get_agent_store_dir", return_value=agent_store
            ), patch("lib_agent.Path.home", return_value=home):
                created = ensure_agent_exists(
                    "bench-custom",
                    "llama-cpp/qwen3.6-35b-a3b-mtp",
                    workspace,
                    base_url="http://127.0.0.1:8088/v1",
                    api_key="local-key",
                )

            models = json.loads((agent_store / "agent" / "models.json").read_text("utf-8"))

        self.assertTrue(created)
        self.assertEqual(run.call_count, 3)
        self.assertEqual(models["defaultProvider"], "llama-cpp")
        self.assertEqual(models["defaultModel"], "qwen3.6-35b-a3b-mtp")
        provider = models["providers"]["llama-cpp"]
        self.assertEqual(provider["baseUrl"], "http://127.0.0.1:8088/v1")
        self.assertEqual(provider["apiKey"], "local-key")
        self.assertEqual(provider["models"][0]["id"], "qwen3.6-35b-a3b-mtp")
        self.assertEqual(provider["models"][0]["name"], "llama-cpp/qwen3.6-35b-a3b-mtp")
        auth_call = run.call_args_list[2]
        self.assertEqual(
            auth_call.args[0],
            [
                "openclaw",
                "models",
                "--agent",
                "bench-custom",
                "auth",
                "paste-api-key",
                "--provider",
                "llama-cpp",
            ],
        )
        self.assertEqual(auth_call.kwargs["input"], "local-key\n")

    def test_custom_provider_auth_registration_failure_aborts_setup(self) -> None:
        with TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            list_result = subprocess.CompletedProcess(
                ["openclaw", "agents", "list"], 0, stdout="", stderr=""
            )
            create_result = subprocess.CompletedProcess(
                ["openclaw", "agents", "add"], 0, stdout="", stderr=""
            )
            auth_result = subprocess.CompletedProcess(
                ["openclaw", "models", "auth", "paste-api-key"],
                1,
                stdout="",
                stderr="credential store is locked",
            )

            with patch(
                "lib_agent.subprocess.run",
                side_effect=[list_result, create_result, auth_result],
            ), patch(
                "lib_agent._get_agent_store_dir", return_value=root / "agent-store"
            ), patch("lib_agent.Path.home", return_value=root / "home"):
                with self.assertRaisesRegex(
                    RuntimeError,
                    "Failed to register local auth for provider llama-cpp on agent "
                    "bench-custom: credential store is locked",
                ):
                    ensure_agent_exists(
                        "bench-custom",
                        "llama-cpp/qwen3.6-35b-a3b-mtp",
                        root / "workspace",
                        base_url="http://127.0.0.1:8088/v1",
                        api_key="local-key",
                    )


class CustomEndpointExecutionTests(unittest.TestCase):
    def test_local_mode_adds_local_flag_for_single_session_task(self) -> None:
        with TemporaryDirectory() as tmp_dir:
            workspace = Path(tmp_dir)
            completed = subprocess.CompletedProcess(
                ["openclaw", "agent"], 0, stdout="done", stderr=""
            )
            with patch("lib_agent.cleanup_agent_sessions"), patch(
                "lib_agent.is_fws_task", return_value=False
            ), patch("lib_agent.prepare_task_workspace", return_value=workspace), patch(
                "lib_agent.subprocess.run", return_value=completed
            ) as run, patch("lib_agent._load_transcript", return_value=([], None)):
                execute_openclaw_task(
                    task=_task(),
                    agent_id="bench-custom",
                    model_id="llama-cpp/qwen3.6-35b-a3b-mtp",
                    run_id="run-1",
                    timeout_multiplier=1.0,
                    skill_dir=ROOT,
                    local_mode=True,
                )

        command = run.call_args.args[0]
        self.assertEqual(command[:3], ["openclaw", "agent", "--local"])

    def test_wall_clock_budget_exhausted_during_setup_skips_agent_process(self) -> None:
        with TemporaryDirectory() as tmp_dir:
            workspace = Path(tmp_dir)
            with patch("lib_agent.cleanup_agent_sessions"), patch(
                "lib_agent.is_fws_task", return_value=False
            ), patch("lib_agent.prepare_task_workspace", return_value=workspace), patch(
                "lib_agent.subprocess.run"
            ) as run, patch("lib_agent._load_transcript", return_value=([], None)), patch(
                "lib_agent.time.time", side_effect=[0.0, 0.0, 11.0, 11.0]
            ):
                result = execute_openclaw_task(
                    task=_task(),
                    agent_id="bench-custom",
                    model_id="llama-cpp/qwen3.6-35b-a3b-mtp",
                    run_id="run-1",
                    timeout_multiplier=1.0,
                    task_wall_clock_seconds=10.0,
                    skill_dir=ROOT,
                )

        run.assert_not_called()
        self.assertTrue(result["timed_out"])
        self.assertTrue(result["hard_timeout_exceeded"])
        self.assertEqual(result["hard_timeout_limit_seconds"], 10.0)

    def test_single_session_uses_remaining_wall_clock_budget(self) -> None:
        with TemporaryDirectory() as tmp_dir:
            workspace = Path(tmp_dir)
            completed = subprocess.CompletedProcess(
                ["openclaw", "agent"], 0, stdout="done", stderr=""
            )
            with patch("lib_agent.cleanup_agent_sessions"), patch(
                "lib_agent.is_fws_task", return_value=False
            ), patch("lib_agent.prepare_task_workspace", return_value=workspace), patch(
                "lib_agent.subprocess.run", return_value=completed
            ) as run, patch("lib_agent._load_transcript", return_value=([], None)), patch(
                "lib_agent.time.time", side_effect=[0.0, 0.0, 3.0, 4.0]
            ):
                execute_openclaw_task(
                    task=_task(),
                    agent_id="bench-custom",
                    model_id="llama-cpp/qwen3.6-35b-a3b-mtp",
                    run_id="run-1",
                    timeout_multiplier=1.0,
                    task_wall_clock_seconds=10.0,
                    skill_dir=ROOT,
                )

        self.assertEqual(run.call_args.kwargs["timeout"], 7.0)


if __name__ == "__main__":
    unittest.main()
