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
  --bidirectional Live calls + cross-direction probe via outer model->probe_peer.
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

sys.path.insert(0, str(pathlib.Path(__file__).parent))

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

    _depth_raw = os.environ.get(_RECURSION_ENV, '0')
    try:
        _depth = int(_depth_raw)
    except (ValueError, TypeError):
        report = {
            'error': 'malformed_env',
            'env': _RECURSION_ENV,
            'value': _depth_raw,
            'message': (
                f'Environment variable {_RECURSION_ENV} has a non-integer value '
                f'{_depth_raw!r}. Unset it or set it to 0 and retry.'
            ),
            'overall': 'Fail',
        }
        print(json.dumps(report, indent=2))
        sys.exit(1)
    if _depth > 0:
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

    live_failed = args.live and any(
        k.startswith('live:') and not k.endswith(':session_id') and str(v).startswith('Fail')
        for k, v in results.items()
    )

    if args.bidirectional:
        if preflight_failed:
            results['bidirectional:claude-to-codex'] = (
                'NotRun(preflight failed — fix preflight first)'
            )
            results['bidirectional:codex-to-claude'] = (
                'NotRun(preflight failed — fix preflight first)'
            )
        elif live_failed:
            results['bidirectional:claude-to-codex'] = (
                'NotRun(live check failed — fix live checks first)'
            )
            results['bidirectional:codex-to-claude'] = (
                'NotRun(live check failed — fix live checks first)'
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

    overall = report.get('overall', 'Fail')
    return 0 if overall == 'Pass' else 1


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
    docs_ok = True
    for doc in REQUIRED_DOCS:
        p = root / doc
        if p.is_file():
            results[f'doc:{doc}'] = 'Pass'
        elif p.exists():
            results[f'doc:{doc}'] = 'Fail(exists but is not a file)'
            docs_ok = False
        else:
            results[f'doc:{doc}'] = 'Fail(missing)'
            docs_ok = False

    # CLI versions — skip entirely when config is invalid or required docs are missing
    config_invalid = bool(config_errors)
    if config_invalid or not docs_ok:
        reason = 'invalid config' if config_invalid else 'missing required files'
        for cli in ('claude', 'codex'):
            results[f'cli:{cli}:version'] = f'NotRun({reason} — fix errors above first)'
    else:
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

    # Rule hash — guard is_file before hashing
    workflow = root / WORKFLOW_DOC
    if workflow.is_file():
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
    elif workflow.exists():
        results['rule_hash'] = 'Fail(workflow path exists but is not a file)'
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

    workflow = root / WORKFLOW_DOC
    rule_hash = hashlib.sha256(workflow.read_bytes()).hexdigest()[:16]

    context_script = root / 'scripts' / 'probe_context.py'
    if not context_script.is_file():
        for cli in ('claude', 'codex'):
            results[f'live:{cli}'] = 'Fail(probe_context.py missing)'
        return results, logs

    from _probe import make_context_cmd_str, probe_env, invoke_cli
    from _protocol import decode, validate_payload
    from _cli_helper import build_args, run_with_timeout

    for cli in ('claude', 'codex'):
        nonce = secrets.token_hex(12)
        path = _get_cli_path(cli, config, overrides)
        if not path:
            results[f'live:{cli}'] = 'NotRun(CLI not found)'
            continue

        ctx_cmd_str, _ctx_args = make_context_cmd_str(root, context_script, nonce)

        try:
            if cli == 'claude':
                prompt = _make_live_prompt_claude(nonce, ctx_cmd_str)
                user_args = [
                    '-p', prompt,
                    '--effort', 'low',
                    '--tools', 'Read,Bash',
                    '--allowedTools', 'Read,Bash',
                    '--output-format', 'stream-json',
                    '--verbose',
                ]
                run_env = probe_env(os.environ, depth=0)
                rc, out, err = invoke_cli(cli, path, user_args, root, timeout, env=run_env)
            else:
                prompt = _make_live_prompt_codex(nonce, ctx_cmd_str)
                user_args = [
                    'exec', '--json',
                    '--sandbox', 'read-only',
                    '--cd', str(root),
                    '--skip-git-repo-check',
                    prompt,
                ]
                run_env = probe_env(os.environ, depth=0)
                rc, out, err = invoke_cli(cli, path, user_args, root, timeout, env=run_env)
        except ValueError as e:
            results[f'live:{cli}'] = f'Fail(shim resolution: {e})'
            continue
        except subprocess.TimeoutExpired:
            results[f'live:{cli}'] = 'Fail(timeout)'
            continue
        except OSError as e:
            results[f'live:{cli}'] = f'Fail(OSError: {e})'
            continue
        except Exception as e:
            results[f'live:{cli}'] = f'Fail({type(e).__name__}: {e})'
            continue

        logs[f'live:{cli}:stdout'] = out
        logs[f'live:{cli}:stderr'] = err

        # Decode and validate
        try:
            inner, session_id = decode(cli, rc, out)
        except ValueError as exc:
            results[f'live:{cli}'] = f'Fail(decode: {exc})'
            continue

        if inner.get('nonce') != nonce:
            results[f'live:{cli}'] = 'Fail(nonce mismatch or missing)'
            continue

        # root must be absolute — do NOT normalize '.' to expected root
        got_root = inner.get('root', '')
        if not got_root or not pathlib.Path(str(got_root)).is_absolute():
            results[f'live:{cli}'] = f'Fail(root missing or not absolute: {got_root!r})'
            continue

        try:
            validate_payload(inner, {'root': str(root), 'rule_hash': rule_hash})
        except ValueError as exc:
            results[f'live:{cli}'] = f'Fail({exc})'
            continue

        results[f'live:{cli}'] = f'Pass(session_id={session_id})'
        results[f'live:{cli}:session_id'] = session_id

    return results, logs


def _make_live_prompt_claude(nonce, ctx_cmd_str):
    return (
        f'You are a connectivity probe. Do not delegate to agents or call other CLIs. '
        f'Use Bash to run this exact command and capture its JSON output:\n'
        f'  {ctx_cmd_str}\n'
        f'Return ONLY the JSON object from that command as your entire response. '
        f'Do not add any other text, explanation, or wrapping. '
        f'The expected nonce is {nonce!r} — do not supply expected hash or root yourself; '
        f'run the command and return its output verbatim as JSON.'
    )


def _make_live_prompt_codex(nonce, ctx_cmd_str):
    return (
        f'Connectivity probe. Do NOT delegate or call other CLIs. '
        f'Use native Windows PowerShell (powershell.exe) to run this command. '
        f'Do NOT use Bash or WSL. login=false if supported by your tool.\n'
        f'Command: {ctx_cmd_str}\n'
        f'Return ONLY the JSON object it prints. '
        f'Expected nonce: {nonce!r}. Compute all values by running the command, '
        f'do not fabricate hash or root.'
    )


# ---------------------------------------------------------------------------
# Bidirectional probe
# ---------------------------------------------------------------------------

def _run_bidirectional(root, config, overrides, timeouts):
    results = {}
    logs = {}
    timeout = _get_timeout(timeouts, 'live_secs', 180)

    probe_script = root / 'scripts' / 'probe_peer.py'
    if not probe_script.is_file():
        results['bidirectional:claude-to-codex'] = 'Fail(probe_peer.py missing)'
        results['bidirectional:codex-to-claude'] = 'Fail(probe_peer.py missing)'
        return results, logs

    from _probe import make_probe_peer_cmd_str, probe_env, invoke_cli

    report_dir = root / 'reports' / 'connection_reports'
    report_dir.mkdir(parents=True, exist_ok=True)

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

        # Build safe command string that the outer model must run
        peer_cmd_str, _peer_args = make_probe_peer_cmd_str(
            root, probe_script, direction, inner_nonce
        )

        outer_prompt = _make_bidi_prompt(direction, outer_nonce, inner_nonce, peer_cmd_str)

        # depth=1 for outer — signals it may call one helper
        run_env = probe_env(os.environ, depth=1)

        try:
            if outer_cli == 'claude':
                user_args = [
                    '-p', outer_prompt,
                    '--effort', 'low',
                    '--tools', 'Read,Bash',
                    '--allowedTools', 'Read,Bash',
                    '--output-format', 'stream-json',
                    '--verbose',
                ]
            else:
                user_args = [
                    'exec', '--json',
                    '--sandbox', 'workspace-write',
                    '--cd', str(root),
                    '--skip-git-repo-check',
                    outer_prompt,
                ]
            rc, out, err = invoke_cli(outer_cli, outer_path, user_args, root, timeout, env=run_env)
        except ValueError as e:
            results[f'bidirectional:{direction}'] = f'Fail(shim resolution: {e})'
            continue
        except subprocess.TimeoutExpired:
            results[f'bidirectional:{direction}'] = 'Fail(timeout)'
            continue
        except OSError as e:
            results[f'bidirectional:{direction}'] = f'Fail(OSError: {e})'
            continue
        except Exception as e:
            results[f'bidirectional:{direction}'] = f'Fail({type(e).__name__}: {e})'
            continue

        logs[f'bidirectional:{direction}:stdout'] = out
        logs[f'bidirectional:{direction}:stderr'] = err

        # Decode outer response
        from _protocol import decode, validate_payload
        try:
            outer_payload, outer_session = decode(outer_cli, rc, out)
        except ValueError as exc:
            results[f'bidirectional:{direction}'] = f'Fail(outer decode: {exc})'
            continue

        # Validate outer nonce and required fields
        try:
            validate_payload(outer_payload, {
                'outer_nonce': outer_nonce,
                'inner_nonce': inner_nonce,
                'direction': direction,
            })
        except ValueError as exc:
            results[f'bidirectional:{direction}'] = f'Fail(outer payload: {exc})'
            continue

        # tool_used must be boolean True (not truthy int or string)
        if outer_payload.get('tool_used') is not True:
            results[f'bidirectional:{direction}'] = 'Fail(outer payload tool_used != true)'
            continue

        # tool_rc must be an integer (not bool); 0 = success
        tool_rc = outer_payload.get('tool_rc')
        if isinstance(tool_rc, bool) or not isinstance(tool_rc, int):
            results[f'bidirectional:{direction}'] = (
                f'Fail(outer payload tool_rc={tool_rc!r}; must be integer exit code)'
            )
            continue
        if tool_rc != 0:
            diag = outer_payload.get('diagnostic', '')
            results[f'bidirectional:{direction}'] = (
                f'Fail(probe command exit code={tool_rc}; diagnostic={diag!r})'
            )
            continue

        # Verify real tool_use event in raw stream
        tool_evidence, tool_detail = _check_outer_tool_evidence(
            outer_cli, out, peer_cmd_str, inner_nonce
        )

        # Read and validate inner evidence artifact written by probe_peer.py
        artifact_path = report_dir / f'probe_{direction}_{inner_nonce}.json'
        if not artifact_path.exists():
            status = 'Fail(inner artifact not written'
            if not tool_evidence:
                status += f'; no tool evidence — {tool_detail}'
            status += ')'
            results[f'bidirectional:{direction}'] = status
            continue

        try:
            artifact = json.loads(artifact_path.read_text('utf-8'))
        except Exception as e:
            results[f'bidirectional:{direction}'] = f'Fail(bad artifact: {e})'
            continue

        # Validate artifact fields
        status = _validate_bidi_artifact(
            artifact, inner_nonce, direction, str(root), outer_session,
            tool_evidence, tool_detail, out
        )
        results[f'bidirectional:{direction}'] = status

        # Store session IDs as metadata (not status checks); keys ending :session_id are excluded from pass/fail counts
        results[f'bidirectional:{direction}:outer:session_id'] = outer_session
        inner_session = artifact.get('inner_session_id', '') if isinstance(artifact, dict) else ''
        if inner_session:
            results[f'bidirectional:{direction}:inner:session_id'] = inner_session

    return results, logs


def _make_bidi_prompt(direction, outer_nonce, inner_nonce, peer_cmd_str):
    if direction == 'codex-to-claude':
        shell_instruction = (
            'Use your shell/exec tool with the native Windows PowerShell executable '
            '(powershell.exe) to run the command below. '
            'Do NOT use Bash or WSL. login=false if supported by your tool.'
        )
    else:
        shell_instruction = (
            'Use your Bash tool to run the command below exactly as written.'
        )
    return (
        f'CONNECTIVITY PROBE depth=1. direction={direction!r}. '
        f'You are authorized to run exactly ONE shell command — the bounded peer probe below. '
        f'Do NOT invoke check_connection.py, do NOT call probe_peer.py again, '
        f'do NOT spawn agents or delegate further. '
        f'{shell_instruction}\n\n'
        f'Command to run once:\n'
        f'  {peer_cmd_str}\n\n'
        f'After the command completes, return ONLY valid JSON as your entire response. '
        f'If the command succeeded (exit code 0), return:\n'
        f'{{"outer_nonce":"{outer_nonce}","inner_nonce":"{inner_nonce}",'
        f'"direction":"{direction}","tool_used":true,"tool_rc":0}}\n'
        f'If the command failed (non-zero exit code), return:\n'
        f'{{"outer_nonce":"{outer_nonce}","inner_nonce":"{inner_nonce}",'
        f'"direction":"{direction}","tool_used":true,"tool_rc":<actual exit code>,'
        f'"diagnostic":"<brief error description>"}}\n'
        f'Do not fabricate success. Set tool_rc to the actual exit code the shell reported.'
    )


def _check_outer_tool_evidence(outer_cli, out, expected_cmd_str, inner_nonce):
    """
    Verify the outer model actually invoked the peer probe via a real tool event.

    For Claude stream-json:
      - Finds assistant event with message.content tool_use name=Bash whose
        input.command matches expected_cmd_str (strip whitespace).
      - Then finds user event with message.content tool_result for that
        tool_use_id with no is_error/interrupted/failed exit status.
      - Requires actual nested events; never falls back to final-text.
    For Codex JSONL:
      - Finds item.completed with item.type=command_execution, exit_code=0,
        status=completed, and expected_cmd_str is a substring of the executed command
        (Codex may wrap it in a powershell -Command invocation).
      - Rejects echo/Write-Output of nonce or expected text, started-only, failed.

    All malformed/non-object lines cause immediate failure.

    Returns (bool, detail_str).
    """
    if outer_cli == 'claude':
        return _claude_tool_evidence(out, expected_cmd_str)
    if outer_cli == 'codex':
        return _codex_tool_evidence(out, expected_cmd_str)
    return False, 'unknown outer CLI'


def _claude_tool_evidence(out, expected_cmd_str):
    """Parse Claude stream-json events for real Bash tool_use + tool_result."""
    expected_cmd = expected_cmd_str.strip()
    events = []
    for line in out.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            return False, f'malformed JSON line in stream: {line[:80]!r}'
        if not isinstance(obj, dict):
            return False, f'non-object line in stream: {line[:80]!r}'
        events.append(obj)

    # Find tool_use in assistant event.message.content (real nested structure)
    bash_tool_use_id = None
    for ev in events:
        # Real stream-json: event has 'message' key with role/content
        msg = ev.get('message')
        if isinstance(msg, dict):
            content = msg.get('content', [])
            if isinstance(content, list):
                for block in content:
                    if (isinstance(block, dict)
                            and block.get('type') == 'tool_use'
                            and block.get('name') == 'Bash'):
                        cmd = block.get('input', {}).get('command', '').strip()
                        if cmd == expected_cmd:
                            bash_tool_use_id = block.get('id')
                            break
        if bash_tool_use_id:
            break

    if not bash_tool_use_id:
        return False, (
            f'no Bash tool_use event with expected command found; '
            f'expected={expected_cmd!r}'
        )

    # Find matching tool_result in user event.message.content
    for ev in events:
        msg = ev.get('message')
        if isinstance(msg, dict):
            content = msg.get('content', [])
            if isinstance(content, list):
                for block in content:
                    if (isinstance(block, dict)
                            and block.get('type') == 'tool_result'
                            and block.get('tool_use_id') == bash_tool_use_id):
                        if block.get('is_error') is True:
                            return False, (
                                f'Bash tool_result for id={bash_tool_use_id} has is_error=true'
                            )
                        return True, f'tool_use_id={bash_tool_use_id}'

    return False, f'no tool_result found for tool_use_id={bash_tool_use_id}'


def _codex_tool_evidence(out, expected_cmd_str):
    """Parse Codex JSONL for item.completed command_execution matching expected command."""
    import re as _re
    expected_cmd = expected_cmd_str.strip()
    for line in out.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            return False, f'malformed JSON line in JSONL: {line[:80]!r}'
        if not isinstance(obj, dict):
            return False, f'non-object line in JSONL: {line[:80]!r}'
        if obj.get('type') == 'item.completed':
            item = obj.get('item', {})
            if (isinstance(item, dict)
                    and item.get('type') == 'command_execution'
                    and item.get('exit_code') == 0
                    and item.get('status') == 'completed'):
                cmd = item.get('command', '')
                if not expected_cmd or expected_cmd not in cmd:
                    continue
                # Reject echo/Write-Output/printf wrappers of the full expected command.
                # A legitimate wrapper is powershell -Command <...> or pwsh -Command <...>.
                # An echo/Write-Output wrapper is not a real execution of the command.
                stripped_cmd = cmd.strip()
                echo_pattern = _re.compile(
                    r'^(echo|Write-Output|printf|Write-Host)\s', _re.IGNORECASE
                )
                if echo_pattern.match(stripped_cmd):
                    continue
                return True, 'command_execution exit_code=0 cmd contains expected command'
    return False, (
        f'no item.completed command_execution exit_code=0 with expected command; '
        f'expected={expected_cmd!r}'
    )


def _validate_bidi_artifact(artifact, inner_nonce, direction, expected_root,
                             outer_session_id, tool_evidence, tool_detail, outer_raw_stdout):
    """Full validation of the probe_peer.py evidence artifact."""
    if not isinstance(artifact, dict):
        return 'Fail(artifact is not a JSON object)'

    required = ('status', 'direction', 'nonce', 'rc', 'root', 'rule_hash',
                'inner_session_id', 'target_cli', 'raw_inner_stdout')
    missing = [f for f in required if f not in artifact]
    if missing:
        return f'Fail(artifact missing fields: {missing})'

    if artifact.get('status') != 'Pass':
        return f'Fail(inner probe status={artifact.get("status")})'

    rc = artifact.get('rc')
    if isinstance(rc, bool) or not isinstance(rc, int) or rc != 0:
        return f'Fail(inner rc={rc!r}; expected int 0, not bool)'

    if artifact.get('nonce') != inner_nonce:
        return 'Fail(inner nonce mismatch in artifact)'

    if artifact.get('direction') != direction:
        return f'Fail(direction mismatch: {artifact.get("direction")})'

    expected_target = 'codex' if direction == 'claude-to-codex' else 'claude'
    if artifact.get('target_cli') != expected_target:
        return f'Fail(target_cli mismatch: got {artifact.get("target_cli")!r} expected {expected_target!r})'

    # root must be absolute and match expected
    art_root = artifact.get('root', '')
    if not art_root or not pathlib.Path(str(art_root)).is_absolute():
        return f'Fail(artifact root not absolute: {art_root!r})'
    try:
        if pathlib.Path(str(art_root)).resolve() != pathlib.Path(expected_root).resolve():
            return f'Fail(artifact root mismatch: {art_root!r})'
    except OSError as e:
        return f'Fail(cannot resolve artifact root: {e})'

    inner_session = artifact.get('inner_session_id', '')
    if not isinstance(inner_session, str) or not inner_session:
        return 'Fail(artifact inner_session_id missing or empty)'

    # Compute expected rule_hash from expected_root's workflow — missing file is Fail
    try:
        workflow = pathlib.Path(expected_root) / 'docs/ai-collaboration-workflow.md'
        if not workflow.is_file():
            return 'Fail(workflow file missing — cannot validate rule_hash)'
        expected_hash = hashlib.sha256(workflow.read_bytes()).hexdigest()[:16]
        if artifact.get('rule_hash') != expected_hash:
            return (
                f'Fail(artifact rule_hash mismatch: '
                f'got={artifact.get("rule_hash")!r} expected={expected_hash!r})'
            )
    except OSError as e:
        return f'Fail(cannot compute rule_hash for validation: {e})'

    # raw_inner_stdout must be a nonempty string
    raw_inner = artifact.get('raw_inner_stdout')
    if not isinstance(raw_inner, str) or not raw_inner:
        return 'Fail(artifact raw_inner_stdout missing or empty)'

    # Re-decode raw_inner_stdout and validate nonce, root, rule_hash, direction, session
    inner_cli = 'codex' if direction == 'claude-to-codex' else 'claude'
    try:
        from _protocol import decode as _decode
        re_inner, re_session = _decode(inner_cli, 0, raw_inner)
    except ValueError as exc:
        return f'Fail(re-decode of raw_inner_stdout: {exc})'

    if re_session != inner_session:
        return f'Fail(re-decoded inner session mismatch: {re_session!r} != {inner_session!r})'
    if re_inner.get('nonce') != inner_nonce:
        return f'Fail(re-decoded inner nonce mismatch: {re_inner.get("nonce")!r})'

    # Validate all key fields in re-decoded payload via validate_payload (not nonce only)
    try:
        expected_hash = hashlib.sha256(
            (pathlib.Path(expected_root) / 'docs/ai-collaboration-workflow.md').read_bytes()
        ).hexdigest()[:16]
        from _protocol import validate_payload as _vp
        _vp(re_inner, {
            'nonce': inner_nonce,
            'root': expected_root,
            'rule_hash': expected_hash,
            'direction': direction,
        })
    except OSError as e:
        return f'Fail(cannot read workflow for re-decoded validation: {e})'
    except ValueError as exc:
        return f'Fail(re-decoded inner payload field mismatch: {exc})'

    if not tool_evidence:
        return f'NotVerified(artifact valid but outer tool event not found: {tool_detail})'

    return 'Pass'


# ---------------------------------------------------------------------------
# Report
# ---------------------------------------------------------------------------

def _build_report(session_id, timestamp, root, config, results, live, bidirectional):
    # Separate metadata (session_id) from actual checks
    model_session_ids = {
        k: v for k, v in results.items() if k.endswith(':session_id')
    }
    check_results = {k: v for k, v in results.items() if not k.endswith(':session_id')}

    passed = sum(1 for v in check_results.values() if str(v).startswith('Pass'))
    failed = sum(1 for v in check_results.values() if str(v).startswith('Fail'))
    not_verified = sum(1 for v in check_results.values() if str(v).startswith('NotVerified'))
    not_run = sum(1 for v in check_results.values() if str(v).startswith('NotRun'))

    overall_fail = failed > 0
    overall_not_verified = (not overall_fail) and (not_verified > 0)
    requested_not_run = (not overall_fail) and (not overall_not_verified) and (
        (live and any(
            k.startswith('live:') and str(v).startswith('NotRun')
            for k, v in check_results.items()
        ))
        or (bidirectional and any(
            k.startswith('bidirectional:') and str(v).startswith('NotRun')
            for k, v in check_results.items()
        ))
    )

    if overall_fail:
        overall = 'Fail'
    elif overall_not_verified:
        overall = 'NotVerified'
    elif requested_not_run:
        overall = 'NotRun'
    else:
        overall = 'Pass'

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
            'not_verified': not_verified,
            'not_run': not_run,
        },
        'overall': overall,
    }


