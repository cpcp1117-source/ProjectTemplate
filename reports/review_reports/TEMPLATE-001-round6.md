# Delivery Quality Re-review

## Review Target

- Task ID: TEMPLATE-001; Date: 2026-10-26; Reviewer: Codex; Implementation Owner: Claude.
- Baseline: local a6a6075; runtime snapshot: reports/test_reports/TEMPLATE-001-round6-manifest.json.
- Claude sessions: round5 b01bbb39-17b2-4d04-b4e1-60600622b826; round6 ea86be2a-4876-40a1-b50a-95ee30c073d4.
- User-authorized repair continuation: rounds 5-6 completed. Additional authorization pending; no further code edits by Codex.

## Executive Verdict

**Not Ready**. Claude-to-Codex now works in a real invocation. Codex-to-Claude timed out. Do not merge or claim full bidirectional acceptance.

## Validation Evidence

| Check | Result | Evidence |
|---|---|---|
| Unit tests, native Windows | 125 total: 123 passed / 1 failed / 1 skipped | python -X utf8 -m unittest discover -s tests -v; exit1 |
| compileall scripts | Pass | python -X utf8 -m compileall -q scripts; exit0 |
| Direct live Claude / Codex | Pass / Pass | run 7a0695678c41f684 |
| Claude-to-Codex | Pass | Actual outer tool and inner artifact, session and payload verification |
| Codex-to-Claude | Fail(timeout) | Same run; 180-second outer timeout; helper cleanup returned normally |
| Native PowerShell shell smoke | Pass | Actual command execution exit0 |
| Special-path subprocess argv and Claude Bash argv reconstruction | Pass | Isolated root containing CJK, spaces, apostrophe, &, $, % |
| Same displayed command executed in native PowerShell | Fail | Wrong argv / path; shlex quoting is unsuitable for native PowerShell |
| Bare full-command echo | Rejected | Correctly returns no tool evidence |
| PowerShell-wrapped Write-Output of expected command | Incorrectly accepted | Returns tool evidence True without actual peer invocation |
| git diff --check | Fail | Two trailing-space lines in docs/collaboration-validation.md |
| Three consecutive full live runs / minimum bidirectional task artifacts | Not Run | Stop after first true reverse-direction failure; no useful repeated timeout |

Full CLI logs remain local and ignored under reports/connection_reports/check_7a0695678c41f684*.json. Independent logs: workspace work/round6-tests.log, round6-independent.log, round6-network-smoke.log, round6-network-enabled-smoke.log. No credential content published.

## Findings

| ID | Severity | Axis | Evidence / Impact | Required Fix | Owner | Verification |
|---|---|---|---|---|---|---|
| R6-01 | Major | Integration / Operational Risk | Reverse direction timed out; separate default sandbox HTTP diagnostic got socket permission denied. These support a network restriction hypothesis; timeout itself does not prove its sole cause. A one-off network_access=true diagnostic progressed to TLS/SSPI error, so reachability remains undetermined | Investigate actual helper lifecycle and credential/network restrictions; use supported scoped permissions or host-side bounded tool integration, without disabling sandbox or copying credentials | Claude | Actual codex-to-claude succeeds, real inner session and tool evidence; denied permissions fail honestly |
| R6-02 | Major | Engineering / Portability | _ps_command_str emits POSIX shlex quoting for both shell types. Running displayed command in native PowerShell fails for special-character root | Separate native PowerShell and Bash command syntax; preserve argument arrays; avoid model rewriting commands to compensate | Claude | Execute generated commands for both variants with special paths and verify JSON fields |
| R6-03 | Major | Validation Evidence | _codex_tool_evidence accepts pwsh -Command Write-Output of expected command via substring. Current full_command_echo test only tests final prose, not command event | Parse known execution wrappers and reject printing/quoted data; validate whole event stream | Claude | True execution passes; echo / Write-Output inside PowerShell wrappers and malformed trailing events fail |
| R6-04 | Minor | Validation / Docs | Optional PS1 test rejects comment saying No -EncodedCommand; assertion is based on raw substring; 2 trailing spaces in docs | Test executable behavior or parsed tokens, keep intended command restrictions; cleanup whitespace | Claude | Full suite pass, appropriate platform skip explicit; diff check clean |

## Review Axes

- Spec Fidelity: Transparent commands and conditional success reporting improve the original refusal path; real Claude-to-Codex passes. Full reverse path remains unmet.
- Engineering Standards: CLI strict parsing/full-field artifact validation preserved; shell quoting and command matching remain incomplete.
- Validation Evidence and Operational Risk: Local tests cannot replace live nested execution. No successful repeated full integration or real reverse collaboration task demonstrated.

## Handoff Notes

Keep TEMPLATE-001 and current dirty snapshot. Source changes remain local and uncommitted; existing remote Draft PR remains at previous reviewed commit. Do not publish a Ready claim. Next implementation requires explicit extra rounds under COLLABORATION.md section7. Reuse current evidence and investigate reverse permissions before another full 180-second test. Outer/inner time budgets must allow clean diagnostics and cleanup.
