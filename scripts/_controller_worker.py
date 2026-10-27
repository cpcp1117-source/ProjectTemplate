"""
Detached worker process for the host controller.

Invoked by collaboration.py start; runs as a background job.
Owns the child CLI process (Claude or Codex appserver), timeout, and cancellation.
Writes state transitions to .collaboration/controller/jobs/<JOB_ID>/state.json.
Captures stdout/stderr to stdout.log / stderr.log in the job directory.
Releases the root-level active-job lock on terminal state only when process-tree
cleanup is confirmed; retains lock if cleanup is uncertain (sets cleanup_required).

Allowed actors:
  claude  — invokes Claude CLI with stream-json output, parses result event.
  codex   — invokes Codex App Server via _appserver_adapter, collects agentMessage.

Entry point: python -m scripts._controller_worker <root> <job_id>
"""
import json
import os
import pathlib
import queue
import subprocess
import sys
import threading
import time

sys.path.insert(0, str(pathlib.Path(__file__).parent))
from _controller_state import (
    job_dir, read_state, write_state_atomic, release_lock,
    cancel_requested, TERMINAL_STATES
)
from _cli_helper import build_args, find_cli
from _controller_process import ProcessOwner, ContainmentError

# Grace period (seconds) for child process cleanup before force-kill.
_CLEANUP_GRACE = 8

# Graceful-exit window: after receiving result event, wait this long for the
# CLI to exit normally before force-killing.  Preserves genuine rc0 success.
_GRACEFUL_EXIT_SECS = 5


def main():
    if len(sys.argv) != 3:
        print('Usage: _controller_worker.py <root> <job_id>', file=sys.stderr)
        sys.exit(1)
    root = pathlib.Path(sys.argv[1]).resolve()
    job_id = sys.argv[2]

    try:
        state = read_state(root, job_id)
    except Exception as e:
        print(f'worker: cannot read state: {e}', file=sys.stderr)
        sys.exit(1)

    actor = state.get('actor', 'claude')
    task_file = pathlib.Path(state.get('task_file', ''))
    timeout_secs = None
    _d_early = job_dir(root, job_id)

    try:
        # Require immutable snapshot; no fallback to mutable original task file.
        snap_path = _d_early / 'task_snapshot.json'
        if not snap_path.is_file():
            raise ValueError(
                f'task snapshot missing for job {job_id}; '
                f'start a new job — snapshots are required for all controller jobs'
            )
        task_data = _verify_task_snapshot(snap_path)
        timeout_secs = task_data.get('timeout_secs', 300)
        if not isinstance(timeout_secs, (int, float)) or timeout_secs <= 0:
            timeout_secs = 300
    except Exception as e:
        _set_failed(root, job_id, f'task validation: {e}')
        release_lock(root, job_id)
        sys.exit(1)

    try:
        from _project import load_config
        config = load_config(root)
        if not isinstance(config, dict):
            raise ValueError('load_config returned non-dict')
    except Exception as e:
        _set_failed(root, job_id, f'config load failed: {e}')
        release_lock(root, job_id)
        sys.exit(1)

    overrides = config.get('overrides', {})
    d = job_dir(root, job_id)

    from _controller_state import get_process_creation_identity
    worker_identity = get_process_creation_identity(os.getpid())
    _update_state(root, job_id, {
        'status': 'running', 'phase': 'starting',
        'pid': os.getpid(), 'worker_identity': worker_identity,
        'child_pid': None,
    })

    # Outcome object written before any outer-finally so lock decision survives exceptions.
    _outcome = {'cleanup_uncertain': False}
    try:
        if actor == 'claude':
            uncertain = _run_claude(root, job_id, task_data, task_file, config, overrides,
                                    timeout_secs, d, _outcome)
            _outcome['cleanup_uncertain'] = uncertain
        elif actor == 'codex':
            _run_codex(root, job_id, task_data, config, overrides, timeout_secs, d, _outcome)
        else:
            raise ValueError(f'unknown actor: {actor!r}')
    except Exception as e:
        _set_failed(root, job_id, str(e))
    finally:
        # Fail-closed lock: persist cleanup_required BEFORE releasing lock.
        # _outcome['cleanup_uncertain'] is set eagerly inside each _run_* path
        # so a raised exception never prevents the flag from reaching state.
        if _outcome['cleanup_uncertain']:
            _update_state(root, job_id, {'cleanup_required': True})
        else:
            try:
                state = read_state(root, job_id)
            except Exception:
                # Never replace corrupt/unreadable state with {} then release.
                # Retain lock if state is unreadable.
                _outcome['cleanup_uncertain'] = True
                try:
                    _update_state(root, job_id, {'cleanup_required': True})
                except Exception:
                    pass
                state = None
            if state is not None and not state.get('cleanup_required'):
                release_lock(root, job_id)


