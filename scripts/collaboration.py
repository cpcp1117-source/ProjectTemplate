"""
Host-side job controller for AI collaboration.

This is our glue layer over the official Claude CLI (stream-json) and Codex App Server
(stdio JSONRPC) interfaces. It is not the official OpenAI plugin or a persistent service.
Current chat reports progress while the turn is active; no proactive notifications after
the turn closes.

Commands:
  start   -- launch a detached background job, print job_id JSON quickly
  status  -- poll job metadata and phase (non-destructive)
  result  -- retrieve terminal result (error if still running)
  cancel  -- request cooperative cancellation
  recover -- mark interrupted when worker confirmed gone (refuses live workers)

Usage:
  python -X utf8 scripts/collaboration.py start --actor claude \\
      --task-file tasks/my_task.json [--root /abs/path]
  python -X utf8 scripts/collaboration.py status --job JOB_ID [--root PATH]
  python -X utf8 scripts/collaboration.py result --job JOB_ID [--root PATH]
  python -X utf8 scripts/collaboration.py cancel --job JOB_ID [--root PATH]
  python -X utf8 scripts/collaboration.py recover --job JOB_ID [--root PATH]

Output: JSON to stdout. All errors print JSON with 'error' key and exit nonzero.
"""

import argparse
import json
import math
import os
import pathlib
import secrets
import subprocess
import sys
import time

sys.path.insert(0, str(pathlib.Path(__file__).parent))

from _controller_state import (
    validate_job_id, job_dir, read_state, write_state_atomic,
    create_job, release_lock, request_cancel, cancel_requested,
    worker_gone, get_process_creation_identity, TERMINAL_STATES,
)


def main(argv=None):
    parser = argparse.ArgumentParser(description='AI collaboration job controller')
    parser.add_argument('command', choices=['start', 'status', 'result', 'cancel', 'recover'])
    parser.add_argument('--actor', choices=['claude', 'codex'])
    parser.add_argument('--task-file')
    parser.add_argument('--job', dest='job_id')
    parser.add_argument('--root', help='Absolute project root (default: auto-detect)')
    args = parser.parse_args(argv)

    root = _resolve_root(args.root)

    try:
        if args.command == 'start':
            _cmd_start(root, args)
        elif args.command == 'status':
            _cmd_status(root, args)
        elif args.command == 'result':
            _cmd_result(root, args)
        elif args.command == 'cancel':
            _cmd_cancel(root, args)
        elif args.command == 'recover':
            _cmd_recover(root, args)
    except SystemExit:
        raise
    except Exception as e:
        _error_exit(str(e))


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------

def _cmd_start(root, args):
    if not args.actor:
        _error_exit('start requires --actor')
    if not args.task_file:
        _error_exit('start requires --task-file')

    task_file = pathlib.Path(args.task_file)
    if not task_file.is_absolute():
        task_file = (root / task_file).resolve()
    if not task_file.is_file():
        _error_exit(f'task file not found: {task_file}')

    task_data = _load_and_validate_task(task_file, root)

    _validate_environment(root)

    job_id = secrets.token_hex(8)
    baseline = _capture_baseline(root)

    try:
        create_job(root, job_id, args.actor, task_file, task_data, baseline)
    except (FileExistsError, RuntimeError) as e:
        _error_exit(str(e))

    try:
        snap_path, snapshot_hash = _save_task_snapshot(root, job_id, task_data, task_file, baseline)
    except Exception as e:
        release_lock(root, job_id)
        _error_exit(f'failed to save task snapshot: {e}')

    # Persist snapshot hash and dirty_hashes in state for provenance tracking.
    # Use one captured manifest (already in snapshot) — no re-capture.
    try:
        state = read_state(root, job_id)
        state['snapshot_hash'] = snapshot_hash
        state['snapshot_source'] = str(snap_path)
        # Expose dirty_hashes from the single captured snapshot manifest.
        snap_data = json.loads(snap_path.read_text('utf-8'))
        state['dirty_hashes'] = snap_data.get('_dirty_hashes', {})
        write_state_atomic(root, job_id, state)
    except Exception:
        pass  # non-fatal: hash is in snapshot itself

    worker_args = _build_worker_args(root, job_id)
    try:
        _launch_detached(worker_args, root)
    except OSError as e:
        # Definite launch failure: no process was created. Mark terminal immediately
        # so state is consistent, then release lock and exit nonzero.
        try:
            _state = read_state(root, job_id)
            _state['status'] = 'failed'
            _state['phase'] = 'failed'
            _state['error'] = f'launch_failed: {e}'
            _state['cleanup_required'] = False
            write_state_atomic(root, job_id, _state)
        except Exception:
            pass
        release_lock(root, job_id)
        _error_exit(f'failed to launch worker: {e}')
    except Exception as e:
        release_lock(root, job_id)
        _error_exit(f'failed to launch worker: {e}')

    _print_json({
        'job_id': job_id,
        'actor': args.actor,
        'status': 'pending',
        'task_id': task_data.get('task_id', ''),
        'root': str(root),
    })


