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

## Current Status

**Pending Validation** — the test suite has been written but not yet executed
by Codex as the independent verifier. Do not claim tests pass until
`tasks/reports/TEMPLATE-001-round3.md` is updated with Codex run results.

## Defect Disclosure

The test `test_missing_raw_inner_stdout_fails` in `TestValidateBidiArtifact`
documents a known potential defect: if `_validate_bidi_artifact` does not
reject artifacts that are missing `raw_inner_stdout`, that is a protocol
enforcement gap. The test asserts the correct intended behavior (Fail), and
is expected to expose the defect if present.
