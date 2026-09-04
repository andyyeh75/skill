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

configure_openclaw_proxy_environment
[[ "$HTTP_PROXY" == 'http://proxy-png.intel.com:911' ]]
[[ "$HTTPS_PROXY" == 'http://proxy-png.intel.com:911' ]]
[[ "$NO_PROXY" == 'localhost,127.0.0.1,10.0.0.0/8,192.168.0.0/16' ]]
[[ "$http_proxy" == "$HTTP_PROXY" ]]
[[ "$https_proxy" == "$HTTPS_PROXY" ]]
[[ "$no_proxy" == "$NO_PROXY" ]]
