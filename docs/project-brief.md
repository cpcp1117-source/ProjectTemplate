# Project Brief

## Project ID

TBD (e.g. PROJ-001)

## Project Name

TBD

## Problem

TBD

## Goal

TBD

## Target Users

TBD

## Core Use Cases

TBD

## In Scope

TBD

## Out of Scope

TBD

## Success Criteria

TBD

## Constraints

TBD

## Assumptions

TBD

## Open Questions

TBD

## Collaboration Scope

<!-- Fill in when setting up a real project -->

### Claude Code Responsibilities

TBD (e.g. frontend, backend, integration, tests, implementation docs)

### Codex Responsibilities

TBD (e.g. requirements, design review, verification, release docs)

### Shared Boundaries

- No actor self-approval: Claude Code does not approve its own implementation; Codex does not modify implementation then approve it.
- Both read `docs/ai-collaboration-workflow.md` before each task.
- Timeout for each task defined in `tasks/current_task.md`.

## Runtime / Environment

TBD (e.g. Python 3.11+, Node 20+, OS requirements)

## Setup / Run / Test Commands

```bash
# Setup
TBD

# Run
TBD

# Test
python -X utf8 -m unittest tests/test_scripts.py -v
```

## Deployment Target / Rollback

- Target: TBD (e.g. production server, container registry, GitHub Pages)
- Rollback: TBD (e.g. revert to previous image tag, git revert + redeploy)

## Monitoring

- Scope: TBD (e.g. uptime, error rate, latency)
- Interval: TBD (e.g. every 5 minutes)
- Notification: TBD (e.g. email alert, Slack webhook)

## CLI Invocation Examples

```bash
# Initialize collaboration config (run once per project):
python scripts/init_project.py

# Preflight check (no model calls):
python scripts/check_connection.py

# Live connectivity test (requires CLI login):
python scripts/check_connection.py --live

# Bidirectional probe (outer model calls helper, requires --live):
python scripts/check_connection.py --live --bidirectional

# Run tests (stdlib unittest, no pytest required):
python -m unittest tests/test_scripts.py
```

## Session and Log Handling

- Model session IDs are recorded in `reports/connection_reports/check_<run_id>.json` under `model_session_ids`.
- Raw stdout/stderr logs saved to `reports/connection_reports/check_<run_id>_raw.json`.
- Connection reports are gitignored; see `.gitignore`.
