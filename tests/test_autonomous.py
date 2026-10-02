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
                self.assertEqual(task["status"], "running")
                autonomous.update_task(tid, status="completed")
                self.assertEqual(autonomous.get_task(tid)["status"], "completed")
            finally:
                autonomous.STATE_FILE = old_state

    def test_shell_blocks_dangerous_command(self):
        with self.assertRaises(PermissionError):
            autonomous.shell("rm -rf /")

if __name__ == "__main__":
    unittest.main()
