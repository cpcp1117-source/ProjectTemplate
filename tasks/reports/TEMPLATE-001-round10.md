# TEMPLATE-001 Round 10 Implementation Report

## Summary

Five ordered repair groups from the Round 10 review. All changes additive to existing dirty state. No existing changes reverted. File tools only; no Bash or agent invocations.

## Status

Ready for Review — NotRun (Codex runs tests independently).

## Files Changed

| File | Change |
|---|---|
| `scripts/_controller_process.py` | NEW: shared process-tree kill helper (`kill_tree`, `posix_spawn_kwargs`) |
| `scripts/_appserver_adapter.py` | Fix 1+3: kill-before-pipe, `__enter__` cleanup, `_recv_response` timeout, ID matching, server request handling |
| `scripts/_controller_worker.py` | Fix 1+2: kill_tree before drain, fail-closed lock, snapshot no-fallback, `_build_claude_prompt` no task_file ref, `_run_codex` saves result_text |
| `scripts/collaboration.py` | Fix 2: `_validate_environment` calls `load_config`, `_capture_dirty_hashes` NUL/deletions, `_save_task_snapshot` `is_relative_to`, snapshot_hash in state/result, child_pid in `recover` |
| `tests/test_controller.py` | Fix 1–3: replace `test_kill_proc_tree_does_not_raise`, add `TestProcessContainment`, `TestAppServerAdapterRound10`, `TestWorkerSnapshotIntegration` |
| `docs/controller-usage.md` | Fix 5: fail-closed lock description, Windows safe-stopping section, snapshot section updated |
| `README.md` | Fix 5: status updated to round10, safe stopping/recovery steps, no permanent chat bridge claim |

## Ordered Fixes

### Fix 1 — Process Tree Containment (Priority 1)

**Problem**: `test_child_holds_pipe_after_parent_result` took 5.14s. `close()` closed pipes before killing child, so the child held its write end of stdout open and the reader thread blocked. `_kill_proc_tree` swallowed all errors and returned None (no cleanup proof). Main `finally` unconditionally released lock.

**Root cause**: `close()` sequence was stdin→stdout→stderr→wait. Closing parent's read end of stdout while child still holds write end does not interrupt the reader thread on Windows. Child's 5s sleep held the pipe open.

**Changes**:

`scripts/_controller_process.py` (new):
- `kill_tree(proc, grace)` → `(terminated: bool, uncertain: bool)`. Windows: `taskkill /F /T /PID` then `proc.kill()` fallback; polls for exit confirmation within grace. POSIX: `os.killpg` then `proc.kill()`. Never raises.
- `posix_spawn_kwargs()`: returns `{'start_new_session': True}` on POSIX for process-group kill; no-op on Windows.

`scripts/_appserver_adapter.py`:
- `close()`: calls `kill_tree(proc, grace=5)` FIRST (killing child closes its write end of stdout → reader thread gets EOF), THEN closes pipes. Sequence is now: kill→wait→close pipes.
- `_start()`: adds `posix_spawn_kwargs()` on non-Windows for process-group containment.
- `__enter__`: wraps `_start()` in try/except; calls `self.close()` on failure so child is not leaked.

`scripts/_controller_worker.py`:
- Removed `_kill_proc_tree()` (which returned None, swallowed all errors).
- `_run_claude()`: calls `kill_tree(proc, grace=_CLEANUP_GRACE)` to kill process tree BEFORE draining stderr or closing pipes. Returns `cleanup_uncertain: bool`.
- `main()` finally block: reads state to check `cleanup_required`; only releases lock if cleanup was NOT uncertain. Sets `cleanup_required=True` in state when `kill_tree` returns `uncertain=True`.
- `_run_claude()` and `_run_codex()` both use `posix_spawn_kwargs()` for process-group containment on POSIX.

**Tests** (`TestProcessContainment`):
- `test_kill_tree_already_finished_returns_terminated`: kill_tree on exited process → `(True, False)`.
- `test_kill_tree_running_process_returns_terminated`: kill_tree on sleeping process → terminated=True.
- `test_kill_tree_kills_grandchild`: parent spawns grandchild; kill_tree terminates both.
- `test_kill_tree_cancel_cleanup_bounded`: completes within grace period.

**Tests** (`TestAppServerAdapterRound10`):
- `test_enter_cleanup_on_init_failure`: `__enter__` cleans up child on init exception.

### Fix 2 — Snapshot Partial (Priority 2)

**Problem**: Worker fell back to mutable original task file when snapshot missing. `_build_claude_prompt` referenced original `task_file` in else branch. `prompt_file` used `str.startswith` for containment (sibling-prefix bypass). `_validate_environment` did not call `load_config`. `_capture_dirty_hashes` used non-NUL git output (broke CJK/special-char paths), skipped deletions, skipped staged changes. Snapshot hash not exposed in state/result.

