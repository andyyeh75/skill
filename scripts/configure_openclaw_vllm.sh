#!/usr/bin/env bash
# Configure and verify the host OpenClaw Gateway used by /opt/docker-amd.
# This intentionally does not use Docker or any ima-openclaw container.
#
# Optional overrides:
#   OPENCLAW_BIN=/home/intel/.local/bin/openclaw
#   OPENCLAW_GATEWAY_SERVICE=openclaw-gateway.service
#   OPENCLAW_VLLM_BASE_URL=http://10.5.235.33:8000/v1
#   OPENCLAW_VLLM_MODEL=Qwen3.6-35B-A3B
#   OPENCLAW_VLLM_PROVIDER_ID=intel-vllm
#   OPENCLAW_HTTP_PROXY=http://proxy-png.intel.com:911
#   OPENCLAW_HTTPS_PROXY=http://proxy-png.intel.com:911
#   OPENCLAW_NO_PROXY=localhost,127.0.0.1,10.0.0.0/8,192.168.0.0/16
set -euo pipefail

OPENCLAW_BIN="${OPENCLAW_BIN:-openclaw}"
GATEWAY_SERVICE="${OPENCLAW_GATEWAY_SERVICE:-openclaw-gateway.service}"
BASE_URL="${OPENCLAW_VLLM_BASE_URL:-http://10.5.235.33:8000/v1}"
MODEL_ID="${OPENCLAW_VLLM_MODEL:-Qwen3.6-35B-A3B}"
PROVIDER_ID="${OPENCLAW_VLLM_PROVIDER_ID:-intel-vllm}"
MODEL_REF="${PROVIDER_ID}/${MODEL_ID}"
HTTP_PROXY_VALUE="${OPENCLAW_HTTP_PROXY:-${HTTP_PROXY:-http://proxy-png.intel.com:911}}"
HTTPS_PROXY_VALUE="${OPENCLAW_HTTPS_PROXY:-${HTTPS_PROXY:-http://proxy-png.intel.com:911}}"
NO_PROXY_VALUE="${OPENCLAW_NO_PROXY:-${NO_PROXY:-localhost,127.0.0.1,10.0.0.0/8,192.168.0.0/16}}"

# The values below are placed in a systemd Environment= directive. Reject
# line/control characters and quotes rather than emitting a malformed unit.
for proxy_value in "$HTTP_PROXY_VALUE" "$HTTPS_PROXY_VALUE" "$NO_PROXY_VALUE"; do
  case "$proxy_value" in
    *$'\n'*|*$'\r'*|*'"'*)
      echo 'OpenClaw proxy values must not contain quotes or line breaks' >&2
      exit 64
      ;;
  esac
done

for required in "$OPENCLAW_BIN" curl jq systemctl; do
  command -v "$required" >/dev/null || {
    echo "Required command not found: $required" >&2
    exit 127
  }
done

# Check reachability first. --noproxy prevents a private endpoint from being
# sent to a corporate HTTP proxy when this script is run interactively.
echo "Checking ${BASE_URL}/models..."
models_json="$(curl --noproxy '*' --fail-with-body --silent --show-error \
  --connect-timeout 5 --max-time 20 "${BASE_URL}/models")"
jq -e --arg model "$MODEL_ID" '.data[] | select(.id == $model)' <<<"$models_json" >/dev/null || {
  echo "Model ${MODEL_ID} was not advertised by ${BASE_URL}/models" >&2
  exit 1
}

# vLLM exposes OpenAI-compatible chat completions and has been verified to
# return OpenAI-compatible tool_calls for this Qwen deployment.
provider_json="$(jq -cn --arg baseUrl "$BASE_URL" --arg model "$MODEL_ID" '
  {
    baseUrl: $baseUrl,
    api: "openai-completions",
    timeoutSeconds: 600,
    request: {allowPrivateNetwork: true},
    models: [{
      id: $model,
      name: ("vLLM: " + $model),
      reasoning: false,
      input: ["text"],
      cost: {input: 0, output: 0, cacheRead: 0, cacheWrite: 0},
      contextWindow: 256000,
      maxTokens: 8192,
      compat: {supportsTools: true}
    }]
  }
')"

echo "Configuring ${MODEL_REF}..."
"$OPENCLAW_BIN" config set "models.providers.${PROVIDER_ID}" "$provider_json" --strict-json --merge
# Qwen served by vLLM otherwise defaults to generating its reasoning trace.
# Inject this vLLM chat-template argument for every OpenClaw call to this
# specific model. It applies to normal turns and tool-result continuations.
agent_model_json="$(jq -cn --arg model "$MODEL_REF" '
  {($model): {params: {chat_template_kwargs: {enable_thinking: false}}}}
')"
"$OPENCLAW_BIN" config set agents.defaults.models "$agent_model_json" --strict-json --merge
"$OPENCLAW_BIN" config validate

