"""Regression coverage for refreshing an existing custom-endpoint agent."""
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

from lib_agent import ensure_agent_exists  # noqa: E402


class ExistingCustomEndpointAgentTests(unittest.TestCase):
    def test_existing_agent_refreshes_its_model_contract(self) -> None:
        with TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            workspace = root / "workspace"
            agent_store = root / "agent-store"
            home = root / "home"
            list_result = subprocess.CompletedProcess(
                ["openclaw", "agents", "list"], 0, stdout="- bench-custom\n", stderr=""
            )
            root_config_result = subprocess.CompletedProcess(
                ["openclaw", "config", "set"], 0, stdout="", stderr=""
            )
            root_provider_result = subprocess.CompletedProcess(
                ["openclaw", "config", "get"], 0, stdout="{}", stderr=""
            )
            root_models_result = subprocess.CompletedProcess(
                ["openclaw", "config", "get"], 0, stdout="[]", stderr=""
            )
            root_models_update_result = subprocess.CompletedProcess(
                ["openclaw", "config", "set"], 0, stdout="", stderr=""
            )
            auth_result = subprocess.CompletedProcess(
                ["openclaw", "models", "auth", "paste-api-key"], 0, stdout="", stderr=""
            )
            with patch(
                "lib_agent.subprocess.run",
                side_effect=[
                    list_result,
                    root_provider_result,
                    root_config_result,
                    root_models_result,
                    root_models_update_result,
                    auth_result,
                ],
            ) as run, patch(
                "lib_agent._get_agent_workspace", return_value=workspace
            ), patch(
                "lib_agent._get_agent_store_dir", return_value=agent_store
            ), patch("lib_agent.Path.home", return_value=home):
                created = ensure_agent_exists(
                    "bench-custom",
                    "sycl/Qwen3.6-35B-A3B-MTP-SYCL",
                    workspace,
                    base_url="http://127.0.0.1:8090/v1",
                    api_key="local-key",
                )

            models = json.loads((agent_store / "agent" / "models.json").read_text("utf-8"))

        self.assertFalse(created)
        self.assertEqual(run.call_count, 6)
        self.assertEqual(models["defaultProvider"], "sycl")
        self.assertEqual(models["defaultModel"], "Qwen3.6-35B-A3B-MTP-SYCL")
        self.assertEqual(models["providers"]["sycl"]["models"][0]["contextWindow"], 200000)

    def test_new_custom_provider_is_created_atomically_with_timeout(self) -> None:
        with TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            workspace = root / "workspace"
            agent_store = root / "agent-store"
            list_result = subprocess.CompletedProcess(
                ["openclaw", "agents", "list"], 0, stdout="- bench-custom\n", stderr=""
            )
            missing_provider = subprocess.CompletedProcess(
                ["openclaw", "config", "get"],
                1,
                stdout="",
                stderr="Config path not found: models.providers.isolated",
            )
            success = subprocess.CompletedProcess(["openclaw"], 0, stdout="", stderr="")
            with patch(
                "lib_agent.subprocess.run",
                side_effect=[
                    list_result,
                    missing_provider,
                    success,  # complete new provider
                    success,  # agent auth
                ],
            ) as run, patch("lib_agent.CUSTOM_ENDPOINT_TIMEOUT_SECONDS", "1800"), patch(
                "lib_agent._get_agent_workspace", return_value=workspace
            ), patch("lib_agent._get_agent_store_dir", return_value=agent_store), patch(
                "lib_agent.Path.home", return_value=root / "home"
            ):
                ensure_agent_exists(
                    "bench-custom",
                    "isolated/Qwen3.6-35B-A3B-MTP-SYCL",
                    workspace,
                    base_url="http://127.0.0.1:8091/v1",
                    api_key="local-key",
                )

        commands = [call.args[0] for call in run.call_args_list]
        self.assertEqual(commands[2][3], "models.providers.isolated")
        provider = json.loads(commands[2][4])
        self.assertEqual(provider["baseUrl"], "http://127.0.0.1:8091/v1")
        self.assertEqual(provider["timeoutSeconds"], 1800)
        self.assertEqual(provider["models"][0]["id"], "Qwen3.6-35B-A3B-MTP-SYCL")
