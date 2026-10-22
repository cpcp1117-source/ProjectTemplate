#!/usr/bin/env python3
"""
Connection check for AI collaboration CLI tools.

Usage:
  python scripts/check_connection.py [--live] [--bidirectional] [--root PATH]

Modes:
  (default)       Deterministic preflight: no tokens consumed.
                  Checks CLI versions, required docs, config, rule hash.
  --live          Real model calls. Each CLI must return the nonce, actual
                  root, and current rule hash computed from the workflow file.
  --bidirectional Live calls + cross-direction probe via outer model→probe_peer.
                  Requires --live. Outer model must show tool_use evidence.

Exit codes:
  0  All checks Pass
  1  One or more checks Fail

Reports saved to reports/connection_reports/ (gitignored).

No implicit retry on failure. Process-tree cleanup on timeout.
Self-recursive --bidirectional calls are refused.
"""

import argparse
import hashlib
import json
import os
import pathlib
import secrets
import subprocess
import sys
import time


_RECURSION_ENV = 'AI_COLLAB_CHECK_DEPTH'

REQUIRED_DOCS = [
    'docs/ai-collaboration-workflow.md',
    'CLAUDE.md',
    'AGENTS.md',
    'docs/project-brief.md',
    'tasks/current_task.md',
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

WORKFLOW_DOC = 'docs/ai-collaboration-workflow.md'

REQUIRED_TIMEOUT_FIELDS = ('preflight_secs', 'live_secs')


def main(argv=None):
    parser = argparse.ArgumentParser(description='AI collaboration connection check.')
    parser.add_argument('--live', action='store_true',
                        help='Run real model calls (consumes tokens/requires login)')
    parser.add_argument('--bidirectional', action='store_true',
                        help='Add cross-direction probe (requires --live)')
    parser.add_argument('--root', help='Explicit project root for isolated tests')
    args = parser.parse_args(argv)

    if args.bidirectional and not args.live:
        print('ERROR: --bidirectional requires --live.')
        sys.exit(1)

    if int(os.environ.get(_RECURSION_ENV, '0')) > 0:
        print('ERROR: Recursive --bidirectional call detected. Refusing to proceed.')
        sys.exit(1)

    root = _resolve_root(args.root)
    config, config_errors = _load_config(root)
    overrides = config.get('overrides', {})
    timeouts = config.get('timeouts', {})

    session_id = secrets.token_hex(8)
    timestamp = time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())
    results = {}
    raw_logs = {}

    # --- Preflight ---
    results.update(_run_preflight(root, config, config_errors, overrides, timeouts))

    preflight_failed = any(str(v).startswith('Fail') for v in results.values())

    # Short-circuit live and bidirectional on preflight failure
    if args.live:
        if preflight_failed:
            results['live:claude'] = 'NotRun(preflight failed — fix preflight first)'
            results['live:codex'] = 'NotRun(preflight failed — fix preflight first)'
        else:
            live_results, live_logs = _run_live(root, config, overrides, timeouts)
            results.update(live_results)
            raw_logs.update(live_logs)

    if args.bidirectional:
        if preflight_failed:
            results['bidirectional:claude-to-codex'] = (
                'NotRun(preflight failed — fix preflight first)'
            )
            results['bidirectional:codex-to-claude'] = (
                'NotRun(preflight failed — fix preflight first)'
            )
        else:
            bidi_results, bidi_logs = _run_bidirectional(
                root, config, overrides, timeouts
            )
            results.update(bidi_results)
            raw_logs.update(bidi_logs)

    # --- Report ---
    report = _build_report(session_id, timestamp, root, config, results,
                           args.live, args.bidirectional)
    report_path = _save_report(root, session_id, report, raw_logs)

    _print_summary(results, report_path)

    failed = any(str(v).startswith('Fail') for v in results.values())
    return 1 if failed else 0


# ---------------------------------------------------------------------------
# Preflight
# ---------------------------------------------------------------------------

