"""Utilities for reading SSH host definitions from ~/.ssh/config."""

from __future__ import annotations

import glob
import os
import shlex
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Set


@dataclass(slots=True)
class SSHHostConfig:
    """Represents a single SSH host alias from the user's configuration."""

    alias: str
    hostname: Optional[str] = None
    username: Optional[str] = None
    port: Optional[int] = None
    identity_files: tuple[str, ...] = ()
    options: Dict[str, str] = field(default_factory=dict)


def load_ssh_config(path: Optional[Path] = None) -> Dict[str, SSHHostConfig]:
    """Parse the SSH config from *path* returning host aliases by name.

    Supports simple ``Include`` directives and ignores ``Match`` sections. Only
    concrete host aliases (no wildcards) are returned so the caller can safely
    offer completions for connection profiles.
    """

    config_path = (path or Path.home() / ".ssh/config").expanduser()
    parser = _SSHConfigParser()
    parser.load(config_path)
    return parser.hosts


class _SSHConfigParser:
    def __init__(self) -> None:
        self.hosts: Dict[str, SSHHostConfig] = {}
        self._visited: Set[Path] = set()

    def load(self, path: Path) -> None:
        resolved = path.expanduser()
        try:
            resolved = resolved.resolve()
        except FileNotFoundError:
            return
        if not resolved.exists() or resolved in self._visited:
            return
        self._visited.add(resolved)
        current_hosts: List[str] = []
        with resolved.open("r", encoding="utf-8") as handle:
            for raw_line in handle:
                line = raw_line.strip()
                if not line or line.startswith("#"):
                    continue
                if "#" in line:
                    line = line.split("#", 1)[0].strip()
                    if not line:
                        continue
                parts = shlex.split(line, comments=False)
                if not parts:
                    continue
                keyword = parts[0].lower()
                values = parts[1:]
                if keyword == "match":
                    current_hosts = []
                    continue
                if keyword == "host":
                    current_hosts = [
                        value
                        for value in values
                        if value
                        and not any(token in value for token in ("*", "?", "!"))
                    ]
                    for alias in current_hosts:
                        self.hosts.setdefault(alias, SSHHostConfig(alias=alias))
                    continue
                if keyword == "include":
                    self._handle_include(values)
                    continue
                if not current_hosts:
                    continue
                for alias in current_hosts:
                    self._apply(alias, keyword, values)

    def _handle_include(self, values: Iterable[str]) -> None:
        for pattern in values:
            expanded = os.path.expanduser(pattern)
            for match in glob.glob(expanded):
                self.load(Path(match))

    def _apply(self, alias: str, keyword: str, values: List[str]) -> None:
        config = self.hosts.setdefault(alias, SSHHostConfig(alias=alias))
        if not values:
            return
        value = values[0]
        if keyword == "hostname":
            config.hostname = value
        elif keyword == "user":
            config.username = value
        elif keyword == "port":
            try:
                config.port = int(value)
            except ValueError:
                pass
        elif keyword == "identityfile":
            files = list(config.identity_files)
            files.append(os.path.expanduser(value))
            config.identity_files = tuple(files)
        else:
            config.options[keyword] = value


__all__ = ["SSHHostConfig", "load_ssh_config"]
