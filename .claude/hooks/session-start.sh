#!/bin/bash
set -euo pipefail

# Only run in Claude Code on the web (remote) sessions
if [ "${CLAUDE_CODE_REMOTE:-}" != "true" ]; then
  exit 0
fi

# Install Synapse and key optional/test dependencies in editable mode.
# This also compiles the Rust extension via maturin.
# --ignore-installed avoids conflicts with debian-managed system packages (e.g. cryptography).
pip install --ignore-installed -e ".[matrix-synapse-ldap3,oidc,url-preview,sentry,redis,cache-memory,test]"

# Install dev tools (linter)
pip install ruff
