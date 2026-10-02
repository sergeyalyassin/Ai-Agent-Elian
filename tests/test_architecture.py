import tempfile, time, unittest
from pathlib import Path
from core.context_manager import ContextManager
from core.evidence import capture
from core.recovery_engine import RecoveryEngine
from core.scheduler import Scheduler
from core.skill_system import SkillSystem

class ArchitectureTests(unittest.TestCase):
    def test_context_survives_restart(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/"context.json"
            a=ContextManager(p); a.get_or_create_context("chat-1"); a.link_task_to_context("task-1","chat-1")
            b=ContextManager(p); self.assertEqual(b.get_active_task("chat-1"),"task-1")
    def test_evidence_shape(self):
        e=capture("s1","read_file",{"path":"x"},"ok",started_at=time.time())
        self.assertEqual(e["step_id"],"s1"); self.assertEqual(e["status"],"success")
    def test_recovery_classification(self):
        r=RecoveryEngine(); self.assertEqual(r.classify(TimeoutError("x")),"transient"); self.assertEqual(r.classify(PermissionError("x")),"permanent")
    def test_scheduler_due(self):
        tasks=[{"id":"t","run_at":time.time()-1,"status":"queued"}]; seen=[]
        s=Scheduler(lambda:tasks,lambda tid:seen.append(tid)); s.run_due(); self.assertEqual(seen,["t"])
    def test_skill_validation(self):
        with tempfile.TemporaryDirectory() as d:
            s=SkillSystem(d); self.assertTrue(s.validate("def run():\n    return 1"))
            with self.assertRaises(ValueError): s.validate("eval('x')")

if __name__=="__main__": unittest.main()
