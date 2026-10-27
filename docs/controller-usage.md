# Controller Usage

**Host controller** (`scripts/collaboration.py`) is our glue layer over the official
Claude CLI (stream-json) and Codex App Server (stdio JSONRPC) interfaces.
It is **not** the official OpenAI Codex plugin or a persistent desktop chat bridge.
Current chat reports progress while the active turn is open; there are no proactive
notifications after the turn closes.

The old `check_connection.py` / `probe_peer.py` remain available as **legacy**
diagnostic tools. They are not the default acceptance gate for new work.

## Prerequisites

- `.collaboration/local.json` configured (run `python scripts/init_project.py`)
- `claude` and/or `codex` CLI reachable on PATH or via `overrides` in local config
- Task JSON file (see format below)

## Quick Start

```
# Start a background Claude implementation job
python -X utf8 scripts/collaboration.py start \
    --actor claude \
    --task-file tasks/my_task.json \
    --root /abs/path/to/repo

# Poll status (Codex polls this from current chat)
python -X utf8 scripts/collaboration.py status --job <JOB_ID>

# Retrieve result (only when status == completed/failed/cancelled/interrupted)
python -X utf8 scripts/collaboration.py result --job <JOB_ID>

# Request cooperative cancellation
python -X utf8 scripts/collaboration.py cancel --job <JOB_ID>

# Mark interrupted after confirming worker is gone
python -X utf8 scripts/collaboration.py recover --job <JOB_ID>
```

All commands write JSON to stdout. Errors write JSON with an `error` key to stderr
and exit nonzero.

## Task File Format

```json
{
  "task_id": "PROJ-001",
  "objective": "Implement feature X",
  "prompt": "Optional detailed instructions for the model...",
  "allowed_paths": ["src/feature.py", "tests/test_feature.py"],
  "timeout_secs": 300,
  "baseline": {
    "commit": "a6a6075",
    "dirty_manifest": {}
  }
}
```

Fields:
- `task_id` — nonempty string, matches tasks/current_task.md Task ID
- `objective` — short statement of goal
- `prompt` — optional; detailed instructions (use `prompt_file` for long prompts)
- `prompt_file` — optional repo-relative path to a .md/.txt prompt file
- `allowed_paths` — repo-relative paths only; no `..` or absolute paths
- `timeout_secs` — finite positive number; bounded per Rule 7
- `baseline` — git commit + dirty manifest for version-bound acceptance (Rule 5)

## Status Fields

`status` command returns:
```json
{
  "job_id": "...",
  "actor": "claude|codex",
  "status": "pending|running|completed|failed|cancelled|interrupted",
  "phase": "short phase name",
  "updated_at": "ISO-8601",
  "pid": 1234,
  "session_id": "claude session or null",
  "thread_id": "codex thread or null",
  "last_tool": "last tool name used (claude only)",
  "error": "error message or null",
  "task_id": "...",
  "cleanup_required": false,
  "child_pid": null,
  "worker_identity": null,
  "child_identity": null
}
```

- `cleanup_required` — `true` if process-tree cleanup was uncertain (kill could not be confirmed); use `recover` after verifying both PIDs are gone
- `child_pid` — PID of the child CLI process (Claude or Codex) spawned by the worker; distinct from `pid` (the worker itself)
- `worker_identity` — creation-time identity string for the worker process; used by `recover` to detect PID reuse
- `child_identity` — creation-time identity string for the child CLI process; used by `recover` to detect PID reuse

`result` adds:
- `artifact_paths` — list of repo paths that exist after completion
- `result_text` — bounded (≤4096 chars) final result text from the model (claude only)
- `baseline` — git commit, dirty-file manifest, and `dirty_hashes` (`{path: sha256 | '__DELETED__'}`) captured at job creation via the immutable task snapshot
- `snapshot_hash` — SHA-256 of the immutable task snapshot for version-bound acceptance
- `snapshot_source` — absolute path to the task snapshot file
- `recovered_from_status` — original status before recovery (only present after `recover`)
- `original_error` — error message that was set before recovery, bounded to 4096 chars (only present after `recover`)
- `original_result_text` — result text that was set before recovery, bounded to 4096 chars (only present after `recover`); preserves completed work that was incorrectly locked

Private reasoning, full prompts, and raw logs are never exposed in status or result.

## Actors

### `--actor claude`

Invokes Claude CLI with `--output-format stream-json --verbose`.
Allowed tools: `Read, Write, Edit, Glob, Grep`. No `Bash` or `Agent` calls are granted.
The controller parses the event stream incrementally to update `last_tool` and capture
the final `result` event. `status=completed` means the implementation was submitted
for Codex review — it does NOT mean Codex has accepted it.

### `--actor codex`

Invokes Codex App Server via `codex app-server` (stdio JSONRPC v2 schema):
- `initialize` with `clientInfo:{name,version}` / `initialized` notification
- `thread/start` with `approvalPolicy: never`, `sandbox: read-only`, `ephemeral: true`
- `turn/start` with input as `[{type:"text", text:"...", text_elements:[]}]` array
- `thread/start` response carries thread id at `result.thread.id`
- Completion detected via `turn/completed` notification or `result.turn.status`
- `turn/interrupt` requires both `threadId` and `turnId`
- Unsupported approval requests are declined; the adapter never hangs or auto-approves.
- This is an optional machine-review path; current Codex chat can still review directly.

