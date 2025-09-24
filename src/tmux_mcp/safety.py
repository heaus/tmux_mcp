"""Safety filtering and approval tracking for agent-issued commands."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Iterable, List, Optional


@dataclass(slots=True)
class SafetyConfig:
    safe_mode: bool = True
    destructive_patterns: Iterable[str] = (
        r"rm\s+-rf\s+/",
        r"rm\s+-rf\s+\$\{?HOME\}?",
        r"rm\s+-rf\s+\.",
        r"mkfs\s+",
        r"dd\s+if=",
        r"shutdown\s+-",
        r"reboot",
        r":(){:|:&};:",
    )
    warn_patterns: Iterable[str] = (
        r"rm\s+-rf",
        r"git\s+reset\s+--hard",
        r"docker\s+system\s+prune",
    )


@dataclass(slots=True)
class SafetyEvaluation:
    requires_approval: bool
    blocked: bool
    reason: Optional[str] = None


class SafetyEvaluator:
    """Evaluates commands against destructive pattern lists."""

    def __init__(self, config: Optional[SafetyConfig] = None) -> None:
        self.config = config or SafetyConfig()
        self._destructive = [
            re.compile(pattern, re.IGNORECASE)
            for pattern in self.config.destructive_patterns
        ]
        self._warn = [
            re.compile(pattern, re.IGNORECASE) for pattern in self.config.warn_patterns
        ]

    def evaluate(self, command: str) -> SafetyEvaluation:
        command = command.strip()
        for pattern in self._destructive:
            if pattern.search(command):
                return SafetyEvaluation(
                    requires_approval=True if self.config.safe_mode else False,
                    blocked=False,
                    reason="destructive-pattern",
                )
        for pattern in self._warn:
            if pattern.search(command):
                return SafetyEvaluation(
                    requires_approval=self.config.safe_mode,
                    blocked=False,
                    reason="warn-pattern",
                )
        return SafetyEvaluation(requires_approval=False, blocked=False, reason=None)

    def update_config(
        self,
        *,
        safe_mode: Optional[bool] = None,
        destructive_patterns: Optional[List[str]] = None,
        warn_patterns: Optional[List[str]] = None,
    ) -> None:
        if safe_mode is not None:
            self.config.safe_mode = safe_mode
        if destructive_patterns is not None:
            self.config.destructive_patterns = destructive_patterns
            self._destructive = [
                re.compile(pattern, re.IGNORECASE) for pattern in destructive_patterns
            ]
        if warn_patterns is not None:
            self.config.warn_patterns = warn_patterns
            self._warn = [
                re.compile(pattern, re.IGNORECASE) for pattern in warn_patterns
            ]


__all__ = ["SafetyConfig", "SafetyEvaluation", "SafetyEvaluator"]
