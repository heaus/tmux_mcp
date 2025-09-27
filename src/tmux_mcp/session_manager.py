"""Session management helpers for SSH-backed tmux control."""

from __future__ import annotations

import json
import os
import shlex
import stat
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, Optional

from cryptography.fernet import Fernet

try:
    import keyring  # type: ignore
except ImportError:  # pragma: no cover - optional dependency during testing
    keyring = None

import paramiko
from paramiko.proxy import ProxyCommand
from paramiko.ssh_exception import AuthenticationException, SSHException


class SessionError(RuntimeError):
    """Raised when tmux session interactions fail."""


@dataclass(slots=True)
class ConnectionProfile:
    name: str
    hostname: str
    username: str
    port: int = 22
    identity_file: Optional[str] = None
    ssh_options: Dict[str, str] = field(default_factory=dict)

    def to_ssh_command(self) -> str:
        parts = ["ssh", "-p", str(self.port)]
        if self.identity_file:
            parts.extend(["-i", self.identity_file])
        for key, value in self.ssh_options.items():
            parts.extend(["-o", f"{key}={value}"])
        parts.append(f"{self.username}@{self.hostname}")
        return " ".join(shlex.quote(part) for part in parts)


class KeyProvider:
    def __init__(
        self,
        service_name: str = "tmux_mcp",
        user_name: str = "encryption_key",
        secrets_file: Optional[Path] = None,
    ) -> None:
        self.service_name = service_name
        self.user_name = user_name
        self.secrets_file = secrets_file or Path(
            os.path.expanduser("~/.config/tmux_mcp/.key")
        )

    def get_key(self) -> Optional[bytes]:
        if keyring is not None:
            stored = keyring.get_password(self.service_name, self.user_name)
            if stored is not None:
                return stored.encode("utf-8")
        if self.secrets_file.exists():
            return self.secrets_file.read_text(encoding="utf-8").encode("utf-8")
        return None

    def set_key(self, key: bytes) -> None:
        if keyring is not None:
            keyring.set_password(self.service_name, self.user_name, key.decode("utf-8"))
            return
        self.secrets_file.parent.mkdir(parents=True, exist_ok=True)
        self.secrets_file.write_text(key.decode("utf-8"), encoding="utf-8")
        os.chmod(self.secrets_file, stat.S_IRUSR | stat.S_IWUSR)


class ConnectionProfileStore:
    """Stores SSH connection profiles encrypted at rest."""

    def __init__(
        self,
        *,
        config_dir: Optional[Path] = None,
        key_provider: Optional[KeyProvider] = None,
    ) -> None:
        base_dir = config_dir or Path(
            os.environ.get("TMUX_MCP_HOME", os.path.expanduser("~/.config/tmux_mcp"))
        )
        try:
            base_dir.mkdir(parents=True, exist_ok=True)
            self.config_dir = base_dir
        except PermissionError:
            fallback = Path.cwd() / ".tmux_mcp"
            fallback.mkdir(parents=True, exist_ok=True)
            self.config_dir = fallback
        self.path = self.config_dir / "connections.json.enc"
        self.key_provider = key_provider or KeyProvider()

    def list_profiles(self) -> Dict[str, ConnectionProfile]:
        data = self._load()
        return {
            name: ConnectionProfile(name=name, **payload)
            for name, payload in data.items()
        }

    def get_profile(self, name: str) -> Optional[ConnectionProfile]:
        return self.list_profiles().get(name)

    def save_profile(self, profile: ConnectionProfile) -> None:
        data = self._load()
        data[profile.name] = {
            "hostname": profile.hostname,
            "username": profile.username,
            "port": profile.port,
            "identity_file": profile.identity_file,
            "ssh_options": profile.ssh_options,
        }
        self._store(data)

    def delete_profile(self, name: str) -> None:
        data = self._load()
        data.pop(name, None)
        self._store(data)

    def _load(self) -> Dict[str, Dict[str, object]]:
        if not self.path.exists():
            return {}
        cipher = self._get_cipher()
        payload = self.path.read_bytes()
        decrypted = cipher.decrypt(payload)
        return json.loads(decrypted.decode("utf-8"))

    def _store(self, data: Dict[str, Dict[str, object]]) -> None:
        cipher = self._get_cipher()
        serialized = json.dumps(data).encode("utf-8")
        encrypted = cipher.encrypt(serialized)
        self.path.write_bytes(encrypted)

    def _get_cipher(self) -> Fernet:
        key = self.key_provider.get_key()
        if key is None:
            key = Fernet.generate_key()
            self.key_provider.set_key(key)
        return Fernet(key)


def _format_tmux_command(*parts: str) -> str:
    return " ".join(shlex.quote(part) for part in parts)


@dataclass(slots=True)
class _CommandResult:
    stdout: str
    stderr: str
    returncode: int


