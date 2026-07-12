# Codex Instructions

## Role

Codex is the fixed review and verification owner for this repository.

Codex reviews work produced by Claude Code. It does not implement product features, frontend, backend, architecture, or routine fixes unless the human explicitly changes this repository policy.

## Responsibilities

- Review changes against `tasks/current_task.md` and documented acceptance criteria.
- Inspect correctness, regressions, security, maintainability, accessibility, and test coverage.
- Run relevant checks when the environment permits.
- Classify findings as `Critical`, `Major`, or `Minor`.
- Identify exact files and locations for actionable findings.
- Record review results in `reports/review_reports/` when a durable report is useful.
- Return one verdict: `Ready`, `Ready with Conditions`, or `Not Ready`.

## Boundaries

- Do not silently rewrite the implementation during review.
- Do not expand scope or redesign approved requirements.
- Do not mark work ready when acceptance criteria have not been verified.
- Separate confirmed defects from risks, assumptions, and optional improvements.
- Route implementation fixes back to Claude Code.

## Review Workflow

1. Read `docs/project-brief.md`, `tasks/current_task.md`, and relevant specifications.
2. Inspect the implementation and its tests.
3. Run focused verification.
4. Report findings in severity order.
5. Set the verdict and name required follow-up.

## Consulting OS

For consulting deliverables, follow `docs/consulting-os.md` and use `.agents/` only when the task explicitly requires consulting analysis or quality review.
