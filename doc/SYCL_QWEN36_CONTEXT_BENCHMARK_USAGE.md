# Qwen3.6 llama.cpp SYCL/Level Zero benchmark launcher

`scripts/run_intel_sycl_qwen36_context_benchmark.sh` runs Qwen3.6-35B-A3B-MTP with the official llama.cpp SYCL release on an Intel GPU through Level Zero. It has two modes:

- No `--suite`: streaming context microbenchmark with TTFT, decode TPS, and optional server CPU telemetry.
- `--suite`: PinchBench execution and GNAI judging against the same llama.cpp server.

The launcher uses the official b10756 SYCL FP16 package by default, a compatible oneAPI 2025.3 runtime, `level_zero:0`, `--gpu-layers all`, 16 CPU threads, a 98,304-token context, and `--cache-ram 8192` MiB. It does not enable persistent disk KV caching.

## Prerequisites

Run from the repository root. The defaults expect these local assets:

- The Qwen3.6 GGUF model at the path configured by `QWEN_MODEL`.
- The extracted official release at `downloads/llama-b10756-bin-ubuntu-sycl-fp16-x64/llama-b10756`.
- The extracted oneAPI 2025.3 runtime at `downloads/oneapi-2025.3-runtime/root/opt/intel/oneapi`.
- Docker access and Intel GPU device nodes, including `/dev/dri/renderD128`.
- OpenClaw configured for local-agent PinchBench execution. For GNAI judging, load the GNAI credential as described by the project `SKILL.md`.

The launcher refuses to run if `LLAMA_SYCL_PORT` is already occupied. Stop the previous server first or select an unused port; this prevents a health check from silently connecting to a stale server.

```bash
docker ps --format '{{.Names}}\t{{.Status}}' | rg 'llama-sycl-qwen36'
ss -ltnH 'sport = :8090'
```

## Streaming context benchmark

This measures client-observed time-to-first-token (TTFT) and llama.cpp server-reported decode TPS for short and long prompts. The measurement tool adds a unique identifier at the beginning of every prompt to avoid prompt-prefix cache reuse.

```bash
LLAMA_SYCL_CTX_SIZE=98304 \
bash ./scripts/run_intel_sycl_qwen36_context_benchmark.sh \
  outputs/sycl_context_b10756_fp16
```

Useful overrides:

```bash
LLAMA_SYCL_RELEASE_VARIANT=fp32 \
LLAMA_SYCL_CTX_SIZE=98304 \
LLAMA_SYCL_RUNS=5 \
LLAMA_SYCL_WARMUP_RUNS=1 \
LLAMA_SYCL_SHORT_WORDS=32 \
LLAMA_SYCL_LONG_WORDS=16384 \
LLAMA_SYCL_MAX_TOKENS=128 \
LLAMA_SYCL_CACHE_RAM_MIB=8192 \
bash ./scripts/run_intel_sycl_qwen36_context_benchmark.sh outputs/sycl_context_fp32
```

The output directory contains `effective_config.env` (all resolved runtime values and server arguments), the device list (`sycl-ls.log`), health and model responses, the server log, per-run stream traces, and `bench-sycl-qwen36-35b-context.json`. Use the JSON's p50 TTFT and TPS values for the report; TPS comes from llama.cpp's full predicted-token timing interval, not generic agent throughput.

## PinchBench suites

Pass any standard PinchBench suite selector after the output directory. The launcher leaves judging enabled unless `--no-judge` is supplied.

```bash
# One task
LLAMA_SYCL_CTX_SIZE=98304 \
bash ./scripts/run_intel_sycl_qwen36_context_benchmark.sh \
  outputs/sycl_task_daily_summary \
  --suite task_daily_summary --runs 1 --judge gnai/gpt-5.6-luna

# Several explicit tasks
bash ./scripts/run_intel_sycl_qwen36_context_benchmark.sh \
  outputs/sycl_selected_tasks \
  --suite task_summary,task_memory,task_session_chain_analysis \
  --runs 1 --judge gnai/gpt-5.6-luna

# A category or the complete suite
bash ./scripts/run_intel_sycl_qwen36_context_benchmark.sh \
  outputs/sycl_coding \
  --suite coding --runs 1 --judge gnai/gpt-5.6-luna
```

