"""
Shared process-tree containment for the host controller.

ProcessOwner is the primary abstraction: spawn a child with Windows Job Object
(or POSIX process group), confirm cleanup, and expose outcome.  Used by both
_controller_worker (_run_claude) and AppServerAdapter.

Windows Job Object design (approved round-11 architecture):
  - Child is spawned SUSPENDED (CREATE_SUSPENDED | CREATE_NO_WINDOW).
  - Non-inheritable job handle created with KILL_ON_JOB_CLOSE.
  - proc._handle is assigned to the job BEFORE the child runs any user code.
  - Primary thread is located via Toolhelp32 snapshot and resumed.
  - On containment/resume failure: child is terminated and job closed immediately.
  - Cleanup: TerminateJobObject then QueryInformationJobObject confirms
    ActiveProcesses == 0.
  - If worker crashes, the non-inherited KILL_ON_JOB_CLOSE handle closes and
    all owned children are automatically terminated by the kernel.

POSIX:
  - start_new_session=True gives the child its own process group.
  - killpg terminates the whole group.
  - Group liveness checked independently of parent exit.

Legacy helpers kept for existing callers that have not yet migrated:
  kill_tree(proc, grace) -- calls ProcessOwner.terminate() without spawn path
  posix_spawn_kwargs()   -- returns start_new_session kwargs (no-op on Windows)
"""
import ctypes
import ctypes.wintypes
import os
import subprocess
import sys
import threading
import time


# ---------------------------------------------------------------------------
# Windows ctypes declarations
# ---------------------------------------------------------------------------

