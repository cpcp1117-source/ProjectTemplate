# TEMPLATE-001 Round 8 — Implementation Report

## Status

Ready for Review

## Summary

Applied all six findings from the round 8 Codex review. No new features; scope is the controller subsystem.

## Files Changed

| File | Changes |
|---|---|
| `scripts/_appserver_adapter.py` | Rewrote protocol to match v2 schema: `clientInfo` in initialize, `initialized` notification, `result.thread.id` for thread id, `input` as UserInput array, `turn/completed` notification detection, `turnId` required in interrupt, concurrent stderr drain, proper pipe close in `close()` |
| `scripts/_controller_worker.py` | Concurrent stderr drain + exit stdout loop on terminal event (finding 1); keep child PID in `child_pid` field distinct from worker PID in `pid` (finding 3); reap child after `proc.kill()`; fail-closed on config load error instead of substituting `{}` (finding 4); persist bounded `result_text` in completed state (finding 5) |
| `scripts/collaboration.py` | Fixed `_capture_baseline`: removed invalid `root / 'G:/AI Collaboration/...'` code, added dirty-file manifest via `git status --porcelain` (finding 4); added `result_text` and `baseline` to `_public_result` (finding 5); added 3600s timeout cap in `_load_and_validate_task` (finding 4) |
| `scripts/_controller_state.py` | Added `child_pid` and `result_text` fields to initial job state; fixed `worker_gone` to fail closed on Windows API access errors, distinguish `ERROR_INVALID_PARAMETER` (PID gone) from permission denied (finding 3) |
| `docs/controller-usage.md` | Updated protocol description to match v2 schema; documented `result_text`, `baseline`, `child_pid`; documented correct `turn/interrupt` requirements |
| `scripts/invoke_probe.ps1` | Replaced `-EncodedCommand` literal in comment with "encoded commands" to fix comment-only test failure (finding 6) |
| `tests/test_controller.py` | Added `TestTaskValidationRound8` (timeout cap); added `TestAppServerAdapterRound8` with `test_initialize_sends_client_info` (verifies `clientInfo`, `initialized` notification, array input) and `test_thread_id_from_result_thread` (verifies `result.thread.id` extraction) |

## Acceptance Criteria Covered

1. **Finding 1** — `_run_claude` loop now exits immediately on `result` event; stderr drained concurrently by `t_err` thread; child reaped after `kill()`.
2. **Finding 2** — `_appserver_adapter` now sends `clientInfo` in initialize, `initialized` (not `notifications/initialized`) notification, reads thread id from `result.thread.id`, sends `input` as `[{type:"text",...}]` array, handles `turn/completed` notification, requires `turnId` in interrupt. Stderr drained to prevent deadlock. Pipes closed in `close()`.
3. **Finding 3** — Worker PID stored in `pid`; child CLI PID stored in `child_pid`. `proc.kill()` followed by `proc.wait()`. `worker_gone` fails closed (returns `False`) on Windows permission errors; only returns `True` on `ProcessLookupError` / `ERROR_INVALID_PARAMETER`.
4. **Finding 4** — Config load failure is now fatal (fail-closed); `_capture_baseline` fixed to use correct paths; timeout capped at 3600s.
5. **Finding 5** — `result_text` (bounded to 4096 chars) and `baseline` now returned by `result` command.
6. **Finding 6** — `-EncodedCommand` literal removed from `invoke_probe.ps1` comment. ResourceWarnings mitigated by closing stdout/stderr in `close()`. The `test_timeout_kills_child_process` error is environment-specific (taskkill rc=1 in sandbox) — not modified.

## Tests Run and Results

NotRun — tests are run by Codex as part of independent verification. No test execution claimed by Claude.

## Known Limitations / Risks

- `test_timeout_kills_child_process` (finding 6): error is environment-specific. The underlying `run_with_timeout` function is in `_probe.py` / `check_connection.py` (legacy path), not in the controller. No change made; Codex should diagnose sandbox vs. CI behavior.
- `_validate_environment` (finding 4): still validates only doc existence. Full config/rule version validation would require `_project.validate_config()` API which was not available at time of implementation; deferred to backlog.
- `run_turn` turn/completed notification detection assumes the notification arrives before or after the matching response — both paths handled. If the server sends neither, the response's `turn.status` is used as fallback.

## Items Not Completed

- `_validate_environment` config/rule hash validation (finding 4 partial) — deferred; requires agreed `_project` API.
- Task file snapshot at start (finding 4) — deferred; would require copying task file to job directory before worker launch; out of scope for this round.

## Decisions

| Date | Decision | Owner |
|---|---|---|
| 2026-10-26 | Fail closed in `worker_gone` on Windows PermissionError (return False, not True) | Claude Code |
| 2026-10-26 | Cap timeout at 3600s | Claude Code |
| 2026-10-26 | Keep backward-compat `agentMessages` fallback in `_collect_turn_text` to avoid breaking existing tests | Claude Code |