def _run_preflight(root, config, config_errors, overrides, timeouts):
    results = {}

    # Config errors are hard failures
    for err in config_errors:
        results[f'config:{err["key"]}'] = f'Fail({err["msg"]})'

    # Validate timeout field types
    for field in REQUIRED_TIMEOUT_FIELDS:
        val = timeouts.get(field)
        if val is not None:
            if not _is_finite_positive_number(val):
                results[f'config:timeouts.{field}'] = (
                    f'Fail(invalid timeout: {val!r} must be a finite positive number, '
                    f'not bool/NaN/string/zero)'
                )

    timeout = _get_timeout(timeouts, 'preflight_secs', 15)

    # Required docs
    for doc in REQUIRED_DOCS:
        p = root / doc
        results[f'doc:{doc}'] = 'Pass' if p.exists() else 'Fail(missing)'

    # CLI versions
    for cli in ('claude', 'codex'):
        path = _get_cli_path(cli, config, overrides)
        if not path:
            results[f'cli:{cli}:version'] = 'Fail(not found)'
            continue
        try:
            from _cli_helper import build_args, run_with_timeout
            full_args = build_args(path, ['--version'])
            rc, out, err = run_with_timeout(full_args, timeout_secs=timeout)
            version_line = out.strip().splitlines()[0] if out.strip() else ''
            if rc == 0 and version_line:
                results[f'cli:{cli}:version'] = f'Pass({version_line})'
            elif rc == 0 and not version_line:
                results[f'cli:{cli}:version'] = 'Fail(--version returned empty output)'
            else:
                results[f'cli:{cli}:version'] = f'Fail(rc={rc})'
        except ValueError as e:
            results[f'cli:{cli}:version'] = f'Fail(shim resolution: {e})'
        except FileNotFoundError:
            results[f'cli:{cli}:version'] = 'Fail(executable not found)'
        except subprocess.TimeoutExpired:
            results[f'cli:{cli}:version'] = 'Fail(timeout)'
        except Exception as e:
            results[f'cli:{cli}:version'] = f'Fail({type(e).__name__})'

    # Rule hash
    workflow = root / WORKFLOW_DOC
    if workflow.exists():
        actual_hash = hashlib.sha256(workflow.read_bytes()).hexdigest()[:16]
        stored_hash = config.get('rule_hash', '')
        if not stored_hash:
            results['rule_hash'] = f'Fail(no stored hash in config; current={actual_hash})'
        elif actual_hash == stored_hash:
            results['rule_hash'] = 'Pass'
        else:
            results['rule_hash'] = (
                f'Fail(mismatch: stored={stored_hash} actual={actual_hash})'
            )
    else:
        results['rule_hash'] = 'Fail(workflow doc missing)'

    return results


# ---------------------------------------------------------------------------
# Live checks
# ---------------------------------------------------------------------------

def _run_live(root, config, overrides, timeouts):
    results = {}
    logs = {}
    timeout = _get_timeout(timeouts, 'live_secs', 180)
    rule_hash = hashlib.sha256(
        (root / WORKFLOW_DOC).read_bytes()
    ).hexdigest()[:16]

    for cli in ('claude', 'codex'):
        nonce = secrets.token_hex(12)
        path = _get_cli_path(cli, config, overrides)
        if not path:
            results[f'live:{cli}'] = 'NotRun(CLI not found)'
            continue

        try:
            from _cli_helper import build_args, run_with_timeout
            if cli == 'claude':
                prompt = _make_live_prompt_claude(nonce)
                full_args = build_args(path, [
                    '-p', prompt,
                    '--output-format', 'json',
                    '--allowedTools', 'Bash',
                ])
            else:
                prompt = _make_live_prompt_codex(nonce)
                full_args = build_args(path, [
                    'exec',
                    '--json',
                    '--sandbox', 'read-only',
                    '--cd', str(root),
                    '--skip-git-repo-check',
                    prompt,
                ])

            rc, out, err = run_with_timeout(full_args, timeout_secs=timeout,
                                            cwd=str(root))
        except ValueError as e:
            results[f'live:{cli}'] = f'Fail(shim resolution: {e})'
            continue
        except subprocess.TimeoutExpired:
            results[f'live:{cli}'] = 'Fail(timeout)'
            continue
        except Exception as e:
            results[f'live:{cli}'] = f'Fail({type(e).__name__}: {e})'
            continue

        logs[f'live:{cli}:stdout'] = out
        logs[f'live:{cli}:stderr'] = err

        status, model_session_id = _parse_live_response(
            cli, rc, out, nonce, str(root), rule_hash
        )
        results[f'live:{cli}'] = status
        if model_session_id:
            results[f'live:{cli}:session_id'] = model_session_id

    return results, logs