if os.name == 'nt':
    _kernel32 = ctypes.WinDLL('kernel32', use_last_error=True)

    HANDLE = ctypes.c_void_p
    DWORD  = ctypes.wintypes.DWORD
    BOOL   = ctypes.wintypes.BOOL
    WORD   = ctypes.wintypes.WORD
    ULONG  = ctypes.c_ulong

    # ---- Toolhelp32 ----
    class _THREADENTRY32(ctypes.Structure):
        _fields_ = [
            ('dwSize',             DWORD),
            ('cntUsage',           DWORD),
            ('th32ThreadID',       DWORD),
            ('th32OwnerProcessID', DWORD),
            ('tpBasePri',          ctypes.c_long),
            ('tpDeltaPri',         ctypes.c_long),
            ('dwFlags',            DWORD),
        ]

    TH32CS_SNAPTHREAD = 0x00000004

    _kernel32.CreateToolhelp32Snapshot.restype  = HANDLE
    _kernel32.CreateToolhelp32Snapshot.argtypes = [DWORD, DWORD]
    _kernel32.Thread32First.restype  = BOOL
    _kernel32.Thread32First.argtypes = [HANDLE, ctypes.POINTER(_THREADENTRY32)]
    _kernel32.Thread32Next.restype   = BOOL
    _kernel32.Thread32Next.argtypes  = [HANDLE, ctypes.POINTER(_THREADENTRY32)]
    _kernel32.CloseHandle.restype    = BOOL
    _kernel32.CloseHandle.argtypes   = [HANDLE]

    THREAD_SUSPEND_RESUME = 0x0002
    _kernel32.OpenThread.restype  = HANDLE
    _kernel32.OpenThread.argtypes = [DWORD, BOOL, DWORD]
    _kernel32.ResumeThread.restype  = DWORD
    _kernel32.ResumeThread.argtypes = [HANDLE]

    # ---- Job Objects ----
    _kernel32.CreateJobObjectW.restype  = HANDLE
    _kernel32.CreateJobObjectW.argtypes = [ctypes.c_void_p, ctypes.c_wchar_p]

    class _IO_COUNTERS(ctypes.Structure):
        _fields_ = [
            ('ReadOperationCount',  ctypes.c_uint64),
            ('WriteOperationCount', ctypes.c_uint64),
            ('OtherOperationCount', ctypes.c_uint64),
            ('ReadTransferCount',   ctypes.c_uint64),
            ('WriteTransferCount',  ctypes.c_uint64),
            ('OtherTransferCount',  ctypes.c_uint64),
        ]

    class _JOBOBJECT_BASIC_LIMIT_INFORMATION(ctypes.Structure):
        _fields_ = [
            ('PerProcessUserTimeLimit', ctypes.c_int64),
            ('PerJobUserTimeLimit',     ctypes.c_int64),
            ('LimitFlags',              DWORD),
            ('MinimumWorkingSetSize',   ctypes.c_size_t),
            ('MaximumWorkingSetSize',   ctypes.c_size_t),
            ('ActiveProcessLimit',      DWORD),
            ('Affinity',                ctypes.c_size_t),
            ('PriorityClass',           DWORD),
            ('SchedulingClass',         DWORD),
        ]

    class _JOBOBJECT_EXTENDED_LIMIT_INFORMATION(ctypes.Structure):
        _fields_ = [
            ('BasicLimitInformation', _JOBOBJECT_BASIC_LIMIT_INFORMATION),
            ('IoInfo',                _IO_COUNTERS),
            ('ProcessMemoryLimit',    ctypes.c_size_t),
            ('JobMemoryLimit',        ctypes.c_size_t),
            ('PeakProcessMemoryUsed', ctypes.c_size_t),
            ('PeakJobMemoryUsed',     ctypes.c_size_t),
        ]

    JobObjectExtendedLimitInformation = 9
    JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x2000

    _kernel32.SetInformationJobObject.restype  = BOOL
    _kernel32.SetInformationJobObject.argtypes = [
        HANDLE, ctypes.c_int,
        ctypes.c_void_p, DWORD,
    ]

    _kernel32.AssignProcessToJobObject.restype  = BOOL
    _kernel32.AssignProcessToJobObject.argtypes = [HANDLE, HANDLE]

    _kernel32.TerminateJobObject.restype  = BOOL
    _kernel32.TerminateJobObject.argtypes = [HANDLE, DWORD]

    class _JOBOBJECT_BASIC_ACCOUNTING_INFORMATION(ctypes.Structure):
        _fields_ = [
            ('TotalUserTime',             ctypes.c_int64),
            ('TotalKernelTime',           ctypes.c_int64),
            ('ThisPeriodTotalUserTime',   ctypes.c_int64),
            ('ThisPeriodTotalKernelTime', ctypes.c_int64),
            ('TotalPageFaultCount',       DWORD),
            ('TotalProcesses',            DWORD),
            ('ActiveProcesses',           DWORD),
            ('TotalTerminatedProcesses',  DWORD),
        ]

    JobObjectBasicAccountingInformation = 1

    _kernel32.QueryInformationJobObject.restype  = BOOL
    _kernel32.QueryInformationJobObject.argtypes = [
        HANDLE, ctypes.c_int,
        ctypes.c_void_p, DWORD,
        ctypes.POINTER(DWORD),
    ]

    _kernel32.TerminateProcess.restype  = BOOL
    _kernel32.TerminateProcess.argtypes = [HANDLE, DWORD]


# ---------------------------------------------------------------------------
# ProcessOwner
# ---------------------------------------------------------------------------

class ContainmentError(Exception):
    """
    Raised when process containment or resume fails during spawn.

    cleanup_confirmed: True  — child was proven stopped before raise.
    cleanup_confirmed: False — child state is unknown; caller must retain lock.
    child_pid:         int or None — PID of the spawned child if known before failure.
    child_identity:    str or None — creation-time identity for the child PID, if available.
    """
    def __init__(self, message, cleanup_confirmed=True, child_pid=None, child_identity=None):
        super().__init__(message)
        self.cleanup_confirmed = cleanup_confirmed
        self.child_pid = child_pid
        self.child_identity = child_identity


