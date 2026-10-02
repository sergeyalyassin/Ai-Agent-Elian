from __future__ import annotations
import json, time
from typing import Callable
from core.policy import ApprovalRequired

class AgentLoop:
    """Checkpointed plan/execute/verify/recover loop with approval pauses."""
    def __init__(self, *, registry, load_task: Callable[[str], dict | None], update_task: Callable[..., dict | None], plan: Callable[..., dict], verify: Callable[..., bool], decide_next: Callable[..., dict | None] | None = None, max_steps=20, max_replans=3, max_retries=2):
        self.registry=registry; self.load_task=load_task; self.update_task=update_task; self.plan=plan; self.verify=verify; self.decide_next=decide_next; self.max_steps=max_steps; self.max_replans=max_replans; self.max_retries=max_retries
    def _save(self, tid, **changes): return self.update_task(tid, **changes)
    def _result(self,index,step,ok,value=None,error=None,attempts=1):
        return {"step":index,"tool":step.get("tool"),"purpose":step.get("purpose",""),"ok":bool(ok),"attempts":attempts,"at":time.time(),**({"result":value} if value is not None else {}),**({"error":str(error)} if error is not None else {})}
    def run(self,tid,goal,*,resume=False):
        task=self.load_task(tid)
        if not task: raise ValueError("task not found")
        results=list(task.get("results") or []); replans=int(task.get("replans",0)); plan_steps=list(task.get("plan") or []) if resume else []; index=int(task.get("step_index",0)) if resume else 0
        if not plan_steps:
            self._save(tid,status="planning")
            plan_steps=list((self.plan(goal,results) or {}).get("steps") or [])[:self.max_steps]
            if not plan_steps: raise ValueError("planner returned no executable steps")
            index=0; self._save(tid,plan=plan_steps,step_index=0,results=results,replans=replans,status="running",started_at=time.time())
        while index<len(plan_steps) and len(results)<self.max_steps:
            task=self.load_task(tid) or task
            if task.get("status") in {"cancelled","awaiting_approval"}: return task
            step=plan_steps[index]; inputs=dict(step.get("input") or {}); previous=json.dumps(results[-6:],ensure_ascii=False)[:16000]
            spec = self.registry.get(step.get("tool"))
            for k,v in list(inputs.items()):
                if isinstance(v,str): inputs[k]=v.replace("{{PREVIOUS_RESULTS}}",previous)
            attempts=0; last_error=None; value=None; ok=False
            while attempts<=self.max_retries:
                attempts+=1
                try:
                    self.registry.get(step.get("tool")); value=self.registry.execute(step.get("tool"),inputs); ok=self.verify(step=step,result=value,task=self.load_task(tid))
                    if ok: break
                    last_error="verification failed"
                except ApprovalRequired as exc:
                    self._save(tid,status="awaiting_approval",pending_approval={"tool":exc.tool,"reason":exc.reason,"step":index,"input":inputs,"created_at":time.time()},step_index=index,current_tool=exc.tool)
                    return self.load_task(tid)
                except Exception as exc:
                    last_error=f"{type(exc).__name__}: {exc}"; value=None
                # Never blindly repeat a non-idempotent side effect: a timeout or
                # failed verifier can mean the external action actually succeeded.
                if not spec.idempotent:
                    break
                if attempts<=self.max_retries:
                    self._save(tid,status="recovering",last_error=last_error,current_tool=step.get("tool"),attempts=attempts); time.sleep(min(2**(attempts-1),8))
            item=self._result(index,step,ok,value,last_error if not ok else None,attempts); results.append(item); self._save(tid,results=results,last_step=item,pending_approval=None)
            if ok:
                index+=1
                # Adaptive control: after each observation the agent may replace the
                # next planned action instead of blindly following a stale plan.
                if self.decide_next:
                    try:
                        candidate = self.decide_next(goal, results)
                        if isinstance(candidate, dict) and candidate.get("tool") in self.registry.names():
                            plan_steps.insert(index, candidate)
                    except Exception:
                        pass
                self._save(tid,status="running",step_index=index,last_error=None); continue
            if replans>=self.max_replans:
                self._save(tid,status="failed",step_index=index,error=last_error or "step failed",replans=replans,finished_at=time.time()); return self.load_task(tid)
            replans+=1; self._save(tid,status="replanning",replans=replans,last_error=last_error); new_plan=self.plan(goal,results); plan_steps=list((new_plan or {}).get("steps") or [])[:self.max_steps]
            if not plan_steps:
                self._save(tid,status="failed",error="replanner returned no steps",finished_at=time.time()); return self.load_task(tid)
            index=0; self._save(tid,plan=plan_steps,step_index=0,status="running",replans=replans)
        final_ok=self._final_verify(goal,results); self._save(tid,status="completed" if final_ok else "failed",step_index=index,verified=final_ok,finished_at=time.time(),error=None if final_ok else "final verification failed"); return self.load_task(tid)
    def _final_verify(self,goal,results):
        try: return bool(self.verify(step={"purpose":"final goal verification","verify":goal},result=results[-8:],task={"goal":goal,"results":results}))
        except Exception: return False
