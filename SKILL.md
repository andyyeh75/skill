---
name: pinchbench
description: Run PinchBench benchmarks to evaluate OpenClaw agent performance across real-world tasks. Use when testing model capabilities, comparing models, submitting benchmark results to the leaderboard, or running local/private judge experiments such as a remote Ollama judge.
metadata:
  author: pinchbench
  version: "2.0.0-rc1"
  homepage: https://pinchbench.com
  repository: https://github.com/pinchbench/skill
---

# PinchBench Benchmark Skill

PinchBench measures how well LLM models perform as the brain of an OpenClaw agent. Results are collected on a public leaderboard at [pinchbench.com](https://pinchbench.com), but the benchmark can also be run locally with uploads disabled.

## Prerequisites

- Python 3.10+
- [uv](https://docs.astral.sh/uv/) package manager or an initialized `.venv`
- A running OpenClaw instance
- API credentials for the tested model provider
- Optional: a local or remote Ollama server for private LLM judging

## Quick Start

```bash
cd <skill_directory>

# Run benchmark with a specific model
uv run scripts/benchmark.py --model openrouter/anthropic/claude-sonnet-4

# Run only automated tasks
uv run scripts/benchmark.py --model openrouter/anthropic/claude-sonnet-4 --suite automated-only

# Run specific tasks
uv run scripts/benchmark.py --model openrouter/anthropic/claude-sonnet-4 --suite task_calendar,task_stock

# Skip leaderboard upload for local experiments
uv run scripts/benchmark.py --model openrouter/anthropic/claude-sonnet-4 --no-upload
```

## Best Known Method: Remote Ollama Judge

Use `--judge ollama/<model>` when the benchmark subject model should still run through OpenClaw, but LLM grading should go directly to an Ollama server. This can ask Ollama's native `/api/chat` endpoint for strict JSON.

1. On the Ollama host, pull the judge model and make sure the server is reachable from the benchmark machine:

```bash
ollama pull qwen3-coder:30B
ollama serve
```

2. On the benchmark machine, point PinchBench at the remote Ollama base URL. Use the host root URL, `/v1`, `/api/chat`, or `/v1/chat/completions`; the benchmark normalizes these to the native `/api/chat` endpoint.

```bash
export OLLAMA_BASE_URL=http://<ollama-host>:11434
export OLLAMA_JUDGE_NUM_CTX=4096
export OLLAMA_JUDGE_NUM_PREDICT=2048
export OLLAMA_JUDGE_KEEP_ALIVE=0
```

PowerShell equivalent:

```powershell
$env:OLLAMA_BASE_URL = "http://<ollama-host>:11434"
$env:OLLAMA_JUDGE_NUM_CTX = "4096"
$env:OLLAMA_JUDGE_NUM_PREDICT = "2048"
$env:OLLAMA_JUDGE_KEEP_ALIVE = "0"
```

3. Smoke test with a small suite before the full run:

```bash
uv run scripts/benchmark.py \
  --model openai/gpt-5.4-mini \
  --judge ollama/qwen3-coder:30B \
  --suite task_sanity \
  --output-dir results/ollama_smoke \
  --no-upload \
  --no-parallel-judge \
  --verbose
```

4. Run the full local benchmark once the smoke test passes:

```bash
uv run scripts/benchmark.py \
  --model openai/gpt-5.4-mini \
  --judge ollama/qwen3-coder:30B \
  --suite all \
  --output-dir results/full_qwen3_coder_$(date +%Y%m%d) \
  --no-upload \
  --no-parallel-judge \
  --no-fail-fast \
  --verbose
```

On Windows with the checked-in virtual environment, the command shape is:

```powershell
.\.venv\Scripts\python.exe scripts\benchmark.py `
  --model openai/gpt-5.4-mini `
  --judge ollama/qwen3-coder:30B `
  --suite all `
  --output-dir results\full_qwen3_coder_20260623 `
  --no-upload `
  --no-parallel-judge `
  --no-fail-fast `
  --verbose
```

### Ollama Judge Options

| Variable | Use |
| --- | --- |
| `OLLAMA_BASE_URL` | Remote or local Ollama base URL. Defaults to `http://localhost:11434`. |
| `OLLAMA_API_KEY` | Optional bearer token for an authenticated Ollama proxy. |
| `OLLAMA_JUDGE_NUM_CTX` | Sets native `options.num_ctx`; lower it if the judge host runs out of KV cache memory. |
| `OLLAMA_JUDGE_NUM_PREDICT` | Sets native `options.num_predict`; default is `2048`. |
| `OLLAMA_JUDGE_KEEP_ALIVE` | Sets native `keep_alive`; use `0` to unload the judge model after each request. |
| `OLLAMA_JUDGE_STREAM` | Set to `1` to consume streamed native chat chunks. |

Operational notes:

- Prefer `--no-parallel-judge` for remote Ollama unless the judge host has enough memory for concurrent model work.
- Keep `--no-upload` on while comparing private/local judge behavior.
- Use a date-stamped `--output-dir` so result JSON, transcripts, and logs stay grouped.
- If the judge returns prose instead of JSON, lower temperature is already forced; try a stronger judge model or reduce context pressure with `OLLAMA_JUDGE_NUM_CTX`.

## Judge Backends

By default, LLM judging runs through an OpenClaw judge agent. Passing `--judge` switches to direct judge API mode.

Supported direct judge prefixes:

- `openrouter/<provider>/<model>` using `OPENROUTER_API_KEY`
- `kilo/<provider>/<model>` using `KILO_API_KEY`
- `anthropic/<model>` using `ANTHROPIC_API_KEY`
- `openai/<model>` using `OPENAI_API_KEY`
- `ollama/<model>` using native Ollama chat
- `claude` or `claude:<model>` using headless Claude CLI

## Command Line Options

| Option | Description |
| --- | --- |
| `--model` | Model identifier for the OpenClaw benchmark agent. |
| `--judge` | Optional direct judge backend/model. |
| `--suite` | `all`, `automated-only`, a category name, category combination, or comma-separated task IDs. |
| `--core` | Run the representative core task subset. |
| `--output-dir` | Results directory. |
| `--timeout-multiplier` | Scale task timeouts for slower models. |
| `--runs` | Number of runs per task for averaging. |
| `--thinking` | OpenClaw reasoning depth: `off`, `minimal`, `low`, `medium`, `high`, `xhigh`, or `adaptive`. |
| `--no-upload` | Skip uploading to leaderboard. |
| `--no-parallel-judge` | Grade synchronously after each task. |
| `--no-fail-fast` | Continue even if sanity fails. |
| `--no-judge-cache` | Disable persistent judge result caching. |
| `--clear-judge-cache` | Clear the judge cache before running. |
| `--verbose` | Log transcript and workspace details for debugging. |
| `--register` | Request new API token for submissions. |
| `--upload FILE` | Upload previous results JSON. |

## Results

Results are saved as JSON in the output directory. Session transcripts are saved next to the result file in `{run_id}_transcripts/`.

```bash
# View task scores
jq '.tasks[] | {task_id, score: .grading.mean}' results/*.json

# Show failed tasks
jq '.tasks[] | select(.grading.mean < 0.5)' results/*.json

# Calculate overall score
jq '{average: ([.tasks[].grading.mean] | add / length)}' results/*.json
```


## Partial Tasks (23)

| Task | Category | Description |
|------|----------|-------------|
| `task_sanity` | Basic | Verify agent works |
| `task_calendar` | Productivity | Calendar event creation |
| `task_stock` | Research | Stock price lookup |
| `task_blog` | Writing | Blog post creation |
| `task_weather` | Coding | Weather script |
| `task_summary` | Analysis | Document summarization |
| `task_events` | Research | Conference research |
| `task_email` | Writing | Email drafting |
| `task_memory` | Memory | Context retrieval |
| `task_files` | Files | File structure creation |
| `task_workflow` | Integration | Multi-step API workflow |
| `task_clawdhub` | Skills | ClawHub interaction |
| `task_skill_search` | Skills | Skill discovery |
| `task_image_gen` | Creative | Image generation |
| `task_humanizer` | Writing | Text humanization |
| `task_daily_summary` | Productivity | Daily digest |
| `task_email_triage` | Email | Inbox triage |
| `task_email_search` | Email | Email search |
| `task_market_research` | Research | Market analysis |
| `task_spreadsheet_summary` | Analysis | Spreadsheet analysis |
| `task_eli5_pdf_summary` | Analysis | PDF simplification |
| `task_openclaw_comprehension` | Knowledge | OpenClaw docs comprehension |
| `task_second_brain` | Memory | Knowledge management |


## Adding Custom Tasks

Create a markdown file in `tasks/` following `TASK_TEMPLATE.md`. Each task needs:

- YAML frontmatter with id, name, category, grading type, and timeout
- Prompt section
- Expected behavior
- Grading criteria
- Automated checks, LLM judge rubric, or both

## Leaderboard

View public results at [pinchbench.com](https://pinchbench.com). For private experiments, keep `--no-upload` enabled.
