# TEMPLATE-001 Round 7 — Implementation Report

**Task**: Codex-first controller architecture  
**Actor**: Claude Code  
**Baseline**: commit a6a6075 + dirty manifest per controller-baseline.json  
**Status**: Implementation Submitted / Awaiting Codex Review

---

## Summary of Changes

Implemented a Python stdlib host-side job controller that Codex can poll from the current
chat session. The controller wraps the official Claude CLI (stream-json) and Codex App
Server (stdio JSONRPC) without shell=True, model-generated commands, or persistent services.

---

## Files Changed / Created

| Path | Action |
|------|--------|
| `scripts/collaboration.py` | Created — main CLI (start/status/result/cancel/recover) |
| `scripts/_controller_state.py` | Created — job state persistence, lock, cancel sentinel |
| `scripts/_controller_worker.py` | Created — detached worker; owns child process, timeout, cancellation |
| `scripts/_appserver_adapter.py` | Created — Codex App Server stdio JSONRPC adapter |
| `tests/test_controller.py` | Created — stdlib tests (no network, no real model calls) |
| `docs/controller-usage.md` | Created — usage docs, task file format, security notes |

**Not modified**: `check_connection.py`, `_probe.py`, `_protocol.py`, `_project.py`,
`_cli_helper.py`, `.gitignore` (`.collaboration/` already covers controller jobs),
`docs/ai-collaboration-workflow.md`, reviewer reports, manifests, global config.

---

## Acceptance Criteria Coverage

| Criterion | Status |
|-----------|--------|
| `start --actor claude` returns job_id JSON quickly (<5s excl. validation) | Implemented |
| `status` returns public metadata only (no private prompts/reasoning) | Implemented |
| `result` errors if job still running | Implemented |
| `cancel` cooperative (sentinel + bounded wait) | Implemented |
| `recover` refuses live workers; marks interrupted only when worker confirmed gone | Implemented |
| No shell=True with untrusted input; argv-safe for CJK/special chars | Implemented |
| CREATE_NO_WINDOW on Windows; detached worker | Implemented |
| One active job at a time (root-level lock); exclusive creation | Implemented |
| Path traversal rejected in job_id and allowed_paths | Implemented |
| Appserver: initialize/thread/start/turn/start lifecycle | Implemented |
| Appserver: approval requests declined; never hangs | Implemented |
| Appserver: interleaved notifications handled | Implemented |
| old check_connection/probe kept as LEGACY (not removed) | Preserved |
| `.collaboration/` already gitignored (covers controller jobs) | Confirmed |
| stdlib tests: fake CLIs, RPC lifecycle, failure modes | Implemented |
| Token check comment-substring regression test | Implemented |

---

## Tests Run and Results

**NotRun** — No test runner executed in this implementation session (file tools only,
no Bash/PowerShell). Tests are written and ready for Codex to run:

```
python -m pytest tests/test_controller.py -v
```

or

```
python -m unittest tests.test_controller -v
```

Codex should independently run the full test suite including the new controller tests.

---

## Known Limitations / Risks

1. **Real model round-trip not tested by Claude** — Codex acceptance criteria item 3
   (real controller → Claude JSON-file task, real controller → Codex appserver review)
   must be verified by Codex independently (by design per Rule 4).

2. **`_run_codex` uses `codex app-server` subcommand** — actual subcommand name
   may differ on Codex 0.160.0; Codex should verify and report if it needs adjustment.

3. **PID liveness on Windows** uses `GetExitCodeProcess`; process may have been
   recycled to a different process with the same PID in extreme conditions. Current
   implementation errs on the side of refusing recover (safe failure mode).

4. **`_capture_baseline` git call** — non-fatal if git not on PATH; baseline will be
   empty dict. Controller still starts the job.

5. **Worker crash before state write** — if the worker process dies before the first
   state update, status stays 'pending' indefinitely. `recover` handles this (worker PID
   will be None → treated as gone).

---

## Items Not Completed

- Real integration test (controller → actual Claude/Codex): must be run by Codex (Rule 4)
- Codex appserver subcommand name verification on 0.160.0
- README update to point at controller (deferred; minimal scope per task)

---

## Handoff to Codex

Status in `tasks/current_task.md`: not updated (template is blank; Codex owns Steps 1-3, 6).  
Codex: please run `python -m pytest tests/test_controller.py -v` and verify:
- All stdlib unit tests pass
- Real `collaboration.py start --actor claude --task-file <file>` returns job_id within 5s
- `collaboration.py status` shows running before completion
- `collaboration.py result` returns completed/failed state
- Appserver adapter works against actual `codex app-server` (verify subcommand)
- Cancel and bounded recover work as described
