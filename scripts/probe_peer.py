#!/usr/bin/env python3
"""
Bounded peer probe helper for bidirectional connection testing.

Invoked by the outer model (Claude or Codex) via a Bash tool call, orchestrated
by check_connection.py --bidirectional ONLY.

This script has NO further delegation authority (Rule 8: No Recursive Delegation).
It calls the target CLI once, validates the response strictly, and writes evidence.

Usage:
  python scripts/probe_peer.py --direction claude-to-codex|codex-to-claude
                               --nonce NONCE --root ROOT

Writes inner evidence to:
  reports/connection_reports/probe_<direction>_<nonce>.json  (gitignored)

Evidence includes: status, direction, nonce, rc, inner_session_id, root, rule_hash,
                   target_cli, raw_inner_stdout, stderr.

Restrictions:
  - Depth guard: refuses if AI_COLLAB_NO_DELEGATION set or AI_COLLAB_CHECK_DEPTH > 1.
  - Does NOT call check_connection.py or probe_peer.py itself.
  - May only write to reports/connection_reports/.
  - Reports auth/sandbox blocks honestly without bypassing them.
"""

import argparse
import hashlib
import json
import os
import pathlib
import re
import subprocess
import sys
import time

_RECURSION_ENV = 'AI_COLLAB_CHECK_DEPTH'
_NO_DELEGATION_ENV = 'AI_COLLAB_NO_DELEGATION'

WORKFLOW_DOC = 'docs/ai-collaboration-workflow.md'


