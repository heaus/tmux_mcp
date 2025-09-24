# Project Kinesis – tmux MCP Agent

Project Kinesis lets Cursor register an MCP tool that shares a tmux session with an AI agent. The agent connects over SSH, issues commands through tmux, and persists a unified command/output audit log so humans can supervise every task.

## Quick Start

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python -m tmux_mcp.agent --config-dir .cursor
```

To smoke test the tmux integration, run `python scripts/start_mcp_agent.py --session mcp-dev --window agent` which launches the MCP server with a disposable session named `mcp-dev`.

## Cursor MCP Registration

1. Point Cursor at `.cursor/manifest.json`. The manifest advertises stdio transport plus the tool schemas enumerated in `.cursor/capabilities.json`.
2. Cursor will call `health_check` followed by `connect_session`. Supply the stored profile name and desired session/window identifiers.
3. Invoke `submit_command` with a `task_id`, tmux coordinates, and the shell command. When safe mode flags a command, Cursor should call `approve_command` or `reject_command` with the provided `command_id`.
4. Use `read_context` for on-demand pane capture and `list_profiles`/`upsert_profile` to manage SSH connections.

A project-level `.cursor/mcp.json` is included so Cursor auto-launches the agent when the workspace opens; update its command or environment fields as needed.

## Logging & Audit

All commands, approvals, and captured output deltas are appended to `logs/agent_activity.log` as JSONL. Each record includes the Cursor task id plus tmux session/window/pane fields so multiple tasks remain distinguishable, even when they share the same session.

## Development Workflow

- Source lives under `src/tmux_mcp/`; tests mirror the structure in `tests/`.
- Format with `black`, lint with `ruff`, and keep pytest branch coverage near 90% using `pytest --cov=tmux_mcp --cov-report=term-missing`.
- Update `.cursor/feature-flags.yaml` when adjusting default safety behaviour, and document any new tools or prompts in `README.md` and `AGENTS.md`.