**Changes** (`scripts/_controller_worker.py`):
- Removed fallback: if `task_snapshot.json` missing → `raise ValueError('task snapshot missing ...')` → `_set_failed` + `release_lock` + `sys.exit(1)`. Happens before `load_config` call so test root does not need full config.
- `_build_claude_prompt(task_data)`: removed `task_file` parameter; else branch now says `'\n\nNo additional prompt provided; work from the task objective above.'` instead of referencing mutable `task_file`.

**Changes** (`scripts/collaboration.py`):
- `_save_task_snapshot()`: uses `pf_resolved.relative_to(root_resolved)` (raises `ValueError` on outside path) instead of `str.startswith`. Missing or outside-root `prompt_file` raises `ValueError` at save time (surfaced as `_error_exit` in `_cmd_start`). Returns `(snap_path, snapshot_hash)`.
- `_cmd_start()`: unpacks `(snap_path, snapshot_hash)`, writes `snapshot_hash` to state via `write_state_atomic`.
- `_public_result()`: includes `snapshot_hash` field.
- `_validate_environment()`: calls `load_config(root)` before `create_job`; errors via `_error_exit`. Happens after `_load_and_validate_task` so all existing failure tests are unaffected.
- `_capture_dirty_hashes()`: four git commands with `-z` NUL delimiter (handles CJK, spaces, quotes without C-quoting). Includes `git diff --cached` (staged). Includes `git ls-files --deleted`. Missing files → `'__DELETED__'` marker. Excludes `.git` and `.collaboration/` (credentials/logs).

**Tests** (`TestWorkerSnapshotIntegration`):
- `test_worker_fails_on_missing_snapshot`: worker exits nonzero, state=failed, error mentions snapshot.
- `test_worker_fails_on_tampered_snapshot`: worker exits nonzero, state=failed, error mentions tamper.
- `test_snapshot_hash_returned_from_save`: returned hash matches `_snapshot_hash` in file.
- `test_mutated_original_task_does_not_affect_snapshot`: snapshot content unchanged after original mutation.
- `_save_snapshot_via_collab` helper updated to unpack `(snap_path, hash)` tuple.

### Fix 3 — App Server (Priority 3)

**Problem**: Terminal notifications not checked against threadId/turnId. `_recv_response` raised `AppServerError` on any 5s `queue.Empty` even when overall timeout longer. Official server requests (`item/commandExecution/requestApproval`, `item/fileChange/requestApproval`) not recognized; unknown id+method disappeared/hung instead of getting JSONRPC error.

**Changes** (`scripts/_appserver_adapter.py`):
- `_recv_response()`: `queue.Empty` → `continue` (recheck deadline at top of loop). No longer raises on 5s chunks.
- `run_turn()`: terminal notifications (`turn/completed`, `turn/failed`, `turn/interrupted`) check `params.get('threadId') == thread_id`. If `turn_id` already known (from response), also checks `turn.get('id') == turn_id`. Unrelated-thread completions are skipped.
- `run_turn()`: distinguishes server-to-client requests (has both `method` and `id`) from notifications (method only) and responses (id only). Routes to `_handle_server_request()`.
- `_handle_server_request()` (new, replaces `_decline_approval`): methods in `_DECLINE_APPROVAL_METHODS` → `{"id": id, "result": {"decision": "decline"}}`. Other unknown methods → JSONRPC error `{"id": id, "error": {"code": -32601, "message": "Method not supported: ..."}}`.
- `_DECLINE_APPROVAL_METHODS`: `item/commandExecution/requestApproval`, `item/fileChange/requestApproval`, `item/permissions/requestApproval`, `applyPatchApproval`, `execCommandApproval`, `approval/request`.

**Tests** (`TestAppServerAdapterRound10`):
- `test_recv_response_continues_past_5s_queue_empty`: server takes 6s for `thread/start` response; timeout=12s → succeeds.
- `test_unrelated_thread_completion_ignored`: wrong-threadId notification ignored; correct-threadId notification accepted.
- `test_command_execution_approval_declined`: `item/commandExecution/requestApproval` → decline response.
- `test_file_change_approval_declined`: `item/fileChange/requestApproval` → decline response.
- `test_unknown_server_request_gets_jsonrpc_error`: `item/tool/call` → JSONRPC error code -32601.
- `test_actual_final_text_nonempty`: `run_turn` returns actual agent text from server.

### Fix 4 — Legacy Test (Priority 4)

Comment-only fix (round 9); `test_kill_proc_tree_does_not_raise` replaced rather than kept:

