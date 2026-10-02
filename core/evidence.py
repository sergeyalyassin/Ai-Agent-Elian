from __future__ import annotations
from dataclasses import dataclass, asdict
from typing import Any
import time, uuid

@dataclass
class Evidence:
    id: str
    step_id: str
    tool: str
    inputs: dict[str,Any]
    output: Any
    status: str
    started_at: float
    finished_at: float
    error: str | None = None
    verification: bool | None = None
    def as_dict(self): return asdict(self)

def capture(step_id, tool, inputs, output=None, status="success", started_at=None, error=None, verification=None):
    return Evidence(str(uuid.uuid4()),str(step_id),str(tool),inputs,output,status,started_at or time.time(),time.time(),error,verification).as_dict()
