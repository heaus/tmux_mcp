"""Entry point exposing the tmux agent as a Model Context Protocol tool."""

from __future__ import annotations

import argparse
import json
import logging
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml
from importlib import resources

from .command_bridge import CommandBridge, CommandRequest
from .logging_utils import StructuredLogWriter
from .safety import SafetyConfig, SafetyEvaluator
from .session_manager import (
    ConnectionProfile,
    ConnectionProfileStore,
    SessionError,
    SessionManager,
)
from .ssh_config import SSHHostConfig, load_ssh_config

DEFAULT_SESSION = "cursor-shared"
DEFAULT_WINDOW = "agent"
DEFAULT_PANE = "0"
PROTOCOL_VERSION = "2024-11-05"


CONFIG_PACKAGE = "tmux_mcp.config"
FEATURE_FLAGS_RESOURCE = "feature-flags.yaml"
CAPABILITIES_RESOURCE = "capabilities.json"

LOGGER = logging.getLogger(__name__)

@dataclass(slots=True)
class ToolDefinition:
    name: str
    description: str
    input_schema: Dict[str, Any]
    output_schema: Optional[Dict[str, Any]] = None

    def to_payload(self) -> Dict[str, Any]:
        payload = {
            "name": self.name,
            "description": self.description,
            "inputSchema": self.input_schema,
        }
        if self.output_schema:
            payload["outputSchema"] = self.output_schema
        return payload


