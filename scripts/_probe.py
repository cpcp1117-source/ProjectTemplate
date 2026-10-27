"""
Shared probe invocation helpers for check_connection.py and probe_peer.py.

Provides:
  - invoke_cli(cli, path, user_args, root, timeout, env) -> (rc, stdout, stderr)
  - shell_command(args) -> command string for evidence/logging
  - make_ps_file_cmd(ps_script, params) -> subprocess arg list for PS -File invocation
  - make_safe_bash_cmd(args) -> shlex-quoted string for Claude Bash tool (POSIX or PS -Command)
  - make_probe_peer_cmd_str(root, probe_script, direction, inner_nonce) -> (cmd_str, subprocess_args)
  - make_context_cmd_str(root, context_script, nonce, direction) -> (cmd_str, subprocess_args)

Model-facing Windows command uses powershell.exe -NoProfile -NonInteractive -Command
with & and the absolute Python exe directly — avoids ExecutionPolicy issues with -File.
invoke_probe.ps1 remains available for manual use but is not used in default probes.
No -EncodedCommand, no Invoke-Expression, no ExecutionPolicy bypass. Never bare 'python'.
"""

import os
import pathlib
import shlex
import subprocess
import sys


def _ps_path():
    """Return absolute path to powershell.exe via %SystemRoot%."""
    system_root = os.environ.get('SystemRoot', r'C:\Windows')
    return str(pathlib.Path(system_root) / 'System32' / 'WindowsPowerShell' / 'v1.0' / 'powershell.exe')


def make_ps_file_cmd(ps_script, params):
    """
    Return a subprocess arg list: powershell.exe -File <ps_script> [params...].

    ps_script: absolute path to a .ps1 file (str or Path).
    params: flat list of ['-Param', 'value', ...] strings.
    No -EncodedCommand. Args are passed as literal subprocess elements.
    """
    ps = _ps_path()
    return [ps, '-NoProfile', '-NonInteractive', '-File', str(ps_script)] + list(params)


def make_safe_bash_cmd(args):
    """
    Return a shell string suitable for Claude's Bash tool.

    On Windows: wraps args in a powershell.exe -NoProfile -NonInteractive -Command
    invocation using & with the absolute Python exe and literal arguments.
    No -EncodedCommand, no ExecutionPolicy change, no invoke_probe.ps1.
    On POSIX: shlex.join of the arg list (direct Python invocation).

    args is the Python subprocess arg list (python, script, --param, value, ...).
    """
    if os.name == 'nt':
        # Build a readable PS -Command invocation with & and literal args.
        # Single quotes in PS -Command: double any existing single quotes.
        def _ps_quote(s):
            return "'" + str(s).replace("'", "''") + "'"
        inner = '& ' + ' '.join(_ps_quote(a) for a in args)
        ps = _ps_path().replace('\\', '/')
        # Wrap for Bash: powershell.exe -NoProfile -NonInteractive -Command '<inner>'
        return shlex.join([ps, '-NoProfile', '-NonInteractive', '-Command', inner])
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


def _ps_command_str(py_args):
    """
    Build a powershell.exe -Command string invoking Python directly via &.

    py_args: list of strings [python_exe, script, arg, ...].
    Single quotes are doubled for PowerShell quoting inside -Command '...'.
    Returns the full cmd_str (for embedding in a model prompt / Bash tool call)
    and the subprocess_args list (for local evidence verification).
    """
    def _ps_quote(s):
        return "'" + str(s).replace("'", "''") + "'"

    ps = _ps_path()
    inner = '& ' + ' '.join(_ps_quote(a) for a in py_args)
    subprocess_args = [ps, '-NoProfile', '-NonInteractive', '-Command', inner]
    # Readable string for Claude Bash tool: shlex-quote the whole PS invocation
    ps_fwd = ps.replace('\\', '/')
    cmd_str = shlex.join([ps_fwd, '-NoProfile', '-NonInteractive', '-Command', inner])
    return cmd_str, subprocess_args


def make_probe_peer_cmd_str(root, probe_script, direction, inner_nonce):
    """
    Build the exact safe command string that the outer model must run via Bash/shell.

    On Windows: returns a readable powershell.exe -Command string invoking Python
    directly via & with literal arguments. No -File, no ExecutionPolicy change,
    no -EncodedCommand. Inspectable by Read tool.
    On POSIX: returns shlex-quoted Python invocation.

    Returns (cmd_str, subprocess_args_for_verification).
    cmd_str is embedded in the model prompt.
    subprocess_args is what we'd run directly (for evidence matching).
    """
    root = pathlib.Path(root)
    if os.name == 'nt':
        py_args = [
            sys.executable, '-X', 'utf8',
            str(probe_script),
            '--direction', direction,
            '--nonce', inner_nonce,
            '--root', str(root),
        ]
        return _ps_command_str(py_args)
    else:
        subprocess_args = [
            sys.executable, '-X', 'utf8',
            str(probe_script),
            '--direction', direction,
            '--nonce', inner_nonce,
            '--root', str(root),
        ]
        cmd_str = shlex.join(str(a) for a in subprocess_args)
    return cmd_str, subprocess_args


def make_context_cmd_str(root, context_script, nonce, direction=None):
    """
    Build safe command string for probe_context.py invocation.

    On Windows: readable powershell.exe -Command string invoking Python directly
    via & with literal arguments. No -File, no ExecutionPolicy change.
    On POSIX: shlex-quoted Python invocation.

    Returns (cmd_str, subprocess_args).
    """
    root = pathlib.Path(root)
    if os.name == 'nt':
        py_args = [
            sys.executable, '-X', 'utf8',
            str(context_script),
            '--nonce', nonce,
        ]
        if direction is not None:
            py_args += ['--direction', direction]
        return _ps_command_str(py_args)
    else:
        subprocess_args = [
            sys.executable, '-X', 'utf8',
            str(context_script),
            '--nonce', nonce,
        ]
        if direction is not None:
            subprocess_args += ['--direction', direction]
        cmd_str = shlex.join(str(a) for a in subprocess_args)
    return cmd_str, subprocess_args


def probe_env(base_env, depth, no_delegation=False):
    """Build environment dict for a probe subprocess."""
    env = (base_env or os.environ).copy()
    env['AI_COLLAB_CHECK_DEPTH'] = str(depth)
    if no_delegation:
        env['AI_COLLAB_NO_DELEGATION'] = '1'
    env['MAX_THINKING_TOKENS'] = '0'
    env['CLAUDE_CODE_DISABLE_ADAPTIVE_THINKING'] = '1'
    return env
