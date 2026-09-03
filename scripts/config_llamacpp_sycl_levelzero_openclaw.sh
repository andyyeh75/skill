#!/usr/bin/env bash
# Shared configuration for the official llama.cpp SYCL/Level Zero benchmark.
#
# Source this file from a launcher after setting `workspace`.  The functions
# deliberately perform no I/O: the caller owns validation, container lifetime,
# and temporary OpenClaw configuration restoration.
#
# Useful overrides: LLAMA_SYCL_RELEASE_VARIANT, LLAMA_SYCL_RELEASE_DIR,
# ONEAPI_SYCL_RUNTIME_ROOT, LLAMA_SYCL_PORT, LLAMA_SYCL_CTX_SIZE,
# LLAMA_SYCL_DEVICE_SELECTOR, LLAMA_SYCL_THREADS, LLAMA_SYCL_GPU_LAYERS, and
# LLAMA_SYCL_CACHE_RAM_MIB.  LLAMA_SYCL_CACHE_RAM remains a compatibility alias.

configure_llamacpp_sycl_levelzero() {
    : "${workspace:?set workspace before configuring llama.cpp SYCL}"

    image=${ONEAPI_IMAGE:-intel/oneapi-toolkit:2026.1.0-devel-ubuntu26.04}
    model=${QWEN_MODEL:-/opt/docker-amd/models/lemonade/huggingface/hub/models--unsloth--Qwen3.6-35B-A3B-MTP-GGUF/snapshots/5bc3e238d916f48a861bac2f8a1990a0e9b7e98d/Qwen3.6-35B-A3B-UD-Q4_K_XL.gguf}
    release_variant=${LLAMA_SYCL_RELEASE_VARIANT:-fp16}
    case "$release_variant" in
        fp16|fp32) ;;
        *) echo "LLAMA_SYCL_RELEASE_VARIANT must be fp16 or fp32, got: $release_variant" >&2; return 64 ;;
    esac

    release_dir=${LLAMA_SYCL_RELEASE_DIR:-"$workspace/downloads/llama-b10756-bin-ubuntu-sycl-${release_variant}-x64/llama-b10756"}
    # Official b10756 binaries require the oneAPI 2025 ABI (libsycl.so.8).
    oneapi_runtime_root=${ONEAPI_SYCL_RUNTIME_ROOT:-"$workspace/downloads/oneapi-2025.3-runtime/root/opt/intel/oneapi"}
    oneapi_runtime_ld='/oneapi-runtime/compiler/2025.3/lib:/oneapi-runtime/mkl/2025.3/lib:/oneapi-runtime/dnnl/2025.3/lib'

    port=${LLAMA_SYCL_PORT:-8090}
    ctx_size=${LLAMA_SYCL_CTX_SIZE:-98304}
    short_words=${LLAMA_SYCL_SHORT_WORDS:-32}
    long_words=${LLAMA_SYCL_LONG_WORDS:-16384}
    runs=${LLAMA_SYCL_RUNS:-3}
    warmup_runs=${LLAMA_SYCL_WARMUP_RUNS:-1}
    max_tokens=${LLAMA_SYCL_MAX_TOKENS:-128}
    pinchbench_max_tokens=${LLAMA_SYCL_PINCHBENCH_MAX_TOKENS:-16384}

    llama_sycl_card_node=${LLAMA_SYCL_CARD_NODE:-/dev/dri/card1}
    llama_sycl_render_node=${LLAMA_SYCL_RENDER_NODE:-/dev/dri/renderD128}
    llama_sycl_device_selector=${LLAMA_SYCL_DEVICE_SELECTOR:-level_zero:0}
    llama_sycl_server_alias=${LLAMA_SYCL_SERVER_ALIAS:-Qwen3.6-35B-A3B-MTP-SYCL}
    llama_sycl_threads=${LLAMA_SYCL_THREADS:-16}
    llama_sycl_threads_batch=${LLAMA_SYCL_THREADS_BATCH:-16}
    llama_sycl_gpu_layers=${LLAMA_SYCL_GPU_LAYERS:-all}
    llama_sycl_cache_ram=${LLAMA_SYCL_CACHE_RAM_MIB:-${LLAMA_SYCL_CACHE_RAM:-8192}}
    [[ "$llama_sycl_cache_ram" =~ ^[0-9]+$ ]] || {
        echo "LLAMA_SYCL_CACHE_RAM_MIB must be a non-negative integer MiB value" >&2
        return 64
    }

    llama_sycl_device_args=(
        "--device=${llama_sycl_card_node}"
        "--device=${llama_sycl_render_node}"
    )
    llama_sycl_level_zero_env=(
        -e "ONEAPI_DEVICE_SELECTOR=${llama_sycl_device_selector}"
        -e GGML_SYCL_ENABLE_LEVEL_ZERO=1
    )
    llama_sycl_server_env=(
        -e UR_L0_ENABLE_RELAXED_ALLOCATION_LIMITS=1
        -e ZES_ENABLE_SYSMAN=1
        -e "LD_LIBRARY_PATH=/llama:${oneapi_runtime_ld}:/opt/intel/oneapi/compiler/2026.1/lib:/opt/intel/oneapi/mkl/2026.1/lib:/opt/intel/oneapi/tbb/2023.1/lib/intel64/gcc4.8:/opt/intel/oneapi/dnnl/2026.0/lib"
    )
    llama_sycl_server_args=(
        --model /models/qwen3.6-35b.gguf --alias "$llama_sycl_server_alias"
        --host 127.0.0.1 --port "$port" --ctx-size "$ctx_size" --parallel 1
        --threads "$llama_sycl_threads" --threads-batch "$llama_sycl_threads_batch"
        --gpu-layers "$llama_sycl_gpu_layers" --cache-ram "$llama_sycl_cache_ram"
        --spec-type draft-mtp --spec-draft-n-max 3 --spec-draft-p-min 0.75
        --reasoning off --log-colors off
    )
}

configure_openclaw_sycl_pinchbench() {
    : "${ctx_size:?configure llama.cpp SYCL before OpenClaw}"
    : "${pinchbench_max_tokens:?configure llama.cpp SYCL before OpenClaw}"
    : "${pinchbench_tool_result_max_chars:?set PinchBench tool-result limit first}"

    # The provider registry is process-global.  A unique provider keeps two
    # benchmark launches from redirecting one another's temporary endpoint.
    openclaw_sycl_provider_id="sycl-llamacpp-${port}-${BASHPID}"
    openclaw_sycl_model_ref="${openclaw_sycl_provider_id}/${llama_sycl_server_alias}"
    openclaw_sycl_base_url="http://127.0.0.1:${port}/v1"
    openclaw_sycl_api_key=${LLAMA_SYCL_OPENCLAW_API_KEY:-ollama-local}
    openclaw_sycl_timeout_seconds=${LLAMA_SYCL_OPENCLAW_TIMEOUT_SECONDS:-1800}
    [[ "$openclaw_sycl_timeout_seconds" =~ ^[1-9][0-9]*$ ]] || {
        echo "LLAMA_SYCL_OPENCLAW_TIMEOUT_SECONDS must be a positive integer" >&2
        return 64
    }
    export PINCHBENCH_CUSTOM_CONTEXT_WINDOW="$ctx_size"
    export PINCHBENCH_CUSTOM_MAX_TOKENS="$pinchbench_max_tokens"
    export PINCHBENCH_CUSTOM_TIMEOUT_SECONDS="$openclaw_sycl_timeout_seconds"
    export PINCHBENCH_OPENCLAW_TOOL_RESULT_MAX_CHARS="$pinchbench_tool_result_max_chars"
}
