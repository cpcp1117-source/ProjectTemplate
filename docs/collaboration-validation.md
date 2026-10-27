# Collaboration Validation Guide

## Purpose

This document explains the known-defect / snapshot / recovery validation
procedure used in the test suite and how to apply it to real project work.

## Simple Checks Before Live Calls

Before running `--live` (which consumes account quota):

1. Run preflight: `python -X utf8 scripts/check_connection.py`
2. Confirm all `doc:*` and `rule_hash` entries are `Pass`.
3. Confirm `cli:claude:version` and `cli:codex:version` are `Pass`.
4. Only proceed to `--live` when preflight is fully green.

Optional bidirectional is `--live --bidirectional`; requires both CLIs logged in.

## Known-Defect / Snapshot / Recovery Walkthrough

The test `TestDefectSnapshotRecovery` in `tests/test_scripts.py` demonstrates
this pattern using an isolated fixture function. Here is the general procedure
for production work:

### Step 1 — Capture baseline

Before implementing or modifying a function, record its content hash
(e.g. SHA-256 of the source bytes) as the acceptance baseline.

```
hash_before = sha256(source_of_function_A)
acceptance_A = { hash: hash_before, verdict: "Pass", date: "..." }
```

### Step 2 — Expose defect

Write a test that asserts the **correct intended behavior**, not the current
(buggy) behavior. This test will fail and must remain strict — do not weaken
assertions to hide bugs.

```python
self.assertEqual(math_add(3, 4), 7)  # fails when implementation does 3-4=-1
```

### Step 3 — Fix and record

Apply the fix. Compute the new hash.

```
hash_after = sha256(source_of_function_A_fixed)
# hash_after != hash_before  →  prior acceptance A is invalidated
```

### Step 4 — Invalidate prior acceptance

Because `hash_after != hash_before`, acceptance_A no longer covers the current
state. Document the invalidation and re-run verification.

```
acceptance_A.status = "Superseded by fix at <commit>"
acceptance_B = { hash: hash_after, verdict: "Pending", date: "..." }
```

### Step 5 — Re-verify

Run affected tests. Confirm `math_add(3, 4) == 7`. Record new acceptance_B.

## Shell-Only Smoke Test (no tokens consumed)

Before any live call, confirm the helper scripts are executable and produce no
import or syntax errors:

```
python -X utf8 scripts/check_connection.py
```

All `doc:*` and `rule_hash` entries should be `Pass`.  
`cli:claude:version` and `cli:codex:version` should be `Pass`.  
Only proceed to `--live` when preflight is fully green.

Unit tests (no live calls, no model quota):

```
python -X utf8 -m unittest tests/test_scripts.py -v
```

All tests must pass. `NotRun` tests must be listed explicitly in the
implementation report with the reason they were not run.

## Live Bidirectional Runs

Run at least 3 sequential `--live --bidirectional` checks and record each
run's JSON report path. All three runs must produce `overall: Pass`.

```
python -X utf8 scripts/check_connection.py --live --bidirectional
python -X utf8 scripts/check_connection.py --live --bidirectional
python -X utf8 scripts/check_connection.py --live --bidirectional
```

For each run, record: `run_id`, `overall`, `bidirectional:claude-to-codex`,
`bidirectional:codex-to-claude`, and the artifact path.

## Minimum Collaboration Artifacts

A successful bidirectional run must produce, in `reports/connection_reports/`:

- `check_<run_id>.json` with `overall: Pass`
- `probe_claude-to-codex_<inner_nonce>.json` with `status: Pass`
- `probe_codex-to-claude_<inner_nonce>.json` with `status: Pass`

Each probe artifact must contain real values (not mocked/echoed):
`inner_session_id`, `raw_inner_stdout`, `rule_hash`, `rc: 0` (integer, not bool).

## Negative Cases (must be confirmed failing)

1. Run with `AI_COLLAB_NO_DELEGATION=1 python -X utf8 scripts/probe_peer.py ...`
   → must exit non-zero with "delegation refused" on stderr.

2. Run with `AI_COLLAB_CHECK_DEPTH=2 python -X utf8 scripts/probe_peer.py ...`
   → must exit non-zero with "Depth guard" on stderr.

3. Run probe_peer.py with `--nonce ../escape`
   → must exit non-zero; no artifact file created.

4. A `check_<run_id>.json` artifact where `probe_*.json` has `rc: true` (boolean)
   must produce `Fail(inner rc=True ...)` in `bidirectional:*` status.

## Current Status

**Round 12 implementation complete** — pending Codex round 12 independent verification.
Current status: Not Ready until Codex verifies full suite, reviewer tests, real Claude+Codex chain, and clean-project walkthrough.

## Controller Acceptance Gate (Default)

The default acceptance gate for new work is the host controller workflow:

1. `collaboration.py start --actor claude --task-file <task>` — Claude implements
2. `collaboration.py status --job <ID>` / `result` — Codex polls and retrieves
3. Codex independently reviews the result and diff

The old bidirectional `check_connection.py` 3-run gate is **legacy diagnostic** only.
It is preserved in historical reports but is not required for controller-based work.

## Defect Disclosure

The test `test_missing_raw_inner_stdout_fails` in `TestValidateBidiArtifact`
documents a known potential defect: if `_validate_bidi_artifact` does not
reject artifacts that are missing `raw_inner_stdout`, that is a protocol
enforcement gap. The test asserts the correct intended behavior (Fail), and
is expected to expose the defect if present.

The `_validate_bidi_artifact` function now also validates that the re-decoded
inner payload matches `nonce`, `root`, `rule_hash`, and `direction` via
`validate_payload` (not nonce only), and that the workflow file exists
(missing workflow file is now `Fail`, not a silent skip).