def _make_live_prompt_claude(nonce):
    return (
        f'You are a connectivity probe. '
        f'Use Bash to run: python -c "import hashlib, pathlib; '
        f'p=pathlib.Path(\'docs/ai-collaboration-workflow.md\'); '
        f'print(hashlib.sha256(p.read_bytes()).hexdigest()[:16])" '
        f'to get rule_hash, and: python -c "import pathlib; print(str(pathlib.Path(\'.\').resolve()))" '
        f'to get root. '
        f'Return ONLY this JSON, nothing else: '
        f'{{"nonce":"{nonce}","root":"<absolute root>","rule_hash":"<computed>"}}'
    )


def _make_live_prompt_codex(nonce):
    return (
        f'Connectivity probe. '
        f'Read docs/ai-collaboration-workflow.md. '
        f'Compute its SHA-256 (first 16 lowercase hex chars). '
        f'Return ONLY this JSON: '
        f'{{"nonce":"{nonce}","root":"<absolute project root>","rule_hash":"<computed>"}}'
    )


def _parse_live_response(cli, rc, raw_out, nonce, expected_root, expected_hash):
    """
    Validate a live CLI response.

    Returns (status_string, session_id_or_None).
    Rejects empty, malformed, error-state, or missing-nonce responses.
    Requires exact nonce match; reports root/hash mismatches distinctly.
    """
    if not raw_out or not raw_out.strip():
        return 'Fail(empty response)', None

    if cli == 'claude':
        return _parse_claude_output(rc, raw_out, nonce, expected_root, expected_hash)
    if cli == 'codex':
        return _parse_codex_output(rc, raw_out, nonce, expected_root, expected_hash)

    return 'Fail(unknown CLI)', None


def _parse_claude_output(rc, raw_out, nonce, expected_root, expected_hash):
    """
    Validate Claude --output-format json envelope.

    Required: type=result, subtype=success, is_error=false, session_id present.
    Required: result field parseable as JSON with matching nonce, root, rule_hash.
    """
    if rc != 0:
        return f'Fail(rc={rc})', None

    try:
        data = json.loads(raw_out.strip())
    except json.JSONDecodeError:
        return 'Fail(malformed JSON — not a valid Claude envelope)', None

    if not isinstance(data, dict):
        return 'Fail(unexpected JSON type — expected object)', None

    # Reject error responses
    if data.get('type') == 'result' and data.get('subtype') in ('error', 'turn.failed'):
        return f'Fail(subtype={data.get("subtype")})', None
    if data.get('is_error'):
        return f'Fail(is_error=true: {data.get("error", "")})', None

    # Require type=result, subtype=success
    if data.get('type') != 'result':
        return f'Fail(type={data.get("type")!r} expected "result")', None
    if data.get('subtype') != 'success':
        return f'Fail(subtype={data.get("subtype")!r} expected "success")', None

    # Require session_id
    session_id = data.get('session_id', '')
    if not session_id:
        return 'Fail(session_id missing or empty)', None

    # Parse result field as JSON
    result_text = data.get('result', '')
    if not result_text:
        return 'Fail(result field missing or empty)', None

    try:
        inner = json.loads(result_text) if isinstance(result_text, str) else result_text
    except json.JSONDecodeError:
        return 'Fail(result field is not valid JSON)', None

    if not isinstance(inner, dict):
        return 'Fail(result JSON is not an object)', None

    # Validate nonce (required exact match)
    if inner.get('nonce') != nonce:
        return f'Fail(nonce mismatch or missing in result)', None

    # Report root/hash mismatches separately (informational — model may normalize paths)
    warnings = []
    if inner.get('root') != expected_root:
        warnings.append(f'root_mismatch(got={inner.get("root")!r})')
    if inner.get('rule_hash') != expected_hash:
        warnings.append(f'rule_hash_mismatch(got={inner.get("rule_hash")!r} expected={expected_hash})')

    if warnings:
        return f'Fail({"; ".join(warnings)})', session_id

    return f'Pass(session_id={session_id})', session_id


