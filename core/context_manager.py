from __future__ import annotations
import json, threading, time, uuid
from pathlib import Path
from typing import Any

class ContextManager:
    """Durable conversation/task context mapping. Context is application state, not model memory."""
    def __init__(self, path: str | Path):
        self.path=Path(path); self.path.parent.mkdir(parents=True, exist_ok=True); self._lock=threading.RLock()
    def _load(self):
        try:
            d=json.loads(self.path.read_text(encoding="utf-8"))
            return d if isinstance(d,dict) else {"version":1,"contexts":{},"tasks":{}}
        except Exception:
            return {"version":1,"contexts":{},"tasks":{}}
    def _save(self,d):
        tmp=self.path.with_suffix(self.path.suffix+".tmp")
        tmp.write_text(json.dumps(d,ensure_ascii=False,indent=2),encoding="utf-8"); tmp.replace(self.path)
    def get_or_create_context(self, conversation_id: str) -> dict[str,Any]:
        with self._lock:
            d=self._load(); key=str(conversation_id)
            ctx=d["contexts"].setdefault(key,{"id":key,"created_at":time.time(),"active_task_id":None,"message_ids":[]})
            self._save(d); return ctx
    def link_task_to_context(self, task_id: str, conversation_id: str):
        with self._lock:
            d=self._load(); key=str(conversation_id)
            d["contexts"].setdefault(key,{"id":key,"created_at":time.time(),"active_task_id":None,"message_ids":[]})["active_task_id"]=str(task_id)
            d["tasks"][str(task_id)]=key; self._save(d)
    def get_active_task(self, conversation_id: str):
        return self.get_or_create_context(conversation_id).get("active_task_id")
    def add_message(self, conversation_id: str, message_id: str | None = None):
        with self._lock:
            d=self._load(); ctx=d["contexts"].setdefault(str(conversation_id),{"id":str(conversation_id),"created_at":time.time(),"active_task_id":None,"message_ids":[]})
            if message_id: ctx["message_ids"]=(ctx.get("message_ids") or [])[-199:]+[str(message_id)]
            self._save(d)
    def clear_active(self, conversation_id: str, task_id: str | None = None):
        with self._lock:
            d=self._load(); ctx=d["contexts"].get(str(conversation_id))
            if ctx and (task_id is None or ctx.get("active_task_id")==str(task_id)): ctx["active_task_id"]=None
            self._save(d)
