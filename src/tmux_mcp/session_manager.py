"""Session management helpers wrapping libtmux and SSH profile storage."""

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

try:
    import libtmux  # type: ignore
except ImportError:  # pragma: no cover - optional dependency during testing
    libtmux = None


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
        self.config_dir = config_dir or Path(os.path.expanduser("~/.config/tmux_mcp"))
        self.config_dir.mkdir(parents=True, exist_ok=True)
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


class SessionManager:
    """Controls tmux sessions and panes via libtmux."""

    def __init__(
        self, profile_store: ConnectionProfileStore, *, server_factory=None
    ) -> None:
        self.profile_store = profile_store
        if server_factory is None:
            if libtmux is None:
                raise RuntimeError(
                    "libtmux must be installed when no server_factory is provided"
                )
            server_factory = lambda profile: libtmux.Server(
                ssh_command=profile.to_ssh_command()
            )  # type: ignore[arg-type]
        self.server_factory = server_factory
        self._server = None
        self._current_profile: Optional[ConnectionProfile] = None

    def connect(
        self, profile_name: str, *, session_name: str, window_name: Optional[str] = None
    ) -> None:
        profile = self.profile_store.get_profile(profile_name)
        if profile is None:
            raise SessionError(f"Profile '{profile_name}' not found")
        self._server = self.server_factory(profile)
        self._current_profile = profile
        self.ensure_session(session_name=session_name, window_name=window_name)

    def ensure_session(self, *, session_name: str, window_name: Optional[str] = None):
        if self._server is None:
            raise SessionError("Server not connected")
        session = self._server.find_where({"session_name": session_name})
        if session is None:
            session = self._server.new_session(
                session_name=session_name, attach=False, kill_session=False
            )
        if window_name is not None:
            window = session.find_where({"window_name": window_name})
            if window is None:
                window = session.new_window(window_name=window_name, attach=False)
            return window
        return session

    def get_pane(self, session_name: str, window_name: str, pane_ref: str):
        if self._server is None:
            raise SessionError("Server not connected")
        session = self._server.find_where({"session_name": session_name})
        if session is None:
            raise SessionError(f"Session '{session_name}' not available")
        window = session.find_where({"window_name": window_name})
        if window is None:
            raise SessionError(f"Window '{window_name}' not available")
        for pane_obj in window.panes:
            pane_id = pane_obj.get("pane_id")
            pane_index = str(pane_obj.get("pane_index"))
            if pane_ref in {pane_id, pane_index}:
                return pane_obj
        raise SessionError(f"Pane '{pane_ref}' not available")

    def disconnect(self) -> None:
        self._server = None
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
