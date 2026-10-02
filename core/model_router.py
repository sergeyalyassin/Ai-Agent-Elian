from __future__ import annotations
from typing import Callable

class ModelRouter:
    """Task-aware model selection. Uses existing OpenRouter candidate provider."""
    def __init__(self,candidates: Callable[[str],list[str]]): self.candidates=candidates
    def route(self,task_type="general",budget=None):
        models=self.candidates(task_type)
        if not models: raise RuntimeError("No model available")
        return models[0]
    def candidates_for(self,task_type="general"): return self.candidates(task_type)
