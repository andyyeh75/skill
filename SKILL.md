---
name: pinchbench
description: Run PinchBench benchmarks to evaluate OpenClaw agent performance across real-world tasks. Use when testing model capabilities, comparing models, submitting benchmark results to the leaderboard.
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

## Judge Backends

By default, LLM judging runs through an OpenClaw judge agent. Passing `--judge` switches to direct judge API mode.

Supported direct judge prefixes:

- `openrouter/<provider>/<model>` using `OPENROUTER_API_KEY`
- `kilo/<provider>/<model>` using `KILO_API_KEY`
- `anthropic/<model>` using `ANTHROPIC_API_KEY`
- `openai/<model>` using `OPENAI_API_KEY`
- `claude` or `claude:<model>` using headless Claude CLI
- `gnai/<model>` using `GNAI_API_KEY` or `~/gnai_api_key.rc`

### SYCL Runtime and Scoring Limits

The Intel SYCL Qwen launcher checks the configured RAM KV cache; the standard
profile uses 8 GiB (`LLAMA_SYCL_CACHE_RAM_MIB=8192`). A task may execute for up
to 10 minutes, independently of `--timeout-multiplier`; only a task that
reaches that 10-minute limit receives a score of `0.0`:

| Variable | Default | Meaning |
| --- | ---: | --- |
| `PINCHBENCH_TASK_WALL_CLOCK_SECONDS` | `600` | Hard execution stop (10 minutes). |
| `PINCHBENCH_SCORE_ZERO_AFTER_SECONDS` | `600` | A task that reaches 10 minutes receives a final score of `0.0`. |

The result JSON retains elapsed time and timeout metadata so the final report
can distinguish an execution cutoff from a score zero caused by the
10-minute limit.

### OpenClaw SYCL provider stream-idle watchdog

For long local llama.cpp prefills, configure the OpenClaw provider timeout
separately from PinchBench task cutoffs. The provider stream-idle watchdog
aborts a model request if no response token arrives before this limit; changing
`--timeout-multiplier`, an agent `--timeout`, or a systemd service lifetime
does not raise it.

For the local `sycl` provider, set a 30-minute ceiling in
`~/.openclaw/openclaw.json`:

```json
{
  "models": {
    "providers": {
      "sycl": {
        "timeoutSeconds": 1800
      }
    }
  }
}
```

Validate and activate the change before benchmarking:

```bash
openclaw config validate
systemctl --user restart openclaw-gateway.service
openclaw config get models.providers.sycl.timeoutSeconds
```

Use an OpenClaw agent timeout of at least 1800 seconds for the corresponding
run. Apply this only to the intended `sycl` provider; do not raise unrelated
cloud or judge-provider timeouts. Record the setting in the run report because
an idle-watchdog abort is not a model-quality or TTFT result.

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

### Local Lemonade Timeout Guidance

Use bounded task timeouts for normal Lemonade smoke tests and scored comparisons. For a
deliberate artifact-collection run where slow local tasks must finish, add:

```bash
--timeout-multiplier 1000
```

This is a multiplier, not a seconds value. A value of `1000` is effectively unlimited and can
allow runaway tasks to consume excessive time and tokens, so record it with the run and use it
only for diagnostic or artifact-preservation workflows. See
`doc/PINCHBENCH_LEMONADE_QWEN3_6_35B_GUIDE.md` for bounded smoke, scored, and full-run profiles.

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


## Partial Tasks for automated grading (23)

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
