# Cursor MCP tmux Agent (Broadcom Internal)

The tmux MCP agent is built specifically for Cursor's Model Context Protocol and is intended for internal BRCM workflows. It gives Cursor a persistent foothold inside a remote tmux session so the assistant can issue commands, capture output, and keep a fully auditable history of everything it does.

The agent opens a single Paramiko SSH connection per profile and reuses it for every tmux interaction. This keeps command latency low and avoids repeatedly spawning local `ssh` processes.

## Requirements

- Operating system: macOS or Windows with WSL (the agent cannot run inside our VDI environment because it needs a locally installed Python toolchain and packages).
- Python 3.11 or later on the machine running Cursor.
- A reachable remote host with:
  - `ssh` access.
  - `tmux` installed and discoverable in `PATH`.
- (Optional) Host aliases defined in `~/.ssh/config` so the agent can hydrate profiles automatically.

## Installation

### Python version and environment

The `python -m venv .venv` command asks Python to create a virtual environment in the `.venv/` directory so the agent's dependencies do not leak into your global interpreter. The agent is tested against Python 3.11.11.

If you need Python 3.11.11 specifically:

- **macOS** – Install it system-wide with Homebrew (`brew install python@3.11`).
- **WSL / Linux** – Install from your package manager (e.g. `sudo add-apt-repository ppa:deadsnakes/ppa && sudo apt install python3.11 python3.11-venv`).

### Package setup

1. Copy this package into your target directory.
2. Create an isolated Python environment inside that directory and install the runtime dependencies (replace `<your_directory>` with the absolute path where you extracted the source code):

   ```bash
   cd <your_directory>/tmux_mcp
   python -m venv .venv
   source .venv/bin/activate
   pip install -r requirements.txt
   ```

## Cursor MCP Setup

The package includes `.cursor/mcp.json`, which registers the agent with Cursor as an `stdio` MCP server. Open the workspace in Cursor and it will launch the agent automatically. If you need to change the command, environment variables, or working directory, edit that file before opening the project.

If you are integrating with another MCP client, you can use the following template. Replace `<your_directory>` with the same absolute path used above so the MCP host executes the virtualenv interpreter and startup script directly:

```json
{
  "mcpServers": {
    "tmux-mcp-agent": {
      "type": "stdio",
      "command": "<your_directory>/tmux_mcp/.venv/bin/python",
      "args": [
        "<your_directory>/tmux_mcp/scripts/start_mcp_agent.py",
        "--log-level", "INFO",
        "--session", "cursor-shared",
        "--window", "agent",
        "--pane", "0"
      ]
    }
  }
}
```

Place the JSON wherever your MCP host expects server definitions and ensure the `tmux_mcp` package is importable.

The top-level fields mean:

- `type` – Must remain `stdio`; Cursor communicates with the agent over stdin/stdout.
- `command` – Absolute path to the Python interpreter that should launch the agent (typically the `.venv/bin/python` inside this project).
- `args` – Ordered list of arguments given to that interpreter. The first element should be the launcher script followed by any CLI flags you want to pass through.

### MCP server arguments

The startup script `scripts/start_mcp_agent.py` forwards CLI options to `tmux_mcp.agent`. You can adjust any of these in the `args` array:

- `--log-level` – Logging verbosity (`DEBUG`, `INFO`, `WARNING`, `ERROR`).
- `--session` – Default tmux session to create/attach (default: `cursor-shared`).
- `--window` – Default tmux window name (default: `agent`).
- `--pane` – Default tmux pane identifier (default: `0`).
- `--log` – Path to the structured log file (default: `logs/agent_activity.log`).

Add or remove options as needed; Cursor will pass them verbatim when launching the MCP server.

## Working With tmux Sessions

1. Use the `list_profiles` and `upsert_profile` tools to manage encrypted SSH connection profiles. Supplying only a `name` and `source_host` is enough when the host exists in `~/.ssh/config`; the agent will inherit hostname, user, port, identity files, and common options automatically.
2. Cursor typically calls `health_check` followed by `connect_session`. Provide the profile name plus the desired tmux `session`, `window`, and optional `pane` identifiers.
3. To run commands, invoke `submit_command`. If safe mode flags a command, respond with `approve_command` or `reject_command` using the returned `command_id`.
4. Retrieve live context from a pane at any time with `read_context`.

**Example (Cursor chat prompt)**

```
Connect to the tmux server using profile "staging", session "cursor", window "agent".
```

Cursor will translate that message into a `connect_session` tool call:

```json
{
  "profile": "staging",
  "session": "cursor",
  "window": "agent"
}
```

Once connected, you can follow up with prompts such as “Run `npm test` in the current pane,” and the agent will send the command via `submit_command`.

**Example (create or update a profile)**

```
Create an SSH profile called "staging" for host 203.0.113.15 using user "deploy" and key file "/Users/bo/keys/staging.pem".
```

Cursor translates that into an `upsert_profile` tool call similar to:

```json
{
  "profile": {
    "name": "staging",
    "hostname": "203.0.113.15",
    "username": "deploy",
    "identity_file": "/Users/bo/keys/staging.pem"
  }
}
```

Subsequent “Connect to staging” prompts reuse the stored profile automatically. Call `upsert_profile` again with the same `name` to overwrite credentials or ports when they change.

The agent ensures the target session and window exist, creates them when necessary, and keeps a persistent pane snapshot so it can report only the delta after each command.

## Logging & Auditing

Every command, approval decision, and captured output delta is appended to `logs/agent_activity.log` as JSON Lines. Each record includes the Cursor task identifier plus the tmux session/window/pane, making it easy to trace parallel tasks that share the same tmux session.

## Troubleshooting

- **Paramiko TripleDES warning** – The current Paramiko release still references TripleDES. The cryptography library marks it as deprecated, so you may see `CryptographyDeprecationWarning` in the logs. It is a harmless warning; newer Paramiko releases will remove the legacy cipher automatically.
- **SSH connection issues** – Confirm the profile’s identity file exists locally and that any `ProxyCommand` or `IdentitiesOnly` options are valid. The agent reports these errors back through `SessionError` messages.
- **tmux not found** – Make sure `tmux` is installed on the remote host and available in the PATH exposed to the SSH session.

## Customisation

- Default feature flags live in `src/tmux_mcp/config/feature-flags.yaml`. Adjust them to change safe-mode behaviour or approval patterns.
- Capability definitions are in `src/tmux_mcp/config/capabilities.json`. Update this file if you add or rename tools/resources.
- When you add new connection profiles by hand, remember that the agent stores them encrypted on disk using Fernet; deleting `.tmux_mcp/` will remove locally cached profiles.

## Support

If Cursor reports that the MCP server is unavailable, run `python -m tmux_mcp.agent --log-level DEBUG` from a terminal to inspect the startup logs. Open an issue with the collected logs if you need further assistance.
