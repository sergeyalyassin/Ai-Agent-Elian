from __future__ import annotations

import json
import time
import traceback
from typing import Any, Callable


class AgentLoop:
    """Persistent plan/execute/verify/recover loop with bounded retries."""

    def __init__(self, *, registry, load_task: Callable[[str], dict | None],
                 update_task: Callable[..., dict | None], plan: Callable[..., dict],
                 verify: Callable[..., bool], max_steps: int = 20,
                 max_replans: int = 3, max_retries: int = 2):
        self.registry = registry
        self.load_task = load_task
        self.update_task = update_task
        self.plan = plan
        self.verify = verify
        self.max_steps = max_steps
        self.max_replans = max_replans
        self.max_retries = max_retries

    def _save(self, tid, **changes):
        self.update_task(tid, **changes)

    def _result(self, index, step, ok, value=None, error=None, attempts=1):
        item = {
            "step": index, "tool": step.get("tool"),
            "purpose": step.get("purpose", ""), "ok": bool(ok),
            "attempts": attempts, "at": time.time()
        }
        if value is not None:
            item["result"] = value
        if error is not None:
            item["error"] = str(error)
        return item

    def run(self, tid: str, goal: str, *, resume: bool = False) -> dict:
        task = self.load_task(tid)
        if not task:
            raise ValueError("task not found")

        results = list(task.get("results") or [])
        replans = int(task.get("replans", 0))
        plan_steps = list(task.get("plan") or [])
        index = int(task.get("step_index", 0)) if resume else 0

        self._save(tid, status="planning" if not plan_steps or not resume else "running")
        if not plan_steps or not resume:
            plan = self.plan(goal, results)
            plan_steps = list(plan.get("steps") or [])[: self.max_steps]
            if not plan_steps:
                raise ValueError("planner returned no executable steps")
            index = 0
            self._save(tid, plan=plan_steps, step_index=0, results=results,
                       replans=replans, status="running")

        while index < len(plan_steps) and len(results) < self.max_steps:
            task = self.load_task(tid) or task
            if task.get("status") == "cancelled":
                return task

            step = plan_steps[index]
            tool_name = step.get("tool")
            inputs = dict(step.get("input") or {})
            previous = json.dumps(results[-4:], ensure_ascii=False)[:12000]
            for key, value in list(inputs.items()):
                if isinstance(value, str):
                    inputs[key] = value.replace("{{PREVIOUS_RESULTS}}", previous)

            attempts = 0
            last_error = None
            value = None
            ok = False
            while attempts <= self.max_retries:
                attempts += 1
                try:
                    spec = self.registry.get(tool_name)
                    value = self.registry.execute(tool_name, inputs)
                    ok = self.verify(step=step, result=value, task=self.load_task(tid))
                    if ok:
                        break
                    last_error = "verification failed"
                except Exception as exc:
                    last_error = str(exc)
                    value = None
                if attempts <= self.max_retries:
                    self._save(tid, status="recovering", last_error=last_error)
                    time.sleep(min(2 ** (attempts - 1), 8))

            item = self._result(index, step, ok, value, last_error if not ok else None, attempts)
            results.append(item)
            if ok:
                index += 1
                self._save(tid, status="running", step_index=index, results=results,
                           last_error=None)
                continue

            if replans >= self.max_replans:
                self._save(tid, status="failed", step_index=index, results=results,
                           error=last_error or "step failed", replans=replans)
                return self.load_task(tid)

            replans += 1
            self._save(tid, status="replanning", results=results, replans=replans,
                       step_index=index, last_error=last_error)
            new_plan = self.plan(goal, results)
            plan_steps = list(new_plan.get("steps") or [])[: self.max_steps]
            if not plan_steps:
                self._save(tid, status="failed", results=results,
                           error="replanner returned no steps")
                return self.load_task(tid)
            # Replanning is real: replace only the unexecuted suffix.
            self._save(tid, plan=plan_steps, step_index=index, results=results,
                       status="running", replans=replans)

        final_ok = self._final_verify(goal, results)
        self._save(tid, status="completed" if final_ok else "failed",
                   step_index=index, results=results, verified=final_ok,
                   error=None if final_ok else "final verification failed")
        return self.load_task(tid)

    def _final_verify(self, goal, results):
        try:
            return bool(self.verify(step={"purpose": "final goal verification", "verify": goal},
                                    result=results[-5:], task={"goal": goal, "results": results}))
        except Exception:
            return False
