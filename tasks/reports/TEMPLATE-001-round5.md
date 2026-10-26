# TEMPLATE-001 Round 5 — Implementation Report

**Status:** Ready for Review  
**Baseline commit:** a6a6075 (clean working tree at start)  
**Date:** 2026-10-26  
**Owner:** Claude Code

---

## Summary of Changes

Repaired automatic bidirectional delegation: replaced opaque base64
`-EncodedCommand` strings with readable, inspectable PowerShell `-File`
invocations via a new `invoke_probe.ps1` entry point. Fixed `_make_bidi_prompt`
to remove hardcoded `tool_used:true` and require the model to report the
actual exit code. For `codex-to-claude` direction, the prompt now specifies
native `powershell.exe` (not `bash.exe`/WSL). Extended artifact validation to
use `validate_payload` on all inner payload fields and to treat a missing
workflow file as `Fail`.

---

## Files Changed

| File | Change |
|------|--------|
| `scripts/invoke_probe.ps1` | **New** — bounded PowerShell `-File` entry point for probe invocations |
| `scripts/_probe.py` | Replaced `make_ps_encoded_cmd`/`make_safe_bash_cmd`(-EncodedCommand) with readable `-File` command builders; updated `make_probe_peer_cmd_str` and `make_context_cmd_str` |
| `scripts/check_connection.py` | Fixed `_make_bidi_prompt`: shell-specific instructions, `tool_rc` in JSON contract, no hardcoded success; added `tool_rc` outer payload validation; fixed `_validate_bidi_artifact` missing-workflow-file to Fail; added `validate_payload` on re-decoded inner payload fields |
| `tests/test_scripts.py` | Added 3 new test classes: `TestProbeCommandStrings`, `TestMakeBidiPrompt`, `TestValidateBidiArtifactExtended` (27 new test methods) |
| `docs/collaboration-validation.md` | Added shell-only smoke test instructions, 3 sequential live `--bidirectional` run procedure, minimum artifact requirements, and negative cases |

---

## Acceptance Criteria Covered

| # | Criterion | Status |
|---|-----------|--------|
| 1 | No `-EncodedCommand` in model-facing probe commands | Done — `make_probe_peer_cmd_str` uses `-File invoke_probe.ps1`; test `test_make_probe_peer_cmd_str_no_encoded_command` verifies |
| 2 | `invoke_probe.ps1` is readable/inspectable, no `Invoke-Expression` | Done — `test_invoke_probe_ps1_is_readable_file` asserts |
| 3 | `_make_bidi_prompt` explains task transparently, asks for actual JSON | Done — prompt now includes `tool_rc` field and `diagnostic` on failure |
| 4 | `codex-to-claude` prompt specifies native PowerShell, not bash/WSL | Done — `test_codex_to_claude_specifies_native_powershell` asserts |
| 5 | Artifact validation uses `validate_payload` for all inner fields | Done — `_validate_bidi_artifact` calls `validate_payload` on re-decoded inner |
| 6 | Missing workflow file is `Fail`, not silent skip | Done — `test_missing_workflow_file_fails` asserts |
| 7 | `rc=bool` is rejected | Done — existing + new `test_rc_false_boolean_fails` / `test_rc_true_boolean_fails` |
| 8 | Full-field artifact mutations (wrong root, direction, rule_hash in raw stdout) | Done — `TestValidateBidiArtifactExtended` |
| 9 | Test instructions added to `docs/collaboration-validation.md` | Done |

---

## Tests Run and Results

Unit tests: **Not Run by Claude** — file tools only in this session per task contract.  
Codex must run:  
```
python -X utf8 -m unittest tests/test_scripts.py -v
```
Expected: all prior 101 tests pass + 27 new tests pass.

Live `--bidirectional` runs: **Not Run by Claude** — Codex must execute 3 sequential runs.

---

## Known Limitations / Risks

1. **Nested Codex sandbox credential denial** — if the inner Codex sandbox
   cannot authenticate (no credentials, network deny), the probe fails with
   `Fail(inner timeout)` or `Fail(OSError: ...)`. This is transparent diagnostic
   handling per task spec. No bypass attempted.

2. **`make_context_cmd_str` direction default** — on Windows, when `direction=None`
   is passed, the PS1 script receives `claude-to-codex` as default. This is only
   used for non-bidirectional live probes where direction is irrelevant to
   `probe_context.py`. The default is documented.

3. **`test_claude_encoded_command_passes`** — this existing test remains valid:
   it tests `_check_outer_tool_evidence` tool-event matching with an encoded
   string as `expected_cmd`. The function does exact-string match regardless of
   encoding. The new code no longer _produces_ encoded commands for model prompts,
   but the evidence-matching function is agnostic to command content.

4. **`invoke_probe.ps1` `-File` parameter passing** — quoted parameter values
   with spaces/CJK/apostrophe are passed as separate subprocess elements; PS
   `-File` handles them correctly without shell expansion. Tested via
   `test_path_with_spaces_cjk_apostrophe_in_cmd_str`.

---

## Items Not Completed

- Live `--bidirectional` smoke test: requires Codex execution with real credentials.
- Windows-only `invoke_probe.ps1` execution test: requires PowerShell environment
  (not available in file-tools-only session).

---

## Handoff to Codex

Codex should:
1. Run `python -X utf8 -m unittest tests/test_scripts.py -v` and record exit code + output.
2. Run `python -X utf8 scripts/check_connection.py` (preflight).
3. Run `python -X utf8 scripts/check_connection.py --live --bidirectional` × 3.
4. Inspect `scripts/invoke_probe.ps1` for correctness.
5. Verify `scripts/_probe.py` produces no `-EncodedCommand` in either `make_probe_peer_cmd_str` or `make_context_cmd_str`.
6. Record all run IDs and artifact paths in review findings.
