"""
Shared probe invocation helpers for check_connection.py and probe_peer.py.

Provides:
  - invoke_cli(cli, path, user_args, root, timeout, env) -> (rc, stdout, stderr)
  - shell_command(args) -> command string for evidence/logging
  - make_ps_encoded_cmd(args) -> PowerShell -EncodedCommand invocation list
  - make_safe_bash_cmd(args) -> shlex-quoted string for Claude Bash tool

Safe Windows PS command building:
  - Constructs a PS script using & with literal quoted args; double single-quotes inside.
  - UTF-16LE base64 encode for -EncodedCommand; no shell metacharacter expansion.
  - For POSIX: shlex.join of the arg list.
  - Never uses bare 'python' or relative paths.
"""

import base64
import os
import pathlib
import shlex
import subprocess
import sys


def _ps_path():
    """Return absolute path to powershell.exe via %SystemRoot%."""
    system_root = os.environ.get('SystemRoot', r'C:\Windows')
    return str(pathlib.Path(system_root) / 'System32' / 'WindowsPowerShell' / 'v1.0' / 'powershell.exe')


def _ps_literal(s):
    """Escape a string for use inside PS single-quoted string literal."""
    return s.replace("'", "''")


def _build_ps_script(args):
    """
    Build a PowerShell script that invokes args safely.

    Each arg is placed in a single-quoted PS literal; & calls the first.
    Appends '; exit $LASTEXITCODE' so the PS process propagates rc.
    """
    if not args:
        raise ValueError('args must be non-empty')
    quoted = [f"'{_ps_literal(str(a))}'" for a in args]
    return '& ' + ' '.join(quoted) + '; exit $LASTEXITCODE'


def make_ps_encoded_cmd(args):
    """
    Return a subprocess arg list that runs args via PowerShell -EncodedCommand.

    Safe for args containing spaces, CJK, apostrophes, &, $, %, etc.
    """
    script = _build_ps_script(args)
    encoded = base64.b64encode(script.encode('utf-16-le')).decode('ascii')
    ps = _ps_path()
    return [ps, '-NoProfile', '-NonInteractive', '-EncodedCommand', encoded]


def make_safe_bash_cmd(args):
    """
    Return a shell string suitable for Claude's Bash tool (POSIX shlex).

    Converts PowerShell.exe path to forward slashes for Bash compatibility.
    """
    if os.name == 'nt':
        ps = _ps_path().replace('\\', '/')
        script = _build_ps_script(args)
        encoded = base64.b64encode(script.encode('utf-16-le')).decode('ascii')
        parts = [ps, '-NoProfile', '-NonInteractive', '-EncodedCommand', encoded]
        return shlex.join(parts)
    return shlex.join(str(a) for a in args)


def shell_command(args):
    """Return a human-readable command string for evidence/logging."""
    return shlex.join(str(a) for a in args)


def invoke_cli(cli, path, user_args, root, timeout_secs, env=None):
    """
    Invoke a CLI safely, returning (rc, stdout, stderr).

    Uses _cli_helper.build_args for .cmd/.py resolution.
    On Windows uses PS encoded command for subprocess to avoid shell injection.
    """
    sys.path.insert(0, str(pathlib.Path(__file__).parent))
    from _cli_helper import build_args, run_with_timeout

    args = build_args(path, list(user_args))
    return run_with_timeout(args, timeout_secs=timeout_secs, cwd=str(root), env=env)


def make_probe_peer_cmd_str(root, probe_script, direction, inner_nonce):
    """
    Build the exact safe command string that outer model must run via Bash.

    Returns (cmd_str, subprocess_args_for_verification).
    cmd_str is what we embed in the prompt (shlex-quoted or PS-encoded).
    """
    args = [
        sys.executable, '-X', 'utf8',
        str(probe_script),
        '--direction', direction,
        '--nonce', inner_nonce,
        '--root', str(root),
    ]
    if os.name == 'nt':
        cmd_str = make_safe_bash_cmd(args)
    else:
        cmd_str = shlex.join(str(a) for a in args)
    return cmd_str, args


def make_context_cmd_str(root, context_script, nonce, direction=None):
    """
    Build safe command string for probe_context.py invocation.

    Returns (cmd_str, subprocess_args).
    """
    args = [
        sys.executable, '-X', 'utf8',
        str(context_script),
        '--nonce', nonce,
    ]
    if direction is not None:
        args += ['--direction', direction]
    if os.name == 'nt':
        cmd_str = make_safe_bash_cmd(args)
    else:
        cmd_str = shlex.join(str(a) for a in args)
    return cmd_str, args


def probe_env(base_env, depth, no_delegation=False):
    """Build environment dict for a probe subprocess."""
    env = (base_env or os.environ).copy()
    env['AI_COLLAB_CHECK_DEPTH'] = str(depth)
    if no_delegation:
        env['AI_COLLAB_NO_DELEGATION'] = '1'
    env['MAX_THINKING_TOKENS'] = '0'
    env['CLAUDE_CODE_DISABLE_ADAPTIVE_THINKING'] = '1'
    return env
