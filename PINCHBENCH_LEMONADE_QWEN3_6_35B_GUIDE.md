# PinchBench Lemonade Runbook

This runbook describes how to run PinchBench with a local Lemonade
OpenAI-compatible endpoint. The example model is:

```text
lemonade/Qwen3.6-35B-A3B-MTP-GGUF
```

The recommended workflow is:

1. Run a smoke test after configuration changes.
2. Run a no-judge throughput pass to collect transcripts cheaply.
3. Run a small scored suite with an isolated judge.
4. Run the full suite only after the smaller runs are stable.

## Endpoint and model configuration

The default local Lemonade endpoint is:

```text
http://127.0.0.1:13305/api/v1
```

PinchBench and OpenClaw use the OpenAI-compatible chat-completions route
under that base URL:

```text
http://127.0.0.1:13305/api/v1/chat/completions
```

Prefer explicit PinchBench flags for benchmark runs so the endpoint used by
the benchmark is visible in the command and does not depend on generated
agent configuration:

```bash
MODEL="lemonade/Qwen3.6-35B-A3B-MTP-GGUF"
BASE_URL="http://127.0.0.1:13305/api/v1"
API_KEY="${OPENAI_API_KEY:-dummy}"
```

Use the model name after `lemonade/` as the provider model ID. For example,
the API receives `Qwen3.6-35B-A3B-MTP-GGUF` as the model value.

If OpenClaw is configured directly, its Lemonade provider should use the same
base URL and `api: "openai-completions"`. Keep the advertised
`contextWindow` at or below the context size actually served by Lemonade.
Start with `maxTokens: 4096`; raise it only when a task demonstrably needs
longer output. The exact OpenClaw config path varies by installation, so
prefer the PinchBench `--base-url` and `--api-key` flags for reproducible runs.

## Important flags

- `--base-url URL` selects the Lemonade endpoint and bypasses OpenRouter model validation.
- `--api-key KEY` supplies the key for the benchmark agent endpoint.
- `--judge MODEL` selects a direct API judge. Use `--judge`, not `--judge-model`.
- `--no-judge` executes tasks and saves transcripts without calling a judge.
- `--no-parallel-judge` grades synchronously; use it when agent and judge share local hardware.
- `--no-upload` keeps results local.
- `--no-fail-fast` continues after a zero-scoring sanity task.
- `--timeout-multiplier N` scales task timeouts. Use a bounded value for normal comparisons.

`--no-judge` is for throughput, stability, and transcript collection. It is
not a scored benchmark: each task receives a skipped grade and the result
should not be compared with normally graded runs.

Do not make `--timeout-multiplier 1000` a general default. It is an intentional
artifact-collection setting for slow local runs and can allow runaway tasks to
consume excessive time and tokens. Use it only when the goal is to let slow
tasks finish and preserve their final artifacts.

## Run profiles

Set the shared variables first:

```bash
MODEL="lemonade/Qwen3.6-35B-A3B-MTP-GGUF"
BASE_URL="http://127.0.0.1:13305/api/v1"
API_KEY="${OPENAI_API_KEY:-dummy}"
```

### 1. Smoke test

Run this after changing Lemonade, OpenClaw, or PinchBench configuration:

```bash
./scripts/run.sh \
  --model "$MODEL" \
  --base-url "$BASE_URL" \
  --api-key "$API_KEY" \
  --suite task_sanity \
  --no-upload \
  --verbose
```

Confirm that:

- the task completes without provider or context errors;
- a transcript is written under the selected results directory; and
- the response is task-shaped rather than an extended self-dialogue.

### 2. No-judge throughput pass

Use a narrower suite first. `automated-only` is useful for checking concrete
task behavior without judge latency:

```bash
./scripts/run.sh \
  --model "$MODEL" \
  --base-url "$BASE_URL" \
  --api-key "$API_KEY" \
  --suite automated-only \
  --no-judge \
  --no-upload \
  --no-fail-fast \
  --output-dir "results/lemonade_nojudge_$(date +%Y%m%d)"
```