def _update_state(root, job_id, patch):
    try:
        state = read_state(root, job_id)
    except Exception:
        state = {}
    state.update(patch)
    write_state_atomic(root, job_id, state)


def _set_failed(root, job_id, error):
    _update_state(root, job_id, {'status': 'failed', 'phase': 'failed', 'error': error})


def _run_claude(root, job_id, task_data, task_file, config, overrides,
                timeout_secs, d, _outcome):
    """
    Run Claude CLI.  Returns True if process-tree cleanup was uncertain.

    _outcome dict is written eagerly so cleanup_uncertain survives any exception
    raised before the return statement reaches main().

    Fix 1 — success-not-killed:
      After receiving the result event we give the CLI a bounded graceful-exit
      window (_GRACEFUL_EXIT_SECS) before force-killing.  If the CLI exits rc0
      within that window, no kill is needed and cleanup succeeds cleanly.
      If it does not exit, we force-kill and check rc.
      A hung root after success envelope = 'shutdown timeout' error (not silent pass).
      Genuine nonzero rc (exit without result, or post-result nonzero) = failed.

    Fix 2 — Job Object containment via ProcessOwner:
      Child is spawned through ProcessOwner.spawn() which:
        - Windows: CREATE_SUSPENDED, assigns to Job Object (KILL_ON_JOB_CLOSE),
          resumes primary thread.
        - POSIX: start_new_session=True.
      ProcessOwner.terminate() handles group cleanup with ActiveProcesses confirmation.
    """
    cli_path = find_cli('claude', overrides) or find_cli('claude', config.get('cli', {}))
    if not cli_path:
        raise ValueError('claude CLI not found')

    prompt = _build_claude_prompt(task_data)
    allowed_tools = 'Read,Write,Edit,Glob,Grep'

    user_args = [
        '-p', prompt,
        '--tools', allowed_tools,
        '--allowedTools', allowed_tools,
        '--output-format', 'stream-json',
        '--verbose',
    ]

    args = build_args(cli_path, user_args)

    stdout_log = d / 'stdout.log'
    stderr_log = d / 'stderr.log'

    _update_state(root, job_id, {'phase': 'running', 'last_tool': None})

    kwargs = dict(
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        cwd=str(root),
    )

    from _controller_state import get_process_creation_identity
    owner = ProcessOwner()
    # After child ownership begins, mark cleanup_required immediately so any
    # exception path will retain the lock until cleanup is confirmed.
    try:
        proc = owner.spawn(args, **kwargs)
    except ContainmentError as e:
        if not e.cleanup_confirmed:
            # Spawn cleanup was not confirmed; retain lock with uncertainty.
            _outcome['cleanup_uncertain'] = True
            patch = {'cleanup_required': True}
            child_pid = getattr(e, 'child_pid', None)
            child_identity = getattr(e, 'child_identity', None)
            if child_pid is not None:
                patch['child_pid'] = child_pid
            if child_identity is not None:
                patch['child_identity'] = child_identity
            _update_state(root, job_id, patch)
        raise ValueError(f'process containment failed: {e}')

    # From this point forward the child is owned; persist proof immediately.
    _outcome['cleanup_uncertain'] = True
    child_identity = get_process_creation_identity(proc.pid)
    _update_state(root, job_id, {
        'child_pid': proc.pid, 'child_identity': child_identity,
        'cleanup_required': True,
    })

    stdout_buf = []
    stderr_buf = []
    stdout_q = queue.Queue()
    stderr_q = queue.Queue()

    def _reader(pipe, q):
        try:
            for line in pipe:
                q.put(line)
        finally:
            q.put(None)

    t_out = threading.Thread(target=_reader, args=(proc.stdout, stdout_q), daemon=True)
    t_err = threading.Thread(target=_reader, args=(proc.stderr, stderr_q), daemon=True)
    t_out.start()
    t_err.start()

    deadline = time.monotonic() + timeout_secs
    out_done = False
    terminal_event = None
    session_id = None
    cleanup_reason = None

    while not out_done:
        if cancel_requested(root, job_id):
            cleanup_reason = 'cancel requested'
            break

        if time.monotonic() > deadline:
            cleanup_reason = f'timeout after {timeout_secs}s'
            break

        try:
            line = stdout_q.get(timeout=0.1)
        except queue.Empty:
            continue
        if line is None:
            out_done = True
            continue
        stdout_buf.append(line)
        try:
            obj = json.loads(line.decode('utf-8', 'replace').strip())
        except (json.JSONDecodeError, AttributeError):
            continue
        if not isinstance(obj, dict):
            continue

        ev_type = obj.get('type')
        if ev_type == 'system' and 'session_id' in obj:
            session_id = obj['session_id']
            _update_state(root, job_id, {'session_id': session_id})

        if ev_type == 'assistant':
            msg = obj.get('message', {})
            for block in msg.get('content', []):
                if isinstance(block, dict) and block.get('type') == 'tool_use':
                    _update_state(root, job_id, {'last_tool': block.get('name', '')})

        if ev_type == 'result':
            terminal_event = obj
            if not session_id:
                session_id = obj.get('session_id')
                if session_id:
                    _update_state(root, job_id, {'session_id': session_id})
            # Stop reading stdout; give CLI a bounded graceful-exit window.
            break

    # --- Determine whether this looks like a success attempt ---
    is_potential_success = (
        terminal_event is not None
        and terminal_event.get('subtype') == 'success'
        and not terminal_event.get('is_error', True)
        and terminal_event.get('result')
        and cleanup_reason is None
    )

    # Save partial artifact metadata before any cleanup so it is available
    # for diagnosis even if the task ultimately fails.
    _save_partial_diagnostics(root, job_id, terminal_event, session_id)

    # cleanup_uncertain starts True (child was spawned); cleared only on confirmed stop.
    cleanup_uncertain = True
    rc = None

    def _set_uncertain(val):
        nonlocal cleanup_uncertain
        cleanup_uncertain = val
        _outcome['cleanup_uncertain'] = val
        # Persist immediately so main()'s finally has correct state even if
        # an exception is raised before the function returns.
        try:
            _update_state(root, job_id, {'cleanup_required': val})
        except Exception:
            pass

    if is_potential_success:
        # Give CLI bounded graceful-exit time before force-killing.
        graceful_deadline = time.monotonic() + _GRACEFUL_EXIT_SECS
        exited_gracefully = False
        while time.monotonic() < graceful_deadline:
            proc.poll()
            if proc.returncode is not None:
                exited_gracefully = True
                break
            remaining = graceful_deadline - time.monotonic()
            try:
                proc.wait(timeout=min(remaining, 0.2))
                exited_gracefully = True
                break
            except subprocess.TimeoutExpired:
                continue
            except Exception:
                break
        rc = proc.returncode

        if not exited_gracefully:
            # Root is still running after graceful window; force-kill group.
            terminated, uncertain = owner.terminate(grace=_CLEANUP_GRACE)
            _set_uncertain(uncertain)
            # After kill: rc may be nonzero (killed).  We know result was success,
            # but the hung-root counts as a shutdown error, not silent pass.
            rc = proc.returncode
            _drain_stderr(stderr_q, t_err, stderr_buf, _CLEANUP_GRACE)
            _write_logs(stdout_buf, stderr_buf, stdout_log, stderr_log)
            err = f'shutdown timeout: CLI did not exit within {_GRACEFUL_EXIT_SECS}s after success result'
            raise ValueError(err)

        # CLI exited within graceful window; confirm remaining descendants gone.
        terminated, uncertain = owner.terminate(grace=_CLEANUP_GRACE)
        _set_uncertain(uncertain)

    else:
        # Cancel, timeout, or failure: force-kill group before draining pipes.
        terminated, uncertain = owner.terminate(grace=_CLEANUP_GRACE)
        _set_uncertain(uncertain)

    # Drain stderr (bounded).
    _drain_stderr(stderr_q, t_err, stderr_buf, _CLEANUP_GRACE)
    _write_logs(stdout_buf, stderr_buf, stdout_log, stderr_log)

    # Reap child process if not yet reaped.
    try:
        proc.wait(timeout=max(3, _CLEANUP_GRACE))
    except subprocess.TimeoutExpired:
        terminated2, uncertain2 = owner.terminate(grace=3)
        if not terminated2:
            _set_uncertain(True)
    rc = proc.returncode

    # Close pipe file objects to suppress ResourceWarnings (process already dead).
    for _pipe in (proc.stdout, proc.stderr):
        try:
            if _pipe is not None:
                _pipe.close()
        except OSError:
            pass

    # --- Cancel path ---
    if cleanup_reason == 'cancel requested':
        _update_state(root, job_id, {
            'status': 'cancelled', 'phase': 'cancelled',
            'error': 'cancel requested'
        })
        return cleanup_uncertain

    # --- Timeout / other cleanup reason ---
    if cleanup_reason:
        raise ValueError(cleanup_reason)

    # --- No result event ---
    if terminal_event is None:
        raise ValueError(f'no result event in stream (rc={rc})')

    subtype = terminal_event.get('subtype', '')
    is_error = terminal_event.get('is_error', True)
    result_text = terminal_event.get('result') or ''

    # Genuine failure: nonzero rc, error envelope, or missing result text.
    if rc != 0 or subtype != 'success' or is_error or not result_text:
        err = terminal_event.get('error') or f'subtype={subtype} is_error={is_error} rc={rc}'
        raise ValueError(f'Claude task failed: {err}')

    artifact_paths = _collect_artifact_paths(task_data, root)
    _MAX_RESULT_BYTES = 4096
    safe_result = result_text[:_MAX_RESULT_BYTES] if result_text else ''
    _update_state(root, job_id, {
        'status': 'completed',
        'phase': 'completed',
        'session_id': session_id,
        'artifact_paths': artifact_paths,
        'result_text': safe_result,
        'error': None,
        'cleanup_required': cleanup_uncertain,
    })
    return cleanup_uncertain


