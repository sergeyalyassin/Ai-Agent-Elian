from __future__ import annotations

import json
from typing import Any, Callable

def adaptive_next_step(decider: Callable[..., Any], goal: str, results: list[dict[str, Any]],
                       tools: list[str]) -> dict[str, Any] | None:
    """Normalize an LLM's next-action decision and reject unknown tools."""
    raw = decider(goal, results)
    if not raw:
        return None
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except Exception:
            return None
    step = raw.get("step") if isinstance(raw, dict) else None
    if not isinstance(step, dict) or step.get("tool") not in tools:
        return None
    if not isinstance(step.get("input", {}), dict):
        return None
    return step
