#!/usr/bin/env bash
set -euo pipefail

workspace=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
source "$workspace/scripts/config_llamacpp_sycl_levelzero_openclaw.sh"

LLAMA_SYCL_CACHE_RAM_MIB=8192
configure_llamacpp_sycl_levelzero
pinchbench_tool_result_max_chars=6000
configure_openclaw_sycl_pinchbench

[[ "$llama_sycl_cache_ram" == 8192 ]]
[[ "$openclaw_sycl_provider_id" == "sycl-llamacpp-${port}-${BASHPID}" ]]
[[ "$openclaw_sycl_model_ref" == "${openclaw_sycl_provider_id}/${llama_sycl_server_alias}" ]]
[[ "$openclaw_sycl_timeout_seconds" == 1800 ]]
[[ "$PINCHBENCH_CUSTOM_TIMEOUT_SECONDS" == 1800 ]]
