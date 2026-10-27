"""
Job state persistence for the host controller.

Each job lives under .collaboration/controller/jobs/<JOB_ID>/.
  state.json  — atomic write (tmp + replace), single source of truth
  worker.pid  — written by worker on start, removed on terminal state
  cancel      — sentinel file: cooperative cancellation request
  stdout.log  — worker stdout capture
  stderr.log  — worker stderr capture
  lock        — root-level active-job lock (one active job at a time)

State machine: pending -> running -> (completed|failed|cancelled|interrupted)
Terminal states: completed, failed, cancelled, interrupted.

Writers: worker process owns state transitions running->terminal.
         start CLI writes initial pending state.
Readers: status/result CLI read state.json (no write).
"""
import json
import os
import pathlib
import re
import time

_VALID_JOB_ID = re.compile(r'^[A-Za-z0-9_-]{4,64}$')
TERMINAL_STATES = frozenset(('completed', 'failed', 'cancelled', 'interrupted'))
ACTIVE_LOCK = '.collaboration/controller/lock'


def validate_job_id(job_id):
    """Raise ValueError if job_id is not filename-safe."""
    if not isinstance(job_id, str) or not _VALID_JOB_ID.match(job_id):
        raise ValueError(
            f'invalid job_id {job_id!r}: must be 4-64 chars, only [A-Za-z0-9_-]'
        )


def job_dir(root, job_id):
    """Return absolute path to job directory (does not create it)."""
    validate_job_id(job_id)
    base = pathlib.Path(root) / '.collaboration' / 'controller' / 'jobs' / job_id
    # Guard against path traversal (redundant given regex, but explicit)
    resolved = base.resolve()
    expected = (pathlib.Path(root) / '.collaboration' / 'controller' / 'jobs').resolve()
    if not str(resolved).startswith(str(expected)):
        raise ValueError(f'path traversal detected in job_id: {job_id!r}')
    return base


def read_state(root, job_id):
    """
    Read and return the state dict for job_id, or raise FileNotFoundError / ValueError.
    """
    d = job_dir(root, job_id)
    state_file = d / 'state.json'
    if not state_file.is_file():
        raise FileNotFoundError(f'job not found: {job_id}')
    try:
        return json.loads(state_file.read_text('utf-8'))
    except json.JSONDecodeError as e:
        raise ValueError(f'corrupt state.json for job {job_id}: {e}')


def write_state_atomic(root, job_id, state_dict):
    """
    Atomically write state_dict to state.json via tmp-replace.

    state_dict must be JSON-serialisable and contain at least 'status'.
    """
    d = job_dir(root, job_id)
    d.mkdir(parents=True, exist_ok=True)
    state_file = d / 'state.json'
    tmp_file = d / 'state.json.tmp'
    state_dict['updated_at'] = time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())
    text = json.dumps(state_dict, indent=2, ensure_ascii=False)
    tmp_file.write_text(text, encoding='utf-8')
    tmp_file.replace(state_file)


