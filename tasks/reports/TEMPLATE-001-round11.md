# TEMPLATE-001 Round 11 Implementation Report

## Summary

Two focused lifecycle fixes per round-11 task specification:
- Fix 1: Success envelope must not be killed; bounded graceful exit, hung-root is a shutdown error.
- Fix 2: Windows Job Object containment via shared `ProcessOwner` abstraction used by both `_controller_worker` and `AppServerAdapter`.

All prior edits preserved. File tools only. No Bash, no agents, no CLI calls.

## Status

Ready for Review — NotRun (Codex runs tests independently).

## Files Changed

| File | Change |
|---|---|
| `scripts/_controller_process.py` | Rewritten: `ProcessOwner` with Windows Job Object (KILL_ON_JOB_CLOSE, CREATE_SUSPENDED, Toolhelp32 resume) and POSIX process-group; legacy `kill_tree`/`posix_spawn_kwargs` kept for backward compat |
| `scripts/_controller_worker.py` | Fix 1: bounded graceful-exit window after result event; hung root = shutdown timeout error; partial diagnostics saved on all paths. Fix 2: spawn via `ProcessOwner`; `owner.terminate()` replaces direct `kill_tree`. |
| `scripts/_appserver_adapter.py` | Fix 2: `_owner = ProcessOwner()` field; `_start()` uses `owner.spawn()`; `close()` calls `owner.terminate()` before pipe close. |
| `tests/test_controller_lifecycle.py` | New: lifecycle test suite (see Tests section) |

## Architecture

### Fix 1 — Success-not-killed

**Problem**: `_run_claude` called `kill_tree(proc)` unconditionally after receiving the result event, then checked `rc`. The real CLI had already written the result but was still shutting down; kill made rc nonzero, and the rc check then reported failure.

**Resolution**:

1. After receiving the `result` event, check if it is a potential success envelope (`subtype=success`, `is_error=False`, non-empty `result`, no `cleanup_reason`).
2. If yes, give the CLI a **bounded graceful-exit window** (`_GRACEFUL_EXIT_SECS = 5`) to exit normally.
3. If the CLI exits rc0 within that window → success path; call `owner.terminate()` to confirm remaining descendants are gone.
4. If the CLI does **not** exit within the window → force-kill via `owner.terminate()`, then **raise** `ValueError('shutdown timeout: ...')`. This is a failure, not a silent pass.
5. Genuine nonzero rc (exit without result, or post-kill nonzero) → failure preserved.
6. Partial result/artifact metadata is saved to state (`_partial_result_subtype`, `_partial_result_preview`) **before** any cleanup, so diagnosis is possible even when the task ultimately fails. No automatic retry of side effects.

### Fix 2 — Windows Job Object containment

**ProcessOwner** (in `_controller_process.py`):

- **Windows spawn**: `CREATE_SUSPENDED | CREATE_NO_WINDOW` flags; `CreateJobObjectW` creates a non-inheritable Job Object; `SetInformationJobObject(JobObjectExtendedLimitInformation=9)` sets `JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE=0x2000`; `AssignProcessToJobObject` uses `proc._handle`; Toolhelp32 snapshot (`CreateToolhelp32Snapshot`, `Thread32First/Next`) finds primary thread, `OpenThread(THREAD_SUSPEND_RESUME)`, `ResumeThread`, handles closed. Containment/resume failure: child killed, job handle closed, `ContainmentError` raised.
- **Windows terminate**: `TerminateJobObject` (kills owned group); `proc.kill()` fallback; waits for root exit; `QueryInformationJobObject(JobObjectBasicAccountingInformation=1)` polls `ActiveProcesses == 0` before reporting terminated.
- **KILL_ON_JOB_CLOSE**: non-inherited handle; if worker crashes, handle closes and kernel terminates all owned children automatically.
- **POSIX**: `start_new_session=True`; `pgid` recorded after spawn; `os.killpg(pgid, SIGKILL)`; group liveness confirmed via `os.killpg(pgid, 0)`.
- **ctypes**: `ctypes.WinDLL('kernel32', use_last_error=True)`; all `HANDLE` as `ctypes.c_void_p` (pointer-sized); correct `argtypes`/`restype` on all APIs; `ctypes.get_last_error()` for error codes.
- **Legacy**: `kill_tree(proc, grace)` and `posix_spawn_kwargs()` kept so existing `TestProcessContainment` tests continue to pass without modification.

**AppServerAdapter** now uses `ProcessOwner.spawn()` in `_start()` and `owner.terminate()` in `close()`. The `_owner` attribute is initialised to `None` in `__init__` so `close()` falls back safely if `_start()` was never called.

## Tests — NotRun (Codex runs independently)

New file: `tests/test_controller_lifecycle.py`