The old test validated wrong behavior (swallowing all errors). Replaced with `TestProcessContainment` which uses `kill_tree` from `_controller_process` and asserts meaningful return values (`terminated`, `uncertain`). The old `cw._kill_proc_tree` helper no longer exists in the worker (removed; superseded by `_controller_process.kill_tree`).

### Fix 5 — Docs (Priority 5)

`docs/controller-usage.md`:
- "Lock is not released while uncertainty exists" — now actually implemented; described accurately.
- Fail-closed lock behavior described (cleanup_required flag, recover usage).
- Windows Safe Stopping section added with step-by-step commands for cancel→kill→recover.
- Task Snapshot section updated: NUL-delimited hashes, `is_relative_to` containment, `__DELETED__` markers, snapshot_hash in state/result, no-fallback policy.

`README.md`:
- Status banner updated to round10 pending Codex verification.
- Safe stopping/recovery steps added to Error Recovery section.
- No permanent chat bridge claim (user stays in Codex chat).
- Tests section updated.

## Acceptance Criteria Coverage

| Criterion | Status |
|---|---|
| 1. `test_child_holds_pipe_after_parent_result` ≤4s: kill tree before pipe close | Implemented |
| 1. `_kill_proc_tree` returns meaningful status; not swallowed | Implemented (`kill_tree` in `_controller_process`) |
| 1. Lock retained when cleanup uncertain (`cleanup_required`) | Implemented |
| 1. `__enter__` init failure cleans up | Implemented |
| 1. Grandchild pipe test | Implemented (`test_kill_tree_kills_grandchild`) |
| 2. No fallback to mutable task file in worker | Implemented |
| 2. `_build_claude_prompt` does not reference original task_file | Implemented |
| 2. `prompt_file` containment via `is_relative_to` | Implemented |
| 2. `_validate_environment` calls `load_config` | Implemented |
| 2. `_capture_dirty_hashes` NUL-delimited, deletions, staged | Implemented |
| 2. Snapshot hash in state/result | Implemented |
| 3. Terminal notifications matched by threadId | Implemented |
| 3. `_recv_response` continues past 5s queue.Empty | Implemented |
| 3. `item/commandExecution/requestApproval` declined | Implemented |
| 3. `item/fileChange/requestApproval` declined | Implemented |
| 3. Unknown id+method gets JSONRPC -32601 error | Implemented |
| 3. `_run_codex` saves bounded result_text | Implemented (saves before status check) |
| 4. `test_kill_proc_tree_does_not_raise` replaced with meaningful tests | Implemented |
| 5. False cleanup claim removed; fail-closed described accurately | Implemented |
| 5. README round10 status | Implemented |
| 5. Windows safe stopping/recovery | Implemented |

## Tests (NotRun — Codex runs independently)

New test classes added to `tests/test_controller.py`:
- `TestProcessContainment` (4 tests): kill_tree return values, grandchild termination, bounded cleanup
- `TestAppServerAdapterRound10` (7 tests): recv_response timeout, ID matching, approval declines, JSONRPC error, final text, enter cleanup
- `TestWorkerSnapshotIntegration` (4 tests): missing snapshot → failed, tampered → failed, hash return, mutation isolation

Existing test classes preserved. `TestTaskSnapshot._save_snapshot_via_collab` updated to unpack `(snap_path, hash)` tuple. `TestProcessLifecycleBounded` class replaced by `TestProcessContainment`.

## Known Limitations

- `_capture_dirty_hashes` requires `git` on PATH; returns empty dict silently on non-git roots.
- `_validate_environment` now calls `load_config`; tests that call `start` with a valid task but no `.collaboration/local.json` will fail at environment validation. All existing CLI failure tests fail before reaching `_validate_environment` (they fail at task validation), so existing tests are unaffected.
- `kill_tree` on Windows uses `taskkill` (standard on all Windows) and polls; uncertain=True means the lock is retained until manual recovery.
- Worker snapshot integration tests run the worker subprocess; they need the root to have state + lock created by `create_job` but NOT a full `.collaboration/local.json` (worker fails at snapshot before config load).

## Items Not Completed

- Job Object (`JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE`) not implemented; `taskkill /F /T` is used instead. Job Object would give stronger guaranteed containment but requires ctypes and is complex. `kill_tree` returns `uncertain=True` if exit cannot be confirmed, which triggers the fail-closed lock.
- `recover` could be extended to clear `cleanup_required` after PID verification, but current implementation clears it in state after confirming both PIDs are gone (which is the correct sequence).

## Handoff

Codex to run `python -X utf8 -m unittest discover -s tests -v` and verify independently.
No commits. No raw logs or session data included.
