from __future__ import annotations
class ToolRouter:
    def __init__(self,registry): self.registry=registry
    def capabilities(self):
        return [{"name":n,"description":self.registry.get(n).description,"risk":self.registry.get(n).risk,"idempotent":self.registry.get(n).idempotent} for n in self.registry.names()]
    def select_tool(self,step):
        name=step.get("tool")
        if name not in self.registry.names(): raise ValueError("Unknown tool: "+str(name))
        return self.registry.get(name)
