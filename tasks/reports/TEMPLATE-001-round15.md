# TEMPLATE-001 Round 15 — Implementation Report

## Summary

Completed three concrete data paths required for all nine reviewer acceptance cases to pass:

1. **Real exception producer** — `ContainmentError` extended with `child_pid`/`child_identity` constructor fields; `_spawn_windows` captures the child PID and creation-time identity via `get_process_creation_identity` before dropping `proc`, then raises with those fields populated.

2. **App Server normal child metadata** — `AppServerAdapter._start` captures `child_pid` and `child_identity` after successful spawn; `_run_codex` reads them from the adapter immediately after `__enter__` and persists to job state, setting `cleanup_required=True`.

3. **App Server uncertain spawn stays uncertain** — `AppServerAdapter._start` preserves `child_pid`, `child_identity`, and sets `_spawn_uncertain=True` when a `ContainmentError` with `cleanup_confirmed=False` is raised; `close()` no longer overrides `cleanup_confirmed=True` when `_spawn_uncertain` is set; `_run_codex` `finally` block persists child identity fields and `cleanup_required` from the adapter regardless of whether `__enter__` succeeded.

## Files Changed

- `scripts/_controller_process.py` — `ContainmentError.__init__` gains optional `child_pid`/`child_identity` params; `_spawn_windows` except block captures PID and identity before cleanup attempt.
- `scripts/_appserver_adapter.py` — `__init__` adds `child_pid`, `child_identity`, `_spawn_uncertain` fields; `_start` populates them on success and on uncertain failure; `close()` guards `cleanup_confirmed=True` behind `not self._spawn_uncertain`.
- `scripts/_controller_worker.py` — `_run_codex` reads `adapter.child_pid`/`child_identity` after `__enter__` and persists immediately; `finally` block persists child fields and `cleanup_required` from adapter state in all exit paths.

## Acceptance Criteria Covered

All nine reviewer cases from `test_recovery_identity.py`:

| Test | Result |
|---|---|
| `test_reused_pid_does_not_block_or_kill_unrelated_process` | PASS |
| `test_live_same_identity_refuses_recover` | PASS |
| `test_pending_without_pid_refuses_recover` | PASS |
| `test_worker_and_child_identity_persisted_on_real_local_process` | PASS |
| `test_failed_launch_is_terminal_and_unlocks` | PASS |
| `test_uncertain_spawn_preserves_child_identity_for_recovery` | PASS |
| `test_actual_containment_exception_preserves_spawned_pid` | PASS (was FAIL) |
| `test_codex_child_identity_persisted_on_real_local_server` | PASS (was FAIL) |
| `test_uncertain_codex_spawn_preserves_identity_and_lock` | PASS (was FAIL) |

## Tests Run and Results

```
python -X utf8 -m unittest discover -s "G:/AI Collaboration/work" -p "test_recovery_identity.py" -v
Ran 9 tests in 1.531s — OK

python -X utf8 -m unittest discover -s tests -p "test_*.py" -v
Ran 217 tests in 28.109s — OK (skipped=1)
```

Skipped test is `test_posix_cmd_uses_python_directly` — POSIX path format check, not applicable on Windows.

## Known Limitations or Risks

- `get_process_creation_identity` is Windows-only; returns `None` on POSIX. The `child_identity` field will be `None` on POSIX, which is the existing documented behavior.
- On Windows, if the process exits between `proc.pid` capture and `get_process_creation_identity` call, identity returns `None` (acceptable — process is already gone, recovery can confirm it).

## Items Not Completed

None. All three paths from the task specification are implemented and verified.

## Status

Ready for Review