def _save_report(root, session_id, report, raw_logs):
    report_dir = root / 'reports' / 'connection_reports'
    report_dir.mkdir(parents=True, exist_ok=True)

    path = report_dir / f'check_{session_id}.json'
    path.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding='utf-8')

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
    Load .collaboration/local.json using the shared validated loader.

    Returns (config_dict, error_list).
    error_list entries: {'key': str, 'msg': str}
    """
    from _project import load_config as _shared_load
    try:
        config = _shared_load(root)
        return config, []
    except ValueError as e:
        msg = str(e)
        if 'config not found' in msg:
            key = 'missing'
            user_msg = f'{msg} — run: python scripts/init_project.py'
        elif 'malformed JSON' in msg or 'cannot read' in msg:
            key = 'parse'
            user_msg = msg
        else:
            key = 'invalid'
            user_msg = msg
        safe_config = {
            'timeouts': {'preflight_secs': 15, 'live_secs': 180},
            'cli': {},
            'overrides': {},
        }
        return safe_config, [{'key': key, 'msg': user_msg}]
    except OSError as e:
        safe_config = {
            'timeouts': {'preflight_secs': 15, 'live_secs': 180},
            'cli': {},
            'overrides': {},
        }
        return safe_config, [{'key': 'oserror', 'msg': str(e)}]


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
    if isinstance(val, bool):
        return False
    if not isinstance(val, (int, float)):
        return False
    import math
    return math.isfinite(val) and val > 0


if __name__ == '__main__':
    sys.path.insert(0, str(pathlib.Path(__file__).parent))
    sys.exit(main())