def _parse_codex_output(rc, raw_out, nonce, expected_root, expected_hash):
    """
    Validate Codex --json JSONL output.

    Required: rc=0, session_id or thread_id present, turn.completed event,
    item.completed with item.type=agent_message containing nonce.
    Rejects: turn.failed, error-type events, malformed or empty JSONL.
    """
    if rc != 0:
        return f'Fail(rc={rc})', None

    lines = [l.strip() for l in raw_out.splitlines() if l.strip()]
    if not lines:
        return 'Fail(empty JSONL response)', None

    has_turn_completed = False
    has_agent_message = False
    nonce_found = False
    inner_text = None
    session_id = None

    for line in lines:
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            # Malformed JSONL line — continue scanning but do not credit nonce
            continue

        if not isinstance(obj, dict):
            continue

        t = obj.get('type', '')

        # Hard failure on error events
        if t in ('turn.failed', 'error'):
            return f'Fail(error event: type={t!r} msg={obj.get("message", obj.get("error", ""))})', None

        # Collect session/thread IDs
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
                # Extract text from content list
                content = item.get('content', [])
                if isinstance(content, list):
                    for block in content:
                        if isinstance(block, dict) and block.get('type') == 'text':
                            text = block.get('text', '')
                            inner_text = text
                            if nonce in text:
                                nonce_found = True
                elif isinstance(content, str) and nonce in content:
                    inner_text = content
                    nonce_found = True

    if not has_turn_completed:
        return 'Fail(turn.completed event not found in JSONL)', None
    if not has_agent_message:
        return 'Fail(no item.completed with type=agent_message found)', None
    if not nonce_found:
        return 'Fail(nonce not found in agent_message content)', None
    if not session_id:
        return 'Fail(no session_id or thread_id in JSONL output)', None

    # Validate inner JSON for root/hash if present
    warnings = []
    if inner_text:
        try:
            inner = json.loads(inner_text)
            if isinstance(inner, dict):
                if inner.get('root') != expected_root:
                    warnings.append(f'root_mismatch(got={inner.get("root")!r})')
                if inner.get('rule_hash') != expected_hash:
                    warnings.append(
                        f'rule_hash_mismatch(got={inner.get("rule_hash")!r} '
                        f'expected={expected_hash})'
                    )
        except json.JSONDecodeError:
            pass  # Text wasn't JSON — nonce was found in raw text, acceptable

    if warnings:
        return f'Fail({"; ".join(warnings)})', session_id

    return f'Pass(session_id={session_id})', session_id


# ---------------------------------------------------------------------------
# Bidirectional probe
# ---------------------------------------------------------------------------

