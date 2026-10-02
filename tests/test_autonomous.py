import importlib.util
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("agent", ROOT / "agent.py")
agent = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(agent)

SPEC2 = importlib.util.spec_from_file_location("autonomous_agent", ROOT / "autonomous_agent.py")
autonomous = importlib.util.module_from_spec(SPEC2)
SPEC2.loader.exec_module(autonomous)

class AutonomousTests(unittest.TestCase):
    def test_plan_json_parser(self):
        obj = autonomous.parse_json('{"steps":[{"tool":"list_files","input":{}}]}')
        self.assertEqual(obj["steps"][0]["tool"], "list_files")

    def test_workspace_path_boundary(self):
        with tempfile.TemporaryDirectory() as d:
            old = autonomous.ROOT
            autonomous.ROOT = Path(d)
            try:
                self.assertEqual(autonomous.safe_path("x.txt").parent, Path(d))
                with self.assertRaises(ValueError):
                    autonomous.safe_path("../outside.txt")
            finally:
                autonomous.ROOT = old

    def test_state_roundtrip(self):
        with tempfile.TemporaryDirectory() as d:
            old_state = autonomous.STATE_FILE
            autonomous.STATE_FILE = Path(d) / "task_state.json"
            try:
                tid = autonomous.new_task("اختبار مهمة مستقلة")
                task = autonomous.get_task(tid)
                self.assertEqual(task["status"], "queued")
                autonomous.update_task(tid, status="completed")
                self.assertEqual(autonomous.get_task(tid)["status"], "completed")
            finally:
                autonomous.STATE_FILE = old_state

    def test_shell_blocks_dangerous_command(self):
        with self.assertRaises(PermissionError):
            autonomous.shell("rm -rf /")

    def test_browser_tool_is_registered(self):
        self.assertIn("browser", autonomous.REGISTRY.names())

    def test_tool_registry_contains_core_tools(self):
        self.assertIn("shell", autonomous.REGISTRY.names())
        self.assertIn("github", autonomous.REGISTRY.names())
        self.assertIn("delete_file", autonomous.REGISTRY.names())

    def test_cancel_task(self):
        tid = autonomous.new_task("cancel test")
        self.assertEqual(autonomous.cancel_task(tid), f"تم إلغاء {tid}.")
        self.assertEqual(autonomous.get_task(tid)["status"], "cancelled")

    def test_agent_loop_checkpoints_and_replans(self):
        calls = []
        class FakeRegistry:
            def get(self, name): return object()
            def execute(self, name, inputs):
                calls.append((name, inputs))
                return {"returncode": 0, "value": len(calls)}
        state = {"t": {"goal": "test", "status": "queued", "results": [],
                       "plan": [], "step_index": 0, "replans": 0}}
        def load(_): return state["t"]
        def update(_, **changes): state["t"].update(changes); return state["t"]
        def plan(goal, results):
            return {"steps": [{"tool": "x", "input": {}, "purpose": "do", "verify": "returncode_zero"}]}
        loop = autonomous.AgentLoop(registry=FakeRegistry(), load_task=load,
                                    update_task=update, plan=plan,
                                    verify=lambda **kw: True, max_steps=3,
                                    max_replans=1, max_retries=0)
        result = loop.run("t", "test")
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["step_index"], 1)
        self.assertTrue(state["t"].get("verified"))
        
    def test_verify_returncode(self):
        self.assertTrue(autonomous.verify_step(
            {"verify": "returncode_zero", "purpose": "test"},
            {"returncode": 0},
            {"goal": "test"}
        ))
        self.assertFalse(autonomous.verify_step(
            {"verify": "returncode_zero", "purpose": "test"},
            {"returncode": 1},
            {"goal": "test"}
        ))

if __name__ == "__main__":
    unittest.main()