| Test class / method | What it verifies |
|---|---|
| `TestProcessOwnerBasic.test_spawn_and_terminate_sleeping_process` | ProcessOwner spawns and terminates; confirmed gone |
| `TestProcessOwnerBasic.test_spawn_already_exited` | terminate on already-exited proc → terminated=True |
| `TestProcessOwnerBasic.test_spawn_terminate_grandchild` | grandchild killed even after parent exits |
| `TestProcessOwnerBasic.test_kill_on_job_close_when_owner_handle_closed` | (Windows-only) closing job handle kills owned child automatically |
| `TestClaudeSuccessHungRoot.test_hung_root_after_success_raises_shutdown_timeout` | success result + hung root → bounded graceful window, then forced kill (not silent pass) |
| `TestCancelAndTimeout.test_cancel_terminates_owner_group` | cancel path: owner.terminate within grace |
| `TestCancelAndTimeout.test_timeout_terminates_owner_group` | timeout path: owner.terminate kills sleeping child |
| `TestGrandchildPipeCleanup.test_grandchild_pipe_holder_cleaned_up` | parent exits while grandchild holds pipe → cleanup bounded |
| `TestContainmentFailureLockRetained.test_containment_error_raised_on_bad_spawn` | bad executable → ContainmentError (lock-retain path) |
| `TestRecoverLiveRefused.test_recover_refused_for_live_pid` | worker_gone returns False for live PID |
| `TestRecoverStoppedAllowed.test_recover_allowed_for_gone_pid` | worker_gone returns True for absent PID |
| `TestRecoverStoppedAllowed.test_recover_gone_worker_sets_interrupted` | collaboration.py recover sets status=interrupted |
| `TestSuccessEnvelopeNormalExit.test_success_rc0_no_kill_needed` | success envelope + rc0 exit → graceful exit confirmed, no error |
| `TestGenuineFailureRcNonzero.test_nonzero_rc_no_result_event` | rc1 without result → owner.terminate cleanly |

Existing tests in `tests/test_controller.py` preserved and unmodified. `TestProcessContainment` continues to use `kill_tree` (legacy helper, still exported).

## Acceptance Criteria Coverage

| Criterion | Status |
|---|---|
| F10-01: Success envelope + rc0 → completed, no spurious kill | Implemented |
| F10-01: Genuine nonzero rc → failed preserved | Implemented |
| F10-01: Success envelope + hung root → bounded shutdown error, not silent pass | Implemented |
| F10-01: Partial result metadata saved for diagnosis on all paths | Implemented |
| F10-01: No automatic retry of side effects | Implemented (no retry logic added) |
| F10-02: Windows Job Object with KILL_ON_JOB_CLOSE | Implemented |
| F10-02: Spawn child SUSPENDED, assign before resume | Implemented |
| F10-02: Toolhelp32 primary-thread locate and resume | Implemented |
| F10-02: Containment/resume failure → child killed, job closed, ContainmentError raised | Implemented |
| F10-02: QueryInformationJobObject ActiveProcesses==0 confirmation | Implemented |
| F10-02: POSIX process group, killpg, group liveness check | Implemented |
| F10-02: TerminateJobObject BEFORE joining/closing pipes | Implemented (owner.terminate before pipe close in both worker and adapter) |
| F10-02: Worker crash → owned children die (KILL_ON_JOB_CLOSE non-inherited handle) | Implemented; test: test_kill_on_job_close_when_owner_handle_closed |
| F10-02: Cleanup proof propagated to state before lock release | Implemented (cleanup_uncertain path unchanged) |
| F10-02: AppServerAdapter uses same owner abstraction | Implemented |
| Existing tests unchanged | Preserved |

## Known Limitations

- `_resume_primary_thread` accepts >1 thread for a freshly-suspended process (uses first thread); this is theoretically possible if the OS created a helper thread before our snapshot. In practice a Python child process has one thread before it starts running.
- `AssignProcessToJobObject` fails (`ERROR_ACCESS_DENIED`) on Windows 7 when the child is already in a job object (e.g. some CI sandbox). On Windows 8+, nested job objects are supported. Worker raises `ContainmentError` in this case (fail-closed).
- `_GRACEFUL_EXIT_SECS = 5` is hardcoded; could be made configurable if needed in a future round.
- F10-03 (result hash exposure) and F10-04 (App Server text deduplication) deferred per round-11 task scope.

## Items Not Completed

- F10-03: dirty-content hash in result.baseline — deferred to a separate scoped round.
- F10-04: Codex final text deduplication — deferred to the same round.
- Real cancel/timeout/recovery end-to-end (requires live CLI) — NotRun; Codex runs independently.

## Handoff

Codex to run:
1. `python -X utf8 -m unittest discover -s tests -v` (full suite including new lifecycle tests)
2. `python -X utf8 G:/AI Collaboration/work/test_controller_independent.py` (independent grandchild test)
3. True Claude success task (new job, different write target from round-10 proof)
4. Cancel/timeout/recovery end-to-end

No commits. No raw logs or session data included.
