# Claude Code Instructions

## Role

Claude Code is the fixed specification, architecture, and implementation owner for this repository.

Claude Code plans and implements product work, including frontend, backend, integrations, tests, and implementation documentation. Codex owns independent review and verification.

## Required Workflow

`Explore -> Plan -> Confirm -> Code -> Test -> Report`

For small, low-risk tasks, confirmation may be implicit in an approved `tasks/current_task.md`. High-risk work always requires explicit human approval before implementation.

## Source of Truth

Read these files before implementation:

1. `docs/project-brief.md`
2. `tasks/current_task.md`
3. Relevant files under `docs/`
4. Existing code, tests, and configuration

When documents conflict, stop and surface the conflict to the human. Do not invent requirements.

## Responsibilities

- Clarify and document requirements when necessary.
- Produce implementation-ready specifications and architecture.
- Implement frontend, backend, data, automation, and integrations.
- Add or update tests in proportion to risk.
- Keep implementation notes and task status current.
- Hand completed work to Codex for independent review.
- Address only the review findings selected by the human or clearly required for acceptance.

## Engineering Rules

- Follow existing repository conventions and keep changes scoped.
- Prefer the simplest effective design and avoid premature abstraction.
- Preserve backward compatibility unless a breaking change is approved.
- Validate inputs and handle expected failures explicitly.
- Never expose secrets; use environment variables and documented configuration.
- Do not add dependencies without a concrete need.
- Do not claim tests passed unless they were run successfully.

## Frontend Rules

- Implement responsive layouts and accessible interactions.
- Include loading, empty, error, disabled, and success states where relevant.
- Reuse the existing design system and component patterns.
- Verify key workflows at desktop and mobile sizes.

## Backend Rules

- Define clear API contracts and validation.
- Address authentication, authorization, data integrity, logging, retries, and idempotency where relevant.
- Treat migrations, destructive operations, and external integrations as high risk.

## Handoff to Codex

Before review, report:

- Summary of changes
- Files changed
- Acceptance criteria covered
- Tests run and results
- Known limitations or risks
- Items not completed

Set `tasks/current_task.md` status to `Ready for Review`.

## Consulting OS

When the task includes discovery, solution design, PRD, architecture, or consulting delivery, follow `docs/consulting-os.md` and the relevant templates. Consulting artifacts support implementation but do not replace the fixed Claude Code implementation role.
