import unittest
from core.policy import ApprovalRequired, ExecutionPolicy

class PolicyTests(unittest.TestCase):
    def test_low_risk_is_allowed(self):
        ExecutionPolicy().check("read_file", {"path":"README.md"}, "low")

    def test_shell_requires_approval(self):
        with self.assertRaises(ApprovalRequired):
            ExecutionPolicy().check("shell", {"command":"echo ok"}, "critical")

    def test_browser_read_actions_are_low_risk(self):
        p=ExecutionPolicy()
        p.check("browser", {"action":"snapshot"}, "high")
        with self.assertRaises(ApprovalRequired):
            p.check("browser", {"action":"click","selector":"#submit"}, "high")

    def test_github_read_is_low_but_write_requires_approval(self):
        p=ExecutionPolicy()
        p.check("github", {"method":"GET","url":"https://api.github.com/user"}, "high")
        with self.assertRaises(ApprovalRequired):
            p.check("github", {"method":"POST","url":"https://api.github.com/repos/x/y/issues","payload":{}}, "high")

    def test_full_auto_is_explicit(self):
        import os
        old=os.environ.get("AGENT_APPROVAL_MODE")
        try:
            os.environ["AGENT_APPROVAL_MODE"]="full-auto"
            ExecutionPolicy(full_access=True).check("shell", {"command":"echo ok"}, "critical")
        finally:
            if old is None: os.environ.pop("AGENT_APPROVAL_MODE",None)
            else: os.environ["AGENT_APPROVAL_MODE"]=old

if __name__ == "__main__":
    unittest.main()