def create_job(root, job_id, actor, task_file, task_data, baseline):
    """
    Create a new job in pending state using exclusive creation.

    Raises FileExistsError if the job_id already exists.
    Raises RuntimeError if a root-level active-job lock is held.
    """
    d = job_dir(root, job_id)
    lock_path = pathlib.Path(root) / ACTIVE_LOCK

    # Exclusive root-level lock: refuse second active job
    try:
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(str(lock_path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        os.write(fd, job_id.encode())
        os.close(fd)
    except FileExistsError:
        existing = _read_lock(lock_path)
        raise RuntimeError(
            f'another job is active: {existing}; '
            f'wait for it to complete or use recover if the worker is gone'
        )

    try:
        d.mkdir(parents=True, exist_ok=False)
    except FileExistsError:
        lock_path.unlink(missing_ok=True)
        raise FileExistsError(f'job {job_id!r} already exists')

    state = {
        'job_id': job_id,
        'actor': actor,
        'status': 'pending',
        'phase': 'created',
        'task_file': str(task_file),
        'task_id': task_data.get('task_id', ''),
        'baseline': baseline,
        'pid': None,
        'child_pid': None,
        'session_id': None,
        'thread_id': None,
        'last_tool': None,
        'error': None,
        'artifact_paths': [],
        'result_text': None,
        'created_at': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()),
    }
    write_state_atomic(root, job_id, state)
    return state


def _read_lock(lock_path):
    try:
        return lock_path.read_text('utf-8').strip() or '(unknown)'
    except OSError:
        return '(unreadable)'


def release_lock(root, job_id):
    """Release the root-level active-job lock if it matches job_id."""
    lock_path = pathlib.Path(root) / ACTIVE_LOCK
    try:
        existing = _read_lock(lock_path)
        if existing == job_id:
            lock_path.unlink(missing_ok=True)
    except OSError:
        pass


def cancel_requested(root, job_id):
    """Return True if the cancel sentinel file exists for this job."""
    d = job_dir(root, job_id)
    return (d / 'cancel').exists()


def request_cancel(root, job_id):
    """Write cancel sentinel for cooperative cancellation."""
    d = job_dir(root, job_id)
    (d / 'cancel').touch()


def worker_gone(pid):
    """Return True if no process with pid is running; False if alive or uncertain."""
    if pid is None:
        return True
    try:
        if os.name == 'nt':
            import ctypes
            import ctypes.wintypes
            # Use WinDLL(use_last_error=True) with full restype/argtypes.
            # BOOL failures => unknown (return False), not gone.
            _k32 = ctypes.WinDLL('kernel32', use_last_error=True)
            HANDLE = ctypes.c_void_p
            DWORD = ctypes.wintypes.DWORD
            BOOL = ctypes.wintypes.BOOL

            _k32.OpenProcess.restype = HANDLE
            _k32.OpenProcess.argtypes = [DWORD, BOOL, DWORD]
            _k32.GetExitCodeProcess.restype = BOOL
            _k32.GetExitCodeProcess.argtypes = [HANDLE, ctypes.POINTER(DWORD)]
            _k32.CloseHandle.restype = BOOL
            _k32.CloseHandle.argtypes = [HANDLE]

            PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
            handle = _k32.OpenProcess(
                PROCESS_QUERY_LIMITED_INFORMATION, False, int(pid)
            )
            if not handle:
                last_error = ctypes.get_last_error()
                ERROR_INVALID_PARAMETER = 87  # PID does not exist
                return last_error == ERROR_INVALID_PARAMETER
            try:
                exit_code = DWORD(0)
                ok = _k32.GetExitCodeProcess(handle, ctypes.byref(exit_code))
            finally:
                _k32.CloseHandle(handle)
            if not ok:
                return False  # BOOL failure => unknown, fail closed
            STILL_ACTIVE = 259
            return exit_code.value != STILL_ACTIVE
        else:
            os.kill(pid, 0)
            return False
    except ProcessLookupError:
        return True
    except (PermissionError, OSError):
        return False  # fail closed: uncertain, assume alive


def get_process_creation_identity(pid):
    """
    Return a stable creation-time identity string for the given PID, or None if unavailable.

    Uses GetProcessTimes FILETIME (creation time) combined with PID as a 64-bit integer
    string.  A different identity means the PID has been reused and the original is gone.
    Returns None on any API failure (access denied, process gone, etc.).
    """
    if pid is None or os.name != 'nt':
        return None
    try:
        import ctypes
        import ctypes.wintypes
        _k32 = ctypes.WinDLL('kernel32', use_last_error=True)
        HANDLE = ctypes.c_void_p
        DWORD = ctypes.wintypes.DWORD
        BOOL = ctypes.wintypes.BOOL

        class FILETIME(ctypes.Structure):
            _fields_ = [('dwLowDateTime', DWORD), ('dwHighDateTime', DWORD)]

        _k32.OpenProcess.restype = HANDLE
        _k32.OpenProcess.argtypes = [DWORD, BOOL, DWORD]
        _k32.GetProcessTimes.restype = BOOL
        _k32.GetProcessTimes.argtypes = [
            HANDLE,
            ctypes.POINTER(FILETIME), ctypes.POINTER(FILETIME),
            ctypes.POINTER(FILETIME), ctypes.POINTER(FILETIME),
        ]
        _k32.CloseHandle.restype = BOOL
        _k32.CloseHandle.argtypes = [HANDLE]

        PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
        handle = _k32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, int(pid))
        if not handle:
            return None
        try:
            creation = FILETIME()
            exit_ft = FILETIME()
            kernel_ft = FILETIME()
            user_ft = FILETIME()
            ok = _k32.GetProcessTimes(
                handle,
                ctypes.byref(creation), ctypes.byref(exit_ft),
                ctypes.byref(kernel_ft), ctypes.byref(user_ft),
            )
        finally:
            _k32.CloseHandle(handle)
        if not ok:
            return None
        creation_int = (creation.dwHighDateTime << 32) | creation.dwLowDateTime
        return f'{pid}:{creation_int}'
    except Exception:
        return None
