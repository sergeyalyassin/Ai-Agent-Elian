from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any


class ApprovalRequired(PermissionError):
    def __init__(self, tool: str, reason: str):
        self.tool = tool
        self.reason = reason
        super().__init__(f"approval required for {tool}: {reason}")


@dataclass(frozen=True)
class PolicyDecision:
    allowed: bool
    requires_approval: bool = False
    reason: str = ""


class ExecutionPolicy:
    """Runtime authorization layer between the planner and side-effecting tools."""
    def __init__(self, full_access: bool = False):
        self.full_access = bool(full_access)
        self.mode = os.getenv("AGENT_APPROVAL_MODE", "dangerous").strip().lower()

    def risk(self, name: str, inputs: dict[str, Any], declared: str) -> str:
        name = str(name)
        if name == "browser":
            action = str(inputs.get("action", "")).lower()
            return "low" if action in {"open", "snapshot", "screenshot", "close"} else "high"
        if name == "github":
            method = str(inputs.get("method", "GET")).upper()
            return "low" if method in {"GET", "HEAD", "OPTIONS"} else "high"
        return declared

    def authorize(self, name: str, inputs: dict[str, Any], declared: str) -> PolicyDecision:
        risk = self.risk(name, inputs, declared)
        if self.mode in {"off", "none"}:
            return PolicyDecision(True, False, "approval policy disabled")
        if self.mode in {"auto", "autonomous"}:
            return PolicyDecision(True, False, "autonomous approval mode")
        if risk in {"low", "medium"}:
            return PolicyDecision(True, False, "low-risk operation")
        if self.full_access and self.mode == "full-auto":
            return PolicyDecision(True, False, "full-auto explicitly enabled")
        return PolicyDecision(False, True, f"{risk}-risk side effect requires explicit approval")

    def check(self, name: str, inputs: dict[str, Any], declared: str) -> None:
        decision = self.authorize(name, inputs, declared)
        if decision.requires_approval:
            raise ApprovalRequired(name, decision.reason)
        if not decision.allowed:
            raise PermissionError(decision.reason)
