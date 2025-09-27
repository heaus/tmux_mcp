# Architecture Overview

Project Kinesis exposes a Cursor-compatible MCP server that brokers access to a shared tmux session.

## Components

- **MCP surface (`tmux_mcp/agent.py`)** – Implements a JSON-RPC server over stdio that Cursor MCP tools can invoke. It routes requests to the orchestration layer and honours the feature flags in `.cursor/feature-flags.yaml`.
- **Session orchestration (`tmux_mcp/session_manager.py`)** – Maintains encrypted SSH connection profiles and uses a persistent Paramiko SSH session to run tmux CLI commands for creating or attaching sessions, windows, and panes.
- **Command bridge (`tmux_mcp/command_bridge.py`)** – Sends commands to tmux panes, captures fresh pane output, and keeps an in-memory snapshot for fast context reads. Every call yields structured log entries tagged with task, session, window, and pane identifiers.
- **Safety layer (`tmux_mcp/safety.py`)** – Evaluates commands against configurable destructive and warning patterns. When safe mode is enabled it queues risky commands for human approval.
- **Logging utilities (`tmux_mcp/logging_utils.py`)** – Serialises command metadata and output deltas into `logs/agent_activity.log` with automatic rotation.

## MCP Data Flow

1. Cursor issues a tool invocation (e.g. `submit_command`) via MCP JSON-RPC.
2. `agent.py` validates parameters, applies safe-mode overrides, and delegates to the command bridge.
3. The bridge resolves the target pane via the session manager, optionally enqueues the command for approval, or executes it and captures the output delta.
4. A `LogRecord` capturing command, output, safety status, and Cursor task identifiers is appended to the unified JSONL history.
5. Responses are returned to Cursor with the command status plus any `command_id` needed for future approvals.

## Extensibility Notes

- Additional safety heuristics can be registered by extending `SafetyEvaluator` and honouring the existing interface.
- MCP tools should be described in `.cursor/capabilities.json`; new tools should follow the same JSON schema structure to remain discoverable by Cursor.
- Session-manager behaviour can be integration-tested by substituting test profiles and stubbing the SSH layer to avoid hitting a real tmux instance.
