from __future__ import annotations
import time
class Scheduler:
    """Durable scheduling primitive; caller supplies persistence/execution."""
    def __init__(self,load_tasks,submit): self.load_tasks=load_tasks; self.submit=submit
    def due(self,now=None):
        now=now or time.time(); out=[]
        for t in self.load_tasks():
            due=t.get("run_at") or t.get("next_run")
            if due:
                try:
                    if float(due)<=now and t.get("status") not in {"completed","cancelled","running"}: out.append(t)
                except (TypeError,ValueError): pass
        return out
    def run_due(self,now=None):
        tasks=self.due(now); [self.submit(t["id"]) for t in tasks]; return tasks
