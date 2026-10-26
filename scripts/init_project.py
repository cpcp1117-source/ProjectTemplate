#!/usr/bin/env python3
"""
Initialize .collaboration/local.json for an AI collaboration project.

Usage:
  python scripts/init_project.py [--root PATH]

Behaviour:
  - Infers project root from this script's location (scripts/ -> parent).
  - Use --root for isolated tests with a different root.
  - Generates .collaboration/local.json (gitignored).
  - Does NOT overwrite an existing config; validates it instead.
  - Detects a moved config root and reports how to regenerate.
  - Fails with a useful error if required template files are missing.
  - Uses exclusive file creation (open 'x') — reports a race cleanly.

No pip dependencies. Python 3.11+ standard library only.
"""

import argparse
import json
import os
import pathlib
import shutil
import sys

sys.path.insert(0, str(pathlib.Path(__file__).parent))
from _project import (
    REQUIRED_PATHS,
    RULE_VERSION,
    WORKFLOW_DOC,
    load_config,
    validate_config,
    workflow_hash,
)


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

    # Always check required files — even on repeated init.
    _check_required_files(root)

    if config_file.exists():
        _check_existing_config(config_file, root)
        # _check_existing_config exits; this line is unreachable.

    rule_hash = workflow_hash(root)
    config = _build_config(root, rule_hash)

    config_file.parent.mkdir(parents=True, exist_ok=True)
    _write_config_exclusive(config_file, config)

    print(f'Created: {config_file}')
    print(f'Project: {config["project_id"]}')
    print(f'Rule version: {RULE_VERSION}  hash: {rule_hash}')
    _warn_missing_clis(config)
    return 0


def _resolve_root(explicit_root):
    if explicit_root:
        return pathlib.Path(explicit_root).resolve()
    return pathlib.Path(__file__).resolve().parent.parent


def _check_required_files(root):
    missing = [str(root / r) for r in REQUIRED_PATHS if not (root / r).is_file()]
    if missing:
        print('ERROR: Required files not found:')
        for m in missing:
            print(f'  {m}')
        sys.exit(1)


def _check_existing_config(config_file, root):
    """Validate the existing config. Exit 0 if valid; exit 1 if invalid/moved/stale."""
    try:
        load_config(root)
    except ValueError as e:
        print(f'ERROR: Existing config is invalid: {e}')
        print('  Delete .collaboration/local.json and re-run to regenerate.')
        sys.exit(1)

    print(f'Config already exists at {config_file} and is valid. No changes made.')
    sys.exit(0)


def _build_config(root, rule_hash):
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


def _write_config_exclusive(config_file, config):
    """Write config using exclusive open — fails cleanly on a concurrent race."""
    content = json.dumps(config, indent=2, ensure_ascii=False)
    try:
        fd = os.open(str(config_file), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644)
    except FileExistsError:
        print(
            f'ERROR: Config appeared at {config_file} while initialising '
            f'(concurrent race). Re-run init_project.py to validate it.'
        )
        sys.exit(1)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as f:
            f.write(content)
    except Exception:
        try:
            os.unlink(str(config_file))
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