class _RemoteTmuxClient:
    """Executes tmux commands on a remote host via a persistent SSH session."""

    def __init__(self, profile: ConnectionProfile, *, timeout: int = 30) -> None:
        self.profile = profile
        self._timeout = timeout
        self._options = {
            key.lower(): value for key, value in profile.ssh_options.items()
        }
        self._proxy: Optional[ProxyCommand] = None
        self._ssh: Optional[paramiko.SSHClient] = None
        self._ssh = self._connect()

    def __del__(self) -> None:  # pragma: no cover - best effort cleanup
        self.close()

    def close(self) -> None:
        if self._ssh is not None:
            try:
                self._ssh.close()
            finally:
                self._ssh = None
        if self._proxy is not None:
            try:
                self._proxy.close()
            finally:
                self._proxy = None

    def ensure_session(
        self, *, session_name: str, window_name: Optional[str] = None
    ) -> None:
        exists = self._run_tmux("has-session", "-t", session_name)
        if exists.returncode != 0:
            args = ["new-session", "-d", "-s", session_name]
            if window_name:
                args.extend(["-n", window_name])
            created = self._run_tmux(*args)
            if created.returncode != 0:
                self._handle_failure(created, f"create session '{session_name}'")
        if window_name:
            if not self._window_exists(session_name, window_name):
                created = self._run_tmux(
                    "new-window", "-t", session_name, "-n", window_name
                )
                if created.returncode != 0:
                    self._handle_failure(
                        created,
                        f"create window '{window_name}' in session '{session_name}'",
                    )

    def get_pane(
        self, session_name: str, window_name: str, pane_ref: str
    ) -> "_RemotePane":
        target = f"{session_name}:{window_name}"
        panes = self._run_tmux(
            "list-panes", "-t", target, "-F", "#{pane_id}:#{pane_index}"
        )
        if panes.returncode != 0:
            self._handle_failure(panes, f"locate panes in window '{window_name}'")
        for line in panes.stdout.splitlines():
            if not line:
                continue
            try:
                pane_id, pane_index = line.split(":", 1)
            except ValueError:
                continue
            if pane_ref in {pane_id, pane_index}:
                return _RemotePane(self, pane_id)
        raise SessionError(f"Pane '{pane_ref}' not available")

    def list_windows(self, session_name: str) -> list[str]:
        result = self._run_tmux(
            "list-windows", "-t", session_name, "-F", "#{window_name}"
        )
        if result.returncode != 0:
            self._handle_failure(result, f"inspect windows for '{session_name}'")
        return [line.strip() for line in result.stdout.splitlines() if line.strip()]

    def _window_exists(self, session_name: str, window_name: str) -> bool:
        return window_name in self.list_windows(session_name)

    def _connect(self) -> paramiko.SSHClient:
        client = paramiko.SSHClient()
        client.load_system_host_keys()
        strict = self._options.get("stricthostkeychecking", "no").lower()
        if strict == "yes":
            client.set_missing_host_key_policy(paramiko.RejectPolicy())
        else:
            client.set_missing_host_key_policy(paramiko.AutoAddPolicy())

        connect_kwargs: Dict[str, object] = {
            "hostname": self.profile.hostname,
            "port": self.profile.port,
            "username": self.profile.username,
            "timeout": self._timeout,
            "allow_agent": True,
            "look_for_keys": True,
        }

        identities_only = self._options.get("identitiesonly")
        if identities_only and identities_only.lower() in {"yes", "true", "1"}:
            connect_kwargs["allow_agent"] = False
            connect_kwargs["look_for_keys"] = False

        if self.profile.identity_file:
            key_path = os.path.expanduser(self.profile.identity_file)
            if os.path.exists(key_path):
                connect_kwargs["key_filename"] = key_path

        password = self._options.get("password")
        if password:
            connect_kwargs["password"] = password

        known_hosts = self._options.get("userknownhostsfile")
        if known_hosts:
            for path in shlex.split(known_hosts):
                expanded = os.path.expanduser(path)
                if os.path.exists(expanded):
                    try:
                        client.load_host_keys(expanded)
                    except Exception:  # pragma: no cover - defensive
                        pass

        proxy_command = self._options.get("proxycommand")
        if proxy_command:
            formatted = (
                proxy_command.replace("%h", self.profile.hostname)
                .replace("%p", str(self.profile.port))
                .replace("%r", self.profile.username)
            )
            self._proxy = ProxyCommand(formatted)
            connect_kwargs["sock"] = self._proxy

        try:
            client.connect(**connect_kwargs)
        except AuthenticationException as exc:  # pragma: no cover - depends on env
            raise SessionError(
                f"SSH authentication failed for profile '{self.profile.name}'"
            ) from exc
        except SSHException as exc:  # pragma: no cover - depends on env
            raise SessionError(
                f"SSH connection failed for profile '{self.profile.name}'"
            ) from exc
        except OSError as exc:  # pragma: no cover - depends on env
            raise SessionError(
                f"Unable to reach {self.profile.hostname}:{self.profile.port}"
            ) from exc

        keepalive = self._options.get("serveraliveinterval")
        if keepalive:
            try:
                interval = int(keepalive)
            except ValueError:  # pragma: no cover - defensive
                interval = 0
            if interval > 0:
                transport = client.get_transport()
                if transport is not None:
                    transport.set_keepalive(interval)

        return client

    def _run_tmux(self, *args: str) -> _CommandResult:
        if self._ssh is None:
            raise SessionError("SSH session not established")
        command = _format_tmux_command("tmux", *args)
        try:
            stdin, stdout, stderr = self._ssh.exec_command(
                command,
                timeout=self._timeout,
            )
            try:
                exit_code = stdout.channel.recv_exit_status()
            finally:
                stdin.close()
            stdout_data = stdout.read().decode("utf-8", errors="replace")
            stderr_data = stderr.read().decode("utf-8", errors="replace")
            return _CommandResult(
                stdout=stdout_data, stderr=stderr_data, returncode=exit_code
            )
        except SSHException as exc:
            raise SessionError(
                "SSH connection lost while issuing tmux command"
            ) from exc

    def _handle_failure(self, result: _CommandResult, action: str) -> None:
        payload = (result.stderr or result.stdout or "unknown error").strip()
        lowered = payload.lower()
        if "tmux" in lowered and "not found" in lowered:
            raise SessionError(
                "tmux binary not found on remote host; install tmux or expose it via PATH"
            )
        if "permission denied" in lowered:
            raise SessionError(f"Permission denied while attempting to {action}")
        if "no such file or directory" in lowered:
            raise SessionError(payload)
        raise SessionError(f"Failed to {action}: {payload}")


