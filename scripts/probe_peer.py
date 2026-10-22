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
  reports/connection_reports/probe_<direction>_<nonce>.json

Evidence includes: status, direction, nonce, rc, session_id, root, rule_hash.

Restrictions:
  - Depth guard: refuses if AI_COLLAB_CHECK_DEPTH > 1.
  - Does NOT call check_connection.py or probe_peer.py itself.
  - May only write to reports/connection_reports/.
  - Reports auth/sandbox blocks honestly without bypassing them.
"""

import argparse
import hashlib
import json
import os
import pathlib
import subprocess
import sys
import time

_RECURSION_ENV = 'AI_COLLAB_CHECK_DEPTH'
_NO_DELEGATION_ENV = 'AI_COLLAB_NO_DELEGATION'

WORKFLOW_DOC = 'docs/ai-collaboration-workflow.md'


def main(argv=None):
    # Depth guard — checked at entry before anything else
    current_depth = int(os.environ.get(_RECURSION_ENV, '0'))
    if current_depth > 1:
        _die(f'Depth guard: AI_COLLAB_CHECK_DEPTH={current_depth} > 1. '
             f'Refusing recursive probe_peer invocation.')

    parser = argparse.ArgumentParser(description='Bounded peer probe helper.')
    parser.add_argument('--direction', required=True,
                        choices=['claude-to-codex', 'codex-to-claude'],
                        help='Which direction to probe')
    parser.add_argument('--nonce', required=True, help='Unique nonce for this probe')
    parser.add_argument('--root', required=True, help='Project root path')
    args = parser.parse_args(argv)

    root = pathlib.Path(args.root).resolve()
    config = _load_config(root)
    overrides = config.get('overrides', {})
    timeout = config.get('timeouts', {}).get('live_secs', 180)

    # Compute local rule_hash for evidence
    workflow = root / WORKFLOW_DOC
    rule_hash = hashlib.sha256(workflow.read_bytes()).hexdigest()[:16] if workflow.exists() else ''

    target_cli = 'codex' if args.direction == 'claude-to-codex' else 'claude'

    cli_path = _get_cli_path(target_cli, config, overrides)
    if not cli_path:
        _write_evidence(root, args.direction, args.nonce, {
            'status': 'Fail',
            'reason': f'{target_cli} CLI not found',
            'nonce': args.nonce,
            'direction': args.direction,
            'target_cli': target_cli,
            'rc': None,
            'session_id': None,
            'root': str(root),
            'rule_hash': rule_hash,
            'inner_response': None,
            'auth_error': False,
        })
        _die(f'{target_cli} CLI not found')

    prompt = _make_probe_prompt(args.nonce, args.direction)

    sys.path.insert(0, str(pathlib.Path(__file__).parent))
    from _cli_helper import build_args, run_with_timeout

    try:
        if target_cli == 'claude':
            full_args = build_args(cli_path, [
                '-p', prompt,
                '--output-format', 'json',
            ])
        else:
            full_args = build_args(cli_path, [
                'exec',
                '--json',
                '--sandbox', 'read-only',
                '--cd', str(root),
                '--skip-git-repo-check',
                prompt,
            ])
    except ValueError as e:
        _write_evidence(root, args.direction, args.nonce, {
            'status': f'Fail(shim resolution: {e})',
            'reason': str(e),
            'nonce': args.nonce,
            'direction': args.direction,
            'target_cli': target_cli,
            'rc': None,
            'session_id': None,
            'root': str(root),
            'rule_hash': rule_hash,
            'inner_response': None,
            'auth_error': False,
        })
        _die(str(e))

    env = os.environ.copy()
    env[_RECURSION_ENV] = str(current_depth + 1)
    env[_NO_DELEGATION_ENV] = '1'

    try:
        rc, out, err = run_with_timeout(
            full_args,
            timeout_secs=timeout,
            cwd=str(root),
            env=env,
        )
    except subprocess.TimeoutExpired:
        _write_evidence(root, args.direction, args.nonce, {
            'status': 'Fail(timeout)',
            'reason': 'timeout',
            'nonce': args.nonce,
            'direction': args.direction,
            'target_cli': target_cli,
            'rc': None,
            'session_id': None,
            'root': str(root),
            'rule_hash': rule_hash,
            'inner_response': None,
            'auth_error': False,
        })
        _die('timeout')
    except Exception as e:
        _write_evidence(root, args.direction, args.nonce, {
            'status': f'Fail({type(e).__name__}: {e})',
            'reason': str(e),
            'nonce': args.nonce,
            'direction': args.direction,
            'target_cli': target_cli,
            'rc': rc if 'rc' in dir() else None,
            'session_id': None,
            'root': str(root),
            'rule_hash': rule_hash,
            'inner_response': None,
            'auth_error': False,
        })
        _die(str(e))

    # Validate inner response strictly
    auth_err = _detect_auth_error(rc, out, err)
    session_id, nonce_found, parse_status = _validate_inner_response(
        target_cli, rc, out, args.nonce
    )

    status = 'Pass' if (parse_status == 'Pass' and not auth_err) else parse_status
    if auth_err and status == 'Pass':
        status = 'Fail(auth_error detected in response)'

    evidence = {
        'status': status,
        'direction': args.direction,
        'target_cli': target_cli,
        'nonce': args.nonce,
        'rc': rc,
        'session_id': session_id,
        'root': str(root),
        'rule_hash': rule_hash,
        'nonce_found': nonce_found,
        'inner_response': out[:2000] if out else None,
        'auth_error': auth_err,
        'timestamp': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()),
    }

    _write_evidence(root, args.direction, args.nonce, evidence)
    print(json.dumps({'status': evidence['status'], 'nonce': args.nonce}))

    if evidence['status'] != 'Pass':
        sys.exit(1)
    return 0


def _make_probe_prompt(nonce, direction):
    return (
        f'You are a bounded peer probe. '
        f'NO_DELEGATION: you may not spawn subagents or call other CLIs. '
        f'Return ONLY this JSON, nothing else: '
        f'{{"probe":"peer","nonce":"{nonce}","direction":"{direction}"}}'
    )


def _validate_inner_response(cli, rc, raw_out, nonce):
    """
    Strictly validate inner CLI response.

    Returns (session_id_or_None, nonce_found_bool, status_str).
    """
    if not raw_out or not raw_out.strip():
        return None, False, 'Fail(empty response)'

    if cli == 'claude':
        return _validate_inner_claude(rc, raw_out, nonce)
    if cli == 'codex':
        return _validate_inner_codex(rc, raw_out, nonce)

    return None, False, 'Fail(unknown CLI)'


def _validate_inner_claude(rc, raw_out, nonce):
    if rc != 0:
        return None, False, f'Fail(rc={rc})'

    try:
        data = json.loads(raw_out.strip())
    except json.JSONDecodeError:
        return None, False, 'Fail(malformed JSON envelope)'

    if not isinstance(data, dict):
        return None, False, 'Fail(unexpected JSON type)'

    if data.get('is_error') or data.get('subtype') in ('error', 'turn.failed'):
        return None, False, f'Fail(error response: subtype={data.get("subtype")})'

    if data.get('type') != 'result' or data.get('subtype') != 'success':
        return None, False, (
            f'Fail(type={data.get("type")!r} subtype={data.get("subtype")!r})'
        )

    session_id = data.get('session_id', '')
    if not session_id:
        return None, False, 'Fail(session_id missing)'

    result_text = data.get('result', '')
    nonce_found = nonce in str(result_text)

    if not nonce_found:
        return session_id, False, 'Fail(nonce not found in result)'

    return session_id, True, 'Pass'


def _validate_inner_codex(rc, raw_out, nonce):
    if rc != 0:
        return None, False, f'Fail(rc={rc})'

    lines = [l.strip() for l in raw_out.splitlines() if l.strip()]
    if not lines:
        return None, False, 'Fail(empty JSONL)'

    session_id = None
    has_turn_completed = False
    has_agent_message = False
    nonce_found = False

    for line in lines:
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue

        if not isinstance(obj, dict):
            continue

        t = obj.get('type', '')

        if t in ('turn.failed', 'error'):
            return session_id, False, (
                f'Fail(error event type={t!r} msg={obj.get("message", obj.get("error", ""))})'
            )

        for id_field in ('session_id', 'thread_id'):
            v = obj.get(id_field, '')
            if v and not session_id:
                session_id = v

        if t == 'turn.completed':
            has_turn_completed = True

        if t == 'item.completed':
            item = obj.get('item', {})
            if item.get('type') == 'agent_message':
                has_agent_message = True
                content = item.get('content', [])
                if isinstance(content, list):
                    for block in content:
                        if isinstance(block, dict) and block.get('type') == 'text':
                            if nonce in block.get('text', ''):
                                nonce_found = True
                elif isinstance(content, str) and nonce in content:
                    nonce_found = True

    if not has_turn_completed:
        return session_id, False, 'Fail(turn.completed not found)'
    if not has_agent_message:
        return session_id, False, 'Fail(no agent_message found)'
    if not nonce_found:
        return session_id, False, 'Fail(nonce not found in agent_message)'
    if not session_id:
        return None, True, 'Fail(no session_id or thread_id)'

    return session_id, True, 'Pass'


def _write_evidence(root, direction, nonce, data):
    report_dir = root / 'reports' / 'connection_reports'
    report_dir.mkdir(parents=True, exist_ok=True)
    path = report_dir / f'probe_{direction}_{nonce}.json'
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding='utf-8')


def _detect_auth_error(rc, out, err):
    combined = (out + err).lower()
    for marker in ('unauthorized', 'login', 'auth', '401', 'forbidden', '403'):
        if marker in combined:
            return True
    return False


def _load_config(root):
    config_file = root / '.collaboration' / 'local.json'
    if config_file.exists():
        try:
            data = json.loads(config_file.read_text(encoding='utf-8'))
            if isinstance(data, dict):
                return data
        except (json.JSONDecodeError, OSError):
            pass
    return {}


def _get_cli_path(cli, config, overrides):
    override = overrides.get(cli) if overrides else None
    if override:
        return str(override)
    stored = config.get('cli', {}).get(cli)
    if stored:
        return stored
    import shutil
    return shutil.which(cli)


def _die(msg):
    print(f'ERROR: {msg}', file=sys.stderr)
    sys.exit(1)


if __name__ == '__main__':
    sys.exit(main())
