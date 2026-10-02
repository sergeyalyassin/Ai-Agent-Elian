from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable
from .policy import ExecutionPolicy

@dataclass(frozen=True)
class ToolSpec:
    name: str
    handler: Callable[..., Any]
    description: str
    risk: str = "low"
    idempotent: bool = True
    timeout: int = 90

class ToolRegistry:
    """Planner-visible tools with enforced execution policy."""
    def __init__(self, policy: ExecutionPolicy | None = None):
        self._tools: dict[str, ToolSpec] = {}
        self.policy = policy or ExecutionPolicy()

    def register(self, name: str, handler: Callable[..., Any], description: str,
                 risk: str = "low", idempotent: bool = True, timeout: int = 90):
        if not name or not callable(handler):
            raise ValueError("invalid tool registration")
        self._tools[name] = ToolSpec(name, handler, description, risk, idempotent, timeout)

    def get(self, name: str) -> ToolSpec:
        if name not in self._tools:
            raise KeyError(f"unknown tool: {name}")
        return self._tools[name]

    def names(self) -> list[str]:
        return list(self._tools)

    def schema_text(self) -> str:
        return "\\n".join(f"- {t.name}: {t.description}; risk={t.risk}; idempotent={t.idempotent}; timeout={t.timeout}s" for t in self._tools.values())

    def execute(self, name: str, inputs: dict[str, Any]) -> Any:
        spec = self.get(name)
        if not isinstance(inputs, dict):
            raise TypeError("tool input must be an object")
        self.policy.check(spec.name, inputs, spec.risk)
        return spec.handler(**inputs)
