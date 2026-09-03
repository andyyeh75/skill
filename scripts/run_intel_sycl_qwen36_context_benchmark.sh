#!/usr/bin/env bash
# Benchmark Qwen3.6-35B through an official llama.cpp SYCL release on Level Zero.
# This intentionally does not enable --cache-disk or any persistent KV feature.
set -Eeuo pipefail

usage() {
    cat <<EOF
usage: $0 OUTPUT_DIR [--suite SUITE] [--runs N] [--judge MODEL] [--no-judge]

Without --suite, run the existing streaming context microbenchmark.
With --suite, start the same SYCL/Level Zero llama.cpp server and run PinchBench.

SUITE accepts the standard PinchBench selectors: a task ID, a 1-based task
ordinal, comma-separated task IDs, a category, category combinations joined
by '+', automated-only, or all.

The default runtime is the extracted b10756 SYCL FP16 release. Override with
LLAMA_SYCL_RELEASE_VARIANT=fp32 or LLAMA_SYCL_RELEASE_DIR=/path/to/llama-b10756.
EOF
}

if [[ ${1:-} == "--help" || ${1:-} == "-h" ]]; then
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
pinchbench_runs=${PINCHBENCH_RUNS:-1}
pinchbench_timeout_multiplier=${PINCHBENCH_TIMEOUT_MULTIPLIER:-1000}
pinchbench_judge=${PINCHBENCH_JUDGE:-gnai/gpt-5.6-luna}
pinchbench_no_judge=false
pinchbench_tool_result_max_chars=${PINCHBENCH_OPENCLAW_TOOL_RESULT_MAX_CHARS:-6000}
pinchbench_task_wall_clock_seconds=${PINCHBENCH_TASK_WALL_CLOCK_SECONDS:-600}
pinchbench_score_zero_after_seconds=${PINCHBENCH_SCORE_ZERO_AFTER_SECONDS:-600}