def _save_partial_diagnostics(root, job_id, terminal_event, session_id):
    """Save partial result/artifact metadata to state for diagnosis even on failure."""
    if terminal_event is None:
        return
    try:
        patch = {'_partial_result_subtype': terminal_event.get('subtype')}
        if session_id:
            patch['_partial_session_id'] = session_id
        result_text = terminal_event.get('result') or ''
        if result_text:
            patch['_partial_result_preview'] = result_text[:256]
        _update_state(root, job_id, patch)
    except Exception:
        pass


def _drain_stderr(stderr_q, t_err, stderr_buf, grace):
    """Drain stderr queue into buf with bounded wait."""
    drain_deadline = time.monotonic() + max(grace, 1.0)
    while time.monotonic() < drain_deadline:
        try:
            line = stderr_q.get(timeout=0.2)
        except queue.Empty:
            if not t_err.is_alive():
                break
            continue
        if line is None:
            break
        elif line:
            stderr_buf.append(line)


def _verify_task_snapshot(snapshot_path):
    """Read snapshot, verify its hash, return data. Raises ValueError on tampering."""
    import hashlib
    text = snapshot_path.read_text('utf-8')
    data = json.loads(text)
    stored_hash = data.pop('_snapshot_hash', None)
    check_content = json.dumps(data, indent=2, ensure_ascii=False, sort_keys=True)
    computed = hashlib.sha256(check_content.encode('utf-8')).hexdigest()
    if stored_hash != computed:
        raise ValueError(f'task snapshot tampered: stored={stored_hash} computed={computed}')
    return data


