"""Utilities for structured, append-only logging."""

from __future__ import annotations

import json
import threading
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Dict


@dataclass(slots=True)
class LogRecord:
    """Represents a single command/action emitted by the agent."""

    task_id: str
    session: str
    window: str
    pane: str
    command: str
    status: str
    stdout: str = ""
    stderr: str = ""
    safety_state: str = "allowed"
    approved_by_user: bool = False
    timestamp: datetime = field(default_factory=datetime.utcnow)
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_json(self) -> str:
        payload: Dict[str, Any] = {
            "timestamp": self.timestamp.isoformat() + "Z",
            "task_id": self.task_id,
            "session": self.session,
            "window": self.window,
            "pane": self.pane,
            "command": self.command,
            "status": self.status,
            "stdout": self.stdout,
            "stderr": self.stderr,
            "safety_state": self.safety_state,
            "approved_by_user": self.approved_by_user,
            "metadata": self.metadata,
        }
        return json.dumps(payload, ensure_ascii=False)


class StructuredLogWriter:
    """Thread-safe helper that appends JSON records and rotates when needed."""

    def __init__(
        self, log_path: Path, *, max_bytes: int = 5_000_000, backups: int = 3
    ) -> None:
        self.log_path = log_path
        self.max_bytes = max_bytes
        self.backups = backups
        self._lock = threading.Lock()
        self.log_path.parent.mkdir(parents=True, exist_ok=True)

    def append(self, record: LogRecord) -> None:
        serialized = record.to_json()
        with self._lock:
            self._rotate_if_needed(len(serialized) + 1)
            with self.log_path.open("a", encoding="utf-8") as fh:
                fh.write(serialized)
                fh.write("\n")

    def _rotate_if_needed(self, incoming_bytes: int) -> None:
        if not self.log_path.exists():
            return
        projected_size = self.log_path.stat().st_size + incoming_bytes
        if projected_size <= self.max_bytes:
            return

        base = self.log_path
        oldest = base.with_name(f"{base.name}.{self.backups}")
        if oldest.exists():
            oldest.unlink()
        for index in range(self.backups - 1, 0, -1):
            src = base.with_name(f"{base.name}.{index}")
            dst = base.with_name(f"{base.name}.{index + 1}")
            if src.exists():
                src.rename(dst)
        base.rename(base.with_name(f"{base.name}.1"))


__all__ = ["StructuredLogWriter", "LogRecord"]