# A generated per-agent models.json can retain an older non-empty baseUrl and
# override models.providers. Refresh only the main Gateway agent's provider,
# while retaining fields such as an existing API-key marker.
config_file="$("$OPENCLAW_BIN" config file | sed "s#^~#${HOME}#")"
state_dir="$(dirname "$config_file")"
agent_models_file="${OPENCLAW_VLLM_AGENT_MODELS_FILE:-${state_dir}/agents/main/agent/models.json}"
if [[ -f "$agent_models_file" ]]; then
  agent_models_tmp="$(mktemp "${agent_models_file}.tmp.XXXXXX")"
  jq --arg provider "$PROVIDER_ID" --argjson config "$provider_json" '
    .providers //= {} |
    .providers[$provider] = ((.providers[$provider] // {}) + $config)
  ' "$agent_models_file" >"$agent_models_tmp"
  chmod 0600 "$agent_models_tmp"
  mv "$agent_models_tmp" "$agent_models_file"
  echo "Refreshed ${agent_models_file}"
fi

# The Gateway is a user systemd service, so it does not inherit the shell's
# proxy environment. Persist these settings in a drop-in for agent web tools
# and Node fetch(). The 10/8 NO_PROXY entry keeps the private vLLM endpoint
# off the corporate proxy.
systemd_user_dir="${XDG_CONFIG_HOME:-${HOME}/.config}/systemd/user"
proxy_dropin_dir="${systemd_user_dir}/${GATEWAY_SERVICE}.d"
proxy_dropin="${proxy_dropin_dir}/20-openclaw-vllm-proxy.conf"
mkdir -p "$proxy_dropin_dir"
proxy_dropin_tmp="$(mktemp "${proxy_dropin}.tmp.XXXXXX")"
{
  printf '%s\n' '[Service]'
  printf 'Environment="HTTP_PROXY=%s"\n' "$HTTP_PROXY_VALUE"
  printf 'Environment="HTTPS_PROXY=%s"\n' "$HTTPS_PROXY_VALUE"
  printf 'Environment="NO_PROXY=%s"\n' "$NO_PROXY_VALUE"
  printf 'Environment="http_proxy=%s"\n' "$HTTP_PROXY_VALUE"
  printf 'Environment="https_proxy=%s"\n' "$HTTPS_PROXY_VALUE"
  printf 'Environment="no_proxy=%s"\n' "$NO_PROXY_VALUE"
  printf '%s\n' 'Environment="NODE_USE_ENV_PROXY=1"'
} >"$proxy_dropin_tmp"
chmod 0600 "$proxy_dropin_tmp"
mv "$proxy_dropin_tmp" "$proxy_dropin"
echo "Configured Gateway proxy environment in ${proxy_dropin}"

echo "Restarting the host OpenClaw Gateway service..."
systemctl --user daemon-reload
systemctl --user restart "$GATEWAY_SERVICE"
for _ in $(seq 1 30); do
  if "$OPENCLAW_BIN" gateway health >/dev/null 2>&1; then
    break
  fi
  sleep 1
done
"$OPENCLAW_BIN" gateway health
systemctl --user show "$GATEWAY_SERVICE" --property=Environment --value | tr ' ' '\n' | \
  grep -E '^(HTTP_PROXY|HTTPS_PROXY|NO_PROXY|NODE_USE_ENV_PROXY)=' >/dev/null || {
    echo "Gateway proxy environment was not applied to ${GATEWAY_SERVICE}" >&2
    exit 1
  }

echo "Running a direct vLLM chat-completions smoke test..."
chat_body="$(jq -cn --arg model "$MODEL_ID" '{model: $model, messages: [{role: "user", content: "Reply with exactly: direct-vllm-ok"}], max_tokens: 256, temperature: 0}')"
direct_result="$(curl --noproxy '*' --fail-with-body --silent --show-error \
  --connect-timeout 5 --max-time 120 -H 'Content-Type: application/json' \
  -d "$chat_body" "${BASE_URL}/chat/completions")"
jq -e '(.choices[0].message.content // .choices[0].text) | contains("direct-vllm-ok")' \
  <<<"$direct_result" >/dev/null
printf '%s\n' "$direct_result" | jq -r '.choices[0].message.content // .choices[0].text'

echo "Running a Gateway agent smoke test with ${MODEL_REF}..."
gateway_result="$("$OPENCLAW_BIN" agent --agent main \
  --session-key "agent:main:vllm-setup-smoke-$(date +%s)" \
  --model "$MODEL_REF" --thinking off \
  --message 'Reply with exactly: gateway-vllm-ok' --timeout 180 --json)"
# OpenClaw 2026.6 nests the completed agent response under .result. Older
# releases return payloads/meta at the top level, so accept either shape.
# The CLI invokes the Gateway unless --local is passed; assert the successful
# response and the selected provider/model instead of relying on an optional
# transport field.
printf '%s\n' "$gateway_result" | jq -e --arg provider "$PROVIDER_ID" --arg model "$MODEL_ID" '
  (.result // .) as $result |
  ($result.meta.agentMeta // {}) as $agent_meta |
  ((.status // "ok") == "ok") and
  (($result.payloads[0].text // "") | contains("gateway-vllm-ok")) and
  ($agent_meta.provider == $provider) and
  ($agent_meta.model == $model)
' >/dev/null
printf '%s\n' "$gateway_result" | jq '
  (.result // .) as $result |
  {
    text: $result.payloads[0].text,
    provider: $result.meta.agentMeta.provider,
    model: $result.meta.agentMeta.model
  }
'

echo "Success: ${MODEL_REF} responded through the host OpenClaw Gateway."
