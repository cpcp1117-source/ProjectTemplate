# Project Template

Reusable repository template for a fixed Claude Code implementation and Codex review workflow, with an optional Consulting OS extension.

## Fixed Roles

| Owner | Fixed responsibility |
|---|---|
| Human | Defines priorities, approves scope, resolves trade-offs, and accepts delivery |
| Claude Code | Specifications, architecture, coding, frontend, backend, tests, and fixes |
| Codex | Independent review, verification, risk analysis, and readiness verdict |

## Quick Start

1. Create a repository from this template.
2. Complete `docs/project-brief.md`.
3. Define the active work in `tasks/current_task.md`.
4. Ask Claude Code to implement the approved task.
5. Ask Codex to review the completed implementation.
6. Route required fixes to Claude Code, then repeat review until accepted.

## Core Files

- `AGENTS.md`: fixed Codex review rules.
- `CLAUDE.md`: fixed Claude Code implementation rules.
- `docs/ai-collaboration-workflow.md`: handoff and governance.
- `docs/consulting-os.md`: optional consulting workflow.
- `tasks/current_task.md`: single source of truth for active work.
- `.agents/`: specialist consulting roles.
- `docs/templates/`: reusable consulting deliverables.

This template intentionally does not prescribe a technology stack. Add project-specific manifests and infrastructure only after the project constraints are known.