class ProcessOwner:
    """
    Owns a single child process and its entire descendant tree.

    On Windows, containment is via a Job Object with KILL_ON_JOB_CLOSE.
    On POSIX, containment is via a new session / process group.

    Usage:
        owner = ProcessOwner()
        proc = owner.spawn(args, **popen_kwargs)   # raises ContainmentError on failure
        # ... use proc as normal Popen object ...
        terminated, uncertain = owner.terminate(grace=8)
        # terminated=True, uncertain=False: all processes confirmed gone
        # terminated=False, uncertain=True: cannot confirm; caller retains lock
    """

    def __init__(self):
        self._proc = None
        self._job_handle = None   # Windows only
        self._pgid = None         # POSIX only

    # ------------------------------------------------------------------
    # Spawn
    # ------------------------------------------------------------------

    def spawn(self, args, **kwargs):
        """
        Spawn args with process-tree containment.

        On Windows: spawn SUSPENDED, assign to Job Object, resume primary thread.
        On POSIX: add start_new_session=True; record pgid after spawn.

        Returns the Popen object.  Raises ContainmentError if containment or
        resume fails (child is reaped/terminated and job closed before raise).
        """
        if os.name == 'nt':
            return self._spawn_windows(args, **kwargs)
        else:
            return self._spawn_posix(args, **kwargs)

    def _spawn_windows(self, args, **kwargs):
        CREATE_SUSPENDED   = 0x00000004
        CREATE_NO_WINDOW   = 0x08000000
        existing_flags = kwargs.pop('creationflags', 0)
        kwargs['creationflags'] = existing_flags | CREATE_SUSPENDED | CREATE_NO_WINDOW

        job_handle = None
        proc = None
        try:
            job_handle = _create_job_with_kill_on_close()
            proc = subprocess.Popen(args, **kwargs)
            self._proc = proc
            # Assign proc._handle (the process HANDLE already opened by Popen) to job.
            _assign_to_job(job_handle, proc)
            # Resume primary thread from suspended state.
            _resume_primary_thread(proc.pid)
            self._job_handle = job_handle
            return proc
        except Exception as exc:
            # Containment/resume failed: kill the suspended/partially-started child.
            # Since child was spawned SUSPENDED, normal termination is safe: kill+wait.
            # cleanup_confirmed only if kill+wait both succeed.
            # Capture child PID and creation identity BEFORE dropping proc.
            child_pid = proc.pid if proc is not None else None
            child_identity = None
            if child_pid is not None:
                try:
                    from _controller_state import get_process_creation_identity
                    child_identity = get_process_creation_identity(child_pid)
                except Exception:
                    pass
            child_cleanup_confirmed = False
            if proc is not None:
                kill_ok = False
                try:
                    proc.kill()
                    kill_ok = True
                except OSError:
                    pass
                if kill_ok:
                    try:
                        proc.wait(timeout=3)
                        child_cleanup_confirmed = True
                    except Exception:
                        pass
            if job_handle is not None:
                try:
                    _kernel32.CloseHandle(job_handle)
                except Exception:
                    pass
            self._proc = None
            self._job_handle = None
            raise ContainmentError(
                f'Windows containment failed: {exc}',
                cleanup_confirmed=child_cleanup_confirmed,
                child_pid=child_pid,
                child_identity=child_identity,
            ) from exc

    def _spawn_posix(self, args, **kwargs):
        kwargs['start_new_session'] = True
        proc = subprocess.Popen(args, **kwargs)
        self._proc = proc
        try:
            self._pgid = os.getpgid(proc.pid)
        except OSError:
            self._pgid = None
        return proc

    # ------------------------------------------------------------------
    # Terminate
    # ------------------------------------------------------------------

    def terminate(self, grace=8):
        """
        Terminate the owned process group and confirm all processes have exited.

        Returns (terminated: bool, uncertain: bool).
        Never raises.
        On unexpected API/logic exceptions returns (False, True): caller retains lock.
        Root process exit alone is NOT group proof; absent job/pgid without proof is unknown.
        """
        if self._proc is None:
            return True, False
        try:
            if os.name == 'nt':
                return self._terminate_windows(grace)
            else:
                return self._terminate_posix(grace)
        except Exception:
            # Do not infer children stopped from root exit alone; return uncertain.
            return False, True

    def _terminate_windows(self, grace):
        deadline = time.monotonic() + grace
        # Terminate owned Job Object group first (strongest guarantee).
        if self._job_handle is not None:
            try:
                _kernel32.TerminateJobObject(self._job_handle, 1)
            except Exception:
                pass
        # Also kill root process directly as a fallback.
        try:
            self._proc.kill()
        except OSError:
            pass
        # Wait for root exit.
        terminated, uncertain = _wait_for_exit(self._proc, deadline)
        if not terminated:
            return False, True
        # Confirm active processes in job reach zero.
        if self._job_handle is not None:
            confirmed = _wait_active_zero(self._job_handle, deadline)
            if not confirmed:
                return False, True
            # Clean up the job handle now that all processes are gone.
            try:
                _kernel32.CloseHandle(self._job_handle)
            except Exception:
                pass
            self._job_handle = None
        return True, False

    def _terminate_posix(self, grace):
        import signal
        deadline = time.monotonic() + grace
        pgid = self._pgid
        if pgid is not None:
            try:
                os.killpg(pgid, signal.SIGKILL)
            except (ProcessLookupError, OSError):
                pass
        try:
            self._proc.kill()
        except OSError:
            pass
        terminated, uncertain = _wait_for_exit(self._proc, deadline)
        if not terminated:
            return False, True
        # Check group liveness independently (group may have survived parent exit).
        if pgid is not None:
            group_gone = _posix_group_gone(pgid, deadline)
            if not group_gone:
                return False, True
        return True, False


