from __future__ import annotations
import ast, hashlib, json
from pathlib import Path

class SkillSystem:
    """Validated procedural skills. Learning may propose skills; execution remains explicit/policy-gated."""
    def __init__(self, root: str|Path):
        self.root=Path(root); self.root.mkdir(parents=True,exist_ok=True)
    def list(self):
        return sorted(p.stem for p in self.root.glob("*.py") if p.name!="__init__.py")
    def validate(self, source):
        tree=ast.parse(source)
        banned={"__import__","eval","exec","compile","open","system","popen"}
        for n in ast.walk(tree):
            if isinstance(n,ast.Call) and isinstance(n.func,ast.Name) and n.func.id in banned: raise ValueError("unsafe skill API: "+n.func.id)
        return True
    def propose(self,name,source,metadata=None):
        self.validate(source); safe="".join(c if c.isalnum() or c in "_-" else "_" for c in str(name))[:80]
        path=self.root/(safe+".py"); path.write_text(source,encoding="utf-8")
        return {"name":safe,"path":str(path),"sha256":hashlib.sha256(source.encode()).hexdigest(),"metadata":metadata or {}}
