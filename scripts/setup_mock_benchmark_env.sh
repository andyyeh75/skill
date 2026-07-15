#!/usr/bin/env bash
set -euo pipefail

if ! command -v fws >/dev/null 2>&1; then
  echo "fws is not installed or not on PATH" >&2
  exit 1
fi

HOME_DIR="${HOME:-/home/intel}"

fws server start >/dev/null 2>&1 || true
sleep 1

# Match PinchBench's fallback env in scripts/lib_fws.py.
export GOOGLE_WORKSPACE_CLI_CONFIG_DIR="${GOOGLE_WORKSPACE_CLI_CONFIG_DIR:-$HOME_DIR/.local/share/fws/config}"
export GOOGLE_WORKSPACE_CLI_TOKEN="${GOOGLE_WORKSPACE_CLI_TOKEN:-fake}"
export HTTPS_PROXY="${HTTPS_PROXY:-http://localhost:4101}"
export SSL_CERT_FILE="${SSL_CERT_FILE:-$HOME_DIR/.local/share/fws/certs/ca-bundle.crt}"
export GH_TOKEN="${GH_TOKEN:-fake}"
export GH_REPO="${GH_REPO:-testuser/my-project}"

echo "Mock benchmark environment loaded."
echo "GOOGLE_WORKSPACE_CLI_CONFIG_DIR=$GOOGLE_WORKSPACE_CLI_CONFIG_DIR"
echo "GOOGLE_WORKSPACE_CLI_TOKEN=$GOOGLE_WORKSPACE_CLI_TOKEN"
echo "HTTPS_PROXY=$HTTPS_PROXY"
echo "SSL_CERT_FILE=$SSL_CERT_FILE"
echo "GH_TOKEN=${GH_TOKEN:+set}"
echo "GH_REPO=$GH_REPO"
