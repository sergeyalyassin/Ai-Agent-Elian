from __future__ import annotations
from dataclasses import dataclass, field
from typing import Any, Callable
import uuid

@dataclass
class Step:
    id: str
    tool: str
    input: dict[str,Any]
    purpose: str
    verify: str
    timeout: int = 90

@dataclass
class Plan:
    id: str
    goal: str
    steps: list[Step] = field(default_factory=list)
    version: int = 1

class PlanningEngine:
    def __init__(self, planner: Callable[...,dict], tool_names: Callable[[],list[str]]):
        self.planner=planner; self.tool_names=tool_names
    def create_plan(self, goal, context=None, previous=None):
        raw=self.planner(goal, previous)
        steps=[]
        for s in (raw or {}).get("steps",[]):
            if isinstance(s,dict) and s.get("tool") in self.tool_names() and isinstance(s.get("input",{}),dict):
                steps.append(Step(str(s.get("id") or uuid.uuid4()),s["tool"],s.get("input",{}),str(s.get("purpose","")),str(s.get("verify",""))))
        if not steps: raise ValueError("No executable plan steps")
        return Plan(str(uuid.uuid4()),str(goal),steps)
    def replan(self, goal, previous, context=None):
        return self.create_plan(goal,context,previous)