class _RemotePane:
    """Represents a tmux pane accessible over a persistent SSH session."""

    def __init__(self, client: _RemoteTmuxClient, pane_id: str) -> None:
        self._client = client
        self._pane_id = pane_id

    def capture_pane(self, lines: int = 200) -> list[str]:
        result = self._client._run_tmux(
            "capture-pane", "-t", self._pane_id, "-p", "-S", f"-{lines}"
        )
        if result.returncode != 0:
            self._client._handle_failure(result, f"capture pane '{self._pane_id}'")
        text = result.stdout or ""
        stripped = text.rstrip("\n")
        if not stripped:
            return []
        return stripped.splitlines()

    def send_keys(self, keys: str, enter: bool = True) -> None:
        args = ["send-keys", "-t", self._pane_id, keys]
        if enter:
            args.append("Enter")
        result = self._client._run_tmux(*args)
        if result.returncode != 0:
            self._client._handle_failure(result, f"send keys to pane '{self._pane_id}'")


class SessionManager:
    """Controls remote tmux sessions and panes via SSH."""

    def __init__(
        self, profile_store: ConnectionProfileStore, *, timeout: int = 30
    ) -> None:
        self.profile_store = profile_store
        self._timeout = timeout
        self._tmux_client: Optional[_RemoteTmuxClient] = None
        self._current_profile: Optional[ConnectionProfile] = None

    def connect(
        self, profile_name: str, *, session_name: str, window_name: Optional[str] = None
    ) -> None:
        profile = self.profile_store.get_profile(profile_name)
        if profile is None:
            raise SessionError(f"Profile '{profile_name}' not found")
        if self._tmux_client is not None:
            self._tmux_client.close()
        self._tmux_client = _RemoteTmuxClient(profile, timeout=self._timeout)
        self._current_profile = profile
        self.ensure_session(session_name=session_name, window_name=window_name)

    def ensure_session(
        self, *, session_name: str, window_name: Optional[str] = None
    ) -> None:
        if self._tmux_client is None:
            raise SessionError("Server not connected")
        self._tmux_client.ensure_session(
            session_name=session_name, window_name=window_name
        )

    def get_pane(
        self, session_name: str, window_name: str, pane_ref: str
    ) -> _RemotePane:
        if self._tmux_client is None:
            raise SessionError("Server not connected")
        return self._tmux_client.get_pane(session_name, window_name, pane_ref)

    def disconnect(self) -> None:
        if self._tmux_client is not None:
            self._tmux_client.close()
        self._tmux_client = None
        self._current_profile = None

    @property
    def current_profile(self) -> Optional[ConnectionProfile]:
        return self._current_profile


__all__ = [
    "ConnectionProfile",
    "ConnectionProfileStore",
    "KeyProvider",
    "SessionError",
    "SessionManager",
]