while [[ $# -gt 0 ]]; do
    case "$1" in
        --suite)
            [[ $# -ge 2 ]] || { echo "--suite requires a value" >&2; exit 64; }
            suite=$2
            shift 2
            ;;
        --runs)
            [[ $# -ge 2 ]] || { echo "--runs requires a value" >&2; exit 64; }
            pinchbench_runs=$2
            shift 2
            ;;
        --judge)
            [[ $# -ge 2 ]] || { echo "--judge requires a value" >&2; exit 64; }
            pinchbench_judge=$2
            shift 2
            ;;
        --no-judge)
            pinchbench_no_judge=true
            shift
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

[[ "$pinchbench_runs" =~ ^[1-9][0-9]*$ ]] || { echo "--runs must be a positive integer" >&2; exit 64; }
[[ "$pinchbench_tool_result_max_chars" =~ ^[1-9][0-9]*$ ]] || {
    echo "PINCHBENCH_OPENCLAW_TOOL_RESULT_MAX_CHARS must be a positive integer" >&2
    exit 64
}
[[ "$pinchbench_task_wall_clock_seconds" =~ ^[1-9][0-9]*$ ]] || {
    echo "PINCHBENCH_TASK_WALL_CLOCK_SECONDS must be a positive integer" >&2
    exit 64
}
[[ "$pinchbench_score_zero_after_seconds" =~ ^[1-9][0-9]*$ ]] || {
    echo "PINCHBENCH_SCORE_ZERO_AFTER_SECONDS must be a positive integer" >&2
    exit 64
}

workspace=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
output_dir=$(cd "$(dirname "$output_arg")" && pwd)/$(basename "$output_arg")
source "$workspace/scripts/config_llamacpp_sycl_levelzero_openclaw.sh"
configure_llamacpp_sycl_levelzero

[[ -f "$model" ]] || { echo "model is not readable: $model" >&2; exit 66; }
[[ -x "$release_dir/llama-server" ]] || {
    echo "missing extracted official SYCL release: $release_dir/llama-server" >&2
    echo "Set LLAMA_SYCL_RELEASE_DIR to the extracted llama-b10756 directory." >&2
    exit 66
}
[[ -x "$release_dir/llama-ls-sycl-device" ]] || {
    echo "missing SYCL device utility: $release_dir/llama-ls-sycl-device" >&2
    exit 66
}
[[ -f "$oneapi_runtime_root/compiler/2025.3/lib/libsycl.so.8" ]] || {
    echo "missing oneAPI 2025 SYCL runtime: $oneapi_runtime_root/compiler/2025.3/lib/libsycl.so.8" >&2
    echo "Set ONEAPI_SYCL_RUNTIME_ROOT to the extracted .../opt/intel/oneapi directory." >&2
    exit 66
}
[[ -f "$oneapi_runtime_root/mkl/2025.3/lib/libmkl_sycl_blas.so.5" ]] || {
    echo "missing oneAPI 2025 MKL runtime: $oneapi_runtime_root/mkl/2025.3/lib/libmkl_sycl_blas.so.5" >&2
    exit 66
}
[[ -f "$oneapi_runtime_root/dnnl/2025.3/lib/libdnnl.so.3" ]] || {
    echo "missing oneAPI 2025 oneDNN runtime: $oneapi_runtime_root/dnnl/2025.3/lib/libdnnl.so.3" >&2
    exit 66
}
[[ -e "$llama_sycl_render_node" ]] || { echo "missing Intel render node $llama_sycl_render_node" >&2; exit 69; }
if ss -ltnH "sport = :$port" | grep -q .; then
    echo "port $port is already in use; stop the existing server or set LLAMA_SYCL_PORT" >&2
    exit 69
fi
if (( long_words + max_tokens >= ctx_size )); then
    echo "long input plus max output must be below ctx size" >&2
    exit 64
fi
if [[ -n "$suite" && pinchbench_max_tokens -ge ctx_size ]]; then
    echo "LLAMA_SYCL_PINCHBENCH_MAX_TOKENS must be below LLAMA_SYCL_CTX_SIZE" >&2
    exit 64
fi

mkdir -p "$output_dir"
export NO_PROXY="127.0.0.1,localhost,::1${NO_PROXY:+,$NO_PROXY}"
export no_proxy="$NO_PROXY"

server_name="llama-sycl-qwen36-${port}-$$"
server_log="$output_dir/server.log"
status_file="$output_dir/benchmark.status"
printf 'started %s\n' "$(date -u +%FT%TZ)" > "$status_file"
status_finalized=false
mid_turn_precheck_changed=false
mid_turn_precheck_was_present=false
mid_turn_precheck_original=''
openclaw_provider_configured=false

restore_mid_turn_precheck() {
    [[ "$mid_turn_precheck_changed" == true ]] || return 0
    if [[ "$mid_turn_precheck_was_present" == true ]]; then
        openclaw config set agents.defaults.compaction.midTurnPrecheck.enabled \
            "$mid_turn_precheck_original" --strict-json >/dev/null 2>&1 || true
    else
        openclaw config unset agents.defaults.compaction.midTurnPrecheck.enabled \
            >/dev/null 2>&1 || true
    fi
}

restore_openclaw_provider() {
    [[ "$openclaw_provider_configured" == true ]] || return 0
    # The provider ID is unique to this launcher process.  Removing it avoids
    # leaving a global OpenClaw endpoint that dies with this temporary server.
    openclaw config unset "models.providers.${openclaw_sycl_provider_id}" \
        >/dev/null 2>&1 || true
}

write_effective_config() {
    local mode=$1
    local binary_sha256 model_size_bytes model_mtime_utc arg_index
    binary_sha256=$(sha256sum "$release_dir/llama-server" | awk '{print $1}')
    model_size_bytes=$(stat -c '%s' "$model")
    model_mtime_utc=$(stat -c '%y' "$model")
    {
        printf '# Resolved SYCL/Level Zero launcher configuration (Bash %q values).\n' "$mode"
        printf 'mode=%q\n' "$mode"
        printf 'container_image=%q\n' "$image"
        printf 'release_variant=%q\n' "$release_variant"
        printf 'release_dir=%q\n' "$release_dir"
        printf 'llama_server_sha256=%q\n' "$binary_sha256"
        printf 'oneapi_runtime_root=%q\n' "$oneapi_runtime_root"
        printf 'oneapi_runtime_ld=%q\n' "$oneapi_runtime_ld"
        printf 'model_path=%q\n' "$model"
        printf 'model_size_bytes=%q\n' "$model_size_bytes"
        printf 'model_mtime=%q\n' "$model_mtime_utc"
        printf 'device_selector=%q\n' "$llama_sycl_device_selector"
        printf 'card_node=%q\n' "$llama_sycl_card_node"
        printf 'render_node=%q\n' "$llama_sycl_render_node"
        printf 'server_alias=%q\n' "$llama_sycl_server_alias"
        printf 'server_port=%q\n' "$port"
        printf 'context_size=%q\n' "$ctx_size"
        printf 'threads=%q\n' "$llama_sycl_threads"
        printf 'threads_batch=%q\n' "$llama_sycl_threads_batch"
        printf 'gpu_layers=%q\n' "$llama_sycl_gpu_layers"
        printf 'cache_ram_mib=%q\n' "$llama_sycl_cache_ram"
        for arg_index in "${!llama_sycl_server_args[@]}"; do
            printf 'llama_server_argv[%s]=%q\n' "$arg_index" "${llama_sycl_server_args[$arg_index]}"
        done
        if [[ "$mode" == pinchbench ]]; then
            printf 'suite=%q\n' "$suite"
            printf 'openclaw_provider_id=%q\n' "$openclaw_sycl_provider_id"
            printf 'openclaw_base_url=%q\n' "$openclaw_sycl_base_url"
            printf 'openclaw_timeout_seconds=%q\n' "$openclaw_sycl_timeout_seconds"
            printf 'pinchbench_runs=%q\n' "$pinchbench_runs"
            printf 'pinchbench_max_tokens=%q\n' "$pinchbench_max_tokens"
            printf 'tool_result_max_chars=%q\n' "$pinchbench_tool_result_max_chars"
            printf 'task_wall_clock_seconds=%q\n' "$pinchbench_task_wall_clock_seconds"
            printf 'score_zero_after_seconds=%q\n' "$pinchbench_score_zero_after_seconds"
        fi
    } > "$output_dir/effective_config.env"
}

cleanup() {
    local exit_status=$?
    if [[ "$status_finalized" != true ]]; then
        printf 'failed %s exit=%s\n' "$(date -u +%FT%TZ)" "$exit_status" > "$status_file" || true
    fi
    restore_openclaw_provider
    restore_mid_turn_precheck
    docker logs "$server_name" >"$server_log" 2>&1 || true
    docker rm -f "$server_name" >/dev/null 2>&1 || true
    return "$exit_status"
}
trap cleanup EXIT

echo "enumerating SYCL devices with official b10756 $release_variant release"
docker run --rm "${llama_sycl_device_args[@]}" \
    "${llama_sycl_level_zero_env[@]}" \
    -v "$release_dir:/llama:ro" \
    -v "$oneapi_runtime_root:/oneapi-runtime:ro" \
    "$image" bash -lc 'source /opt/intel/oneapi/setvars.sh --force >/dev/null && export LD_LIBRARY_PATH=/llama:'"$oneapi_runtime_ld"':$LD_LIBRARY_PATH && exec /llama/llama-ls-sycl-device' \
    |& tee "$output_dir/sycl-ls.log"

render_gid=$(getent group render | cut -d: -f3)
video_gid=$(getent group video | cut -d: -f3)
echo "starting direct SYCL/Level Zero llama-server on 127.0.0.1:$port"
docker run -d --name "$server_name" --network host \
    "${llama_sycl_device_args[@]}" \
    --group-add "$render_gid" --group-add "$video_gid" \
    "${llama_sycl_level_zero_env[@]}" "${llama_sycl_server_env[@]}" \
    -v "$release_dir:/llama:ro" -v "$oneapi_runtime_root:/oneapi-runtime:ro" -v "$model:/models/qwen3.6-35b.gguf:ro" \
    "$image" bash -lc 'source /opt/intel/oneapi/setvars.sh --force >/dev/null && export LD_LIBRARY_PATH=/llama:'"$oneapi_runtime_ld"':$LD_LIBRARY_PATH && exec /llama/llama-server "$@"' llama-server \
        "${llama_sycl_server_args[@]}"

if [[ "$(docker inspect --format '{{.State.Running}}' "$server_name" 2>/dev/null || true)" != "true" ]]; then
    echo "llama-server exited immediately; see $server_log" >&2
    exit 70
fi

ready=false
for _ in $(seq 1 180); do
    if [[ "$(docker inspect --format '{{.State.Running}}' "$server_name" 2>/dev/null || true)" != "true" ]]; then
        echo "llama-server exited before becoming ready; see $server_log" >&2
        exit 70
    fi
    if curl --noproxy '*' --silent --fail --max-time 2 "http://127.0.0.1:${port}/health" > "$output_dir/health.json"; then
        ready=true
        break
    fi
    sleep 2
done
$ready || { echo "server did not become ready in six minutes; see $server_log" >&2; exit 70; }
curl --noproxy '*' --silent --fail --max-time 10 "http://127.0.0.1:${port}/v1/models" > "$output_dir/endpoint-models.json"
server_host_pid=$(docker inspect --format '{{.State.Pid}}' "$server_name")

if [[ -n "$suite" ]]; then
    # PinchBench's isolated OpenClaw agent receives the server's actual
    # context contract and its per-agent tool-result cap from this helper.
    configure_openclaw_sycl_pinchbench
    openclaw_provider_configured=true

    # OpenClaw otherwise evaluates raw tool payloads in its tool-loop guard
    # before the agent-scoped result cap is projected into the prompt. Enable
    # its supported mid-turn precheck just for this benchmark invocation so
    # it can truncate those payloads before the guard aborts the turn.
    if mid_turn_precheck_original=$(openclaw config get \
        agents.defaults.compaction.midTurnPrecheck.enabled 2>/dev/null); then
        mid_turn_precheck_was_present=true
    fi
    openclaw config set agents.defaults.compaction.midTurnPrecheck.enabled \
        true --strict-json
    mid_turn_precheck_changed=true

    # --no-judge disables every grading mode, including task_sanity's automated
    # grader. Forward it only when the caller explicitly requested it.
    if [[ "$pinchbench_no_judge" == true ]]; then
        judge_args=(--no-judge)
    else
        judge_args=(--judge "$pinchbench_judge")
    fi

    mkdir -p "$output_dir/results"
    write_effective_config pinchbench
    printf 'suite=%s\nruns=%s\ncontext_window=%s\nmax_tokens=%s\ntool_result_max_chars=%s\nmid_turn_precheck=true\ntask_wall_clock_seconds=%s\nscore_zero_after_seconds=%s\njudge=%s\n' \
        "$suite" "$pinchbench_runs" "$ctx_size" "$pinchbench_max_tokens" \
        "$pinchbench_tool_result_max_chars" "$pinchbench_task_wall_clock_seconds" \
        "$pinchbench_score_zero_after_seconds" "${judge_args[*]}" > "$output_dir/pinchbench_config.txt"

    set +e
    "$workspace/scripts/run.sh" \
        --model "$openclaw_sycl_model_ref" \
        --base-url "$openclaw_sycl_base_url" --api-key "$openclaw_sycl_api_key" \
        --suite "$suite" --runs "$pinchbench_runs" \
        --timeout-multiplier "$pinchbench_timeout_multiplier" --thinking off \
        --task-wall-clock-seconds "$pinchbench_task_wall_clock_seconds" \
        --score-zero-after-seconds "$pinchbench_score_zero_after_seconds" \
        --no-upload --no-fail-fast "${judge_args[@]}" \
        --output-dir "$output_dir/results" |& tee "$output_dir/benchmark.log"
    benchmark_status=${PIPESTATUS[0]}
    set -e

    printf 'completed %s mode=pinchbench suite=%s benchmark_exit=%s\n' \
        "$(date -u +%FT%TZ)" "$suite" "$benchmark_status" > "$status_file"
    status_finalized=true
    exit "$benchmark_status"
fi

write_effective_config streaming
python3 "$workspace/scripts/measure_llamacpp_context_streaming.py" \
    --base-url "http://127.0.0.1:${port}/v1" \
    --output-dir "$output_dir" --model "$llama_sycl_server_alias" \
    --backend sycl-level-zero \
    --backend-args "official llama.cpp SYCL ${release_variant}; release_dir=${release_dir}; oneAPI_runtime=${oneapi_runtime_root}; image=${image}; ${llama_sycl_device_selector}; GGML_SYCL_ENABLE_LEVEL_ZERO=1; --threads ${llama_sycl_threads}; --threads-batch ${llama_sycl_threads_batch}; --gpu-layers ${llama_sycl_gpu_layers}; --cache-ram ${llama_sycl_cache_ram}; MTP draft" \
    --ctx-size "$ctx_size" --runs "$runs" --warmup-runs "$warmup_runs" \
    --short-input-words "$short_words" --long-input-words "$long_words" \
    --max-tokens "$max_tokens" --cpu-pid "$server_host_pid"

printf 'completed %s\n' "$(date -u +%FT%TZ)" > "$status_file"
status_finalized=true