def _build_claude_prompt(task_data):
    """Build the Claude prompt from snapshot data only; never references mutable originals."""
    objective = task_data.get('objective', '(see task objective)')
    prompt_text = task_data.get('prompt', '')
    allowed_paths = task_data.get('allowed_paths', [])
    paths_hint = ', '.join(allowed_paths) if allowed_paths else '(see task objective)'

    if not prompt_text and task_data.get('prompt_file'):
        prompt_text = task_data.get('_prompt_file_content') or ''

    base = (
        f'Read docs/ai-collaboration-workflow.md and CLAUDE.md before starting. '
        f'Do NOT delegate to agents or call other CLIs. '
        f'Allowed tools: Read, Write, Edit, Glob, Grep only — no Bash, no Agent calls. '
        f'Allowed paths: {paths_hint}. '
        f'Task objective: {objective}. '
    )
    if prompt_text:
        base += f'\n\nDetailed instructions:\n{prompt_text}'
    else:
        base += '\n\nNo additional prompt provided; work from the task objective above.'
    return base


def _run_codex(root, job_id, task_data, config, overrides, timeout_secs, d, _outcome):
    from _appserver_adapter import AppServerAdapter, AppServerError
    cli_path = find_cli('codex', overrides) or find_cli('codex', config.get('cli', {}))
    if not cli_path:
        raise ValueError('codex CLI not found')

    from _cli_helper import build_args as _build_args
    args = _build_args(cli_path, ['app-server'])

    _update_state(root, job_id, {'phase': 'appserver_init'})
    objective = task_data.get('objective', '(see task objective)')
    prompt_text = task_data.get('prompt', objective)

    text = ''
    thread_id = None
    cleanup_confirmed = False
    cleanup_error = None

    # Instantiate adapter before entering with-block so _outcome can be inspected
    # even if __enter__ raises (e.g., App Server init failure).
    from _controller_state import get_process_creation_identity
    adapter = AppServerAdapter(args, cwd=str(root), timeout_secs=timeout_secs)
    try:
        with adapter:
            # Adapter __enter__ succeeded: persist child identity immediately.
            child_pid = adapter.child_pid
            child_identity = adapter.child_identity
            if child_pid is not None:
                _update_state(root, job_id, {
                    'child_pid': child_pid,
                    'child_identity': child_identity,
                    'cleanup_required': True,
                })
                _outcome['cleanup_uncertain'] = True
            _update_state(root, job_id, {'phase': 'thread_start'})
            thread_id = adapter.start_thread(
                cwd=str(root),
                approval_policy='never',
                sandbox='read-only',
                ephemeral=True,
            )
            _update_state(root, job_id, {'thread_id': thread_id, 'phase': 'turn_running'})

            def _cancel_check():
                return cancel_requested(root, job_id)

            text, status = adapter.run_turn(thread_id, prompt_text, cancel_fn=_cancel_check)
        # __exit__ called adapter.close() which terminates owned group.
        # Propagate the adapter's cleanup result.
        cleanup_confirmed = adapter.cleanup_confirmed
        cleanup_error = adapter.cleanup_error
    except AppServerError as e:
        cleanup_confirmed = adapter.cleanup_confirmed
        cleanup_error = adapter.cleanup_error
        raise ValueError(f'appserver error: {e}')
    finally:
        # Persist child identity and cleanup outcome before caller's finally runs.
        # Unconditionally mirror cleanup_confirmed into _outcome so caller's finally
        # never sees stale True from the child-spawn eager-set above.
        # child_pid/identity are always preserved so recovery can identify the process.
        child_pid = adapter.child_pid
        child_identity = adapter.child_identity
        _outcome['cleanup_uncertain'] = not cleanup_confirmed
        patch = {}
        if child_pid is not None:
            patch['child_pid'] = child_pid
        if child_identity is not None:
            patch['child_identity'] = child_identity
        patch['cleanup_required'] = not cleanup_confirmed
        try:
            _update_state(root, job_id, patch)
        except Exception:
            pass

    _MAX_RESULT_BYTES = 4096
    safe_result = text[:_MAX_RESULT_BYTES] if text else ''

    stdout_log = d / 'stdout.log'
    stdout_log.write_text(safe_result, encoding='utf-8')

    if cancel_requested(root, job_id):
        _update_state(root, job_id, {
            'status': 'cancelled', 'phase': 'cancelled', 'error': 'cancel requested'
        })
        return

    if status not in ('completed',):
        raise ValueError(f'codex turn ended with status: {status}')

    artifact_paths = _collect_artifact_paths(task_data, root)
    _update_state(root, job_id, {
        'status': 'completed',
        'phase': 'completed',
        'thread_id': thread_id,
        'artifact_paths': artifact_paths,
        'result_text': safe_result,
        'error': None,
        'cleanup_required': not cleanup_confirmed,
    })


def _collect_artifact_paths(task_data, root):
    allowed = task_data.get('allowed_paths', [])
    found = []
    for rel in allowed:
        p = root / rel
        if p.is_file():
            found.append(str(p))
    return found


def _write_logs(stdout_buf, stderr_buf, stdout_log, stderr_log):
    if stdout_buf:
        stdout_log.write_bytes(b''.join(stdout_buf))
    if stderr_buf:
        stderr_log.write_bytes(b''.join(stderr_buf))


if __name__ == '__main__':
    main()
