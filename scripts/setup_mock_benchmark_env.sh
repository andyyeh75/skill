#!/usr/bin/env bash

if [[ "${BASH_SOURCE[0]}" == "$0" ]]; then
  echo "Source this script so its environment variables remain in the current shell:" >&2
  echo "  source ${BASH_SOURCE[0]}" >&2
  exit 1
fi

if ! command -v fws >/dev/null 2>&1; then
  echo "fws is not installed or not on PATH" >&2
  return 1
fi

if ! fws server start >/dev/null 2>&1; then
  echo "failed to start fws server" >&2
  return 1
fi

# fws is the source of truth for proxy, certificate, and mock credentials.
# Its exports intentionally replace any live-service values in this shell.
if ! FWS_ENV_EXPORTS="$(fws server env)" || [[ -z "$FWS_ENV_EXPORTS" ]]; then
  echo "failed to load environment from fws server" >&2
  unset FWS_ENV_EXPORTS
  return 1
fi
eval "$FWS_ENV_EXPORTS"
unset FWS_ENV_EXPORTS

# Keep benchmark GitHub tasks pinned to the seeded mock repository.
export GH_TOKEN="fake"
export GH_REPO="testuser/my-project"

echo "Mock benchmark environment loaded."
echo "GOOGLE_WORKSPACE_CLI_CONFIG_DIR=$GOOGLE_WORKSPACE_CLI_CONFIG_DIR"
echo "GOOGLE_WORKSPACE_CLI_TOKEN=$GOOGLE_WORKSPACE_CLI_TOKEN"
echo "HTTPS_PROXY=$HTTPS_PROXY"
echo "SSL_CERT_FILE=$SSL_CERT_FILE"
echo "GH_TOKEN=${GH_TOKEN:+set}"
echo "GH_REPO=$GH_REPO"
