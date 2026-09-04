#!/usr/bin/env bash
# Configure the host OpenClaw Gateway for vLLM, then run PinchBench or a direct
# streaming-context microbenchmark.
# This uses the host Gateway configured by configure_openclaw_vllm.sh; it does
# not start or modify an ima-openclaw Docker project.
set -Eeuo pipefail

usage() {
    cat <<'EOF'
usage: scripts/run_openclaw_vllm_pinchbench.sh OUTPUT_DIR [--suite SUITE] [options]

Configure the host OpenClaw Gateway for the vLLM endpoint. With --suite, run
PinchBench through that Gateway. Without --suite, run the shared direct SSE
streaming TTFT/TPS context microbenchmark against vLLM; it never starts a
PinchBench run accidentally.

SUITE accepts the standard PinchBench selectors: a task ID, a 1-based task
ordinal, comma-separated task IDs, a category, category combinations joined
by '+', automated-only, or all.

Options:
  --suite SUITE              PinchBench suite selector
  --runs N                   Runs per task, or context samples without --suite
  --judge MODEL              Judge model (default: PINCHBENCH_JUDGE or
                             gnai/gpt-5.6-luna)
  --no-judge                 Do not run a judge
  --timeout-multiplier N     Scale task timeouts (default:
                             PINCHBENCH_TIMEOUT_MULTIPLIER or 1000).
                             Pass an empty value to use native 1.0x timeouts.
  --thinking LEVEL           OpenClaw thinking level (default: off)
  -h, --help                 Show this help

Endpoint overrides are shared with configure_openclaw_vllm.sh:
  OPENCLAW_VLLM_BASE_URL, OPENCLAW_VLLM_MODEL,
  OPENCLAW_VLLM_PROVIDER_ID, OPENCLAW_BIN, OPENCLAW_GATEWAY_SERVICE

Context-mode overrides (used only without --suite):
  OPENCLAW_VLLM_CONTEXT_SIZE (256000), OPENCLAW_VLLM_CONTEXT_RUNS (3),
  OPENCLAW_VLLM_CONTEXT_WARMUP_RUNS (1), OPENCLAW_VLLM_SHORT_INPUT_WORDS (32),
  OPENCLAW_VLLM_LONG_INPUT_WORDS (16384), OPENCLAW_VLLM_CONTEXT_MAX_TOKENS (128),
  OPENCLAW_VLLM_CONTEXT_TIMEOUT (1800)

PinchBench time-limit overrides (used only with --suite):
  PINCHBENCH_TASK_WALL_CLOCK_SECONDS (600),
  PINCHBENCH_SCORE_ZERO_AFTER_SECONDS (600)

Examples:
  scripts/run_openclaw_vllm_pinchbench.sh results/vllm-sanity \
    --suite task_sanity --no-judge

  scripts/run_openclaw_vllm_pinchbench.sh results/vllm-automated \
    --suite automated-only --runs 1

  scripts/run_openclaw_vllm_pinchbench.sh results/vllm-context --runs 3
EOF
}

if [[ ${1:-} == '--help' || ${1:-} == '-h' ]]; then
    usage
    exit 0
fi