def _run_bidirectional(root, config, overrides, timeouts):
    """
    Invoke outer model (Claude or Codex) with a task to run probe_peer.py.

    The outer model must show tool_use evidence of invoking probe_peer.py.
    The inner evidence artifact is written by probe_peer.py and validated here.

    Outer nonce: proves outer model responded.
    Inner nonce: proves inner probe ran and inner CLI responded.
    """
    results = {}
    logs = {}
    timeout = _get_timeout(timeouts, 'live_secs', 180)

    probe_script = root / 'scripts' / 'probe_peer.py'
    if not probe_script.exists():
        results['bidirectional:claude-to-codex'] = 'Fail(probe_peer.py missing)'
        results['bidirectional:codex-to-claude'] = 'Fail(probe_peer.py missing)'
        return results, logs

    for direction in ('claude-to-codex', 'codex-to-claude'):
        outer_cli = 'claude' if direction == 'claude-to-codex' else 'codex'
        outer_path = _get_cli_path(outer_cli, config, overrides)

        if not outer_path:
            results[f'bidirectional:{direction}'] = (
                f'NotRun({outer_cli} CLI not found for outer invocation)'
            )
            continue

        outer_nonce = secrets.token_hex(12)
        inner_nonce = secrets.token_hex(12)
        report_dir = root / 'reports' / 'connection_reports'
        report_dir.mkdir(parents=True, exist_ok=True)

        probe_cmd = (
            f'{sys.executable} {probe_script} '
            f'--direction {direction} --nonce {inner_nonce} --root {root}'
        )
        outer_prompt = _make_bidi_prompt(direction, outer_nonce, inner_nonce, probe_cmd)

        env = os.environ.copy()
        env[_RECURSION_ENV] = '1'

        try:
            from _cli_helper import build_args, run_with_timeout
            if outer_cli == 'claude':
                full_args = build_args(outer_path, [
                    '-p', outer_prompt,
                    '--output-format', 'json',
                    '--allowedTools', 'Bash',
                    '--max-turns', '3',
                ])
            else:
                full_args = build_args(outer_path, [
                    'exec',
                    '--json',
                    '--sandbox', 'workspace',
                    '--cd', str(root),
                    '--skip-git-repo-check',
                    outer_prompt,
                ])

            rc, out, err = run_with_timeout(
                full_args,
                timeout_secs=timeout,
                cwd=str(root),
                env=env,
            )
        except ValueError as e:
            results[f'bidirectional:{direction}'] = f'Fail(shim resolution: {e})'
            continue
        except subprocess.TimeoutExpired:
            results[f'bidirectional:{direction}'] = 'Fail(timeout)'
            continue
        except Exception as e:
            results[f'bidirectional:{direction}'] = f'Fail({type(e).__name__}: {e})'
            continue

        logs[f'bidirectional:{direction}:stdout'] = out
        logs[f'bidirectional:{direction}:stderr'] = err

        # Verify outer nonce in outer response
        outer_nonce_found = outer_nonce in out

        # Verify outer model showed tool_use for probe_peer invocation
        tool_evidence = _check_outer_tool_evidence(outer_cli, out, probe_script)

        # Read and validate inner evidence artifact
        artifact = report_dir / f'probe_{direction}_{inner_nonce}.json'
        if not artifact.exists():
            if not outer_nonce_found:
                results[f'bidirectional:{direction}'] = (
                    f'Fail(outer nonce not found; inner artifact not written — '
                    f'outer model did not invoke probe_peer)'
                )
            else:
                results[f'bidirectional:{direction}'] = (
                    'NotVerified(outer responded but inner artifact not written — '
                    'outer model may not have invoked probe_peer as a tool)'
                )
            continue

        try:
            evidence = json.loads(artifact.read_text('utf-8'))
        except Exception as e:
            results[f'bidirectional:{direction}'] = f'Fail(bad artifact: {e})'
            continue

        status = _validate_bidi_evidence(
            evidence, inner_nonce, direction, tool_evidence, outer_nonce_found
        )
        results[f'bidirectional:{direction}'] = status

    return results, logs


