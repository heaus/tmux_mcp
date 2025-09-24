# Testing Notes

- Run `pytest --cov=tmux_mcp --cov-report=term-missing` for automated coverage. The integration fakes cover session orchestration, safety gating, and MCP command flow.
- Manual smoke test: `scripts/dev/launch.sh mcp-dev` to start the MCP server locally, then attach a second tmux window and issue commands via a Cursor MCP client.
- When validating safe mode, attempt a destructive command such as `rm -rf /tmp/test-dir` and confirm the tool returns `pending_approval`. Approve or reject via the dedicated tools to see the unified audit log update.
- Inspect `logs/agent_activity.log` after each run; the JSONL records should include the expected `task_id`, `session`, `window`, and `pane` identifiers.