def main(argv=None):
    # Depth / delegation guard — checked at entry before anything else
    if os.environ.get(_NO_DELEGATION_ENV):
        _die_early('AI_COLLAB_NO_DELEGATION is set — delegation refused')

    depth_raw = os.environ.get(_RECURSION_ENV, '0')
    try:
        current_depth = int(depth_raw)
    except (ValueError, TypeError):
        _die_early(f'AI_COLLAB_CHECK_DEPTH is not an integer: {depth_raw!r}')
        return  # unreachable; satisfies linters

    if current_depth > 1:
        _die_early(
            f'Depth guard: AI_COLLAB_CHECK_DEPTH={current_depth} > 1. '
            f'Refusing recursive probe_peer invocation.'
        )

    parser = argparse.ArgumentParser(description='Bounded peer probe helper.')
    parser.add_argument('--direction', required=True,
                        choices=['claude-to-codex', 'codex-to-claude'])
    parser.add_argument('--nonce', required=True)
    parser.add_argument('--root', required=True)
    parser.add_argument('--depth', type=int, default=None)
    args = parser.parse_args(argv)

    # Validate --nonce: must be nonempty bounded hex, no traversal or control chars
    _NONCE_RE = re.compile(r'^[a-fA-F0-9]{1,64}$')
    if not _NONCE_RE.match(args.nonce):
        _die_early(
            f'--nonce must be 1-64 hex characters [a-fA-F0-9], got {args.nonce!r}; '
            f'rejecting to prevent path traversal'
        )

    root_raw = args.root
    try:
        root = pathlib.Path(root_raw).resolve()
    except (TypeError, ValueError) as e:
        _die_early(f'invalid --root: {e}')
        return

    # Validate root is absolute before anything else
    if not pathlib.Path(root_raw).is_absolute():
        _die_early(f'--root must be an absolute path, got {root_raw!r}')

    sys.path.insert(0, str(pathlib.Path(__file__).parent))

    # Validate --depth if provided
    if args.depth is not None:
        if args.depth < 0 or args.depth > 1:
            _die_early(f'--depth must be 0 or 1, got {args.depth!r}')

    config, config_err = _load_config(root)
    if config_err:
        _die_early(f'config error: {config_err}')
        return
    overrides = config.get('overrides', {})
    timeout = config.get('timeouts', {}).get('live_secs', 180)

    # Compute local rule_hash — guard is_file
    workflow = root / WORKFLOW_DOC
    if not workflow.is_file():
        _write_and_die(root, args.direction, args.nonce, {
            'status': 'Fail(workflow doc missing or not a file)',
            'direction': args.direction,
            'target_cli': _target_cli(args.direction),
            'nonce': args.nonce,
            'rc': None,
            'inner_session_id': None,
            'root': str(root),
            'rule_hash': '',
            'raw_inner_stdout': None,
            'stderr': None,
        }, 'workflow doc missing or not a file')

    rule_hash = hashlib.sha256(workflow.read_bytes()).hexdigest()[:16]
    target_cli = _target_cli(args.direction)

    cli_path = _get_cli_path(target_cli, config, overrides)
    if not cli_path:
        _write_and_die(root, args.direction, args.nonce, {
            'status': 'Fail(target CLI not found)',
            'direction': args.direction,
            'target_cli': target_cli,
            'nonce': args.nonce,
            'rc': None,
            'inner_session_id': None,
            'root': str(root),
            'rule_hash': rule_hash,
            'raw_inner_stdout': None,
            'stderr': None,
        }, f'{target_cli} CLI not found')

    # Build context command for inner model
    context_script = root / 'scripts' / 'probe_context.py'
    if not context_script.is_file():
        _write_and_die(root, args.direction, args.nonce, {
            'status': 'Fail(probe_context.py missing)',
            'direction': args.direction,
            'target_cli': target_cli,
            'nonce': args.nonce,
            'rc': None,
            'inner_session_id': None,
            'root': str(root),
            'rule_hash': rule_hash,
            'raw_inner_stdout': None,
            'stderr': None,
        }, 'probe_context.py missing')

    from _probe import make_context_cmd_str, probe_env
    ctx_cmd_str, _ctx_args = make_context_cmd_str(
        root, context_script, args.nonce, direction=args.direction
    )

    try:
        from _cli_helper import build_args, run_with_timeout
        if target_cli == 'claude':
            prompt = _make_inner_prompt_claude(args.nonce, args.direction, ctx_cmd_str)
            user_args = [
                '-p', prompt,
                '--effort', 'low',
                '--tools', 'Read,Bash',
                '--allowedTools', 'Read,Bash',
                '--output-format', 'stream-json',
                '--verbose',
            ]
        else:
            prompt = _make_inner_prompt_codex(args.nonce, args.direction, ctx_cmd_str)
            user_args = [
                'exec', '--json',
                '--sandbox', 'read-only',
                '--cd', str(root),
                '--skip-git-repo-check',
                prompt,
            ]
        full_args = build_args(cli_path, user_args)
    except ValueError as e:
        _write_and_die(root, args.direction, args.nonce, {
            'status': f'Fail(shim resolution: {e})',
            'direction': args.direction,
            'target_cli': target_cli,
            'nonce': args.nonce,
            'rc': None,
            'inner_session_id': None,
            'root': str(root),
            'rule_hash': rule_hash,
            'raw_inner_stdout': None,
            'stderr': None,
        }, str(e))

    # inner runs at depth=2, NO_DELEGATION — must not call any agents
    inner_env = probe_env(os.environ, depth=2, no_delegation=True)

    rc = None
    out = ''
    err = ''
    try:
        rc, out, err = run_with_timeout(
            full_args,
            timeout_secs=timeout,
            cwd=str(root),
            env=inner_env,
        )
    except subprocess.TimeoutExpired:
        _write_and_die(root, args.direction, args.nonce, {
            'status': 'Fail(inner timeout)',
            'direction': args.direction,
            'target_cli': target_cli,
            'nonce': args.nonce,
            'rc': None,
            'inner_session_id': None,
            'root': str(root),
            'rule_hash': rule_hash,
            'raw_inner_stdout': out or None,
            'stderr': err or None,
        }, 'inner timeout')
    except OSError as e:
        _write_and_die(root, args.direction, args.nonce, {
            'status': f'Fail(OSError: {e})',
            'direction': args.direction,
            'target_cli': target_cli,
            'nonce': args.nonce,
            'rc': None,
            'inner_session_id': None,
            'root': str(root),
            'rule_hash': rule_hash,
            'raw_inner_stdout': None,
            'stderr': None,
        }, str(e))
    except Exception as e:
        _write_and_die(root, args.direction, args.nonce, {
            'status': f'Fail({type(e).__name__}: {e})',
            'direction': args.direction,
            'target_cli': target_cli,
            'nonce': args.nonce,
            'rc': None,
            'inner_session_id': None,
            'root': str(root),
            'rule_hash': rule_hash,
            'raw_inner_stdout': None,
            'stderr': None,
        }, str(e))

    # Strict decode and validate inner response
    from _protocol import decode, validate_payload

    status = 'Pass'
    inner_session_id = None
    try:
        inner_payload, inner_session_id = decode(target_cli, rc, out)
    except ValueError as exc:
        status = f'Fail(inner decode: {exc})'
        _write_and_die(root, args.direction, args.nonce, {
            'status': status,
            'direction': args.direction,
            'target_cli': target_cli,
            'nonce': args.nonce,
            'rc': rc,
            'inner_session_id': None,
            'root': str(root),
            'rule_hash': rule_hash,
            'raw_inner_stdout': out,
            'stderr': err,
        }, status)

    # Validate nonce exactly
    if inner_payload.get('nonce') != args.nonce:
        status = f'Fail(nonce mismatch: got {inner_payload.get("nonce")!r})'
        _write_and_die(root, args.direction, args.nonce, {
            'status': status,
            'direction': args.direction,
            'target_cli': target_cli,
            'nonce': args.nonce,
            'rc': rc,
            'inner_session_id': inner_session_id,
            'root': str(root),
            'rule_hash': rule_hash,
            'raw_inner_stdout': out,
            'stderr': err,
        }, status)

    # Validate root (must be absolute, not normalized from '.')
    got_root = inner_payload.get('root', '')
    if not got_root or not pathlib.Path(str(got_root)).is_absolute():
        status = f'Fail(inner root not absolute: {got_root!r})'
        _write_and_die(root, args.direction, args.nonce, {
            'status': status,
            'direction': args.direction,
            'target_cli': target_cli,
            'nonce': args.nonce,
            'rc': rc,
            'inner_session_id': inner_session_id,
            'root': str(root),
            'rule_hash': rule_hash,
            'raw_inner_stdout': out,
            'stderr': err,
        }, status)

    try:
        validate_payload(inner_payload, {
            'root': str(root),
            'rule_hash': rule_hash,
            'direction': args.direction,
        })
    except ValueError as exc:
        status = f'Fail(inner validation: {exc})'
        _write_and_die(root, args.direction, args.nonce, {
            'status': status,
            'direction': args.direction,
            'target_cli': target_cli,
            'nonce': args.nonce,
            'rc': rc,
            'inner_session_id': inner_session_id,
            'root': str(root),
            'rule_hash': rule_hash,
            'raw_inner_stdout': out,
            'stderr': err,
        }, status)

    # Write success evidence
    evidence = {
        'status': 'Pass',
        'direction': args.direction,
        'target_cli': target_cli,
        'nonce': args.nonce,
        'rc': rc,
        'inner_session_id': inner_session_id,
        'root': str(root),
        'rule_hash': rule_hash,
        'raw_inner_stdout': out,
        'stderr': err,
        'timestamp': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()),
    }
    _write_evidence(root, args.direction, args.nonce, evidence)

    print(json.dumps({
        'status': 'Pass',
        'direction': args.direction,
        'nonce': args.nonce,
        'inner_session_id': inner_session_id,
    }))
    return 0