For a slow local setup that must preserve final artifacts, add:

```bash
--timeout-multiplier 1000
```

Record that setting with the run results because it removes the normal
timeout protection.

### 3. Scored pass

Prefer a separate judge host or provider. This prevents agent inference and
judge inference from competing for the same local GPU, CPU, memory, or model
cache.

For an Ollama judge:

```bash
export OLLAMA_JUDGE_BASE_URL="http://<judge-host>:11434"
export OLLAMA_JUDGE_NUM_CTX=4096
export OLLAMA_JUDGE_NUM_PREDICT=2048
export OLLAMA_JUDGE_KEEP_ALIVE=0

./scripts/run.sh \
  --model "$MODEL" \
  --base-url "$BASE_URL" \
  --api-key "$API_KEY" \
  --judge ollama/qwen3-coder:30B \
  --suite automated-only \
  --no-parallel-judge \
  --no-upload \
  --no-fail-fast \
  --output-dir "results/lemonade_scored_$(date +%Y%m%d)"
```

Use `--no-parallel-judge` when the judge is local or otherwise shares
resources with the agent. Parallel judging is appropriate only when the
judge is remote or isolated.

For a Lemonade judge, set its endpoint separately from the agent endpoint:

```bash
export LEMONADE_JUDGE_BASE_URL="http://127.0.0.1:13305/api/v1"
export LEMONADE_API_KEY="${OPENAI_API_KEY:-dummy}"

./scripts/run.sh \
  --model "$MODEL" \
  --base-url "$BASE_URL" \
  --api-key "$API_KEY" \
  --judge lemonade/Qwen3-Coder-30B-A3B-Instruct-GGUF \
  --suite automated-only \
  --no-parallel-judge \
  --no-upload
```

The direct judge path accepts either a Lemonade API base URL or a full
`/chat/completions` URL and sends `LEMONADE_API_KEY` as a bearer token when it
is set. Running the same local model as both agent and judge can distort
throughput, so a separate judge is preferred for comparisons.

### 4. Full run

Run the full suite only after the smoke and scored subset are stable:

```bash
./scripts/run.sh \
  --model "$MODEL" \
  --base-url "$BASE_URL" \
  --api-key "$API_KEY" \
  --judge ollama/qwen3-coder:30B \
  --suite all \
  --no-parallel-judge \
  --no-upload \
  --no-fail-fast \
  --output-dir "results/lemonade_full_$(date +%Y%m%d)"
```

Add `--timeout-multiplier 1000` only for a deliberate long-running artifact
collection pass. Inspect transcripts afterward for over-researching, tool
loops, and excessive token use.

## Tuning rules

Change one setting per run so results remain interpretable.

| Symptom | First adjustment | Follow-up |
| --- | --- | --- |
| Provider or connection errors | Verify the endpoint and run the smoke test | Reduce local concurrency and use `--no-parallel-judge` |
| Reasonable tasks time out | Use a narrower suite and inspect the transcript | Increase the timeout modestly |
| Answers are verbose or loop-like | Lower OpenClaw `maxTokens` | Keep reasoning disabled if supported by the model config |
| Answers truncate | Raise `maxTokens` for the affected suite | Check context size and prompt growth |
| Judge is slow | Use a remote or separate judge | Keep `--no-parallel-judge` for shared hardware |
| High token use with low scores | Inspect the highest-token tasks | Tune model output limits and task scope separately |

Keep normal benchmark runs bounded. Treat an effectively unlimited timeout as
a diagnostic or artifact-preservation mode, not as evidence of efficiency.

## Measuring results

Inspect score, runtime, and token use after each run:

```bash
jq '{
  model,
  run_id,
  suite,
  efficiency: {
    total_tokens: .efficiency.total_tokens,
    total_execution_time_seconds: .efficiency.total_execution_time_seconds,
    tokens_per_task: .efficiency.tokens_per_task,
    score_per_1k_tokens: .efficiency.score_per_1k_tokens
  },
  category_scores
}' results/<run_dir>/*.json
```