def _cmd_status(root, args):
    job_id = _require_job(args)
    state = _read_job(root, job_id)
    _print_json(_public_status(state))


def _cmd_result(root, args):
    job_id = _require_job(args)
    state = _read_job(root, job_id)
    status = state.get('status', '')
    if status not in TERMINAL_STATES:
        _error_exit(f'job {job_id} is not complete (status={status})', code=2)
    _print_json(_public_result(state))
    if status in ('failed', 'cancelled', 'interrupted'):
        sys.exit(1)


def _cmd_cancel(root, args):
    job_id = _require_job(args)
    state = _read_job(root, job_id)
    status = state.get('status', '')
    if status in TERMINAL_STATES:
        _print_json({'job_id': job_id, 'status': status, 'message': 'already terminal'})
        return
    request_cancel(root, job_id)
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        state = _read_job(root, job_id)
        if state.get('status') in TERMINAL_STATES:
            break
        time.sleep(0.5)
    _print_json({'job_id': job_id, 'status': state.get('status'), 'action': 'cancel_requested'})


def _cmd_recover(root, args):
    job_id = _require_job(args)
    state = _read_job(root, job_id)
    status = state.get('status', '')
    cleanup_required = state.get('cleanup_required', False)

    # Terminal without cleanup_required: nothing to do.
    if status in TERMINAL_STATES and not cleanup_required:
        _print_json({'job_id': job_id, 'status': status,
                     'message': 'already terminal; no recovery needed'})
        return

    # Pending with no PID means a worker may still be launching but has not yet
    # written its PID.  Releasing the lock now could free a job that is about to
    # become active.  Refuse until the job has a PID or reaches a terminal state.
    if status == 'pending':
        pid = state.get('pid')
        if not pid:
            _error_exit(
                f'job {job_id} is pending with no worker PID; '
                f'worker launch ownership is unknown — refuse recover to avoid orphan. '
                f'Wait for running/terminal state or verify no worker was launched.'
            )

    # Non-terminal or terminal+cleanup_required: verify worker AND child are gone
    # before clearing the flag and releasing the lock.
    # Use creation-time identity to detect PID reuse: a different nonempty identity
    # means the original process is gone even if that PID is currently live.
    # Same identity and live => refuse (original is still running).
    # API unavailable/unknown => fail closed and refuse.
    pid = state.get('pid')
    if pid:
        saved_worker_identity = state.get('worker_identity')
        if saved_worker_identity:
            current_identity = get_process_creation_identity(pid)
            if current_identity is None:
                # API unavailable: cannot distinguish reuse from alive — fail closed.
                if not worker_gone(pid):
                    _error_exit(
                        f'worker pid={pid} appears alive and identity unavailable; refusing recover. '
                        f'Use cancel first, or wait for natural completion.'
                    )
            elif current_identity == saved_worker_identity:
                # Same identity: original process is still running.
                _error_exit(
                    f'worker pid={pid} appears alive (identity matches); refusing recover. '
                    f'Use cancel first, or wait for natural completion.'
                )
            # Different identity: PID was reused — original is gone, do NOT kill new process.
        else:
            # No saved identity (legacy): fall back to liveness check only.
            if not worker_gone(pid):
                _error_exit(
                    f'worker pid={pid} appears alive; refusing recover. '
                    f'Use cancel first, or wait for natural completion.'
                )

    child_pid = state.get('child_pid')
    if child_pid:
        saved_child_identity = state.get('child_identity')
        if saved_child_identity:
            current_child_identity = get_process_creation_identity(child_pid)
            if current_child_identity is None:
                if not worker_gone(child_pid):
                    _error_exit(
                        f'child process pid={child_pid} appears alive and identity unavailable; refusing recover. '
                        f'Kill child process manually, then retry recover.'
                    )
            elif current_child_identity == saved_child_identity:
                _error_exit(
                    f'child process pid={child_pid} appears alive (identity matches); refusing recover. '
                    f'Kill child process manually, then retry recover.'
                )
            # Different identity: PID reused — do NOT kill new process.
        else:
            if not worker_gone(child_pid):
                _error_exit(
                    f'child process pid={child_pid} appears alive; refusing recover. '
                    f'Kill child process manually, then retry recover.'
                )

    # Preserve pre-recovery status, error, and result so a completed task that was
    # incorrectly locked is not lost from history after recovery overwrites status.
    original_error = state.get('error')
    original_result = state.get('result_text')
    original_status = state.get('status')
    write_state_atomic(root, job_id, {
        **state,
        'status': 'interrupted',
        'phase': 'interrupted',
        'error': f'recovered: worker confirmed gone (original: {original_error})',
        'result_text': original_result,
        'cleanup_required': False,
        'recovered_from_status': original_status,
        'original_error': original_error,
        'original_result_text': original_result,
    })
    release_lock(root, job_id)
    _print_json({
        'job_id': job_id,
        'status': 'interrupted',
        'action': 'recovered',
        'cleanup_required': False,
    })


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _require_job(args):
    if not args.job_id:
        _error_exit('command requires --job JOB_ID')
    try:
        validate_job_id(args.job_id)
    except ValueError as e:
        _error_exit(str(e))
    return args.job_id


