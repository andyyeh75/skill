from __future__ import annotations

import json
import os
import subprocess
import sys
import unittest
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_DIR = ROOT / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from lib_agent import (  # noqa: E402
    _ollama_native_chat_endpoint,
    _openai_compat_chat_endpoint,
    call_judge_api,
)


class _FakeResponse:
    def __init__(self, body: dict | None = None, lines: list[dict] | None = None) -> None:
        self.body = body or {"choices": [{"message": {"content": '{"total": 1.0}'}}]}
        self.lines = lines or []

    def __enter__(self) -> "_FakeResponse":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        return None

    def __iter__(self):
        for line in self.lines:
            yield json.dumps(line).encode("utf-8") + b"\n"

    def read(self) -> bytes:
        return json.dumps(self.body).encode("utf-8")


class KiloJudgeTests(unittest.TestCase):
    def test_call_judge_api_kilo_requires_kilo_api_key(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            result = call_judge_api(
                prompt="grade this",
                model="kilo/anthropic/claude-sonnet-4-5",
            )

        self.assertEqual(result["status"], "error")
        self.assertEqual(result["text"], "")
        self.assertEqual(result["error"], "KILO_API_KEY not set")

    def test_call_judge_api_kilo_posts_to_gateway_with_bare_model(self) -> None:
        captured_request = None

        def fake_urlopen(req, timeout):
            nonlocal captured_request
            captured_request = req
            self.assertEqual(timeout, 12.5)
            return _FakeResponse()

        with patch.dict(os.environ, {"KILO_API_KEY": "test-key"}, clear=True), patch(
            "lib_agent.request.urlopen", side_effect=fake_urlopen
        ):
            result = call_judge_api(
                prompt="grade this",
                model="kilo/anthropic/claude-sonnet-4-5",
                timeout_seconds=12.5,
            )

        self.assertEqual(result["status"], "success")
        self.assertEqual(result["text"], '{"total": 1.0}')
        self.assertIsNotNone(captured_request)
        self.assertEqual(
            captured_request.full_url,
            "https://api.kilo.ai/api/gateway/chat/completions",
        )
        self.assertEqual(captured_request.get_method(), "POST")
        self.assertEqual(captured_request.headers["Authorization"], "Bearer test-key")
        self.assertEqual(captured_request.headers["Content-type"], "application/json")

        payload = json.loads(captured_request.data.decode("utf-8"))
        self.assertEqual(payload["model"], "anthropic/claude-sonnet-4-5")
        self.assertEqual(payload["temperature"], 0.0)
        self.assertEqual(payload["max_completion_tokens"], 2048)
        self.assertEqual(payload["messages"][0]["role"], "system")
        self.assertEqual(payload["messages"][1], {"role": "user", "content": "grade this"})

    def test_call_judge_api_kilo_dispatch_does_not_fall_back_to_openrouter(self) -> None:
        with patch.dict(os.environ, {"KILO_API_KEY": "test-key"}, clear=True), patch(
            "lib_agent._judge_via_openai_compat",
            return_value={"status": "success", "text": "ok"},
        ) as compat:
            result = call_judge_api(
                prompt="grade this",
                model="kilo/openai/gpt-4o",
                timeout_seconds=30,
            )

        self.assertEqual(result, {"status": "success", "text": "ok"})
        compat.assert_called_once_with(
            "grade this",
            "openai/gpt-4o",
            "https://api.kilo.ai/api/gateway/chat/completions",
            "test-key",
            30,
        )


class OllamaJudgeTests(unittest.TestCase):
    def test_call_judge_api_ollama_posts_to_native_chat_endpoint_without_auth(self) -> None:
        captured_request = None

        def fake_urlopen(req, timeout):
            nonlocal captured_request
            captured_request = req
            self.assertEqual(timeout, 9.0)
            return _FakeResponse({"message": {"content": '{"total": 1.0}'}})

        with patch.dict(os.environ, {}, clear=True), patch(
            "lib_agent.request.urlopen", side_effect=fake_urlopen
        ):
            result = call_judge_api(
                prompt="grade this",
                model="ollama/llama3.1",
                timeout_seconds=9.0,
            )

        self.assertEqual(result["status"], "success")
        self.assertEqual(result["text"], '{"total": 1.0}')
        self.assertIsNotNone(captured_request)
        self.assertEqual(captured_request.full_url, "http://localhost:11434/api/chat")
        self.assertNotIn("Authorization", captured_request.headers)

        payload = json.loads(captured_request.data.decode("utf-8"))
        self.assertEqual(payload["model"], "llama3.1")
        self.assertEqual(payload["format"], "json")
        self.assertIs(payload["stream"], False)
        self.assertEqual(payload["options"]["temperature"], 0.0)
        self.assertEqual(payload["options"]["num_predict"], 2048)
        self.assertNotIn("max_tokens", payload)
        self.assertNotIn("max_completion_tokens", payload)
        self.assertNotIn("keep_alive", payload)
        self.assertEqual(payload["messages"][1], {"role": "user", "content": "grade this"})

    def test_call_judge_api_ollama_applies_memory_options_from_env(self) -> None:
        captured_request = None

        def fake_urlopen(req, timeout):
            nonlocal captured_request
            captured_request = req
            return _FakeResponse({"message": {"content": '{"total": 0.75}'}})

        env = {
            "OLLAMA_JUDGE_BASE_URL": "http://example.test:11434/v1",
            "OLLAMA_API_KEY": "proxy-key",
            "OLLAMA_JUDGE_NUM_CTX": "4096",
            "OLLAMA_JUDGE_NUM_PREDICT": "512",
            "OLLAMA_JUDGE_KEEP_ALIVE": "0",
        }
        with patch.dict(os.environ, env, clear=True), patch(
            "lib_agent.request.urlopen", side_effect=fake_urlopen
        ):
            result = call_judge_api(
                prompt="grade this",
                model="ollama/qwen3-coder:30b",
            )

        self.assertEqual(result["status"], "success")
        self.assertEqual(result["text"], '{"total": 0.75}')
        self.assertEqual(captured_request.full_url, "http://example.test:11434/api/chat")
        self.assertEqual(captured_request.headers["Authorization"], "Bearer proxy-key")

        payload = json.loads(captured_request.data.decode("utf-8"))
        self.assertEqual(payload["options"]["num_ctx"], 4096)
        self.assertEqual(payload["options"]["num_predict"], 512)
        self.assertEqual(payload["keep_alive"], 0)

    def test_call_judge_api_ollama_streams_native_chat_chunks(self) -> None:
        def fake_urlopen(req, timeout):
            return _FakeResponse(
                lines=[
                    {"message": {"content": '{"total": '}, "done": False},
                    {"message": {"content": "1.0}"}, "done": False},
                    {"done": True},
                ]
            )

        with patch.dict(os.environ, {"OLLAMA_JUDGE_STREAM": "1"}, clear=True), patch(
            "lib_agent.request.urlopen", side_effect=fake_urlopen
        ):
            result = call_judge_api(
                prompt="grade this",
                model="ollama/llama3.1",
            )

        self.assertEqual(result, {"status": "success", "text": '{"total": 1.0}'})

    def test_ollama_judge_base_url_accepts_host_without_v1(self) -> None:
        with patch.dict(
            os.environ,
            {"OLLAMA_JUDGE_BASE_URL": "http://example.test:11434"},
            clear=True,
        ):
            endpoint = _ollama_native_chat_endpoint()

        self.assertEqual(endpoint, "http://example.test:11434/api/chat")

    def test_ollama_judge_base_url_accepts_old_chat_completions_endpoint(self) -> None:
        with patch.dict(
            os.environ,
            {"OLLAMA_JUDGE_BASE_URL": "http://example.test:11434/v1/chat/completions"},
            clear=True,
        ):
            endpoint = _ollama_native_chat_endpoint()

        self.assertEqual(endpoint, "http://example.test:11434/api/chat")

    def test_ollama_judge_endpoint_ignores_agent_ollama_base_url(self) -> None:
        with patch.dict(
            os.environ,
            {
                "OLLAMA_BASE_URL": "http://agent.example.test:11434",
                "OLLAMA_JUDGE_BASE_URL": "http://judge.example.test:11434",
            },
            clear=True,
        ):
            endpoint = _ollama_native_chat_endpoint()

        self.assertEqual(endpoint, "http://judge.example.test:11434/api/chat")


class LemonadeJudgeTests(unittest.TestCase):
    def test_openai_compat_chat_endpoint_appends_chat_completions(self) -> None:
        endpoint = _openai_compat_chat_endpoint("http://127.0.0.1:13305/api/v1/")

        self.assertEqual(endpoint, "http://127.0.0.1:13305/api/v1/chat/completions")

    def test_openai_compat_chat_endpoint_accepts_full_endpoint(self) -> None:
        endpoint = _openai_compat_chat_endpoint(
            "http://127.0.0.1:8002/v1/chat/completions"
        )

        self.assertEqual(endpoint, "http://127.0.0.1:8002/v1/chat/completions")

    def test_call_judge_api_lemonade_posts_to_local_openai_compat_endpoint(self) -> None:
        captured_request = None

        def fake_urlopen(req, timeout):
            nonlocal captured_request
            captured_request = req
            self.assertEqual(timeout, 17.0)
            return _FakeResponse()

        env = {
            "LEMONADE_JUDGE_BASE_URL": "http://127.0.0.1:8002/v1",
            "LEMONADE_API_KEY": "local-key",
        }
        with patch.dict(os.environ, env, clear=True), patch(
            "lib_agent.request.urlopen", side_effect=fake_urlopen
        ):
            result = call_judge_api(
                prompt="grade this",
                model="lemonade/Qwen3-Coder-30B-A3B-Instruct-GGUF",
                timeout_seconds=17.0,
            )

        self.assertEqual(result["status"], "success")
        self.assertEqual(result["text"], '{"total": 1.0}')
        self.assertIsNotNone(captured_request)
        self.assertEqual(
            captured_request.full_url,
            "http://127.0.0.1:8002/v1/chat/completions",
        )
        self.assertEqual(captured_request.headers["Authorization"], "Bearer local-key")

        payload = json.loads(captured_request.data.decode("utf-8"))
        self.assertEqual(payload["model"], "Qwen3-Coder-30B-A3B-Instruct-GGUF")
        self.assertEqual(payload["temperature"], 0.0)
        self.assertEqual(payload["max_completion_tokens"], 2048)


class CopilotJudgeTests(unittest.TestCase):
    def test_call_judge_api_copilot_uses_default_model_and_safe_options(self) -> None:
        completed = subprocess.CompletedProcess(
            args=["copilot"], returncode=0, stdout='{"total": 0.75}', stderr=""
        )
        with patch("lib_agent.subprocess.run", return_value=completed) as run:
            result = call_judge_api(prompt="grade this", model="copilot", timeout_seconds=12.5)

        self.assertEqual(result, {"status": "success", "text": '{"total": 0.75}'})
        run.assert_called_once()
        cmd = run.call_args.args[0]
        self.assertEqual(cmd[0], "copilot")
        self.assertNotIn("--model", cmd)
        self.assertIn("--allow-all-tools", cmd)
        self.assertIn("--available-tools=", cmd)
        self.assertIn("--no-custom-instructions", cmd)
        self.assertIn("--disable-builtin-mcps", cmd)
        self.assertIn("--disallow-temp-dir", cmd)
        self.assertIn("--no-remote", cmd)
        self.assertIn("--no-remote-export", cmd)
        self.assertEqual(run.call_args.kwargs["timeout"], 12.5)
        self.assertEqual(
            run.call_args.kwargs["input"],
            "You are a strict grading function. "
            "Respond with ONLY a JSON object, no prose, no markdown fences, no extra text."
            "\n\ngrade this",
        )

    def test_call_judge_api_copilot_passes_requested_model(self) -> None:
        completed = subprocess.CompletedProcess(
            args=["copilot"], returncode=0, stdout='{"total": 1.0}', stderr=""
        )
        with patch("lib_agent.subprocess.run", return_value=completed) as run:
            result = call_judge_api(prompt="grade this", model="copilot:auto")

        self.assertEqual(result, {"status": "success", "text": '{"total": 1.0}'})
        cmd = run.call_args.args[0]
        self.assertEqual(cmd[cmd.index("--model") + 1], "auto")

    def test_call_judge_api_copilot_rejects_empty_model_suffix(self) -> None:
        result = call_judge_api(prompt="grade this", model="copilot:")

        self.assertEqual(
            result,
            {"status": "error", "text": "", "error": "Copilot model cannot be empty"},
        )

    def test_call_judge_api_copilot_handles_missing_cli_timeout_and_exit_error(self) -> None:
        with patch("lib_agent.subprocess.run", side_effect=FileNotFoundError):
            missing = call_judge_api(prompt="grade this", model="copilot")
        self.assertEqual(missing["error"], "copilot CLI not found")

        with patch("lib_agent.subprocess.run", side_effect=subprocess.TimeoutExpired("copilot", 5)):
            timeout = call_judge_api(prompt="grade this", model="copilot")
        self.assertEqual(timeout, {"status": "timeout", "text": "", "error": "copilot timed out"})

        completed = subprocess.CompletedProcess(
            args=["copilot"], returncode=2, stdout="", stderr="model unavailable"
        )
        with patch("lib_agent.subprocess.run", return_value=completed):
            failed = call_judge_api(prompt="grade this", model="copilot:auto")
        self.assertEqual(failed["status"], "error")
        self.assertEqual(failed["error"], "copilot exit 2: model unavailable")

    def test_call_judge_api_copilot_marks_quota_exhaustion_as_non_retriable(self) -> None:
        completed = subprocess.CompletedProcess(
            args=["copilot"],
            returncode=1,
            stdout="You have exceeded your monthly quota",
            stderr="",
        )
        with patch("lib_agent.subprocess.run", return_value=completed):
            result = call_judge_api(prompt="grade this", model="copilot:auto")

        self.assertEqual(result["status"], "quota_exceeded")
        self.assertIn("exceeded your monthly quota", result["error"])


if __name__ == "__main__":
    unittest.main()