For an intentional continuation, provide the remaining task IDs as a comma-separated `--suite` value. This preserves the original run's artifacts while writing the continuation to a distinct directory.

```bash
suite=$(uv run python - <<'PY'
from pathlib import Path
from scripts.lib_tasks import TaskLoader
tasks = TaskLoader(Path('tasks')).load_all_tasks()
print(','.join(task.task_id for task in tasks[19:]))  # original tasks 20 through 147
PY
)

bash ./scripts/run_intel_sycl_qwen36_context_benchmark.sh \
  outputs/sycl_resume_0020_0147 \
  --suite "$suite" --runs 1 --judge gnai/gpt-5.6-luna
```

## PinchBench safeguards and configuration

For suite runs, the launcher applies these defaults:

| Setting | Default | Purpose |
| --- | ---: | --- |
| `LLAMA_SYCL_CTX_SIZE` | 98,304 | llama.cpp server context window. |
| `LLAMA_SYCL_CACHE_RAM_MIB` | 8,192 | In-RAM KV cache budget. `LLAMA_SYCL_CACHE_RAM` remains a compatibility alias. |
| `LLAMA_SYCL_PINCHBENCH_MAX_TOKENS` | 16,384 | Custom-endpoint model output limit. |
| `PINCHBENCH_OPENCLAW_TOOL_RESULT_MAX_CHARS` | 6,000 | Per-benchmark-agent cap for retained live tool output. |
| `PINCHBENCH_TASK_WALL_CLOCK_SECONDS` | 600 | Hard execution ceiling per task. |
| `PINCHBENCH_SCORE_ZERO_AFTER_SECONDS` | 600 | Force score zero when the ceiling is reached. |

The script records all resolved runtime values in `effective_config.env` and suite limits in `pinchbench_config.txt`. It temporarily enables OpenClaw's mid-turn precheck during the benchmark so dense web-tool results can be truncated before they cause a tool-loop context overflow. Each run uses an isolated temporary OpenClaw provider with a 1,800-second stream-idle watchdog; both that provider and the prior mid-turn setting are removed or restored when the script exits.

To change a default for one invocation:

```bash
PINCHBENCH_OPENCLAW_TOOL_RESULT_MAX_CHARS=8000 \
PINCHBENCH_TASK_WALL_CLOCK_SECONDS=600 \
PINCHBENCH_SCORE_ZERO_AFTER_SECONDS=600 \
bash ./scripts/run_intel_sycl_qwen36_context_benchmark.sh \
  outputs/sycl_custom_limits --suite task_summary --runs 1 --judge gnai/gpt-5.6-luna
```

## Troubleshooting

- `Permission denied`: invoke the script through `bash`, as in the examples, or mark only that script executable with `chmod u+x scripts/run_intel_sycl_qwen36_context_benchmark.sh`.
- `libsycl.so.8` missing: install or point `ONEAPI_SYCL_RUNTIME_ROOT` at the extracted oneAPI 2025.3 runtime expected by the official b10756 release.
- Port already in use: stop the existing llama-server or set `LLAMA_SYCL_PORT` to a free port. Do not rely on an existing process at the default port.
- Server fails before readiness: inspect `OUTPUT_DIR/server.log`, `OUTPUT_DIR/sycl-ls.log`, and `OUTPUT_DIR/endpoint-models.json`.
- A failed launch: `OUTPUT_DIR/benchmark.status` records a terminal `failed` state and exit code; `effective_config.env` preserves the attempted configuration.
- GNAI judge reports a missing API key: source the project-approved GNAI credential configuration before launching; do not substitute an OpenRouter key for a `gnai/...` judge.
- A task reaches 600 seconds: this is a recorded hard cutoff and intentionally receives a score of zero. Its transcript and elapsed time remain in the results.