# ---------------------------------------------------------------------------
# Windows Job Object helpers
# ---------------------------------------------------------------------------

def _create_job_with_kill_on_close():
    """Create a non-inheritable Job Object with KILL_ON_JOB_CLOSE."""
    handle = _kernel32.CreateJobObjectW(None, None)
    if not handle:
        err = ctypes.get_last_error()
        raise OSError(f'CreateJobObjectW failed: error {err}')
    info = _JOBOBJECT_EXTENDED_LIMIT_INFORMATION()
    info.BasicLimitInformation.LimitFlags = JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
    ok = _kernel32.SetInformationJobObject(
        handle,
        JobObjectExtendedLimitInformation,
        ctypes.byref(info),
        ctypes.sizeof(info),
    )
    if not ok:
        err = ctypes.get_last_error()
        _kernel32.CloseHandle(handle)
        raise OSError(f'SetInformationJobObject failed: error {err}')
    # Make handle non-inheritable.  CreateJobObjectW with NULL security attributes
    # already returns a non-inheritable handle on Vista+; explicit SetHandleInformation
    # for safety.
    HANDLE_FLAG_INHERIT = 0x00000001
    SetHandleInformation = ctypes.windll.kernel32.SetHandleInformation
    SetHandleInformation.restype  = ctypes.wintypes.BOOL
    SetHandleInformation.argtypes = [ctypes.c_void_p, ctypes.wintypes.DWORD, ctypes.wintypes.DWORD]
    SetHandleInformation(handle, HANDLE_FLAG_INHERIT, 0)
    return handle


def _assign_to_job(job_handle, proc):
    """Assign proc to job_handle using proc._handle (set by Popen on Windows)."""
    proc_handle = getattr(proc, '_handle', None)
    if proc_handle is None:
        raise OSError('proc._handle is None; cannot assign to job')
    ok = _kernel32.AssignProcessToJobObject(job_handle, int(proc_handle))
    if not ok:
        err = ctypes.get_last_error()
        # ERROR_ACCESS_DENIED (5) can occur when process is already in a job
        # (nested job support only on Win8+).  Treat as containment failure.
        raise OSError(f'AssignProcessToJobObject failed: error {err}')


def _resume_primary_thread(pid):
    """
    Find the single primary thread of suspended PID via Toolhelp32 snapshot
    and call ResumeThread.  Raises on failure.
    """
    INVALID_HANDLE_VALUE = ctypes.c_void_p(-1).value
    snap = _kernel32.CreateToolhelp32Snapshot(TH32CS_SNAPTHREAD, 0)
    if snap == INVALID_HANDLE_VALUE or snap is None:
        err = ctypes.get_last_error()
        raise OSError(f'CreateToolhelp32Snapshot failed: error {err}')
    thread_ids = []
    try:
        entry = _THREADENTRY32()
        entry.dwSize = ctypes.sizeof(_THREADENTRY32)
        ok = _kernel32.Thread32First(snap, ctypes.byref(entry))
        while ok:
            if entry.th32OwnerProcessID == pid:
                thread_ids.append(entry.th32ThreadID)
            entry.dwSize = ctypes.sizeof(_THREADENTRY32)
            ok = _kernel32.Thread32Next(snap, ctypes.byref(entry))
    finally:
        _kernel32.CloseHandle(snap)

    if not thread_ids:
        raise OSError(f'No threads found for PID {pid}')
    if len(thread_ids) != 1:
        # Multiple threads before resume is unexpected for a freshly-suspended child.
        # Use the first thread (the primary) but do not fail.
        pass
    tid = thread_ids[0]
    th = _kernel32.OpenThread(THREAD_SUSPEND_RESUME, False, tid)
    if not th:
        err = ctypes.get_last_error()
        raise OSError(f'OpenThread({tid}) failed: error {err}')
    try:
        result = _kernel32.ResumeThread(th)
        if result == 0xFFFFFFFF:
            err = ctypes.get_last_error()
            raise OSError(f'ResumeThread failed: error {err}')
    finally:
        _kernel32.CloseHandle(th)