Find the most expensive tasks:

```bash
jq -r '
  .efficiency.per_task
  | sort_by(.total_tokens)
  | reverse
  | .[:15][]
  | [.task_id, .total_tokens, .score]
  | @tsv
' results/<run_dir>/*.json
```

For comparisons, keep the model, suite, timeout policy, judge, and output
limits visible in the run notes. Do not compare a no-judge artifact run with a
normally graded run as if they used the same scoring process.

## Post-hoc grading

Use this procedure when a run used `--no-judge` or when online grading failed
but transcripts were archived.

### Preserve the original run

Do not overwrite the original result JSON. It records execution order,
transcript paths, and the fact that grading was skipped or failed. Save
post-hoc grades and any merged report separately.

Define paths for the run being reviewed:

```bash
RUN_DIR="results/<run_dir>"
RESULT_JSON="$RUN_DIR/<run_id>_<model-slug>.json"
TRANSCRIPT_DIR="$RUN_DIR/<run_id>_transcripts"
MAPPING="$RUN_DIR/<run_id>_task_mapping.tsv"
```

Check whether the run is complete:

```bash
jq '{run_id, model, suite, in_progress, completed_tasks, total_tasks,
     task_count: (.tasks | length)}' "$RESULT_JSON"
```

If `in_progress` is true, grade only tasks already present in `.tasks[]`.

### Build and verify task mapping

The result JSON is the source of truth for task order. Create an ordinal to
task mapping without relying on `benchmark.log`:

```bash
jq -r '
  .tasks
  | to_entries[]
  | [(.key + 1), .value.task_id, .value.status,
     .value.transcript_length,
     (.value.frontmatter.grading_type // "")]
  | @tsv
' "$RESULT_JSON" > "$MAPPING"
```

Verify that each completed task has its definition and transcript:

```bash
while IFS=$'\t' read -r ordinal task_id status transcript_len grading_type; do
  task_file="tasks/${task_id}.md"
  transcript_file="$TRANSCRIPT_DIR/${task_id}.jsonl"
  [ -s "$task_file" ] || echo "missing task: $ordinal $task_id"
  [ -s "$transcript_file" ] || echo "missing transcript: $ordinal $task_id"
done < "$MAPPING"
```

For multi-session tasks, read files matching
`${task_id}_session*.jsonl` in session-number order before the final
`${task_id}.jsonl` transcript.

### Apply grading boundaries

- `llm_judge`: grade from the task definition, rubric, and complete transcript.
- `hybrid`: grade only the LLM component unless the exact automated workspace
  artifacts are also available.
- `automated`: do not invent a final score. Run the native automated grader or
  mark the task ungradable when the required workspace snapshot is missing.

Do not use `benchmark.log` as the grading source. It contains progress and
previews, not necessarily the complete task conversation or workspace state.

Future exact post-hoc grading requires per-task workspace archives in addition
to transcripts, for example:

```text
results/<run_dir>/<run_id>_transcripts/<task_id>.jsonl
results/<run_dir>/<run_id>_workspaces/<task_id>/
```

Save post-hoc grades in a separate file such as:

```text
<run_dir>/posthoc_grades/<run_id>_posthoc_grades.jsonl
```

Include grading coverage and provenance in any final report: native automated
grades, post-hoc LLM grades, complete or partial hybrid grades, and tasks that
remain ungradable.

## Common mistakes

- Use `--judge MODEL`; `--judge-model` is not a PinchBench option.
- Set `LEMONADE_JUDGE_BASE_URL` when using a direct Lemonade judge; the agent's
  `--base-url` does not configure the judge endpoint.
- Use `--no-judge` only when collecting execution artifacts or testing
  throughput; it does not produce comparable scored results.
- Keep the original result JSON when creating post-hoc grades.
- Do not assign scores to automated tasks from a transcript alone when native
  workspace artifacts are unavailable.