def _read_job(root, job_id):
    try:
        return read_state(root, job_id)
    except FileNotFoundError:
        _error_exit(f'job not found: {job_id}')
    except ValueError as e:
        _error_exit(f'corrupt job state: {e}')


def _public_status(state):
    return {
        'job_id': state.get('job_id'),
        'actor': state.get('actor'),
        'status': state.get('status'),
        'phase': state.get('phase'),
        'updated_at': state.get('updated_at'),
        'pid': state.get('pid'),
        'session_id': state.get('session_id'),
        'thread_id': state.get('thread_id'),
        'last_tool': state.get('last_tool'),
        'error': state.get('error'),
        'task_id': state.get('task_id'),
        'cleanup_required': state.get('cleanup_required', False),
        'child_pid': state.get('child_pid'),
        'worker_identity': state.get('worker_identity'),
        'child_identity': state.get('child_identity'),
    }


def _public_result(state):
    base = _public_status(state)
    base['artifact_paths'] = state.get('artifact_paths', [])
    base['result_text'] = state.get('result_text')
    # Expose dirty-content hashes and snapshot provenance in baseline.
    baseline = dict(state.get('baseline') or {})
    if state.get('dirty_hashes') is not None:
        baseline['dirty_hashes'] = state.get('dirty_hashes')
    base['baseline'] = baseline
    base['snapshot_hash'] = state.get('snapshot_hash')
    base['snapshot_source'] = state.get('snapshot_source')
    # Recovery provenance fields: expose only when present, bounded to 4096 chars.
    if 'recovered_from_status' in state:
        base['recovered_from_status'] = state.get('recovered_from_status')
    if 'original_error' in state:
        v = state.get('original_error')
        base['original_error'] = v[:4096] if isinstance(v, str) else v
    if 'original_result_text' in state:
        v = state.get('original_result_text')
        base['original_result_text'] = v[:4096] if isinstance(v, str) else v
    return base


