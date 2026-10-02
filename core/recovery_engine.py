from __future__ import annotations
import time

class RecoveryEngine:
    """Classifies failures without pretending every failure is safe to retry."""
    TRANSIENT=(TimeoutError, ConnectionError)
    def __init__(self,max_retries=2):
        self.max_retries=max_retries
    def classify(self,error):
        if isinstance(error,self.TRANSIENT): return "transient"
        name=type(error).__name__.lower()
        if any(x in name for x in ("timeout","connection","temporary","rate")): return "transient"
        if isinstance(error,(PermissionError,ValueError)): return "permanent"
        return "unknown"
    def backoff(self,attempt): return min(2 ** max(0,attempt-1),8)
    def sleep(self,attempt): time.sleep(self.backoff(attempt))
