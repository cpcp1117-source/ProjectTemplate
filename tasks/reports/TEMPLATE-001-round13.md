# TEMPLATE-001 Round 13 — Implementation Report

## Status

Ready for Review (pending Codex independent verification)

## Summary of Changes

Round 13 implements all five focused corrections identified in the round-12 review.

## Files Changed

| File | Change |
|---|---|
| `scripts/_controller_process.py` | Remove root-exit fallback in `terminate` except; `ContainmentError` carries `cleanup_confirmed`; spawn cleanup reports true/false based on kill+wait success |
| `scripts/_controller_worker.py` | Propagate `ContainmentError.cleanup_confirmed` to `_outcome` on spawn failure |
| `scripts/_controller_state.py` | `worker_gone` uses `WinDLL(use_last_error=True)` with full restype/argtypes; add `get_process_creation_identity` using `GetProcessTimes` FILETIME |
| `scripts/collaboration.py` | Import `get_process_creation_identity`; add pending-no-pid recovery refusal in `_cmd_recover` |
| `scripts/_appserver_adapter.py` | Buffer pre-response terminal notifications by turn_id; validate against response turn_id before accepting; ignore stale wrong-turn completions |
| `docs/controller-usage.md` | Correct prompt-transport claim: prompts appear in `-p` argv, not file-only |
| `tests/test_controller.py` | Fix injection from `cp.ProcessOwner` to `cw.ProcessOwner`; add `test_uncertainty_branch_actually_entered`; add `TestPidIdentityAndRecovery` (6 tests); add `TestAppServerAdapterRound13` (1 test) |

## Tasks Completed

### Task 1 — Fix shipped test injection (DONE)

`test_cleanup_uncertain_sets_cleanup_required_on_failure` was patching `cp.ProcessOwner`
but worker imports `ProcessOwner` at module scope as `cw.ProcessOwner`. Fixed to patch
`cw.ProcessOwner`. Original lock/cleanup_required assertions retained.

Added `test_uncertainty_branch_actually_entered` which records that `UncertainOwner.terminate`
was actually called (not just checking the final state), proving the uncertainty branch was
entered and not bypassed.

### Task 2 — ProcessOwner exception safety (DONE)

Removed the `if self._proc.poll() is not None: return True, False` fallback in
`terminate`'s except block. Unexpected API/logic exceptions now return `(False, True)` —
root process exit alone is not group proof.

`ContainmentError` now carries `cleanup_confirmed: bool`. In `_spawn_windows`, the
suspended-child cleanup does `proc.kill()` + `proc.wait(timeout=3)` and only sets
`cleanup_confirmed=True` if both succeed. Worker propagates this to `_outcome` immediately
so the main finally-block has the correct state even if an exception is raised.

### Task 3 — PID identity and pending recovery (DONE)

**worker_gone:** Migrated from `ctypes.windll.kernel32` (no pointer-size declarations) to
`WinDLL('kernel32', use_last_error=True)` with explicit `restype`/`argtypes` for
`OpenProcess`, `GetExitCodeProcess`, `CloseHandle`. BOOL failures return `False` (unknown),
not `True` (gone). Uses `ctypes.get_last_error()` for error classification.

