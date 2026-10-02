import tempfile
import unittest
from pathlib import Path
from core.memory import PersistentMemory
from core.agent_loop import AgentLoop

class MemoryTests(unittest.TestCase):
    def test_persistent_search_survives_restart(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "memory.sqlite3"
            m = PersistentMemory(p)
            m.add("lesson", "browser login requires waiting for navigation", ["browser", "login"])
            m2 = PersistentMemory(p)
            rows = m2.search("browser login")
            self.assertTrue(rows)
            self.assertIn("waiting", rows[0]["text"])

    def test_non_idempotent_action_is_not_retried(self):
        calls = []
        class R:
            names = lambda self: ["danger"]
            def get(self, name):
                class S: idempotent=False
                return S()
            def execute(self, name, inputs):
                calls.append(1)
                raise RuntimeError("ambiguous timeout")
        state={"t":{"goal":"x","status":"queued","results":[],"plan":[],"step_index":0,"replans":0}}
        loop=AgentLoop(
            registry=R(),
            load_task=lambda _: state["t"],
            update_task=lambda _, **kw: state["t"].update(kw) or state["t"],
            plan=lambda *_: {"steps":[{"tool":"danger","input":{},"purpose":"x","verify":"nonempty"}]},
            verify=lambda **_: False,
            max_steps=2,max_replans=0,max_retries=5
        )
        result=loop.run("t","x")
        self.assertEqual(len(calls), 1)
        self.assertEqual(result["status"], "failed")

if __name__ == "__main__":
    unittest.main()
