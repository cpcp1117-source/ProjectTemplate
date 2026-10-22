#!/usr/bin/env python3
"""
Initialize .collaboration/local.json for an AI collaboration project.

Usage:
  python scripts/init_project.py [--root PATH]

Behaviour:
  - Infers project root from this script's location (scripts/ -> parent).
  - Use --root for isolated tests with a different root.
  - Generates .collaboration/local.json (gitignored).
  - Does NOT overwrite an existing config.
  - Detects a moved config root and reports how to regenerate.
  - Fails with a useful error if required template files are missing.

No pip dependencies. Python 3.11+ standard library only.
"""

import argparse
import hashlib
import json
import os
import pathlib
import sys

REQUIRED_DOCS = [
    'docs/ai-collaboration-workflow.md',
    'docs/project-brief.md',
    'tasks/current_task.md',
    'CLAUDE.md',
    'AGENTS.md',
]

REQUIRED_SKILLS = [
    'docs/skills/collaboration-handoff.md',
    'docs/skills/verify-and-review.md',
    'docs/skills/remediate-findings.md',
    '.agents/skills/collaboration-handoff/SKILL.md',
    '.agents/skills/verify-and-review/SKILL.md',
    '.agents/skills/remediate-findings/SKILL.md',
    '.claude/skills/collaboration-handoff/SKILL.md',
    '.claude/skills/verify-and-review/SKILL.md',
    '.claude/skills/remediate-findings/SKILL.md',
]

RULE_VERSION = '1.0'
WORKFLOW_DOC = 'docs/ai-collaboration-workflow.md'


def main(argv=None):
    parser = argparse.ArgumentParser(
        description='Initialize AI collaboration local config.'
    )
    parser.add_argument(
        '--root',
        help='Explicit project root path (for isolated tests)',
    )
    args = parser.parse_args(argv)

    root = _resolve_root(args.root)
    config_file = root / '.collaboration' / 'local.json'

    _check_existing_config(config_file, root)
    _check_required_files(root)

    rule_hash = _compute_rule_hash(root)
    config = _build_config(root, rule_hash)

    config_file.parent.mkdir(parents=True, exist_ok=True)
    _write_config_atomic(config_file, config)

    print(f'Created: {config_file}')
    print(f'Project: {config["project_id"]}')
    print(f'Rule version: {RULE_VERSION}  hash: {rule_hash}')
    _warn_missing_clis(config)
    return 0


def _resolve_root(explicit_root):
    if explicit_root:
        return pathlib.Path(explicit_root).resolve()
    # scripts/ is one level below project root
    return pathlib.Path(__file__).resolve().parent.parent


def _check_existing_config(config_file, current_root):
    if not config_file.exists():
        return

    try:
        text = config_file.read_text(encoding='utf-8')
    except OSError as e:
        print(f'ERROR: Cannot read existing config at {config_file}: {e}')
        print('  Delete it and re-run to regenerate.')
        sys.exit(1)

    try:
        existing = json.loads(text)
    except json.JSONDecodeError as e:
        print(f'ERROR: Existing config at {config_file} is malformed JSON: {e}')
        print('  Delete it and re-run to regenerate.')
        sys.exit(1)

    if not isinstance(existing, dict):
        print(f'ERROR: Existing config at {config_file} is not a JSON object.')
        print('  Delete it and re-run to regenerate.')
        sys.exit(1)

    stored_root_str = existing.get('root', '')
    if not stored_root_str:
        print(f'ERROR: Existing config at {config_file} has no "root" field.')
        print('  Delete it and re-run to regenerate.')
        sys.exit(1)

    stored_root = pathlib.Path(stored_root_str).resolve()
    if stored_root != current_root:
        print(f'ERROR: Config exists at {config_file} but references a different root.')
        print(f'  Stored root : {stored_root}')
        print(f'  Current root: {current_root}')
        print('  To regenerate: delete .collaboration/local.json and re-run.')
        sys.exit(1)

    # Validate config has useful shape
    missing_fields = []
    for field in ('rule_hash', 'rule_version', 'cli', 'timeouts'):
        if field not in existing:
            missing_fields.append(field)
    if missing_fields:
        print(f'ERROR: Existing config at {config_file} is missing required fields: '
              f'{missing_fields}')
        print('  Delete it and re-run to regenerate.')
        sys.exit(1)

    # Validate rule_hash matches current workflow file
    required_files = REQUIRED_DOCS + REQUIRED_SKILLS
    workflow_path = current_root / WORKFLOW_DOC
    if workflow_path.exists():
        actual_hash = hashlib.sha256(workflow_path.read_bytes()).hexdigest()[:16]
        stored_hash = existing.get('rule_hash', '')
        if stored_hash and stored_hash != actual_hash:
            print(f'WARNING: Config rule_hash mismatch.')
            print(f'  Stored : {stored_hash}')
            print(f'  Actual : {actual_hash}')
            print('  The workflow document has changed since last init.')
            print('  Delete .collaboration/local.json and re-run to update.')

    print(f'Config already exists at {config_file}. No changes made.')
    sys.exit(0)


def _check_required_files(root):
    required = REQUIRED_DOCS + REQUIRED_SKILLS
    missing = [str(root / r) for r in required if not (root / r).exists()]
    if missing:
        print('ERROR: Required files not found:')
        for m in missing:
            print(f'  {m}')
        sys.exit(1)


def _compute_rule_hash(root):
    workflow = root / WORKFLOW_DOC
    return hashlib.sha256(workflow.read_bytes()).hexdigest()[:16]


def _build_config(root, rule_hash):
    import shutil
    return {
        'root': str(root),
        'project_id': root.name,
        'rule_version': RULE_VERSION,
        'rule_hash': rule_hash,
        'cli': {
            'claude': shutil.which('claude') or None,
            'codex': shutil.which('codex') or None,
        },
        'timeouts': {
            'preflight_secs': 15,
            'live_secs': 180,
        },
        'overrides': {},
    }


def _write_config_atomic(config_file, config):
    """Write config using a temp file + rename for atomic, exclusive creation."""
    import tempfile
    parent = config_file.parent
    content = json.dumps(config, indent=2, ensure_ascii=False)
    # Write to a temp file in the same directory, then rename atomically.
    # On Windows, os.replace() is atomic within the same volume.
    fd, tmp_path = tempfile.mkstemp(dir=str(parent), suffix='.tmp')
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as f:
            f.write(content)
        os.replace(tmp_path, str(config_file))
    except Exception:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


def _warn_missing_clis(config):
    for cli in ('claude', 'codex'):
        if not config['cli'].get(cli):
            print(
                f'WARNING: {cli} CLI not found on PATH. '
                f'Set overrides.{cli} in .collaboration/local.json to a valid path.'
            )


if __name__ == '__main__':
    sys.exit(main())