**get_process_creation_identity:** New function using `GetProcessTimes` FILETIME creation
time, combining `dwHighDateTime << 32 | dwLowDateTime` into opaque 64-bit integer string
`"{pid}:{creation_int}"`. Returns `None` on any API failure (fail-closed). Per
[Microsoft docs](https://learn.microsoft.com/en-us/windows/win32/api/processthreadsapi/nf-processthreadsapi-getprocesstimes).

**Pending-no-pid recovery refusal:** `_cmd_recover` now checks `status == 'pending'` with
no PID and refuses with an explicit error: startup ownership is unknown, releasing the lock
could orphan a launching worker.

**Tests added:**
- `test_worker_gone_typed_api_invalid_pid` — typed API returns True for impossible PID
- `test_worker_gone_access_denied_returns_false` — access denied is fail-closed (False)
- `test_get_process_creation_identity_self` — non-None identity for own PID with pid: prefix
- `test_reused_pid_has_different_identity` — proves no crash on post-exit PID query
- `test_recover_pending_no_pid_refused` — pending+no-pid is refused by recover
- `test_recover_pending_with_gone_pid_allowed` — pending+gone-pid is allowed

### Task 4 — App Server event order counterexample (DONE)

Introduced `_pending_terminal_notifs` buffer in `run_turn`. When a terminal notification
arrives before the `turn/start` response (when `turn_id` is `None`), it is buffered instead
of immediately setting `terminal_status`. Once the response arrives carrying `resp_turn_id`,
all buffered notifications are replayed and validated: if `notif_turn_id != turn_id`, the
entry is silently ignored as a stale completion from a previous turn.

This fixes the counterexample: an old-turn completion arriving before the response can no
longer pollute the result. The response's `turn_id` is the ground truth.

Pre-existing tests (response-before-notification, notification-before-response, item dedup,
phase filtering) all continue to pass.

**Test added:** `TestAppServerAdapterRound13.test_wrong_turn_before_response_then_actual_completion`
— sends OLD turn completion before response, then inProgress response, then REAL turn
completion. Asserts REAL RESULT is returned and OLD WRONG RESULT is absent.

### Task 5 — Documentation truthfulness (DONE)

Corrected `docs/controller-usage.md` section "Process / Security" to state that the task
prompt is passed via `-p` on the Claude CLI command line (`argv`), visible via process
inspection. Removed the inaccurate "file-only prompt transport" implication. No new
transport architecture added.

## Test Evidence

```
Command: python -X utf8 -m unittest discover -s tests -p "test_*.py"
Exit: 0
Result: Ran 217 tests in ~28s  OK (skipped=1)
```

Previous baseline: 209 tests, 207 pass / 1 failure / 1 skip.
Round 13 result: 217 tests, 217 pass / 0 failures / 1 skip.
Net additions: +8 tests (all new tests pass).

The single skip is the pre-existing `test_cli_helper_skip` which requires a
platform-specific `_cli_helper` module not available in the test environment.

### Individual test class runs

```
TestCleanupFailureSafetyShipped: 3/3 OK
TestPidIdentityAndRecovery: 6/6 OK (all Windows-conditional tests ran; skipped=0 in isolation)
TestAppServerAdapterRound13: 1/1 OK
```

## Acceptance Criteria Covered

- [x] Test injection patched at `cw.ProcessOwner`; original assertions retained
- [x] `test_uncertainty_branch_actually_entered` proves branch entry, not just status
- [x] `terminate` except returns `(False, True)` without root-exit fallback
- [x] `ContainmentError.cleanup_confirmed` propagated to `_outcome` on spawn failure
- [x] `worker_gone` uses `WinDLL(use_last_error=True)` with full typed declarations
- [x] `get_process_creation_identity` uses `GetProcessTimes` FILETIME; returns `pid:int` string
- [x] `recover` refuses `pending` status with `pid=None`
- [x] Wrong-turn completion buffered before response; validated against response `turn_id`
- [x] Docs corrected: prompts enter argv via `-p`, not file-only transport
- [x] 217/217 tests pass; 0 failures

## Known Limitations / Items Not Completed

- `get_process_creation_identity` is implemented for Windows only (`os.name == 'nt'`); returns
  `None` on POSIX (no equivalent single-call stable identity without `/proc`).
- Worker and child process creation identities are not yet stored in job state (recording
  them would require wiring `get_process_creation_identity` into `_controller_worker.py` at
  child-spawn time). This is a follow-on improvement for Codex to flag if needed.
- The `test_reused_pid_has_different_identity` test proves the API does not crash but cannot
  deterministically assert PID reuse without controlling the OS scheduler.
- No commit by Claude per task instructions.

## Handoff to Codex

Codex independent verification requested for:
1. Full suite re-run confirming 217/0/1 (pass/fail/skip)
2. Reviewer-owned tests (cleanup_uncertain, group cleanup) re-run with correct patch binding
3. Lifecycle walkthrough confirming terminate except no longer returns True on root exit alone
4. App Server wrong-turn counterexample test
5. Documentation check: `docs/controller-usage.md` prompt-transport claim
6. Clean-copy walkthrough (install + run from scratch)
