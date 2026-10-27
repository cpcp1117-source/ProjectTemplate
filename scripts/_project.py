"""
Shared project constants and config validation for AI collaboration scripts.
"""
import hashlib
import json
import math
import pathlib

WORKFLOW_DOC = 'docs/ai-collaboration-workflow.md'
RULE_VERSION = '1.0'

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

REQUIRED_PATHS = REQUIRED_DOCS + REQUIRED_SKILLS


def workflow_hash(root):
    """Return SHA-256[:16] of the workflow doc relative to root."""
    p = pathlib.Path(root) / WORKFLOW_DOC
    if not p.is_file():
        raise ValueError(f'workflow doc is not a file: {p}')
    return hashlib.sha256(p.read_bytes()).hexdigest()[:16]


def validate_config(root, data):
    """
    Validate a config dict against root.

    Raises ValueError with a descriptive message on any problem.
    root must be an absolute pathlib.Path (or coercible to one).
    """
    if not isinstance(data, dict):
        raise ValueError('config must be a JSON object')

    root = pathlib.Path(root)
    if not root.is_absolute():
        raise ValueError(f'root must be an absolute path, got {root!r}')

    for field in ('root', 'project_id', 'rule_version', 'rule_hash', 'cli', 'timeouts', 'overrides'):
        if field not in data:
            raise ValueError(f'config missing required field: {field!r}')

    # project_id must be a nonempty string
    if not isinstance(data['project_id'], str) or not data['project_id']:
        raise ValueError('config project_id must be a nonempty string')

    # root must be absolute and match current root
    stored_root_raw = data['root']
    if not isinstance(stored_root_raw, str):
        raise ValueError(f'config root must be a string, got {stored_root_raw!r}')
    try:
        stored_root_path = pathlib.Path(stored_root_raw)
    except (TypeError, ValueError):
        raise ValueError(f'config root is not a valid path: {stored_root_raw!r}')
    if not stored_root_path.is_absolute():
        raise ValueError(f'config root must be absolute, got {stored_root_raw!r}')
    try:
        if stored_root_path.resolve() != root.resolve():
            raise ValueError(
                f'config root {str(stored_root_path.resolve())!r} does not match '
                f'current root {str(root.resolve())!r}; '
                f'delete .collaboration/local.json and re-run init_project.py'
            )
    except OSError as e:
        raise ValueError(f'cannot resolve config root: {e}')

    # rule_version
    if data['rule_version'] != RULE_VERSION:
        raise ValueError(
            f'config rule_version {data["rule_version"]!r} != expected {RULE_VERSION!r}'
        )

    # rule_hash must match the workflow doc — guard is_file before reading
    workflow = root / WORKFLOW_DOC
    if workflow.is_file():
        actual = hashlib.sha256(workflow.read_bytes()).hexdigest()[:16]
        if data['rule_hash'] != actual:
            raise ValueError(
                f'config rule_hash {data["rule_hash"]!r} != actual {actual!r}; '
                f'workflow doc has changed — delete config and re-run init_project.py'
            )
    elif workflow.exists():
        raise ValueError(f'workflow doc path exists but is not a file: {workflow}')

    # cli: dict, keys only claude/codex, values string or null
    cli = data['cli']
    if not isinstance(cli, dict):
        raise ValueError('config cli must be a JSON object')
    for key in cli:
        if key not in ('claude', 'codex'):
            raise ValueError(f'config cli has unexpected key: {key!r}')
        val = cli[key]
        if val is not None and not isinstance(val, str):
            raise ValueError(f'config cli.{key} must be a string path or null, got {val!r}')

    # overrides: dict, values nonempty strings
    overrides = data['overrides']
    if not isinstance(overrides, dict):
        raise ValueError('config overrides must be a JSON object')
    for key, val in overrides.items():
        if not isinstance(val, str) or not val:
            raise ValueError(
                f'config overrides.{key!r} must be a nonempty string, got {val!r}'
            )

    # timeouts: dict, required fields are finite positive non-bool numbers
    timeouts = data['timeouts']
    if not isinstance(timeouts, dict):
        raise ValueError('config timeouts must be a JSON object')
    for field in ('preflight_secs', 'live_secs'):
        if field not in timeouts:
            raise ValueError(f'config timeouts missing required field: {field!r}')
        val = timeouts[field]
        if (
            isinstance(val, bool)
            or not isinstance(val, (int, float))
            or not math.isfinite(val)
            or val <= 0
        ):
            raise ValueError(
                f'config timeouts.{field} must be a finite positive number (not bool), '
                f'got {val!r}'
            )

    # mandatory paths must exist as files
    for rel in REQUIRED_PATHS:
        if not (root / rel).is_file():
            raise ValueError(f'required file missing: {rel}')


def load_config(root):
    """
    Load and validate .collaboration/local.json relative to root.

    Returns the validated config dict.
    Raises ValueError with a useful message for missing, corrupt, or invalid config.
    """
    root = pathlib.Path(root)
    config_file = root / '.collaboration' / 'local.json'

    if not config_file.exists():
        raise ValueError(
            f'config not found at {config_file}; '
            f'run: python scripts/init_project.py'
        )

    try:
        text = config_file.read_text(encoding='utf-8')
    except OSError as e:
        raise ValueError(f'cannot read config at {config_file}: {e}')

    try:
        data = json.loads(text)
    except json.JSONDecodeError as e:
        raise ValueError(f'malformed JSON in config at {config_file}: {e}')

    validate_config(root, data)
    return data
