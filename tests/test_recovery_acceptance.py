"""Codex independent acceptance regressions for recovery and persisted identities."""
import json
import os
import pathlib
import subprocess
import sys
import tempfile
import types
import unittest
from unittest import mock

SCRIPTS = pathlib.Path(__file__).resolve().parent.parent / 'scripts'
TESTS_TMP = pathlib.Path(__file__).resolve().parent / 'tmp'
TESTS_TMP.mkdir(parents=True, exist_ok=True)
sys.path.insert(0, str(SCRIPTS))
import _controller_state as state
import _controller_worker as worker
import _controller_process as process
import collaboration as cli
import _project

@unittest.skipUnless(os.name == 'nt', 'Windows process creation identity acceptance')
class RecoveryIdentityAcceptance(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=str(TESTS_TMP))
        self.root = pathlib.Path(self.temp.name)
        self.job = 'identity-acceptance'
        self.task = {'task_id':'TEMPLATE-001','objective':'identity acceptance','prompt':'no live model call','allowed_paths':[],'timeout_secs':3}
        self.task_file = self.root/'task.json'
        self.task_file.write_text(json.dumps(self.task),encoding='utf-8')
    def tearDown(self):
        self.temp.cleanup()
    def create(self, **patch):
        state.create_job(self.root,self.job,'claude',self.task_file,self.task,{})
        cli._save_task_snapshot(self.root,self.job,self.task,self.task_file,{})
        current=state.read_state(self.root,self.job)
        current.update(patch)
        state.write_state_atomic(self.root,self.job,current)
    def recover(self):
        return subprocess.run([sys.executable,'-X','utf8',str(SCRIPTS/'collaboration.py'),'recover','--job',self.job,'--root',str(self.root)],capture_output=True,timeout=10)
    def test_reused_pid_does_not_block_or_kill_unrelated_process(self):
        self.create(status='failed',cleanup_required=True,pid=os.getpid(),worker_identity=f'{os.getpid()}:0',child_pid=None)
        p=self.recover()
        self.assertEqual(p.returncode,0,p.stderr.decode('utf-8','replace'))
        self.assertFalse((self.root/state.ACTIVE_LOCK).exists())
    def test_live_same_identity_refuses_recover(self):
        identity=state.get_process_creation_identity(os.getpid())
        self.assertTrue(identity)
        self.create(status='failed',cleanup_required=True,pid=os.getpid(),worker_identity=identity,child_pid=None)
        p=self.recover()
        self.assertNotEqual(p.returncode,0)
        self.assertTrue((self.root/state.ACTIVE_LOCK).exists())
    def test_pending_without_pid_refuses_recover(self):
        self.create(status='pending',pid=None,child_pid=None)
        p=self.recover()
        self.assertNotEqual(p.returncode,0)
        self.assertTrue((self.root/state.ACTIVE_LOCK).exists())
    def test_worker_and_child_identity_persisted_on_real_local_process(self):
        self.create()
        script=self.root/'local_success.py'
        script.write_text('import json;print(json.dumps({"type":"result","subtype":"success","is_error":False,"result":"LOCAL_OK","session_id":"fixture-session"}),flush=True)',encoding='utf-8')
        with mock.patch.object(_project,'load_config',return_value={'overrides':{'claude':sys.executable}}), mock.patch.object(worker,'build_args',return_value=[sys.executable,str(script)]), mock.patch.object(sys,'argv',['worker',str(self.root),self.job]):
            worker.main()
        saved=state.read_state(self.root,self.job)
        self.assertEqual(saved['status'],'completed',saved)
        self.assertEqual(saved.get('worker_identity'),state.get_process_creation_identity(os.getpid()),saved)
        self.assertTrue(str(saved.get('child_identity','')).startswith(str(saved['child_pid'])+':'),saved)
    def test_failed_launch_is_terminal_and_unlocks(self):
        args=types.SimpleNamespace(actor='claude',task_file=str(self.task_file))
        with mock.patch.object(cli,'_validate_environment'), mock.patch.object(cli,'_capture_baseline',return_value={}), mock.patch.object(cli,'_launch_detached',side_effect=OSError('fixture launch rejected')):
            with self.assertRaises(SystemExit):
                cli._cmd_start(self.root,args)
        jobs=list((self.root/'.collaboration/controller/jobs').glob('*/state.json'))
        self.assertEqual(len(jobs),1)
        saved=json.loads(jobs[0].read_text('utf-8'))
        self.assertEqual(saved['status'],'failed',saved)
        self.assertFalse(saved.get('cleanup_required',False),saved)
        self.assertFalse((self.root/state.ACTIVE_LOCK).exists())
    def test_uncertain_spawn_preserves_child_identity_for_recovery(self):
        self.create()
        err=process.ContainmentError('fixture uncertain child',cleanup_confirmed=False)
        err.child_pid=123
        err.child_identity='123:fixture'
        owner=mock.Mock()
        owner.spawn.side_effect=err
        with mock.patch.object(_project,'load_config',return_value={'overrides':{'claude':sys.executable}}), mock.patch.object(worker,'ProcessOwner',return_value=owner), mock.patch.object(sys,'argv',['worker',str(self.root),self.job]):
            worker.main()
        saved=state.read_state(self.root,self.job)
        self.assertTrue(saved.get('cleanup_required'),saved)
        self.assertEqual(saved.get('child_pid'),123,saved)
        self.assertEqual(saved.get('child_identity'),'123:fixture',saved)
        self.assertTrue((self.root/state.ACTIVE_LOCK).exists())
    def test_actual_containment_exception_preserves_spawned_pid(self):
        fake=mock.Mock(pid=123,_handle=123)
        fake.kill.side_effect=OSError('fixture kill unavailable')
        fake.wait.side_effect=subprocess.TimeoutExpired('fixture',1)
        with mock.patch.object(process,'_create_job_with_kill_on_close',return_value=123), mock.patch.object(process,'_assign_to_job'), mock.patch.object(process,'_resume_primary_thread',side_effect=OSError('fixture resume failed')), mock.patch.object(process.subprocess,'Popen',return_value=fake), mock.patch.object(process._kernel32,'CloseHandle',return_value=1):
            with self.assertRaises(process.ContainmentError) as cm:
                process.ProcessOwner().spawn(['fixture-only'])
        self.assertFalse(cm.exception.cleanup_confirmed)
        self.assertEqual(getattr(cm.exception,'child_pid',None),123,'real exception producer lost known child PID')
    def test_codex_child_identity_persisted_on_real_local_server(self):
        self.create(actor='codex')
        script=self.root/'local_server.py'
        script.write_text('''import json,sys
def read(): return json.loads(sys.stdin.readline())
def send(v): print(json.dumps(v),flush=True)
r=read();send({'id':r['id'],'result':{}})
read()
r=read();send({'id':r['id'],'result':{'thread':{'id':'identity-thread'}}})
r=read();send({'id':r['id'],'result':{'turn':{'id':'identity-turn','status':'inProgress','items':[]}}})
send({'method':'turn/completed','params':{'threadId':'identity-thread','turn':{'id':'identity-turn','status':'completed','items':[{'type':'agentMessage','id':'identity-answer','phase':'final_answer','text':'LOCAL_CODEX_OK'}]}}})
sys.stdin.read()
''',encoding='utf-8')
        import _cli_helper
        with mock.patch.object(_project,'load_config',return_value={'overrides':{'codex':sys.executable}}), mock.patch.object(_cli_helper,'build_args',return_value=[sys.executable,str(script)]), mock.patch.object(sys,'argv',['worker',str(self.root),self.job]):
            worker.main()
        saved=state.read_state(self.root,self.job)
        self.assertEqual(saved['status'],'completed',saved)
        self.assertTrue(saved.get('child_pid'),saved)
        self.assertTrue(str(saved.get('child_identity','')).startswith(str(saved['child_pid'])+':'),saved)
        self.assertFalse(saved.get('cleanup_required'),saved)
        self.assertFalse((self.root/state.ACTIVE_LOCK).exists(),'completed Codex job retained root lock')
    def test_uncertain_codex_spawn_preserves_identity_and_lock(self):
        self.create(actor='codex')
        err=process.ContainmentError('fixture codex uncertain child',cleanup_confirmed=False)
        err.child_pid=123
        err.child_identity='123:fixture'
        owner=mock.Mock()
        owner.spawn.side_effect=err
        import _cli_helper
        with mock.patch.object(_project,'load_config',return_value={'overrides':{'codex':sys.executable}}), mock.patch.object(process,'ProcessOwner',return_value=owner), mock.patch.object(_cli_helper,'build_args',return_value=[sys.executable,'fixture-only']), mock.patch.object(sys,'argv',['worker',str(self.root),self.job]):
            worker.main()
        saved=state.read_state(self.root,self.job)
        self.assertEqual(saved['status'],'failed',saved)
        self.assertTrue(saved.get('cleanup_required'),saved)
        self.assertEqual(saved.get('child_pid'),123,saved)
        self.assertEqual(saved.get('child_identity'),'123:fixture',saved)
        self.assertTrue((self.root/state.ACTIVE_LOCK).exists())
    def test_codex_protocol_completed_cleanup_uncertain_retains_lock(self):
        """Protocol status completed but adapter cleanup not confirmed: retain lock, mark error."""
        self.create(actor='codex')
        script=self.root/'local_server_uncertain.py'
        script.write_text('''import json,sys
def read(): return json.loads(sys.stdin.readline())
def send(v): print(json.dumps(v),flush=True)
r=read();send({'id':r['id'],'result':{}})
read()
r=read();send({'id':r['id'],'result':{'thread':{'id':'uncertain-thread'}}})
r=read();send({'id':r['id'],'result':{'turn':{'id':'uncertain-turn','status':'inProgress','items':[]}}})
send({'method':'turn/completed','params':{'threadId':'uncertain-thread','turn':{'id':'uncertain-turn','status':'completed','items':[{'type':'agentMessage','id':'ua','phase':'final_answer','text':'UNCERTAIN'}]}}})
sys.stdin.read()
''',encoding='utf-8')
        import _cli_helper
        import _appserver_adapter as asa
        original_close = asa.AppServerAdapter.close
        def _close_without_confirm(self_a):
            original_close(self_a)
            self_a.cleanup_confirmed = False
        with mock.patch.object(_project,'load_config',return_value={'overrides':{'codex':sys.executable}}), mock.patch.object(_cli_helper,'build_args',return_value=[sys.executable,str(script)]), mock.patch.object(asa.AppServerAdapter,'close',_close_without_confirm), mock.patch.object(sys,'argv',['worker',str(self.root),self.job]):
            worker.main()
        saved=state.read_state(self.root,self.job)
        self.assertTrue(saved.get('cleanup_required'),saved)
        self.assertTrue((self.root/state.ACTIVE_LOCK).exists(),'uncertain cleanup must retain lock')
    def test_recover_preserves_original_status_and_result(self):
        """Recovery from a completed-but-locked job preserves original status/result in history."""
        self.create(status='completed',cleanup_required=True,pid=os.getpid(),
                    worker_identity=f'{os.getpid()}:0',child_pid=None,
                    result_text='original result',error=None)
        p=self.recover()
        self.assertEqual(p.returncode,0,p.stderr.decode('utf-8','replace'))
        saved=state.read_state(self.root,self.job)
        self.assertEqual(saved['status'],'interrupted',saved)
        self.assertEqual(saved.get('recovered_from_status'),'completed',saved)
        self.assertEqual(saved.get('original_result_text'),'original result',saved)
        self.assertFalse(saved.get('cleanup_required'),saved)
        self.assertFalse((self.root/state.ACTIVE_LOCK).exists())

if __name__=='__main__':
    unittest.main(verbosity=2)
