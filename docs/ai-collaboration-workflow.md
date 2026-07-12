# AI Collaboration Workflow

## Role Policy

Roles are fixed under Plan A:

- Claude Code owns specifications, architecture, implementation, tests, and fixes.
- Codex owns independent review, verification, and risk assessment.
- Human owns scope, priority, approvals, and final acceptance.

Do not swap roles task by task. A permanent role-policy change requires an explicit human decision and updates to both root instruction files.

## Standard Workflow

`Human defines task -> Claude Code implements -> Codex reviews -> Claude Code fixes -> Codex re-reviews -> Human accepts`

## Task Entry Criteria

Implementation starts only when `tasks/current_task.md` contains:

- A clear objective
- Requirements and acceptance criteria
- In Scope and Out of Scope
- Risk level
- Known constraints and open questions

## Handoff Contract

Claude Code must provide a change summary, changed files, tests run, known limitations, and acceptance-criteria coverage. Codex must return severity-ranked findings, verification evidence, required owners, and a readiness verdict.

## High-Risk Work

Treat authentication, authorization, payment, billing, database migration, production deployment, secrets, data deletion, core business logic changes, and external integrations as high risk.

High-risk work requires an approved plan before implementation and a Codex review before human acceptance.

## Conflict Rules

- Requirements conflicts are resolved by the human.
- Claude Code does not self-approve its implementation.
- Codex does not implement its own review fixes.
- Optional improvements go to `tasks/backlog.md`; they do not block delivery unless tied to acceptance criteria or material risk.