class MCPAgentServer:
    """JSON-RPC handler that satisfies the MCP surface expected by Cursor."""

    def __init__(
        self,
        *,
        command_bridge: CommandBridge,
        session_manager: SessionManager,
        profile_store: ConnectionProfileStore,
        safety: SafetyEvaluator,
        default_session: str,
        default_window: str,
        default_pane: str,
        server_info: Dict[str, Any],
        tools: List[ToolDefinition],
        prompts: List[Dict[str, Any]],
        resources: List[Dict[str, Any]],
        ssh_hosts: Dict[str, SSHHostConfig],
    ) -> None:
        self.command_bridge = command_bridge
        self.session_manager = session_manager
        self.profile_store = profile_store
        self.safety = safety
        self.default_session = default_session
        self.default_window = default_window
        self.default_pane = default_pane
        self.server_info = server_info
        self.tools = tools
        self.prompts = prompts
        self.resources = resources
        self.ssh_hosts = ssh_hosts
        self._client_info: Dict[str, Any] = {}
        self._initialized = False
        self._handlers = {
            "initialize": self._handle_initialize,
            "initialized": self._handle_initialized,
            "health_check": self._handle_health_check,
            "tools/list": self._handle_list_tools,
            "tools/call": self._handle_call_tool,
            "prompts/list": self._handle_list_prompts,
            "resources/list": self._handle_list_resources,
            "resources/templates/list": self._handle_list_resource_templates,
        }
        self._tool_routes = {
            "connect_session": self._tool_connect_session,
            "submit_command": self._tool_submit_command,
            "approve_command": self._tool_approve_command,
            "reject_command": self._tool_reject_command,
            "read_context": self._tool_read_context,
            "list_profiles": self._tool_list_profiles,
            "upsert_profile": self._tool_upsert_profile,
            "delete_profile": self._tool_delete_profile,
        }

    def serve_forever(self) -> None:
        for line in sys.stdin:
            line = line.strip()
            if not line:
                continue
            try:
                message = json.loads(line)
            except json.JSONDecodeError as exc:
                # If JSON parsing fails there is no request id, so we skip emitting an error.
                continue
            response = self.handle_request(message)
            if response is not None:
                sys.stdout.write(json.dumps(response) + "\n")
                sys.stdout.flush()

    def handle_request(self, message: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        if "error" in message:
            return None

        method = message.get("method")
        request_id = message.get("id")

        if method is None:
            return None

        handler = self._handlers.get(method)
        if handler is None:
            if request_id is not None:
                return self._build_error(
                    request_id, code=-32601, message=f"Unknown method: {method}"
                )
            return None

        params = message.get("params", {})
        try:
            result = handler(params)
        except SessionError as exc:
            LOGGER.warning("Session error during '%s': %s", method, exc)
            if request_id is not None:
                return self._build_error(request_id, code=4001, message=str(exc))
            return None
        except KeyError as exc:
            LOGGER.warning("Missing key for method '%s': %s", method, exc)
            if request_id is not None:
                return self._build_error(
                    request_id, code=4002, message=f"Missing key: {exc}"
                )
            return None
        except Exception as exc:
            LOGGER.exception("Unhandled error in method '%s'", method)
            if request_id is not None:
                return self._build_error(request_id, code=5000, message=str(exc))
            return None

        if request_id is None:
            return None

        if method == "initialized":
            return None

        return {"jsonrpc": "2.0", "id": request_id, "result": result}

    def _emit_error(
        self, request_id: Optional[str], *, code: int, message: str
    ) -> None:
        if request_id is not None:
            payload = self._build_error(request_id, code=code, message=message)
            sys.stdout.write(json.dumps(payload) + "\n")
            sys.stdout.flush()

    def _build_error(
        self, request_id: Optional[str], *, code: int, message: str
    ) -> Dict[str, Any]:
        if request_id is None:
            raise ValueError("Cannot build error response without valid request_id")

        payload = {
            "jsonrpc": "2.0",
            "id": request_id,
            "error": {"code": code, "message": message},
        }
        return payload

    # -- MCP lifecycle -------------------------------------------------

    def _handle_initialize(self, params: Dict[str, Any]) -> Dict[str, Any]:
        self._client_info = params.get("clientInfo", {})
        self._initialized = True
        return {
            "protocolVersion": PROTOCOL_VERSION,
            "serverInfo": self.server_info,
            "capabilities": {
                "tools": {"list": True, "call": True},
                "prompts": {"list": True},
                "resources": {"list": True},
            },
        }

    def _handle_initialized(self, _: Dict[str, Any]) -> None:
        return None

    def _handle_health_check(self, _: Dict[str, Any]) -> Dict[str, Any]:
        return {
            "status": "healthy",
            "version": "0.1.0",
            "capabilities": ["tools", "prompts", "resources"],
            "uptime": "available",
        }

    # -- Listing endpoints ---------------------------------------------

    def _handle_list_tools(self, params: Dict[str, Any]) -> Dict[str, Any]:
        del params  # unused cursor pagination for now
        return {"tools": [tool.to_payload() for tool in self.tools]}

    def _handle_list_prompts(self, params: Dict[str, Any]) -> Dict[str, Any]:
        del params
        return {"prompts": self.prompts}

    def _handle_list_resources(self, params: Dict[str, Any]) -> Dict[str, Any]:
        del params
        return {"resources": self.resources}

    def _handle_list_resource_templates(self, params: Dict[str, Any]) -> Dict[str, Any]:
        del params
        return {"resourceTemplates": []}

    # -- Tool execution ------------------------------------------------

    def _handle_call_tool(self, params: Dict[str, Any]) -> Dict[str, Any]:
        name = params["name"]
        arguments = params.get("arguments", {})
        tool_handler = self._tool_routes.get(name)
        if tool_handler is None:
            raise SessionError(f"Unknown tool '{name}'")
        result = tool_handler(arguments)
        serialized = json.dumps(result)
        payload = {
            "content": [
                {"type": "text", "text": serialized},
            ],
            "structuredOutput": result,
            "structured": result,
            "structured_output": result,
            "isError": False,
        }
        LOGGER.info(
            "Tool '%s' payload: %s",
            name,
            json.dumps(payload, default=str)[:2000],
        )
        return payload

    # -- Tool implementations -----------------------------------------

    def _tool_connect_session(self, params: Dict[str, Any]) -> Dict[str, Any]:
        profile = params["profile"]
        session = params.get("session", self.default_session)
        window = params.get("window", self.default_window)
        LOGGER.debug(
            "Connecting session using profile='%s', session='%s', window='%s'",
            profile,
            session,
            window,
        )
        self.session_manager.connect(profile, session_name=session, window_name=window)
        return {"status": "connected", "session": session, "window": window}

    def _tool_submit_command(self, params: Dict[str, Any]) -> Dict[str, Any]:
        command_id = params.get("command_id") or self.command_bridge.next_command_id()
        task_id = params["task_id"]
        command = params["command"]
        session = params.get("session", self.default_session)
        window = params.get("window", self.default_window)
        pane = params.get("pane", self.default_pane)
        metadata = params.get("metadata")
        force = params.get("force", False)
        safe_mode_override = params.get("safe_mode")
        if force:
            safe_mode_override = False
        old_safe_mode = self.safety.config.safe_mode
        if safe_mode_override is not None:
            self.safety.update_config(safe_mode=safe_mode_override)
        try:
            result = self.command_bridge.submit_command(
                CommandRequest(
                    command_id=command_id,
                    task_id=task_id,
                    session=session,
                    window=window,
                    pane=pane,
                    command=command,
                    metadata=metadata,
                ),
                approved=force,
            )
        finally:
            if safe_mode_override is not None:
                self.safety.update_config(safe_mode=old_safe_mode)
        return {
            "command_id": command_id,
            "status": result.status,
            "stdout": result.stdout,
            "stderr": result.stderr,
            "safety": {
                "requiresApproval": result.safety.requires_approval,
                "blocked": result.safety.blocked,
                "reason": result.safety.reason,
            },
            "approvedByUser": result.approved_by_user,
        }

    def _tool_approve_command(self, params: Dict[str, Any]) -> Dict[str, Any]:
        command_id = params["command_id"]
        try:
            result = self.command_bridge.execute_pending(
                command_id, approved_by_user=True
            )
        except KeyError as exc:
            raise SessionError(f"Unknown pending command '{command_id}'") from exc
        return {
            "command_id": command_id,
            "status": result.status,
            "stdout": result.stdout,
            "stderr": result.stderr,
            "approvedByUser": True,
        }

    def _tool_reject_command(self, params: Dict[str, Any]) -> Dict[str, Any]:
        command_id = params["command_id"]
        try:
            self.command_bridge.reject_pending(command_id)
        except KeyError as exc:
            raise SessionError(f"Unknown pending command '{command_id}'") from exc
        return {"command_id": command_id, "status": "rejected"}

    def _tool_read_context(self, params: Dict[str, Any]) -> Dict[str, Any]:
        session = params.get("session", self.default_session)
        window = params.get("window", self.default_window)
        pane = params.get("pane", self.default_pane)
        context = self.command_bridge.read_context(session, window, pane)
        return {"session": session, "window": window, "pane": pane, "context": context}

    def _tool_list_profiles(self, _: Dict[str, Any]) -> Dict[str, Any]:
        profiles = [
            asdict(profile) for profile in self.profile_store.list_profiles().values()
        ]
        return {"profiles": profiles}

    def _tool_upsert_profile(self, params: Dict[str, Any]) -> Dict[str, Any]:
        profile_payload = dict(params["profile"])
        profile_name = profile_payload.get("name")
        if not profile_name:
            raise SessionError("Profile name is required")

        source_host = profile_payload.get("source_host") or profile_payload.get(
            "host_alias"
        )
        alias = source_host or profile_name
        host_config = self.ssh_hosts.get(alias)

        merged_payload = dict(profile_payload)
        merged_payload.pop("host_alias", None)
        merged_payload.pop("source_host", None)

        if host_config is not None:
            if not merged_payload.get("hostname") and host_config.hostname:
                merged_payload["hostname"] = host_config.hostname
            if not merged_payload.get("username") and host_config.username:
                merged_payload["username"] = host_config.username
            if merged_payload.get("port") in (None, "") and host_config.port:
                merged_payload["port"] = host_config.port
            if not merged_payload.get("identity_file") and host_config.identity_files:
                merged_payload["identity_file"] = host_config.identity_files[0]
            existing_options = merged_payload.get("ssh_options") or {}
            merged_options = dict(host_config.options)
            merged_options.update(existing_options)
            if merged_options:
                merged_payload["ssh_options"] = merged_options

        port_value = merged_payload.get("port")
        if port_value not in (None, ""):
            try:
                merged_payload["port"] = int(port_value)
            except (TypeError, ValueError) as exc:  # pragma: no cover - defensive
                raise SessionError("Port must be an integer") from exc

        if not merged_payload.get("hostname"):
            raise SessionError(
                f"Profile '{profile_name}' is missing 'hostname' and no matching host alias was found"
            )
        if not merged_payload.get("username"):
            raise SessionError(
                f"Profile '{profile_name}' is missing 'username' and no matching host alias was found"
            )

        profile = ConnectionProfile(**merged_payload)
        self.profile_store.save_profile(profile)
        return {"status": "saved", "profile": profile_name}

    def _tool_delete_profile(self, params: Dict[str, Any]) -> Dict[str, Any]:
        name = params["name"]
        self.profile_store.delete_profile(name)
        return {"status": "deleted", "profile": name}


def load_feature_flags(path: Optional[Path] = None) -> Dict[str, Any]:
    if path is not None and path.exists():
        with path.open("r", encoding="utf-8") as fh:
            data = yaml.safe_load(fh) or {}
    else:
        with resources.files(CONFIG_PACKAGE).joinpath(FEATURE_FLAGS_RESOURCE).open(
            "r", encoding="utf-8"
        ) as fh:
            data = yaml.safe_load(fh) or {}
    if not isinstance(data, dict):
        raise ValueError("feature-flags file must contain a mapping")
    return data


def load_capabilities(
    path: Optional[Path] = None,
) -> tuple[
    Dict[str, Any], List[ToolDefinition], List[Dict[str, Any]], List[Dict[str, Any]]
]:
    if path is not None and path.exists():
        with path.open("r", encoding="utf-8") as fh:
            data = json.load(fh)
    else:
        with resources.files(CONFIG_PACKAGE).joinpath(CAPABILITIES_RESOURCE).open(
            "r", encoding="utf-8"
        ) as fh:
            data = json.load(fh)
    provider = data.get("provider", {})
    tools_payload = data.get("tools", [])
    prompts = data.get("prompts", [])
    resources_payload = data.get("resources", [])
    tools = [
        ToolDefinition(
            name=item["name"],
            description=item.get("description", ""),
            input_schema=item.get("inputSchema", {}),
            output_schema=item.get("outputSchema"),
        )
        for item in tools_payload
    ]
    server_info = {
        "name": provider.get("name", "tmux-mcp-agent"),
        "version": provider.get("version", "0.0.0"),
    }
    return server_info, tools, prompts, resources_payload


def build_prompts(
    base_prompts: List[Dict[str, Any]], ssh_hosts: Dict[str, SSHHostConfig]
) -> List[Dict[str, Any]]:
    prompts = list(base_prompts)
    if not ssh_hosts:
        return prompts

    lines = []
    for alias, host in sorted(ssh_hosts.items(), key=lambda item: item[0]):
        host_label = host.hostname or alias
        user_label = host.username or "(default user)"
        port_label = host.port or 22
        identity_label = host.identity_files[0] if host.identity_files else "default"
        lines.append(
            f"- {alias}: hostname={host_label}, user={user_label}, port={port_label}, identity={identity_label}"
        )
    prompt_text = (
        "Create tmux SSH connection profiles using stored host aliases. "
        "Call `upsert_profile` with `source_host` to reuse ~/.ssh/config details.\n"
        "Example: {\"profile\": {\"name\": \"dev-session\", \"source_host\": \"dev\"}}.\n"
        "Override any fields by including them explicitly in the profile object.\n\n"
        "Known host aliases:\n" + "\n".join(lines)
    )
    prompts.append(
        {
            "name": "ssh_profile_helper",
            "description": "Guidance for creating connection profiles from ~/.ssh/config",
            "arguments": [],
            "messages": [{"role": "system", "content": prompt_text}],
        }
    )
    return prompts


def build_server(
    *,
    log_path: Path,
    default_session: str = DEFAULT_SESSION,
    default_window: str = DEFAULT_WINDOW,
    default_pane: str = DEFAULT_PANE,
) -> MCPAgentServer:
    feature_flags = load_feature_flags()
    safe_mode_default = bool(feature_flags.get("default_safe_mode", True))
    default_config = SafetyConfig()
    patterns = feature_flags.get("safe_mode_patterns", {})
    destructive = tuple(
        patterns.get("destructive", tuple(default_config.destructive_patterns))
    )
    warn = tuple(patterns.get("warn", tuple(default_config.warn_patterns)))
    safety_config = SafetyConfig(
        safe_mode=safe_mode_default,
        destructive_patterns=destructive,
        warn_patterns=warn,
    )
    server_info, tools, prompt_templates, resources = load_capabilities()
    ssh_hosts = load_ssh_config()
    LOGGER.info("Loaded %d SSH host aliases from config", len(ssh_hosts))
    prompts = build_prompts(prompt_templates, ssh_hosts)
    profile_store = ConnectionProfileStore()
    safety = SafetyEvaluator(safety_config)
    log_writer = StructuredLogWriter(log_path)
    session_manager = SessionManager(profile_store)
    command_bridge = CommandBridge(session_manager, safety, log_writer)
    return MCPAgentServer(
        command_bridge=command_bridge,
        session_manager=session_manager,
        profile_store=profile_store,
        safety=safety,
        default_session=default_session,
        default_window=default_window,
        default_pane=default_pane,
        server_info=server_info,
        tools=tools,
        prompts=prompts,
        resources=resources,
        ssh_hosts=ssh_hosts,
    )


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Run the tmux MCP agent")
    parser.add_argument(
        "--log-level",
        default="INFO",
        help="Logging verbosity (DEBUG, INFO, WARNING, ERROR)",
    )
    parser.add_argument(
        "--session", default=DEFAULT_SESSION, help="Default tmux session name"
    )
    parser.add_argument(
        "--window", default=DEFAULT_WINDOW, help="Default tmux window name"
    )
    parser.add_argument(
        "--pane", default=DEFAULT_PANE, help="Default tmux pane reference"
    )
    parser.add_argument(
        "--log", default="logs/agent_activity.log", help="Structured log file path"
    )
    args = parser.parse_args(argv)

    numeric_level = getattr(logging, args.log_level.upper(), logging.INFO)
    logging.basicConfig(level=numeric_level)

    server = build_server(
        log_path=Path(args.log),
        default_session=args.session,
        default_window=args.window,
        default_pane=args.pane,
    )
    server.serve_forever()
    return 0


if __name__ == "__main__":  # pragma: no cover - CLI entrypoint
    raise SystemExit(main())
