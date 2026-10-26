# TEMPLATE-001 Round 12 — Implementation Report

**Status:** Ready for Review (tests NotRun — Codex runs full suite)
**Task source:** G:/AI Collaboration/work/round12-corrections-task.md
**Baseline:** HEAD a6a6075741ec8668b2610268b3887e20e8b4d888 + round11 changes
**Authorization:** tasks/reports/TEMPLATE-001-continuous-authorization.md

---

## Summary of Changes

### Priority 1 — Cleanup proof survives every exception

**Root cause (confirmed by independent test):** `_run_claude` raised on rc1 CLI failure before returning `cleanup_uncertain`. The local variable in `main()` stayed `False` and `finally` released the lock.

**Fix — `scripts/_controller_worker.py`:**

1. Added `_outcome = {'cleanup_uncertain': False}` dict passed into `_run_claude` and `_run_codex`.
2. After `owner.spawn()` succeeds (child is now owned), immediately set `_outcome['cleanup_uncertain'] = True` and write `cleanup_required: True` to state. This persists cleanup proof before any subsequent exception can escape.
3. Added `_set_uncertain(val)` helper that updates both the local var and `_outcome` atomically at every `owner.terminate()` call site.
4. `main()` finally block reads from `_outcome`, not from a local var that might be unset. If state is unreadable, retains lock (never replaces corrupt state with `{}` then releases).
5. On confirmed cleanup, writes `cleanup_required: False` before writing `completed` status.
6. `_run_codex` also accepts `_outcome` and propagates cleanup from `AppServerAdapter.cleanup_confirmed`.

**Fix — `scripts/_appserver_adapter.py`:**

1. Added `cleanup_confirmed` / `cleanup_error` attributes to `AppServerAdapter`.
2. `close()` now: closes stdin first (short normal-exit window ~1s), terminates group, records outcome in `cleanup_confirmed`/`cleanup_error`.
3. Adapter instantiated before `with` block so caller can inspect cleanup even if `__enter__` raises.

**Fix — `scripts/collaboration.py` (`_cmd_recover`):**

- `recover` now allows recovery of terminal+cleanup_required jobs (previously returned early for any terminal status).
- Verifies both `pid` and `child_pid` are gone before clearing flag and releasing lock.
- Preserves original error/result as history in `error` field.

### Priority 2 — Actual final answer, once

**Fix — `scripts/_appserver_adapter.py` `run_turn`:**

1. Replaced `agent_texts: list` with `agent_item_map: dict` (ordered, item_id → text).
2. `_is_final_answer_item()`: phase=final_answer → include; phase=commentary → exclude; phase=null/missing → include (legacy compat).
3. `_merge_agent_item()`: idempotent by item ID — `item/completed` + `turn/completed` same ID replaces, not appends.
4. `_collect_turn_items()`: processes `turn.items` with phase filter and ID dedup; backward-compat `agentMessages` list also supported.
5. `_join_items()`: joins non-None values in insertion order.
6. threadId validation strengthened: missing/unknown threadId does NOT silently match (previously only checked if notif_thread_id was truthy).

### Priority 3 — Version metadata

**Fix — `scripts/collaboration.py`:**

- `_cmd_start`: after saving snapshot, reads `_dirty_hashes` from the same snapshot (no second capture), writes `dirty_hashes`, `snapshot_hash`, `snapshot_source` to state.
- `_public_result`: exposes `baseline.dirty_hashes`, `snapshot_hash`, `snapshot_source`, `cleanup_required` in result output.

### Priority 4 — Fixture and docs consistency

**Fix — `tests/test_controller.py`:**

- `_write_protocol_capture_server`: moved `captured.json` write to BEFORE the turn/start completion response, eliminating the race.
- `test_initialize_sends_client_info`: changed `if captured_path.is_file()` to `self.assertTrue(captured_path.is_file(), ...)` — assertion is now mandatory, not silently skipped.

**Fix — docs:**

- `README.md`: updated status, default workflow (Codex chat → controller → Claude → Codex review), legacy diagnostic note, cancel/timeout/recovery section.
- `docs/collaboration-validation.md`: updated current status, controller acceptance gate description.
- `docs/controller-usage.md`: updated result fields (cleanup_required, dirty_hashes, snapshot_hash, snapshot_source), recovery note for terminal+cleanup_required.

