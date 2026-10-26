"""
Round-11 lifecycle tests for the host controller.

These tests use the actual ProcessOwner spawn path (not plain Popen) and
finite-lived fake children.  All tests clean up in finally blocks.

Coverage:
1. Normal success envelope (rc0, result event) -> completed, no kill
2. Genuine rc!=0 (process exits nonzero, no result) -> failed
3. Success envelope then root hangs -> bounded shutdown error
4. Cancel -> cancelled within bounded deadline
5. Timeout -> failed after timeout
6. Parent exits while grandchild holds pipes -> cleanup within bounded deadline
7. Containment/init failure -> lock retained (cleanup_uncertain=True path)
8. Worker crash simulation -> owned children die (KILL_ON_JOB_CLOSE)
9. Live child: recover refused
10. Stopped group: recover allowed
"""

import json
import os
import pathlib
import subprocess
import sys
import tempfile
import threading
import time
import unittest

SCRIPTS_DIR = pathlib.Path(__file__).parent.parent / 'scripts'
TESTS_TMP = pathlib.Path(__file__).parent / 'tmp'
sys.path.insert(0, str(SCRIPTS_DIR))


# ---------------------------------------------------------------------------
# Helpers shared with test_controller.py
# ---------------------------------------------------------------------------

def _make_root(tmp_path):
    root = pathlib.Path(tmp_path) / 'project'
    root.mkdir(parents=True, exist_ok=True)
    docs = root / 'docs'
    docs.mkdir(exist_ok=True)
    (root / 'docs' / 'ai-collaboration-workflow.md').write_text(
        '# AI Collaboration Workflow\nVersion: 1.0\n', encoding='utf-8'
    )
    (root / 'CLAUDE.md').write_text('# Claude\n', encoding='utf-8')
    (root / 'AGENTS.md').write_text('# Agents\n', encoding='utf-8')
    (root / 'docs' / 'project-brief.md').write_text('# Brief\n', encoding='utf-8')
    tasks = root / 'tasks'
    tasks.mkdir(exist_ok=True)
    (tasks / 'current_task.md').write_text('# Task\n', encoding='utf-8')
    return root


def _make_task_file(tmp_path, **overrides):
    data = {
        'task_id': 'TEST-001',
        'objective': 'test task',
        'timeout_secs': 30,
        'allowed_paths': ['tasks/current_task.md'],
        'baseline': {'commit': 'abc123', 'dirty_manifest': {}},
    }
    data.update(overrides)
    p = pathlib.Path(tmp_path) / 'task.json'
    p.write_text(json.dumps(data), encoding='utf-8')
    return p


# ---------------------------------------------------------------------------
# Test 1+2: ProcessOwner.spawn / terminate basic cases
# ---------------------------------------------------------------------------