## Process / Security

- Worker launched with `CREATE_NO_WINDOW` (Windows) / `start_new_session` (POSIX)
- No `shell=True` with untrusted task text; all subprocess args built as lists
- Task prompt is built in-process from the immutable snapshot and passed via `-p` on the
  Claude CLI command line (i.e., it does appear in `argv` and is visible via process
  inspection tools); it is not written to a temporary file or a pipe before invocation.
  Do not put secrets or credentials in the task prompt.
- One active job at a time (root-level lock); second `start` is refused
- Cancel is cooperative: worker checks the sentinel and asks `ProcessOwner` to stop
  the owned process group. Windows uses a Job Object with `KILL_ON_JOB_CLOSE` and
  active-process accounting; POSIX uses an owned process group (`killpg`).
- Normal successful Claude replies receive an exit grace period. Cleanup and pipe
  drain loops have deadlines; unresolved cleanup retains the lock for recovery.
- Worker PID (`pid` field) is distinct from child CLI PID (`child_pid` field); `recover` checks both
- `recover` compares saved process creation identities. A reused PID belongs to a
  different process and is never killed. A live original process or unknown identity
  prevents recovery; access errors fail closed.
- **Fail-closed lock**: if owned-group cleanup returns `uncertain=True`,
  the lock is NOT released and `cleanup_required` is set in state; use `recover` after
  manually verifying both PIDs are gone
- App Server adapter kills child process before closing pipes on close; `__enter__` cleans up on init failure

## Safe Stopping and Recovery

If a job is stuck or you need to stop it, always follow this sequence:

```powershell
# Step 1: Request cooperative cancel (preferred; worker kills its own process tree cleanly)
python -X utf8 scripts\collaboration.py cancel --job <JOB_ID>

# Step 2: Check current status (includes cleanup_required, pid, child_pid, worker_identity, child_identity)
python -X utf8 scripts\collaboration.py status --job <JOB_ID>
```

If the job reaches a terminal status and `cleanup_required` is `false`, no further action is needed.

If `cleanup_required` is `true`, or the job is still non-terminal after cancel, run `recover`:

```powershell
# Step 3: After confirming the worker and child are gone, recover
python -X utf8 scripts\collaboration.py recover --job <JOB_ID>
```

`recover` checks both `pid` (worker) and `child_pid` (Claude/Codex CLI) using the saved creation-time identity (`worker_identity` / `child_identity`) to detect PID reuse before clearing the lock:

- **Same identity and live** → refuses recover; the original process is still running
- **Different identity** → PID was reused by an unrelated process; `recover` does NOT kill the new process, treats the original as gone, and proceeds
- **Identity unavailable** → falls back to liveness check; fails closed if process appears alive

**Do not kill processes manually by PID without first verifying identity.** A PID that appeared in `status` may have been reused. If the worker is gone but an owned child CLI remains live (with matching `child_identity`), Codex must explicitly verify the PID and creation identity before stopping that child; unknown or mismatched identity keeps the lock and is reported for human review.

If `cleanup_required` is set in state (including on jobs already in a terminal status like `failed`), `recover` clears it after verifying both PIDs are gone and releases the lock that was retained pending cleanup confirmation.

## Task Snapshot

At `start` time, `collaboration.py` saves an immutable snapshot of the task to `<job_dir>/task_snapshot.json` before launching the worker:

- Full task JSON plus `_prompt_file_content` (embedded, path-contained with `is_relative_to` check) if `prompt_file` set; missing or outside-root `prompt_file` is rejected at start time
- `_dirty_hashes`: `{path: sha256_hex | '__DELETED__'}` for tracked-dirty, staged, untracked, and deleted files; uses NUL-delimited git output (`-z`) to handle CJK/special-char paths; excludes `.git` and `.collaboration/` credentials
- `_baseline`: git commit and porcelain status captured at job creation
- `_snapshot_hash`: SHA-256 of the snapshot content (excluding the hash field itself)
- `snapshot_hash` is stored in job state and exposed in `result` output

The worker verifies the snapshot hash on startup and rejects if missing or tampered. There is no fallback to the mutable original task file — a missing snapshot is a hard failure. The prompt never references the original task file path.

## Job Storage

Jobs stored under `.collaboration/controller/jobs/<JOB_ID>/` (gitignored).
State writes are atomic (tmp → replace). Raw logs in `stdout.log` / `stderr.log`.

## Relation to Eight Rules

- Rule 3 (Handoff Contract): `start` output is the handoff token; `result` is the return
- Rule 4 (Independent Verification): `status=completed` ≠ accepted; Codex reviews separately
- Rule 5 (Version-bound Acceptance): baseline commit captured at job creation
- Rule 7 (Bounded Retry): `timeout_secs` per task; no automatic retry on failure
- Rule 8 (No Recursive Delegation): worker prompt forbids Bash/Agent; no re-delegation

## Legacy Diagnostics

`check_connection.py` and `probe_peer.py` remain for connectivity troubleshooting.
They are not the primary workflow acceptance gate. Historical failing reports are preserved.