def _capture_dirty_hashes(root):
    """Return {rel_path: sha256_hex|'__DELETED__'} for tracked dirty + untracked files.

    Uses NUL-delimited output (-z) to handle paths with special chars, CJK, and spaces.
    Includes staged changes, unstaged changes, untracked files, and deleted tracked files.
    Excludes .git metadata and .collaboration/ credentials/logs.
    """
    import hashlib
    hashes = {}

    # NUL-delimited output avoids C-quoted paths for CJK, spaces, special chars.
    git_commands = [
        ['git', 'diff', '--name-only', '-z', 'HEAD'],           # unstaged tracked
        ['git', 'diff', '--name-only', '-z', '--cached', 'HEAD'],  # staged tracked
        ['git', 'ls-files', '--others', '--exclude-standard', '-z'],  # untracked
        ['git', 'ls-files', '--deleted', '-z'],                  # deleted tracked
    ]

    for git_args in git_commands:
        try:
            r = subprocess.run(git_args, cwd=str(root), capture_output=True, timeout=10)
            if r.returncode == 0:
                # Split on NUL; last element may be empty after trailing NUL
                paths = r.stdout.split(b'\x00')
                for rel_bytes in paths:
                    if not rel_bytes:
                        continue
                    try:
                        rel = rel_bytes.decode('utf-8')
                    except UnicodeDecodeError:
                        rel = rel_bytes.decode('utf-8', 'replace')
                    rel = rel.strip()
                    if not rel or rel.startswith('.git') or rel in hashes:
                        continue
                    # Exclude .collaboration/ credentials and logs
                    if rel.startswith('.collaboration/'):
                        continue
                    p = pathlib.Path(root) / rel
                    if p.is_file():
                        try:
                            hashes[rel] = hashlib.sha256(p.read_bytes()).hexdigest()
                        except OSError:
                            pass
                    else:
                        hashes[rel] = '__DELETED__'
        except Exception:
            pass
    return hashes


def _save_task_snapshot(root, job_id, task_data, task_file, baseline):
    """Save immutable task snapshot to job dir before launching worker.

    Returns (snap_path, snapshot_hash).
    Raises ValueError if prompt_file is specified but outside root or missing.
    """
    import hashlib
    from _controller_state import job_dir
    d = job_dir(root, job_id)
    d.mkdir(parents=True, exist_ok=True)
    snapshot = dict(task_data)

    pf_rel = snapshot.get('prompt_file')
    if pf_rel and isinstance(pf_rel, str):
        pf_resolved = (pathlib.Path(root) / pf_rel).resolve()
        root_resolved = pathlib.Path(root).resolve()
        # Use is_relative_to (Python 3.9+) to prevent sibling-prefix bypass
        try:
            pf_resolved.relative_to(root_resolved)
            is_inside = True
        except ValueError:
            is_inside = False
        if not is_inside:
            raise ValueError(
                f'prompt_file {pf_rel!r} is outside the project root; '
                f'only repo-relative paths are allowed'
            )
        if not pf_resolved.is_file():
            raise ValueError(f'prompt_file not found: {pf_rel!r}')
        snapshot['_prompt_file_content'] = pf_resolved.read_text('utf-8')

    snapshot['_source_task_file'] = str(task_file)
    snapshot['_dirty_hashes'] = _capture_dirty_hashes(root)
    snapshot['_baseline'] = baseline

    content = json.dumps(snapshot, indent=2, ensure_ascii=False, sort_keys=True)
    h = hashlib.sha256(content.encode('utf-8')).hexdigest()
    snapshot['_snapshot_hash'] = h
    content = json.dumps(snapshot, indent=2, ensure_ascii=False, sort_keys=True)
    snap_path = d / 'task_snapshot.json'
    snap_path.write_text(content, encoding='utf-8')
    return snap_path, h


