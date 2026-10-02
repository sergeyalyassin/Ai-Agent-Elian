from __future__ import annotations
from typing import Any, Callable

class VerificationEngine:
    def __init__(self, verifier: Callable[...,bool]):
        self.verifier=verifier
    def verify_step(self, step, result, task=None):
        return bool(self.verifier(step=step,result=result,task=task))
    def verify_task(self, goal, results):
        return self.verify_step({"purpose":"final goal verification","verify":goal},results,{"goal":goal,"results":results})