def _wait_active_zero(job_handle, deadline, poll_interval=0.05):
    """Poll QueryInformationJobObject until ActiveProcesses == 0 or deadline."""
    acct = _JOBOBJECT_BASIC_ACCOUNTING_INFORMATION()
    returned = ctypes.wintypes.DWORD(0)
    while time.monotonic() < deadline:
        ok = _kernel32.QueryInformationJobObject(
            job_handle,
            JobObjectBasicAccountingInformation,
            ctypes.byref(acct),
            ctypes.sizeof(acct),
            ctypes.byref(returned),
        )
        if ok and acct.ActiveProcesses == 0:
            return True
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            break
        time.sleep(min(poll_interval, remaining))
    return False


# ---------------------------------------------------------------------------
# POSIX helpers
# ---------------------------------------------------------------------------

def _posix_group_gone(pgid, deadline):
    """Return True when POSIX process group pgid has no live members."""
    import signal
    while time.monotonic() < deadline:
        try:
            os.killpg(pgid, 0)
        except ProcessLookupError:
            return True
        except PermissionError:
            return False  # processes exist, we just can't signal them
        time.sleep(0.05)
    try:
        os.killpg(pgid, 0)
        return False
    except ProcessLookupError:
        return True
    except OSError:
        return False


# ---------------------------------------------------------------------------
# Shared wait helper
# ---------------------------------------------------------------------------

def _wait_for_exit(proc, deadline):
    """Poll proc until it exits or deadline passes. Returns (terminated, uncertain)."""
    while time.monotonic() < deadline:
        proc.poll()
        if proc.returncode is not None:
            return True, False
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            break
        try:
            proc.wait(timeout=min(remaining, 0.5))
            return True, False
        except subprocess.TimeoutExpired:
            continue
        except Exception:
            break
    proc.poll()
    if proc.returncode is not None:
        return True, False
    return False, True


# ---------------------------------------------------------------------------
# Legacy helpers (kept for callers that spawn via plain Popen)
# ---------------------------------------------------------------------------

def posix_spawn_kwargs():
    """Extra Popen kwargs for POSIX process-group containment (no-op on Windows)."""
    if os.name == 'nt':
        return {}
    return {'start_new_session': True}


def kill_tree(proc, grace=8):
    """
    Kill proc and all descendants.  Legacy helper; uses taskkill on Windows
    (not Job Object) since no job handle is available.

    Returns (terminated: bool, uncertain: bool).  Never raises.
    """
    if proc is None:
        return True, False
    pid = getattr(proc, 'pid', None)
    if pid is None:
        return True, False
    try:
        if os.name == 'nt':
            return _kill_tree_windows(proc, pid, grace)
        else:
            return _kill_tree_posix(proc, pid, grace)
    except Exception:
        proc.poll()
        if proc.returncode is not None:
            return True, False
        return False, True


def _kill_tree_windows(proc, pid, grace):
    deadline = time.monotonic() + grace
    try:
        subprocess.run(
            ['taskkill', '/F', '/T', '/PID', str(pid)],
            capture_output=True,
            timeout=max(grace - 1, 3),
        )
    except Exception:
        pass
    try:
        proc.kill()
    except OSError:
        pass
    return _wait_for_exit(proc, deadline)


def _kill_tree_posix(proc, pid, grace):
    import signal
    deadline = time.monotonic() + grace
    try:
        pgid = os.getpgid(pid)
        os.killpg(pgid, signal.SIGKILL)
    except (ProcessLookupError, OSError):
        pass
    try:
        proc.kill()
    except OSError:
        pass
    return _wait_for_exit(proc, deadline)
