#!/usr/bin/env bash
set -euo pipefail

SESSION_NAME=${1:-mcp-dev}
python -m tmux_mcp.agent --session "$SESSION_NAME" --window agent --config .cursor/manifest.json
