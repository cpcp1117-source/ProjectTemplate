"""Codex independent acceptance regressions; actual descendant holds pipes."""
import pathlib
import os
import subprocess
import sys
import tempfile
import time
import unittest
import json
from unittest import mock

TESTS_TMP = pathlib.Path(__file__).resolve().parent / 'tmp'
TESTS_TMP.mkdir(parents=True, exist_ok=True)
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / 'scripts'))

@unittest.skipUnless(os.name == 'nt', 'Windows Job Object descendant acceptance')
class RealDescendantCleanup(unittest.TestCase):
    def test_parent_exit_with_grandchild_holding_pipes(self):
        from _appserver_adapter import AppServerAdapter
        with tempfile.TemporaryDirectory(dir=str(TESTS_TMP)) as td:
            server = pathlib.Path(td) / 'fake_server.py'
            server.write_text('''import json, sys, subprocess, time
def recv():
    return json.loads(sys.stdin.readline())
def send(obj):
    print(json.dumps(obj), flush=True)
r=recv(); send({'id':r['id'],'result':{}})
recv()
r=recv(); send({'id':r['id'],'result':{'thread':{'id':'review-thread'}}})
r=recv(); send({'id':r['id'],'result':{'turn':{'id':'review-turn','status':'inProgress','items':[]}}})
subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(8)'])
send({'method':'turn/completed','params':{'threadId':'review-thread','turn':{'id':'review-turn','status':'completed','items':[{'type':'agentMessage','id':'answer','text':'OK'}]}}})
''', encoding='utf-8')
            started = time.monotonic()
            with AppServerAdapter([sys.executable, str(server)], cwd=td, timeout_secs=12) as adapter:
                thread = adapter.start_thread()
                text, status = adapter.run_turn(thread, 'test')
            elapsed = time.monotonic() - started
            self.assertEqual(status, 'completed')
            self.assertIn('OK', text)
            self.assertLess(elapsed, 4.0, f'parent exited but descendant held pipes for {elapsed:.2f}s')

class CleanupFailureSafety(unittest.TestCase):
    def test_cleanup_uncertainty_keeps_lock_on_failed_cli(self):
        import _controller_process as cp
        import _controller_worker as cw
        import _controller_state as state
        import collaboration as cli
        import _project
        real_owner_class = cp.ProcessOwner
        owners = []
        class UncertainOwner:
            def __init__(self):
                self.real = real_owner_class()
                owners.append(self.real)
            def spawn(self, args, **kwargs):
                return self.real.spawn(args, **kwargs)
            def terminate(self, grace=8):
                self.real.terminate(grace=grace)
                return False, True
        with tempfile.TemporaryDirectory(dir=str(TESTS_TMP)) as td:
            root = pathlib.Path(td)
            script = root / 'fake_cli.py'
            script.write_text('import json,sys; print(json.dumps({"type":"result","subtype":"error","is_error":True,"result":"fake failure","session_id":"fake-session"}),flush=True);sys.exit(1)', encoding='utf-8')
            task = {'task_id':'TEMPLATE-001','objective':'test fail closed','prompt':'no live call','allowed_paths':[],'timeout_secs':3}
            task_file = root / 'task.json'
            task_file.write_text(json.dumps(task), encoding='utf-8')
            job = 'review-cleanup-failure'
            state.create_job(root, job, 'claude', task_file, task, {})
            cli._save_task_snapshot(root, job, task, task_file, {})
            try:
                with mock.patch.object(cp, 'ProcessOwner', UncertainOwner), mock.patch.object(cw, 'ProcessOwner', UncertainOwner, create=True), mock.patch.object(_project, 'load_config', return_value={'overrides':{'claude':sys.executable}}), mock.patch.object(cw, 'build_args', return_value=[sys.executable,str(script)]), mock.patch.object(sys, 'argv', ['worker',str(root),job]):
                    cw.main()
                saved = state.read_state(root, job)
                self.assertTrue(saved.get('cleanup_required'), saved)
                self.assertTrue((root / state.ACTIVE_LOCK).exists(), 'uncertain cleanup released one-writer lock')
            finally:
                for owner in owners:
                    owner.terminate(grace=3)

if __name__ == '__main__':
    unittest.main(verbosity=2)
