"""
fws lifecycle management for GWS-based tasks.

Starts/stops the fws server and configures environment variables
so that gws CLI commands are redirected to the local mock.
"""

import logging
import os
import shutil
import subprocess
import time

logger = logging.getLogger("pinchbench")

FWS_CMD = "fws.cmd" if os.name == "nt" else "fws"

# Environment variables that fws sets
MOCK_GWS_ENV_KEYS = [
    "GOOGLE_WORKSPACE_CLI_CONFIG_DIR",
    "GOOGLE_WORKSPACE_CLI_TOKEN",
    "HTTPS_PROXY",
    "SSL_CERT_FILE",
]
LOCAL_BYPASS_ENV_KEYS = ["NO_PROXY", "no_proxy"]


def is_fws_task(frontmatter: dict) -> bool:
    """Check if a task requires fws (category is gws/github or prerequisites include fws)."""
    if frontmatter.get("category") in ("gws", "github"):
        return True
    prereqs = frontmatter.get("prerequisites", [])
    return any("fws" in str(p) for p in prereqs)


def fws_available() -> bool:
    """Check if fws CLI is available."""
    return shutil.which(FWS_CMD) is not None


def start_fws() -> dict:
    """Start the fws server and set environment variables.

    Returns a dict of the original env var values (for restoration).
    """
    logger.info("🔧 Starting fws server...")

    # Stop any existing server
    subprocess.run([FWS_CMD, "server", "stop"], capture_output=True, check=False)
    time.sleep(0.3)

    # Start server
    result = subprocess.run(
        [FWS_CMD, "server", "start"],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )

    if result.returncode != 0:
        logger.error("Failed to start fws: %s", result.stderr)
        raise RuntimeError(f"fws server start failed: {result.stderr}")

    # Parse the env vars from output
    env_vars = {}
    for line in result.stdout.splitlines():
        line = line.strip()
        if line.startswith("export "):
            line = line[7:]
        if "=" in line and any(key in line for key in MOCK_GWS_ENV_KEYS):
            key, _, value = line.partition("=")
            env_vars[key.strip()] = value.strip()

    if not env_vars:
        # Fallback: use default paths
        home = os.path.expanduser("~")
        env_vars = {
            "GOOGLE_WORKSPACE_CLI_CONFIG_DIR": f"{home}/.local/share/fws/config",
            "GOOGLE_WORKSPACE_CLI_TOKEN": "fake",
            "HTTPS_PROXY": "http://localhost:4101",
            "SSL_CERT_FILE": f"{home}/.local/share/fws/certs/ca.crt",
        }

    # gws reads its FWS-rewritten discovery documents over plain HTTP on
    # localhost.  Keep that traffic out of a host-level corporate proxy;
    # otherwise the discovery probe can receive the proxy's 403 page instead
    # of the mock Gmail response.
    bypass_hosts = "127.0.0.1,localhost,::1"
    existing_no_proxy = os.environ.get("NO_PROXY", "")
    if existing_no_proxy:
        bypass_hosts = f"{bypass_hosts},{existing_no_proxy}"
    env_vars["NO_PROXY"] = bypass_hosts
    env_vars["no_proxy"] = bypass_hosts

    # Save original values and set new ones
    original_env = {}
    for key, value in env_vars.items():
        original_env[key] = os.environ.get(key)
        os.environ[key] = value

    logger.info("✅ fws server started, env configured")

    # This is a read-only deployment gate.  It confirms both services used by
    # the final integration cohort are reachable through the same CLIs the
    # task agent will use.  Do this after the environment is installed so the
    # gws discovery cache and gh HTTPS proxy are exercised, not merely the
    # server's listening socket.
    checks = (
        ("GitHub", ["gh", "issue", "list", "--repo", "testuser/my-project", "--state", "open", "--limit", "1"]),
        ("GWS Gmail", ["gws", "gmail", "users", "messages", "list", "--params", '{"userId":"me","q":"is:unread"}']),
        ("GWS Tasks", ["gws", "tasks", "tasklists", "list"]),
    )
    failures = []
    for service, command in checks:
        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
        if result.returncode != 0:
            detail = (result.stderr or result.stdout).strip().replace("\n", " ")
            failures.append(f"{service}: {detail[:240]}")
    if failures:
        stop_fws(original_env)
        raise RuntimeError("FWS integration preflight failed; refusing to run integration task: " + "; ".join(failures))
    logger.info("✅ FWS GitHub and GWS integration preflight passed")
    return original_env


def stop_fws(original_env: dict) -> None:
    """Stop the fws server and restore original environment variables."""
    logger.info("🔧 Stopping fws server...")

    subprocess.run([FWS_CMD, "server", "stop"], capture_output=True, check=False)

    # Restore original env
    for key, value in original_env.items():
        if value is None:
            os.environ.pop(key, None)
        else:
            os.environ[key] = value

    logger.info("✅ fws server stopped, env restored")
