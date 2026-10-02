from __future__ import annotations
from core.memory import PersistentMemory

class MemorySystem:
    """Single facade over durable memory; callers can target memory classes without knowing storage."""
    TYPES={"short_term","long_term","episodic","semantic","procedural","lesson","conversation","error"}
    def __init__(self, store: PersistentMemory): self.store=store
    def store_information(self,text,memory_type="long_term",tags=None,importance=.5,source="agent"):
        kind=memory_type if memory_type in self.TYPES else "long_term"
        return self.store.add(kind,text,tags,importance,source)
    def retrieve_information(self,query,limit=8,memory_type=None):
        rows=self.store.search(query,limit=max(limit*2,limit))
        return [r for r in rows if memory_type is None or r.get("kind")==memory_type][:limit]
    def recent(self,limit=20): return self.store.recent(limit)