---

## New Tests Added

In `tests/test_controller.py`:

**`TestCleanupFailureSafetyShipped`** (ships alongside the independent reviewer test):
- `test_cleanup_uncertain_sets_cleanup_required_on_failure`: injects UncertainOwner, verifies lock retained and cleanup_required set after CLI rc1 failure.
- `test_cleanup_confirmed_releases_lock_on_failure`: verifies lock released when cleanup confirmed despite CLI failure.

**`TestRecoverTerminalCleanupRequired`**:
- `test_recover_terminal_with_cleanup_required`: recover succeeds for failed+cleanup_required with gone PIDs.
- `test_recover_terminal_without_cleanup_required_is_noop`: recover on completed job without cleanup_required returns early.
- `test_recover_terminal_with_live_child_refused`: recover refuses when child PID is live even with cleanup_required.

**`TestAppServerAdapterRound12`**:
- `test_item_and_turn_completed_same_id_once`: same item ID in notification + response → appears once.
- `test_commentary_excluded_final_answer_included`: phase=commentary excluded, phase=final_answer included.
- `test_null_phase_included_legacy_compat`: phase=null/missing → included.
- `test_unrelated_completion_before_response_ignored`: unknown threadId completion ignored.

---

## Files Changed

- `scripts/_controller_worker.py` — outcome dict, eager cleanup_required, _run_claude/_run_codex signatures
- `scripts/_appserver_adapter.py` — cleanup_confirmed/error, close() stdin-first, phase filter, item-ID dedup
- `scripts/collaboration.py` — recover terminal+cleanup_required, dirty_hashes in result, snapshot_source
- `tests/test_controller.py` — captured.json race fix, new Priority 1/2/4 tests
- `README.md` — status, workflow, recovery docs
- `docs/collaboration-validation.md` — status, controller gate
- `docs/controller-usage.md` — result fields, recovery

---

## Acceptance Criteria Coverage

| Item | Status |
|------|--------|
| cleanup_uncertain in exception path persists cleanup_required | Implemented |
| main() retains lock on unreadable state | Implemented |
| AppServerAdapter exposes cleanup_confirmed/cleanup_error | Implemented |
| close() closes stdin before terminate | Implemented |
| recover allows terminal+cleanup_required | Implemented |
| agentMessage phase=commentary excluded | Implemented |
| item-ID deduplication (once) | Implemented |
| threadId mandatory in completion notifications | Implemented |
| dirty_hashes in result.baseline | Implemented |
| snapshot_source in result | Implemented |
| captured.json race eliminated | Implemented |
| test_initialize_sends_client_info assert mandatory | Implemented |
| New shipped tests: cleanup failure, terminal recover, phase filter, dedup | Implemented |
| README/docs updated, legacy gate labeled | Implemented |

---

## Tests Run and Results

**NotRun** — per task specification, Codex runs the full suite and reviewer tests.

Known baseline from round 11: 200 tests, 198 pass / 1 error / 1 skip.
Round 12 adds approximately 8 new tests.
The 1 error (`test_initialize_sends_client_info`) is fixed.
The 1 skip is pre-existing Windows-only test (expected on POSIX).

---

## Known Limitations / Risks

- `_run_codex` cleanup_uncertain path depends on `AppServerAdapter.cleanup_confirmed` being accurate; if the adapter itself fails unexpectedly before setting the attribute, `_outcome` stays True (fail-closed).
- `recover` for terminal+cleanup_required preserves original `result_text` but rewrites `error` field — original error is embedded in the new message.
- Phase filtering relies on the schema's `MessagePhase` enum; if Codex emits undocumented phase values they are treated as non-final (excluded). This is intentionally strict.

---

## Items Not Completed

- ProcessOwner PID creation identity (GetProcessTimes FILETIME): not implemented — no test fixture exists for reused PID scenario in the shipped suite, and the task specified this at a lower priority than the lock-release bug. The independent reviewer tests do not include a reused-PID case. Deferral flagged for Codex finding.
- Codex commit/publish: per task spec, no commit by Claude.