def _make_bidi_prompt(direction, outer_nonce, inner_nonce, probe_cmd):
    return (
        f'CONNECTIVITY PROBE — depth=1, NO_DELEGATION. '
        f'Invoke the bounded peer probe script exactly once using Bash. '
        f'Command: {probe_cmd} '
        f'Do NOT invoke check_connection.py or probe_peer.py again. '
        f'After Bash completes, return ONLY: '
        f'{{"outer_nonce":"{outer_nonce}","inner_nonce":"{inner_nonce}",'
        f'"direction":"{direction}","tool_used":true}}'
    )


def _check_outer_tool_evidence(outer_cli, out, probe_script):
    """
    Check whether the outer model's output contains evidence of invoking probe_peer.py.
    Returns True if tool_use evidence is found, False otherwise.
    """
    probe_name = probe_script.name  # 'probe_peer.py'

    if outer_cli == 'claude':
        try:
            data = json.loads(out.strip())
            if isinstance(data, dict):
                # Check for tool_use in result content or type indicators
                result = data.get('result', '')
                if 'tool_used' in result and probe_name in out:
                    return True
                # Check raw output for Bash tool invocation
                if probe_name in out:
                    return True
        except (json.JSONDecodeError, AttributeError):
            pass
        return probe_name in out

    if outer_cli == 'codex':
        # In JSONL output, look for item with type=bash_output or similar tool event
        for line in out.splitlines():
            try:
                obj = json.loads(line)
                t = obj.get('type', '')
                if t in ('item.completed', 'item.started'):
                    item = obj.get('item', {})
                    item_type = item.get('type', '')
                    if item_type in ('bash', 'tool_use', 'function_call'):
                        content = str(item.get('content', '') or item.get('input', ''))
                        if probe_name in content:
                            return True
            except json.JSONDecodeError:
                pass
        return probe_name in out

    return False


def _validate_bidi_evidence(evidence, inner_nonce, direction, tool_evidence, outer_nonce_found):
    """Validate the inner evidence artifact from probe_peer.py."""
    required = ('status', 'direction', 'nonce', 'rc')
    missing = [f for f in required if f not in evidence]
    if missing:
        return f'Fail(evidence missing fields: {missing})'

    if evidence.get('nonce') != inner_nonce:
        return 'Fail(inner nonce mismatch in artifact)'
    if evidence.get('direction') != direction:
        return f'Fail(direction mismatch in artifact: {evidence.get("direction")})'
    if evidence.get('status') != 'Pass':
        return f'Fail(inner probe status={evidence.get("status")})'
    if evidence.get('rc') != 0:
        return f'Fail(inner rc={evidence.get("rc")})'

    if not tool_evidence:
        return (
            f'NotVerified(inner artifact valid but outer model tool_use evidence '
            f'not found — cannot confirm outer model invoked probe_peer)'
        )

    return 'Pass'


# ---------------------------------------------------------------------------
# Report
# ---------------------------------------------------------------------------

def _build_report(session_id, timestamp, root, config, results, live, bidirectional):
    total = len(results)
    passed = sum(1 for v in results.values() if str(v).startswith('Pass'))
    failed = sum(1 for v in results.values() if str(v).startswith('Fail'))
    not_run = total - passed - failed

    # Collect model session IDs separately
    model_session_ids = {
        k: v for k, v in results.items() if k.endswith(':session_id')
    }
    check_results = {k: v for k, v in results.items() if not k.endswith(':session_id')}

    return {
        'run_id': session_id,
        'timestamp': timestamp,
        'root': str(root),
        'mode': {
            'preflight': True,
            'live': live,
            'bidirectional': bidirectional,
        },
        'baseline': {
            'rule_version': config.get('rule_version', 'unknown'),
            'stored_rule_hash': config.get('rule_hash', 'none'),
        },
        'checks': check_results,
        'model_session_ids': model_session_ids,
        'summary': {
            'total': len(check_results),
            'pass': passed,
            'fail': failed,
            'not_run': not_run,
        },
        'overall': 'Pass' if failed == 0 else 'Fail',
    }