if [[ $# -lt 1 ]]; then
    usage >&2
    exit 64
fi

output_arg=$1
shift
suite=''
runs=${PINCHBENCH_RUNS:-1}
judge=${PINCHBENCH_JUDGE:-gnai/gpt-5.6-luna}
# Keep the historical 1000x default. The single-dash expansion intentionally
# preserves an explicitly empty environment value, which is the manual opt-out
# used to omit --timeout-multiplier and select native task timeouts.
timeout_multiplier=${PINCHBENCH_TIMEOUT_MULTIPLIER-1000}
thinking=${PINCHBENCH_THINKING:-off}
no_judge=false
runs_explicit=false
task_wall_clock_seconds=${PINCHBENCH_TASK_WALL_CLOCK_SECONDS:-600}
score_zero_after_seconds=${PINCHBENCH_SCORE_ZERO_AFTER_SECONDS:-600}

while [[ $# -gt 0 ]]; do
    case "$1" in
        --suite)
            [[ $# -ge 2 ]] || { echo '--suite requires a value' >&2; exit 64; }
            suite=$2
            shift 2
            ;;
        --runs)
            [[ $# -ge 2 ]] || { echo '--runs requires a value' >&2; exit 64; }
            runs=$2
            runs_explicit=true
            shift 2
            ;;
        --judge)
            [[ $# -ge 2 ]] || { echo '--judge requires a value' >&2; exit 64; }
            judge=$2
            shift 2
            ;;
        --no-judge)
            no_judge=true
            shift
            ;;
        --timeout-multiplier)
            [[ $# -ge 2 ]] || { echo '--timeout-multiplier requires a value' >&2; exit 64; }
            timeout_multiplier=$2
            shift 2
            ;;
        --thinking)
            [[ $# -ge 2 ]] || { echo '--thinking requires a value' >&2; exit 64; }
            thinking=$2
            shift 2
            ;;
        --help|-h)
            usage
            exit 0
            ;;
        *)
            echo "unknown option: $1" >&2
            usage >&2
            exit 64
            ;;
    esac
done

if [[ -z "$suite" && "$runs_explicit" != true ]]; then
    runs=${OPENCLAW_VLLM_CONTEXT_RUNS:-3}
fi
[[ "$runs" =~ ^[1-9][0-9]*$ ]] || { echo '--runs must be a positive integer' >&2; exit 64; }
if [[ -n "$timeout_multiplier" ]]; then
    [[ "$timeout_multiplier" =~ ^[1-9][0-9]*$ ]] || {
        echo '--timeout-multiplier must be a positive integer' >&2
        exit 64
    }
fi
if [[ -n "$suite" ]]; then
    [[ "$task_wall_clock_seconds" =~ ^[1-9][0-9]*$ ]] || {
        echo 'PINCHBENCH_TASK_WALL_CLOCK_SECONDS must be a positive integer' >&2
        exit 64
    }
    [[ "$score_zero_after_seconds" =~ ^[1-9][0-9]*$ ]] || {
        echo 'PINCHBENCH_SCORE_ZERO_AFTER_SECONDS must be a positive integer' >&2
        exit 64
    }
    if (( score_zero_after_seconds > task_wall_clock_seconds )); then
        echo 'PINCHBENCH_SCORE_ZERO_AFTER_SECONDS cannot exceed PINCHBENCH_TASK_WALL_CLOCK_SECONDS' >&2
        exit 64
    fi
fi
case "$thinking" in
    off|minimal|low|medium|high|xhigh|adaptive) ;;
    *) echo "unsupported --thinking level: $thinking" >&2; exit 64 ;;
esac

workspace=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
run_dir=$(mkdir -p "$output_arg" && cd "$output_arg" && pwd)
configure_script="$workspace/scripts/configure_openclaw_vllm.sh"
runner="$workspace/scripts/run.sh"
provider_id=${OPENCLAW_VLLM_PROVIDER_ID:-intel-vllm}
model_id=${OPENCLAW_VLLM_MODEL:-Qwen3.6-35B-A3B}
model_ref="${provider_id}/${model_id}"
base_url=${OPENCLAW_VLLM_BASE_URL:-http://10.5.235.33:8000/v1}

[[ -x "$configure_script" ]] || { echo "missing executable: $configure_script" >&2; exit 66; }
if [[ -n "$suite" ]]; then
    [[ -x "$runner" ]] || { echo "missing executable: $runner" >&2; exit 66; }
    command -v uv >/dev/null || { echo 'Required command not found: uv' >&2; exit 127; }
fi

# The benchmark agent uses the Gateway. Preserve direct access to this private
# endpoint if the host has proxy variables or Node proxy support enabled.
endpoint_authority=${base_url#*://}
endpoint_authority=${endpoint_authority%%/*}
endpoint_host=${endpoint_authority%%:*}
for no_proxy_entry in "$endpoint_host" "$endpoint_authority"; do
    [[ -n "$no_proxy_entry" ]] || continue
    case ",${NO_PROXY:-}," in
        *",${no_proxy_entry},"*) ;;
        *) NO_PROXY="${NO_PROXY:+${NO_PROXY},}${no_proxy_entry}" ;;
    esac
done
export NO_PROXY
export no_proxy="$NO_PROXY"

echo 'Configuring and validating the host OpenClaw Gateway...'
"$configure_script" |& tee "$run_dir/configure.log"

if [[ -z "$suite" ]]; then
    context_size=${OPENCLAW_VLLM_CONTEXT_SIZE:-256000}
    context_warmup_runs=${OPENCLAW_VLLM_CONTEXT_WARMUP_RUNS:-1}
    short_input_words=${OPENCLAW_VLLM_SHORT_INPUT_WORDS:-32}
    long_input_words=${OPENCLAW_VLLM_LONG_INPUT_WORDS:-16384}
    context_max_tokens=${OPENCLAW_VLLM_CONTEXT_MAX_TOKENS:-128}
    context_timeout=${OPENCLAW_VLLM_CONTEXT_TIMEOUT:-1800}
    context_script="$workspace/scripts/measure_llamacpp_context_streaming.py"
    [[ -f "$context_script" ]] || { echo "missing: $context_script" >&2; exit 66; }
    printf 'started %s mode=context-streaming\n' "$(date -u +%FT%TZ)" > "$run_dir/status"
    printf 'mode=context-streaming\nruns=%s\nmodel=%s\nbase_url=%s\nctx_size=%s\nwarmup_runs=%s\nshort_input_words=%s\nlong_input_words=%s\nmax_tokens=%s\n' \
        "$runs" "$model_id" "$base_url" "$context_size" "$context_warmup_runs" \
        "$short_input_words" "$long_input_words" "$context_max_tokens" > "$run_dir/context_config.txt"
    echo "Running direct vLLM streaming context benchmark with ${model_id}..."
    python3 "$context_script" \
        --base-url "$base_url" --output-dir "$run_dir" --model "$model_id" \
        --backend vllm-openai-compatible \
        --backend-args "vLLM remote endpoint; base_url=${base_url}; chat_template_kwargs.enable_thinking=false" \
        --ctx-size "$context_size" --runs "$runs" --warmup-runs "$context_warmup_runs" \
        --short-input-words "$short_input_words" --long-input-words "$long_input_words" \
        --max-tokens "$context_max_tokens" --timeout "$context_timeout" \
        --extra-body-json '{"chat_template_kwargs":{"enable_thinking":false}}' \
        --output-filename "bench-vllm-qwen36-35b-context.json" |& tee "$run_dir/benchmark.log"
    printf 'completed %s mode=context-streaming benchmark_exit=0\n' "$(date -u +%FT%TZ)" > "$run_dir/status"
    exit 0
fi

mkdir -p "$run_dir/results"
printf 'started %s\n' "$(date -u +%FT%TZ)" > "$run_dir/status"
timeout_multiplier_label=${timeout_multiplier:-native-1.0x}
printf 'suite=%s\nruns=%s\nmodel=%s\nbase_url=%s\nthinking=%s\ntimeout_multiplier=%s\ntask_wall_clock_seconds=%s\nscore_zero_after_seconds=%s\njudge=%s\n' \
    "$suite" "$runs" "$model_ref" "$base_url" "$thinking" "$timeout_multiplier_label" \
    "$task_wall_clock_seconds" "$score_zero_after_seconds" \
    "$([[ "$no_judge" == true ]] && printf '%s' '--no-judge' || printf '%s' "$judge")" \
    > "$run_dir/pinchbench_config.txt"

if [[ "$no_judge" == true ]]; then
    judge_args=(--no-judge)
else
    judge_args=(--judge "$judge")
fi
runner_args=(
    --model "$model_ref" --suite "$suite" --runs "$runs"
)
if [[ -n "$timeout_multiplier" ]]; then
    runner_args+=(--timeout-multiplier "$timeout_multiplier")
fi
runner_args+=(
    --thinking "$thinking"
    --task-wall-clock-seconds "$task_wall_clock_seconds"
    --score-zero-after-seconds "$score_zero_after_seconds"
    --no-upload --no-fail-fast
    "${judge_args[@]}"
    --output-dir "$run_dir/results"
)

echo "Running PinchBench suite '${suite}' with ${model_ref}..."
set +e
"$runner" "${runner_args[@]}" |& tee "$run_dir/benchmark.log"
benchmark_status=${PIPESTATUS[0]}
set -e

printf 'completed %s suite=%s benchmark_exit=%s\n' \
    "$(date -u +%FT%TZ)" "$suite" "$benchmark_status" > "$run_dir/status"
exit "$benchmark_status"
