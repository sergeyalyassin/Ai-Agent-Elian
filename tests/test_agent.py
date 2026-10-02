import importlib.util
import os
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("agent", ROOT / "agent.py")
agent = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(agent)


class AgentTests(unittest.TestCase):
    def test_memory_summary_has_last_updated(self):
        with tempfile.TemporaryDirectory() as directory:
            original = agent.MEMORY_FILE
            agent.MEMORY_FILE = Path(directory) / "memory.json"
            try:
                summary = agent.Memory().summary()
            finally:
                agent.MEMORY_FILE = original

        self.assertIn("آخر تحديث", summary)

    def test_private_and_malformed_urls_are_rejected(self):
        self.assertFalse(agent.safe_url("http://127.0.0.1/admin"))
        self.assertFalse(agent.safe_url("http://localhost/admin"))
        self.assertFalse(agent.safe_url("file:///etc/passwd"))
        self.assertFalse(agent.safe_url("https://user:pass@example.com"))

    def test_help_and_crossref_are_registered(self):
        help_text = agent.process("help", agent.Memory())
        self.assertIn("/crossref", help_text)

    def test_command_mode_writes_memory_without_telegram(self):
        with tempfile.TemporaryDirectory() as directory:
            original_memory = agent.MEMORY_FILE
            original_root = agent.ROOT
            original_chat = agent.CHAT_ID
            old_task = os.environ.get("TASK")
            agent.ROOT = Path(directory)
            agent.MEMORY_FILE = Path(directory) / "memory.json"
            agent.CHAT_ID = ""
            os.environ["TASK"] = "ping"
            try:
                agent.command()
                self.assertTrue(agent.MEMORY_FILE.exists())
            finally:
                agent.ROOT = original_root
                agent.MEMORY_FILE = original_memory
                agent.CHAT_ID = original_chat
                if old_task is None:
                    os.environ.pop("TASK", None)
                else:
                    os.environ["TASK"] = old_task

    def test_notes_can_be_saved_and_recalled(self):
        with tempfile.TemporaryDirectory() as directory:
            original = agent.MEMORY_FILE
            agent.MEMORY_FILE = Path(directory) / "memory.json"
            try:
                memory = agent.Memory()
                memory.remember(
                    "note",
                    "خطة تعلم Python للمبتدئ",
                    ["python", "learning"],
                )
                matches = memory.recall("python")
            finally:
                agent.MEMORY_FILE = original

        self.assertEqual(len(matches), 1)
        self.assertIn("Python", matches[0]["text"])

    def test_sensitive_request_requires_explicit_resolution(self):
        with tempfile.TemporaryDirectory() as directory:
            original = agent.ACTIONS_FILE
            agent.ACTIONS_FILE = Path(directory) / "pending_actions.json"
            try:
                action = agent.create_pending_action(
                    "email",
                    "إرسال رسالة",
                )
                self.assertEqual(action["status"], "pending")
                resolved = agent.resolve_action(
                    action["id"],
                    approved=False,
                )
            finally:
                agent.ACTIONS_FILE = original

        self.assertEqual(resolved["status"], "rejected")


if __name__ == "__main__":
    unittest.main()