def _load_and_validate_task(task_file, root):
    try:
        text = task_file.read_text('utf-8')
    except OSError as e:
        _error_exit(f'cannot read task file: {e}')
    try:
        data = json.loads(text)
    except json.JSONDecodeError as e:
        _error_exit(f'task file is not valid JSON: {e}')

    if not isinstance(data, dict):
        _error_exit('task file must be a JSON object')

    required = ('task_id', 'objective', 'timeout_secs')
    missing = [f for f in required if f not in data]
    if missing:
        _error_exit(f'task file missing required fields: {missing}')

    if not isinstance(data.get('task_id'), str) or not data['task_id']:
        _error_exit('task_id must be a nonempty string')
    if not isinstance(data.get('objective'), str) or not data['objective']:
        _error_exit('objective must be a nonempty string')

    to = data.get('timeout_secs')
    if (isinstance(to, bool) or not isinstance(to, (int, float))
            or not math.isfinite(to) or to <= 0):
        _error_exit('timeout_secs must be a finite positive number')
    _MAX_TIMEOUT = 3600
    if to > _MAX_TIMEOUT:
        _error_exit(f'timeout_secs must not exceed {_MAX_TIMEOUT}s')

    allowed_paths = data.get('allowed_paths', [])
    if not isinstance(allowed_paths, list):
        _error_exit('allowed_paths must be an array')
    for p in allowed_paths:
        if not isinstance(p, str):
            _error_exit('allowed_paths entries must be strings')
        if pathlib.PurePosixPath(p).is_absolute() or pathlib.PureWindowsPath(p).drive:
            _error_exit(f'allowed_paths must be repo-relative, got: {p!r}')
        if '..' in pathlib.PurePosixPath(p).parts or '..' in pathlib.PureWindowsPath(p).parts:
            _error_exit(f'allowed_paths must not contain ..: {p!r}')

    return data


def _validate_environment(root):
    """Validate required docs and config exist before starting a job."""
    from _project import REQUIRED_DOCS, WORKFLOW_DOC, load_config
    workflow = root / WORKFLOW_DOC
    if not workflow.is_file():
        _error_exit(f'workflow doc missing: {WORKFLOW_DOC}')
    for doc in REQUIRED_DOCS:
        if not (root / doc).is_file():
            _error_exit(f'required doc missing: {doc}')
    try:
        load_config(root)
    except ValueError as e:
        _error_exit(f'environment validation failed: {e}')


def _capture_baseline(root):
    """Capture current git commit + dirty file list for provenance."""
    baseline = {}
    try:
        result = subprocess.run(
            ['git', 'rev-parse', 'HEAD'],
            cwd=str(root), capture_output=True, timeout=5
        )
        if result.returncode == 0:
            baseline['commit'] = result.stdout.decode('utf-8', 'replace').strip()
    except Exception:
        pass
    try:
        result = subprocess.run(
            ['git', 'status', '--porcelain'],
            cwd=str(root), capture_output=True, timeout=5
        )
        if result.returncode == 0:
            dirty = result.stdout.decode('utf-8', 'replace').strip()
            baseline['dirty_manifest'] = dirty if dirty else ''
    except Exception:
        pass
    return baseline


def _build_worker_args(root, job_id):
    worker_script = pathlib.Path(__file__).parent / '_controller_worker.py'
    return [sys.executable, '-X', 'utf8', str(worker_script), str(root), job_id]


def _launch_detached(args, root):
    kwargs = dict(
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        cwd=str(root),
    )
    if os.name == 'nt':
        kwargs['creationflags'] = (
            subprocess.CREATE_NO_WINDOW | subprocess.DETACHED_PROCESS
        )
    else:
        kwargs['start_new_session'] = True
    subprocess.Popen(args, **kwargs)


def _resolve_root(explicit_root):
    if explicit_root:
        return pathlib.Path(explicit_root).resolve()
    return pathlib.Path(__file__).resolve().parent.parent


def _print_json(obj):
    print(json.dumps(obj, indent=2, ensure_ascii=False))


def _error_exit(msg, code=1):
    print(json.dumps({'error': msg}), file=sys.stderr)
    sys.exit(code)


if __name__ == '__main__':
    main()