# ---------------------------------------------------------------------------
# Prompt builders for inner model
# ---------------------------------------------------------------------------

def _make_inner_prompt_claude(nonce, direction, ctx_cmd_str):
    return (
        f'You are a connectivity probe inner target. '
        f'AI_COLLAB_NO_DELEGATION is set — do NOT call agents, CLIs, or delegate. '
        f'Use Bash to run this exact command:\n'
        f'  {ctx_cmd_str}\n'
        f'Return ONLY the JSON object it prints as your entire response. '
        f'Expected nonce: {nonce!r}, direction: {direction!r}. '
        f'Do not fabricate hash or root; run the command and return its output.'
    )


def _make_inner_prompt_codex(nonce, direction, ctx_cmd_str):
    return (
        f'Connectivity probe inner target. NO_DELEGATION: do not delegate. '
        f'Run: {ctx_cmd_str}\n'
        f'Return ONLY the JSON it prints. '
        f'Expected nonce: {nonce!r}, direction: {direction!r}.'
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _target_cli(direction):
    return 'codex' if direction == 'claude-to-codex' else 'claude'


def _write_evidence(root, direction, nonce, data):
    report_dir = root / 'reports' / 'connection_reports'
    try:
        report_dir.mkdir(parents=True, exist_ok=True)
        path = report_dir / f'probe_{direction}_{nonce}.json'
        path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding='utf-8')
    except OSError as e:
        # Print to stderr so caller can detect write failure; absence of artifact is failure
        print(f'ERROR: failed to write evidence artifact: {e}', file=sys.stderr)
        sys.exit(1)


def _write_and_die(root, direction, nonce, data, msg):
    _write_evidence(root, direction, nonce, data)
    print(json.dumps({'status': data.get('status', f'Fail({msg})'),
                      'nonce': nonce}))
    sys.exit(1)


def _die_early(msg):
    print(f'ERROR: {msg}', file=sys.stderr)
    sys.exit(1)


def _load_config(root):
    """
    Load and validate config using shared loader.
    Returns (config_dict, error_str_or_None).
    """
    try:
        from _project import load_config as _shared_load
        config = _shared_load(root)
        return config, None
    except ValueError as e:
        return {}, str(e)
    except OSError as e:
        return {}, f'OSError reading config: {e}'


def _get_cli_path(cli, config, overrides):
    override = overrides.get(cli) if overrides else None
    if override:
        return str(override)
    stored = config.get('cli', {}).get(cli)
    if stored:
        return stored
    import shutil
    return shutil.which(cli)


if __name__ == '__main__':
    sys.exit(main())
