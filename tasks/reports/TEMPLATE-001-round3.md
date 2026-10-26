# Execution Result — TEMPLATE-001 Revision 3 (Batch D)

## Task ID

TEMPLATE-001

## Revision Round

3 / max: 2 (extended per authorized Batch D instruction)

## Owner

Claude Code

## Baseline

- Branch: codex/portable-ai-collaboration
- Commit: 22127c4 (prior round baseline)
- Dirty files manifest: per git status at session start

## Changes Made

### tests/test_scripts.py — Complete replacement

Replaced 58KB old test file with concise stdlib unittest tests (~500 lines, ~40 focused tests).

Coverage includes:
- **Protocol decoder** (`_protocol.py`): Claude single-envelope, stream-json, and all negative cases (rc!=0, is_error not exactly False, missing session_id, non-object result, malformed JSON, auth envelope shape, nonce-containing-401 lexical non-detection)
- **Codex JSONL decoder**: thread.started, turn.completed, item.completed agent_message positives; negatives for missing thread, missing turn.completed, error/turn.failed events, malformed JSONL, non-JSON agent_message text, nonce-only message
- **Config validation** (`_project.py`): all 7 required fields, invalid types (bool timeout, NaN, zero, negative), relative root, moved root, stale rule_hash, wrong rule_version, cli bool value, directory-instead-of-file, all 14 required paths
- **Init project**: creates config, byte preservation on re-init, missing doc fails without overwriting, changed rule hash fails re-init
- **CLI helper subprocess** (`_cli_helper.py`): real .py fake subprocess echoing argv with CJK/spaces/apostrophes/&/$/%/; Windows .cmd fails closed; timeout kills child process (Windows, skipped on POSIX)
- **Outer tool evidence** (`_check_outer_tool_evidence`): real Claude stream-json tool_use+tool_result positive; no tool_use, result is_error=true, no tool_result, wrong nonce all fail; Codex command_execution positive; nonzero exit_code, wrong nonce fail
- **Bidi artifact validation** (`_validate_bidi_artifact`): positive with real inner Codex fixture; missing raw_inner_stdout (exposes known defect); wrong nonce, direction, rc, status; missing required fields; malformed raw_inner_stdout; non-object artifact; no tool evidence gives NotVerified
- **probe_peer.py refusals**: NO_DELEGATION env, depth>1, depth=1 reaches arg parsing (not depth-guarded), invalid nonce with path traversal chars
- **Defect/snapshot/recovery**: isolated fixture with intentional subtraction bug; independent failing assertion; fixed version passes; content hash change invalidates prior acceptance

### docs/ai-collaboration-workflow.md

Updated 16-step role map with exact step names and owners as specified:
1 Requirements Discovery Codex; 2 Scope Definition Codex; 3 Existing System Analysis Codex; 4 Solution Design Claude + Codex review; 5 Technical Design Claude + Codex review; 6 Task Planning Codex; 7 Environment Setup Claude; 8 Implementation Claude; 9 Automated Verification Codex; 10 Code Review Codex leading/Claude responds; 11 Functional Acceptance Claude/Codex review; 12 Remediation & Recheck Claude/Codex; 13 Release Preparation Codex; 14 Deployment & Smoke Test Claude; 15 Documentation & Handoff Claude/Codex review; 16 Monitoring & Maintenance Claude/Codex review.

Updated Template vs. Active Project: fill/update current_task, archive completed tasks, retain reusable template at docs/templates/task-handoff.md. Added Skills dirty-manifest requirement.

### CLAUDE.md

Updated step references to match new 16-step names (4, 5, 7, 8, 11, 12, 14, 15, 16).

### AGENTS.md

Updated step references to match new 16-step names with correct Codex-led and shared-role steps.

### README.md

- Tests section: stdlib unittest only, Windows PowerShell `python -X utf8` quickstart
- Config scope clarifications: no sandbox/network access, no permanent bridge
- Moving project: rename local.json to backup, rerun init (no silent overwrite)
- Skills invocation: `$name` Codex / `/name` Claude, host discovery not assumed
- Status: Pending Validation

### docs/project-brief.md

Added Project ID, Runtime/Environment, Setup/Run/Test commands, Deployment Target/Rollback, Monitoring scope/interval/notification fields as reusable TBD.

### docs/collaboration-validation.md (new)

Known-defect/snapshot/recovery walkthrough, simple checks before live, current status Pending Validation, defect disclosure for `test_missing_raw_inner_stdout_fails`.

## Acceptance Criteria Coverage

- Tests replace old test_scripts.py with concise stdlib unittest: Yes
- ~40 focused tests: ~43 test methods
- Real-format fixtures (not relaxed mocks): Yes
- Expresses correct intended behavior even if initially failing: Yes (known defect tests)
- No weakened assertions: Yes
- No machine paths in committed tests: Yes (uses pathlib relative to __file__)
- 16-step role map updated: Yes
- AGENTS.md, CLAUDE.md aligned: Yes
- README stdlib unittest + PowerShell quickstart: Yes
- project-brief extended with new fields: Yes
- docs/collaboration-validation.md created: Yes

## Tests Run and Results

**Not Run by Claude** — tests/test_scripts.py has not been executed in this session. Codex will run them as part of independent verification (Step 9). No claims of pass/fail are made here.

## Known Limitations and Risks

1. `test_missing_raw_inner_stdout_fails` is expected to fail if `_validate_bidi_artifact` does not enforce `raw_inner_stdout` presence — this is an intentional known-defect exposure, not a test error.
2. `test_defective_fixture_fails` intentionally asserts the buggy result; it will "pass" (i.e. the test assertion succeeds) by confirming the defect exists.
3. `test_invalid_nonce_filename_refuses` depends on probe_peer.py validating nonce for path safety; if current implementation does not check this, the test will fail (intentional defect exposure).
4. Windows-specific tests (`@unittest.skipUnless(os.name == 'nt', ...)`) are skipped on POSIX.
5. `test_actual_windows_shims` from reviewer scratch tests is not included — it requires real installed CLIs and is not portable.

## Items Not Completed

- Did not edit `reports/review_reports/TEMPLATE-001.md` or any test manifest (Codex owns those).
- `.claude/skills/` not modified (Codex already made six valid entrypoints per Batch D instruction).
- No actual monitor started (monitoring fields in project-brief are TBD placeholders only).
- `docs/templates/task-handoff.md` not created in this batch (referenced as existing reusable template per prior rounds).

## Status

Ready for Review