def _save_report(root, session_id, report, raw_logs):
    report_dir = root / 'reports' / 'connection_reports'
    report_dir.mkdir(parents=True, exist_ok=True)

    # Main structured report
    path = report_dir / f'check_{session_id}.json'
    path.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding='utf-8')

    # Raw stdout/stderr for diagnosis (separate file)
    if raw_logs:
        logs_path = report_dir / f'check_{session_id}_raw.json'
        logs_path.write_text(
            json.dumps(raw_logs, indent=2, ensure_ascii=False), encoding='utf-8'
        )

    return path


def _print_summary(results, report_path):
    print('\n--- Connection Check Results ---')
    for key, status in results.items():
        if key.endswith(':session_id'):
            continue
        mark = 'OK' if str(status).startswith('Pass') else (
            '--' if str(status).startswith('Not') else 'XX'
        )
        print(f'  [{mark}] {key}: {status}')
    print(f'\nReport: {report_path}')


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _resolve_root(explicit_root):
    if explicit_root:
        return pathlib.Path(explicit_root).resolve()
    return pathlib.Path(__file__).resolve().parent.parent


def _load_config(root):
    """
    Load .collaboration/local.json.

    Returns (config_dict, error_list).
    error_list entries: {'key': str, 'msg': str}

    Missing config file → empty config (fresh install), no errors.
    Corrupt/non-object JSON → empty config plus error entry.
    """
    config_file = root / '.collaboration' / 'local.json'
    if not config_file.exists():
        return {}, []

    try:
        text = config_file.read_text(encoding='utf-8')
    except OSError as e:
        return {}, [{'key': 'read', 'msg': f'OSError reading config: {e}'}]

    try:
        data = json.loads(text)
    except json.JSONDecodeError as e:
        return {}, [{'key': 'parse', 'msg': f'Malformed JSON in .collaboration/local.json: {e}'}]

    if not isinstance(data, dict):
        return {}, [{'key': 'type', 'msg': 'Config is not a JSON object'}]

    errors = []

    # Validate stored root matches current root
    stored_root_str = data.get('root', '')
    if stored_root_str:
        try:
            stored_root = pathlib.Path(stored_root_str).resolve()
            if stored_root != root.resolve():
                errors.append({
                    'key': 'root_mismatch',
                    'msg': (
                        f'Config root={stored_root!r} does not match '
                        f'current root={root.resolve()!r}. '
                        f'Delete .collaboration/local.json and re-run init_project.py.'
                    )
                })
        except (ValueError, OSError):
            errors.append({'key': 'root_invalid', 'msg': f'Stored root is invalid: {stored_root_str!r}'})

    # Validate timeout field types if present
    timeouts = data.get('timeouts', {})
    if not isinstance(timeouts, dict):
        errors.append({'key': 'timeouts_type', 'msg': 'timeouts must be a JSON object'})

    # Validate env_depth override field if present
    env_depth = data.get('env_depth')
    if env_depth is not None and not _is_finite_positive_number(env_depth):
        errors.append({
            'key': 'env_depth',
            'msg': f'env_depth must be a finite positive number, got {env_depth!r}'
        })

    return data, errors


def _get_cli_path(cli, config, overrides):
    override = overrides.get(cli) if overrides else None
    if override:
        return str(override)
    stored = config.get('cli', {}).get(cli)
    if stored:
        return stored
    import shutil
    return shutil.which(cli)


def _get_timeout(timeouts, field, default):
    val = timeouts.get(field, default)
    if _is_finite_positive_number(val):
        return val
    return default


def _is_finite_positive_number(val):
    """Return True iff val is a finite positive number (not bool, not NaN/Inf, not string)."""
    if isinstance(val, bool):
        return False
    if not isinstance(val, (int, float)):
        return False
    import math
    return math.isfinite(val) and val > 0


if __name__ == '__main__':
    sys.path.insert(0, str(pathlib.Path(__file__).parent))
    sys.exit(main())
