# TEMPLATE-001 Round 9 Implementation Report

## Summary

Five ordered repairs from the Round 8 review. All changes are additive to existing dirty state; no existing changes reverted.

## Status

Ready for Review — NotRun (Codex runs tests independently).

## Files Changed

| File | Change |
|---|---|
| `scripts/invoke_probe.ps1` | Fix 4: comment wording |
| `scripts/_appserver_adapter.py` | Fix 3: completion event order, response_received flag |
| `scripts/_controller_worker.py` | Fix 1+2: bounded cleanup, snapshot verify, _kill_proc_tree |
| `scripts/collaboration.py` | Fix 2: snapshot save at start, _capture_dirty_hashes |
| `tests/test_controller.py` | New: snapshot, event-order, lifecycle tests |
| `docs/controller-usage.md` | Fix 5: snapshot section, bounded cleanup note |
| `README.md` | Fix 5: Codex-chat-led workflow, Windows quick start |

## Ordered Fixes

### Fix 1 — Process Lifecycle (Major)

**Problem**: `proc.wait()` unbounded after result event; cancel/timeout killed only direct child with no process-tree cleanup; lock released before confirmed cleanup.

**Changes** (`_controller_worker.py`):
- Added `_CLEANUP_GRACE = 8` constant.
- Added `_kill_proc_tree(proc)`: on Windows calls `taskkill /F /T /PID`; on POSIX calls `os.killpg(os.getpgid(proc.pid), SIGKILL)`; always follows with `proc.kill()` fallback and bounded `proc.wait(timeout=_CLEANUP_GRACE)`. Never raises.
- Refactored `_run_claude` main loop: extracted `cleanup_reason` break-out variable; on cancel or timeout calls `_kill_proc_tree` before draining.
- Stderr drain bounded to `_CLEANUP_GRACE` seconds.
- `proc.wait()` wrapped with `timeout=remaining_wait`; on `TimeoutExpired` calls `_kill_proc_tree` again.
- Cancel path sets state and returns before lock release (lock released in `finally` block, which only runs after function returns).
- Normal success path: child already exited at `proc.wait()` before lock is released in `finally`.

### Fix 2 — Task/Version Snapshot (Major)

**Problem**: Worker re-read mutable original task file; baseline had only file paths, no content hashes.

**Changes** (`collaboration.py`):
- Added `_capture_dirty_hashes(root)`: runs `git diff --name-only HEAD` and `git ls-files --others --exclude-standard`; SHA-256s each file content; skips `.git` entries.
- Added `_save_task_snapshot(root, job_id, task_data, task_file, baseline)`: saves immutable JSON to `<job_dir>/task_snapshot.json` before worker launch. Embeds `_prompt_file_content` (path-contained; reads file only if within root). Includes `_dirty_hashes`, `_baseline`, and `_snapshot_hash` (SHA-256 of the rest).
- `_cmd_start` calls `_save_task_snapshot` after `create_job`; on failure releases lock and exits.

**Changes** (`_controller_worker.py`):
- Added `_verify_task_snapshot(snapshot_path)`: reads snapshot, pops `_snapshot_hash`, recomputes SHA-256 over remaining content (same `json.dumps` sort/indent), raises `ValueError` if mismatch.
- `main()`: prefers `task_snapshot.json` from job dir; verifies hash on load; falls back to original task file only for pre-snapshot jobs.
- `_build_claude_prompt`: uses `_prompt_file_content` from snapshot; never re-reads original `prompt_file`.

### Fix 3 — App Server Completion (Major)

**Problem**: When `turn/start` response arrives before `turn/completed` notification (normal flow), `terminal_status` was set and continued, never returning; the loop waited until EOF/timeout.

**Changes** (`_appserver_adapter.py`):
- Added `response_received = False` flag in `run_turn`.
- Notification handlers (`turn/completed`, `turn/failed`, `turn/interrupted`): if `response_received` is True, return immediately. Otherwise set `terminal_status` and continue.
- Response handler: on `inProgress` status, set `response_received = True`; if `terminal_status` already set (notification-before-response order), return immediately. Otherwise continue loop.
- Both event orders now handled correctly and return promptly.

### Fix 4 — invoke_probe.ps1 Comment (Minor)

**Problem**: Comment contained literal `Invoke-Expression` substring, failing `test_invoke_probe_ps1_is_readable_file` which asserts `assertNotIn('Invoke-Expression', content)`.

**Change** (`scripts/invoke_probe.ps1`):
- Line 12: `# No Invoke-Expression.` → `# No dynamic evaluation.`
- No execution logic changed.

### Fix 5 — README / Usage (Minor)

**Problem**: README led with old bidirectional probe workflow; no Windows quick start; controller not in default flow.

**Changes** (`README.md`):
- Status banner updated: round 9 repair complete, pending Codex verification, links to round 8 review.
- New "How It Works" section: Codex stays in Codex chat, no permanent bridge, start/poll/result pattern.
- New "Quick Start (Windows)" section: `git clone`, `init_project.py`, task JSON example, all five commands.
- Error Recovery section added.
- Scripts table: `collaboration.py` as primary entry point; legacy tools labeled.
- Tests section: removed "Pending Validation" claim; says "Pending Codex Verification".

**Changes** (`docs/controller-usage.md`):
- Process/Security: bounded cleanup, taskkill /F /T, lock safety.
- New Task Snapshot section describing immutable snapshot fields.

## Acceptance Criteria Coverage

| Criterion | Status |
|---|---|
| 1. Bounded process lifecycle, tree cleanup, lock safety | Implemented |
| 2. Immutable task snapshot, hash verify, dirty content hashes | Implemented |
| 3. App Server completion returns on matching event, both orders | Implemented |
| 4. invoke_probe.ps1 comment fix, regression test passes | Implemented |
| 5. README Codex-chat-led workflow, Windows quick start | Implemented |

## Tests (NotRun — Codex runs independently)

New test classes in `tests/test_controller.py`:

- `TestTaskSnapshot`: snapshot saved and hash verifies; tamper rejected (`ValueError`); original mutation does not affect snapshot; `prompt_file` content embedded; `_dirty_hashes` key present.
- `TestAppServerAdapterRound9`: notification-before-response; response-before-notification; `turn/failed` notification; child holds pipe open after result (must return within 4s, not timeout).
- `TestProcessLifecycleBounded`: `_kill_proc_tree` on finished process (no raise); `_kill_proc_tree` on running sleep process (terminates within 5s).

Existing tests preserved unchanged.

## Known Limitations

- `_capture_dirty_hashes` requires git; returns empty dict silently on non-git roots (matching `_capture_baseline` behavior).
- `_kill_proc_tree` on Windows uses `taskkill` which must be on PATH; this is standard on all Windows versions.
- The `test_child_holds_pipe_after_parent_result` test uses a 4s bound; on very slow CI this may be borderline.

## Items Not Completed

None deferred without reason.

## Handoff

Codex to run `python -X utf8 -m unittest discover -s tests -v` and verify all fixes independently.
Round 8 evidence preserved. No raw logs or session data committed.