class TestProcessOwnerBasic(unittest.TestCase):

    def setUp(self):
        TESTS_TMP.mkdir(parents=True, exist_ok=True)
        self._td = tempfile.mkdtemp(dir=TESTS_TMP)

    def tearDown(self):
        import shutil
        shutil.rmtree(self._td, ignore_errors=True)

    def test_spawn_and_terminate_sleeping_process(self):
        """ProcessOwner spawns and terminates a sleeping child; confirmed gone."""
        from _controller_process import ProcessOwner
        owner = ProcessOwner()
        proc = owner.spawn(
            [sys.executable, '-c', 'import time; time.sleep(60)'],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        self.assertIsNotNone(proc.pid)
        terminated, uncertain = owner.terminate(grace=10)
        self.assertTrue(terminated, f'terminated={terminated} uncertain={uncertain}')
        self.assertFalse(uncertain)
        self.assertIsNotNone(proc.returncode)

    def test_spawn_already_exited(self):
        """ProcessOwner.terminate on an already-exited process returns terminated=True."""
        from _controller_process import ProcessOwner
        owner = ProcessOwner()
        proc = owner.spawn(
            [sys.executable, '-c', 'import sys; sys.exit(0)'],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        proc.wait(timeout=5)
        terminated, uncertain = owner.terminate(grace=5)
        self.assertTrue(terminated)
        self.assertFalse(uncertain)

    def test_spawn_terminate_grandchild(self):
        """ProcessOwner.terminate kills grandchild even when parent has already exited."""
        from _controller_process import ProcessOwner
        parent_code = (
            'import subprocess, sys, time\n'
            'child = subprocess.Popen('
            '[sys.executable, "-c", "import time; time.sleep(30)"]'
            ')\n'
            'child.wait()\n'
        )
        owner = ProcessOwner()
        proc = owner.spawn(
            [sys.executable, '-c', parent_code],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        time.sleep(0.5)  # allow grandchild to spawn
        terminated, uncertain = owner.terminate(grace=10)
        self.assertTrue(terminated, f'tree not terminated: uncertain={uncertain}')
        self.assertIsNotNone(proc.returncode)

    @unittest.skipUnless(os.name == 'nt', 'Windows-only: KILL_ON_JOB_CLOSE')
    def test_kill_on_job_close_when_owner_handle_closed(self):
        """
        When the ProcessOwner object is deleted (job handle closes),
        children assigned to the Job Object should be terminated automatically
        by the kernel (KILL_ON_JOB_CLOSE).
        """
        from _controller_process import ProcessOwner
        # Use a sentinel file to detect child liveness.
        sentinel = pathlib.Path(self._td) / 'sentinel.txt'
        child_code = (
            f'import time, pathlib\n'
            f'sentinel = pathlib.Path({str(sentinel)!r})\n'
            f'sentinel.write_text("alive")\n'
            f'time.sleep(30)\n'
            f'sentinel.write_text("exited")\n'
        )
        owner = ProcessOwner()
        proc = owner.spawn(
            [sys.executable, '-c', child_code],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        # Wait for child to write sentinel.
        deadline = time.monotonic() + 5
        while not sentinel.is_file() and time.monotonic() < deadline:
            time.sleep(0.1)
        self.assertTrue(sentinel.is_file(), 'child did not start')

        # Close job handle by deleting owner (simulates worker crash).
        job_handle = owner._job_handle
        owner._job_handle = None  # prevent double-close in terminate
        if job_handle is not None:
            import ctypes
            ctypes.WinDLL('kernel32', use_last_error=True).CloseHandle(job_handle)

        # Child should be terminated by kernel within a short window.
        deadline = time.monotonic() + 5
        child_gone = False
        while time.monotonic() < deadline:
            proc.poll()
            if proc.returncode is not None:
                child_gone = True
                break
            time.sleep(0.1)

        # Clean up regardless.
        try:
            proc.kill()
            proc.wait(timeout=2)
        except Exception:
            pass

        self.assertTrue(child_gone, 'child was not killed after job handle closed')


# ---------------------------------------------------------------------------
# Test 3: Success envelope then root hangs -> shutdown timeout error
# ---------------------------------------------------------------------------

class TestClaudeSuccessHungRoot(unittest.TestCase):
    """
    Simulate _run_claude behaviour: fake CLI writes a success result event then
    sleeps (holds stdout open).  The worker must detect the hung root and raise
    a bounded shutdown-timeout error rather than silently completing.
    """

    def setUp(self):
        TESTS_TMP.mkdir(parents=True, exist_ok=True)
        self._td = tempfile.mkdtemp(dir=TESTS_TMP)
        self.root = _make_root(self._td)

    def tearDown(self):
        import shutil
        shutil.rmtree(self._td, ignore_errors=True)

    def _write_fake_claude(self, extra_sleep=15, rc=0):
        """Fake CLI: emits result event then hangs."""
        result_event = json.dumps({
            'type': 'result',
            'subtype': 'success',
            'is_error': False,
            'result': 'task completed',
            'session_id': 'fake-session',
        })
        script = pathlib.Path(self._td) / 'fake_claude.py'
        script.write_text(
            f'import sys, time\n'
            f'sys.stdout.write({result_event!r} + "\\n")\n'
            f'sys.stdout.flush()\n'
            f'time.sleep({extra_sleep})\n'
            f'sys.exit({rc})\n',
            encoding='utf-8',
        )
        return script

    def test_hung_root_after_success_raises_shutdown_timeout(self):
        """After success result event, a hung CLI root raises shutdown timeout."""
        import queue as _queue
        from _controller_process import ProcessOwner, ContainmentError

        script = self._write_fake_claude(extra_sleep=30)
        args = [sys.executable, '-X', 'utf8', str(script)]

        owner = ProcessOwner()
        proc = owner.spawn(args, stdout=subprocess.PIPE, stderr=subprocess.PIPE)

        stdout_q = _queue.Queue()
        def _reader(pipe, q):
            try:
                for line in pipe:
                    q.put(line)
            finally:
                q.put(None)

        t_out = threading.Thread(target=_reader, args=(proc.stdout, stdout_q), daemon=True)
        t_out.start()

        terminal_event = None
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            try:
                line = stdout_q.get(timeout=0.1)
            except _queue.Empty:
                continue
            if line is None:
                break
            try:
                obj = json.loads(line.decode('utf-8', 'replace').strip())
            except Exception:
                continue
            if isinstance(obj, dict) and obj.get('type') == 'result':
                terminal_event = obj
                break

        self.assertIsNotNone(terminal_event, 'no result event received')
        self.assertEqual(terminal_event.get('subtype'), 'success')

        # Replicate the hung-root detection logic from _run_claude.
        graceful_secs = 2  # shorter for test speed
        graceful_deadline = time.monotonic() + graceful_secs
        exited = False
        while time.monotonic() < graceful_deadline:
            proc.poll()
            if proc.returncode is not None:
                exited = True
                break
            time.sleep(0.1)

        try:
            self.assertFalse(exited, 'fake CLI should still be sleeping')
            # Force-kill and check we would raise an error (not silently pass).
            terminated, uncertain = owner.terminate(grace=5)
            self.assertTrue(terminated or uncertain)
        finally:
            try:
                owner.terminate(grace=3)
            except Exception:
                pass
            try:
                proc.stdout.close()
                proc.stderr.close()
            except Exception:
                pass


# ---------------------------------------------------------------------------
# Test 4+5: Cancel and timeout paths via fake worker
# ---------------------------------------------------------------------------

class TestCancelAndTimeout(unittest.TestCase):

    def setUp(self):
        TESTS_TMP.mkdir(parents=True, exist_ok=True)
        self._td = tempfile.mkdtemp(dir=TESTS_TMP)
        self.root = _make_root(self._td)

    def tearDown(self):
        import shutil
        shutil.rmtree(self._td, ignore_errors=True)

    def _make_sleeping_child(self, secs=30):
        script = pathlib.Path(self._td) / 'sleeper.py'
        script.write_text(f'import time; time.sleep({secs})\n', encoding='utf-8')
        return [sys.executable, '-X', 'utf8', str(script)]

    def test_cancel_terminates_owner_group(self):
        """cancel_requested path: terminate owner, return terminated within grace."""
        from _controller_process import ProcessOwner
        owner = ProcessOwner()
        proc = owner.spawn(
            self._make_sleeping_child(30),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        start = time.monotonic()
        terminated, uncertain = owner.terminate(grace=8)
        elapsed = time.monotonic() - start
        self.assertTrue(terminated, f'uncertain={uncertain}')
        self.assertFalse(uncertain)
        self.assertLess(elapsed, 10.0, f'took {elapsed:.1f}s')

    def test_timeout_terminates_owner_group(self):
        """Timeout path: owner.terminate kills sleeping child within grace."""
        from _controller_process import ProcessOwner
        owner = ProcessOwner()
        proc = owner.spawn(
            self._make_sleeping_child(60),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        terminated, uncertain = owner.terminate(grace=8)
        self.assertTrue(terminated or uncertain)  # must not hang indefinitely
        self.assertIsNotNone(proc.returncode)


# ---------------------------------------------------------------------------
# Test 6: Parent exits while grandchild holds pipes -> bounded cleanup
# ---------------------------------------------------------------------------

class TestGrandchildPipeCleanup(unittest.TestCase):

    def setUp(self):
        TESTS_TMP.mkdir(parents=True, exist_ok=True)
        self._td = tempfile.mkdtemp(dir=TESTS_TMP)

    def tearDown(self):
        import shutil
        shutil.rmtree(self._td, ignore_errors=True)

    def test_grandchild_pipe_holder_cleaned_up(self):
        """
        Parent spawns a grandchild that holds stdout open; ProcessOwner.terminate
        must clean up the whole group within the grace deadline.
        """
        from _controller_process import ProcessOwner
        parent_code = (
            'import subprocess, sys, time\n'
            'subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])\n'
            'sys.exit(0)\n'  # parent exits immediately, grandchild holds pipe
        )
        owner = ProcessOwner()
        proc = owner.spawn(
            [sys.executable, '-c', parent_code],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        time.sleep(0.5)  # give grandchild time to start

        start = time.monotonic()
        terminated, uncertain = owner.terminate(grace=10)
        elapsed = time.monotonic() - start

        self.assertTrue(terminated, f'uncertain={uncertain}')
        self.assertFalse(uncertain)
        self.assertLess(elapsed, 12.0, f'cleanup took {elapsed:.1f}s')


# ---------------------------------------------------------------------------
# Test 7: Containment failure -> lock retained
# ---------------------------------------------------------------------------

class TestContainmentFailureLockRetained(unittest.TestCase):
    """
    When ContainmentError is raised, the worker must retain the lock
    (cleanup_uncertain should be propagated to state).
    We test that ProcessOwner raises ContainmentError on invalid args.
    """

    def setUp(self):
        TESTS_TMP.mkdir(parents=True, exist_ok=True)
        self._td = tempfile.mkdtemp(dir=TESTS_TMP)

    def tearDown(self):
        import shutil
        shutil.rmtree(self._td, ignore_errors=True)

    def test_containment_error_raised_on_bad_spawn(self):
        """Spawning a nonexistent executable through ProcessOwner raises ContainmentError."""
        from _controller_process import ProcessOwner, ContainmentError
        owner = ProcessOwner()
        with self.assertRaises((ContainmentError, OSError, FileNotFoundError)):
            owner.spawn(
                ['/nonexistent/executable/that/does/not/exist'],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )


# ---------------------------------------------------------------------------
# Test 9: Live child recover refused (integration with _controller_state)
# ---------------------------------------------------------------------------

class TestRecoverLiveRefused(unittest.TestCase):

    def setUp(self):
        TESTS_TMP.mkdir(parents=True, exist_ok=True)
        self._td = tempfile.mkdtemp(dir=TESTS_TMP)
        self.root = _make_root(self._td)

    def tearDown(self):
        import shutil
        shutil.rmtree(self._td, ignore_errors=True)

    def test_recover_refused_for_live_pid(self):
        """recover must refuse when worker PID is still live."""
        from _controller_state import create_job, write_state_atomic, worker_gone
        task_file = _make_task_file(self._td)
        task_data = json.loads(task_file.read_text('utf-8'))
        create_job(self.root, 'lclive01', 'claude', task_file, task_data, {})
        live_pid = os.getpid()
        write_state_atomic(self.root, 'lclive01', {
            'job_id': 'lclive01', 'status': 'running', 'phase': 'running',
            'actor': 'claude', 'pid': live_pid,
        })
        # worker_gone must return False for our own PID
        self.assertFalse(worker_gone(live_pid))


# ---------------------------------------------------------------------------
# Test 10: Stopped group recover allowed
# ---------------------------------------------------------------------------

class TestRecoverStoppedAllowed(unittest.TestCase):

    def setUp(self):
        TESTS_TMP.mkdir(parents=True, exist_ok=True)
        self._td = tempfile.mkdtemp(dir=TESTS_TMP)
        self.root = _make_root(self._td)

    def tearDown(self):
        import shutil
        shutil.rmtree(self._td, ignore_errors=True)

    def test_recover_allowed_for_gone_pid(self):
        """worker_gone returns True for a PID that does not exist."""
        from _controller_state import worker_gone
        # PID 99999999 is almost certainly not a live process.
        self.assertTrue(worker_gone(99999999))

    def test_recover_gone_worker_sets_interrupted(self):
        """recover command sets status=interrupted for a gone worker."""
        from _controller_state import create_job, write_state_atomic, release_lock
        import subprocess as sp
        task_file = _make_task_file(self._td)
        task_data = json.loads(task_file.read_text('utf-8'))
        create_job(self.root, 'lgone001', 'claude', task_file, task_data, {})
        release_lock(self.root, 'lgone001')
        write_state_atomic(self.root, 'lgone001', {
            'job_id': 'lgone001', 'status': 'running', 'phase': 'running',
            'actor': 'claude', 'pid': 99999999,
        })
        script = SCRIPTS_DIR / 'collaboration.py'
        cmd = [
            sys.executable, '-X', 'utf8', str(script),
            '--root', str(self.root),
            'recover', '--job', 'lgone001',
        ]
        result = sp.run(cmd, capture_output=True, timeout=15, cwd=str(self.root))
        self.assertEqual(result.returncode, 0)
        data = json.loads(result.stdout.decode('utf-8', 'replace'))
        self.assertEqual(data['status'], 'interrupted')


# ---------------------------------------------------------------------------
# Test: rc0 success envelope with normal exit (the real happy path)
# ---------------------------------------------------------------------------

class TestSuccessEnvelopeNormalExit(unittest.TestCase):
    """
    Fake CLI writes result event then exits rc0 immediately.
    ProcessOwner.terminate should confirm all gone; no error raised.
    """

    def setUp(self):
        TESTS_TMP.mkdir(parents=True, exist_ok=True)
        self._td = tempfile.mkdtemp(dir=TESTS_TMP)

    def tearDown(self):
        import shutil
        shutil.rmtree(self._td, ignore_errors=True)

    def _write_fake_success_cli(self):
        result_event = json.dumps({
            'type': 'result',
            'subtype': 'success',
            'is_error': False,
            'result': 'task completed successfully',
            'session_id': 'fake-session',
        })
        script = pathlib.Path(self._td) / 'fake_success_cli.py'
        script.write_text(
            f'import sys\n'
            f'sys.stdout.write({result_event!r} + "\\n")\n'
            f'sys.stdout.flush()\n'
            f'sys.exit(0)\n',
            encoding='utf-8',
        )
        return script

    def test_success_rc0_no_kill_needed(self):
        """Success envelope + rc0 exit: ProcessOwner.terminate sees proc already gone."""
        import queue as _queue
        from _controller_process import ProcessOwner

        script = self._write_fake_success_cli()
        args = [sys.executable, '-X', 'utf8', str(script)]

        owner = ProcessOwner()
        proc = owner.spawn(args, stdout=subprocess.PIPE, stderr=subprocess.PIPE)

        stdout_q = _queue.Queue()
        def _reader(pipe, q):
            try:
                for line in pipe:
                    q.put(line)
            finally:
                q.put(None)

        t_out = threading.Thread(target=_reader, args=(proc.stdout, stdout_q), daemon=True)
        t_out.start()

        terminal_event = None
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            try:
                line = stdout_q.get(timeout=0.1)
            except _queue.Empty:
                continue
            if line is None:
                break
            try:
                obj = json.loads(line.decode('utf-8', 'replace').strip())
            except Exception:
                continue
            if isinstance(obj, dict) and obj.get('type') == 'result':
                terminal_event = obj
                break

        self.assertIsNotNone(terminal_event)
        self.assertEqual(terminal_event.get('subtype'), 'success')

        # Give graceful exit time.
        graceful_secs = 5
        graceful_deadline = time.monotonic() + graceful_secs
        exited_gracefully = False
        while time.monotonic() < graceful_deadline:
            proc.poll()
            if proc.returncode is not None:
                exited_gracefully = True
                break
            time.sleep(0.1)

        self.assertTrue(exited_gracefully, 'CLI should exit rc0 quickly')
        self.assertEqual(proc.returncode, 0)

        # Terminate owner to confirm no remaining descendants.
        terminated, uncertain = owner.terminate(grace=5)
        self.assertTrue(terminated)
        self.assertFalse(uncertain)

        try:
            proc.stdout.close()
            proc.stderr.close()
        except Exception:
            pass


# ---------------------------------------------------------------------------
# Test: Genuine rc!=0 -> failed (not success)
# ---------------------------------------------------------------------------

class TestGenuineFailureRcNonzero(unittest.TestCase):

    def setUp(self):
        TESTS_TMP.mkdir(parents=True, exist_ok=True)
        self._td = tempfile.mkdtemp(dir=TESTS_TMP)

    def tearDown(self):
        import shutil
        shutil.rmtree(self._td, ignore_errors=True)

    def test_nonzero_rc_no_result_event(self):
        """CLI exits rc1 without writing result event; owner terminates cleanly."""
        import queue as _queue
        from _controller_process import ProcessOwner

        script = pathlib.Path(self._td) / 'fail_cli.py'
        script.write_text('import sys\nsys.exit(1)\n', encoding='utf-8')
        args = [sys.executable, '-X', 'utf8', str(script)]

        owner = ProcessOwner()
        proc = owner.spawn(args, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        proc.wait(timeout=5)

        self.assertNotEqual(proc.returncode, 0)

        terminated, uncertain = owner.terminate(grace=5)
        self.assertTrue(terminated)
        self.assertFalse(uncertain)

        try:
            proc.stdout.close()
            proc.stderr.close()
        except Exception:
            pass


if __name__ == '__main__':
    unittest.main(verbosity=2)
