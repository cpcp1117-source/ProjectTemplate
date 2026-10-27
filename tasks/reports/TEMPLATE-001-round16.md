# TEMPLATE-001 Round 16 — Implementation Report

## Summary of Changes

Fixed the confirmed Codex cleanup-outcome bug where a completed Codex job retained the active-job lock due to a stale `cleanup_uncertain=True` in `_outcome`. Preserved recovery history fields. Updated the portable acceptance test suite with normal-positive and protocol-completed-cleanup-uncertain regression cases.

## Files Changed

- `scripts/_controller_worker.py` — Bug fix in `_run_codex` finally block
- `scripts/collaboration.py` — Recovery history preservation in `_cmd_recover`
- `tests/test_recovery_acceptance.py` — Added normal-positive Codex assertions and two new test cases

## Root Cause

`_run_codex` set `_outcome['cleanup_uncertain'] = True` eagerly when the adapter child started (line 525), but the `finally` block only wrote `True` on the uncertain path and never cleared it on the confirmed path. The outer `main()` finally then saw the stale `True` and persisted `cleanup_required=True`, retaining the lock after every successful Codex job.

## Fix Detail

**`scripts/_controller_worker.py` — `_run_codex` finally (lines ~547–564):**

Before:
```python
if not cleanup_confirmed:
    _outcome['cleanup_uncertain'] = True
    patch['cleanup_required'] = True
```

After:
```python
_outcome['cleanup_uncertain'] = not cleanup_confirmed
patch['cleanup_required'] = not cleanup_confirmed
```

Both branches are now unconditional. `child_pid` and `child_identity` are always written to patch so recovery identity is preserved on all paths. The conditional `if patch:` guard was also removed — the patch always has at least `cleanup_required` so the guard was redundant and masked the confirmed-cleanup case.

**`scripts/collaboration.py` — `_cmd_recover`:**

Added three history fields before overwriting status to `interrupted`:
- `recovered_from_status` — the pre-recovery status (e.g., `completed`)
- `original_error` — the pre-recovery error value
- `original_result_text` — the pre-recovery result text

This ensures a task that actually completed but was incorrectly locked is not lost from history after recovery.

**`tests/test_recovery_acceptance.py`:**

- `test_codex_child_identity_persisted_on_real_local_server` — added `assertFalse(cleanup_required)` and `assertFalse(ACTIVE_LOCK.exists())` assertions (matching reviewer file)
- `test_codex_protocol_completed_cleanup_uncertain_retains_lock` — new: protocol status completed but adapter cleanup not confirmed; asserts lock retained and `cleanup_required=True`
- `test_recover_preserves_original_status_and_result` — new: recovery from a completed-but-locked job preserves `recovered_from_status`, `original_result_text`, clears `cleanup_required`, releases lock

## Acceptance Criteria

| Criterion | Result |
|---|---|
| Reviewer test file 9/9 pass | PASS |
| Shipped suite all pass | PASS |
| Normal Codex completed job: `cleanup_required=False`, lock absent | PASS |
| Uncertain Codex spawn: `cleanup_required=True`, lock retained | PASS (existing test unchanged) |
| Protocol completed but cleanup uncertain: lock retained | PASS (new test) |
| Recovery history fields preserved | PASS (new test) |

## Tests Run and Results

**Reviewer acceptance (exact command from task spec):**
```
python -X utf8 -m unittest discover -s "G:/AI Collaboration/work" -p "test_recovery_identity.py" -v
Ran 9 tests in 1.450s — OK
```

**Shipped suite:**
```
python -X utf8 -m unittest discover -s tests -p "test_*.py" -v
Ran 230 tests in 30.334s — OK (skipped=1)
```

Previous baseline: 228 total / 227 Pass / 1 Skip  
Current: 230 total / 229 Pass / 1 Skip (+2 new tests, all pass)

## Known Limitations

None. The fix is narrowly scoped to the exact data path described in the task spec. No other implementation or doc files were modified.

## Items Not Completed

None. All task spec requirements implemented.

## Status

Implementation complete. No commit, no publish, no Ready declaration per task spec.
