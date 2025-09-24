"""Bridge between the MCP surface and tmux sessions."""

from __future__ import annotations

import itertools
from dataclasses import dataclass
from typing import Dict, Optional, Tuple

from .logging_utils import LogRecord, StructuredLogWriter
from .safety import SafetyEvaluation, SafetyEvaluator
from .session_manager import SessionManager


@dataclass(slots=True)
class CommandRequest:
    command_id: str
    task_id: str
    session: str
    window: str
    pane: str
    command: str
    metadata: Optional[Dict[str, str]] = None


@dataclass(slots=True)
class CommandResult:
    status: str
    stdout: str
    stderr: str
    safety: SafetyEvaluation
    approved_by_user: bool = False


class CommandBridge:
    """Routes commands to tmux panes and captures resulting output."""

    def __init__(
        self,
        session_manager: SessionManager,
        safety: SafetyEvaluator,
        log_writer: StructuredLogWriter,
    ) -> None:
        self.session_manager = session_manager
        self.safety = safety
        self.log_writer = log_writer
        self._pending: Dict[str, CommandRequest] = {}
        self._pane_snapshots: Dict[Tuple[str, str, str], str] = {}
        self._id_counter = itertools.count(1)

    def next_command_id(self) -> str:
        return f"cmd-{next(self._id_counter)}"

    def submit_command(
        self, request: CommandRequest, *, approved: bool = False
    ) -> CommandResult:
        evaluation = self.safety.evaluate(request.command)
        if evaluation.blocked:
            self._log(request, "blocked", evaluation, stdout="", approved=approved)
            return CommandResult(
                status="blocked",
                stdout="",
                stderr="",
                safety=evaluation,
                approved_by_user=approved,
            )
        if evaluation.requires_approval and not approved:
            self._pending[request.command_id] = request
            self._log(
                request, "pending_approval", evaluation, stdout="", approved=False
            )
            return CommandResult(
                status="pending_approval",
                stdout="",
                stderr="",
                safety=evaluation,
                approved_by_user=False,
            )
        return self._execute(request, evaluation, approved=approved)

    def execute_pending(
        self, command_id: str, *, approved_by_user: bool
    ) -> CommandResult:
        request = self._pending.pop(command_id)
        evaluation = self.safety.evaluate(request.command)
        return self._execute(request, evaluation, approved=approved_by_user)

    def reject_pending(self, command_id: str) -> None:
        request = self._pending.pop(command_id)
        evaluation = self.safety.evaluate(request.command)
        self._log(request, "denied", evaluation, stdout="", approved=False)

    def read_context(self, session: str, window: str, pane: str) -> str:
        pane_obj = self.session_manager.get_pane(session, window, pane)
        snapshot = pane_obj.capture_pane()
        concatenated = "\n".join(snapshot)
        self._pane_snapshots[(session, window, pane)] = concatenated
        return concatenated

    def _execute(
        self, request: CommandRequest, evaluation: SafetyEvaluation, *, approved: bool
    ) -> CommandResult:
        pane_obj = self.session_manager.get_pane(
            request.session, request.window, request.pane
        )
        key = (request.session, request.window, request.pane)
        before_snapshot = self._pane_snapshots.get(key, "")
        if not before_snapshot:
            before_snapshot = "\n".join(pane_obj.capture_pane())
        pane_obj.send_keys(request.command, enter=True)
        after_snapshot_lines = pane_obj.capture_pane()
        after_snapshot = "\n".join(after_snapshot_lines)
        delta = self._calculate_delta(before_snapshot, after_snapshot)
        self._pane_snapshots[key] = after_snapshot
        self._log(request, "executed", evaluation, stdout=delta, approved=approved)
        return CommandResult(
            status="executed",
            stdout=delta,
            stderr="",
            safety=evaluation,
            approved_by_user=approved,
        )

    def _calculate_delta(self, before: str, after: str) -> str:
        if not before:
            return after
        if after.startswith(before):
            return after[len(before) :]
        return after

    def _log(
        self,
        request: CommandRequest,
        status: str,
        evaluation: SafetyEvaluation,
        *,
        stdout: str,
        approved: bool,
    ) -> None:
        record = LogRecord(
            task_id=request.task_id,
            session=request.session,
            window=request.window,
            pane=request.pane,
            command=request.command,
            status=status,
            stdout=stdout,
            stderr="",
            safety_state=(evaluation.reason or "allowed")
            if status != "blocked"
            else "blocked",
            approved_by_user=approved,
            metadata=request.metadata or {},
        )
        self.log_writer.append(record)


__all__ = ["CommandBridge", "CommandRequest", "CommandResult"]
