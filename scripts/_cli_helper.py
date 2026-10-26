"""
Shared CLI invocation helper for AI collaboration scripts.

Design constraints:
- Always use subprocess argument lists (never shell=True with untrusted input).
- On Windows, resolves .cmd shims to native exe or node+js; fails closed if unresolvable.
- UTF-8 stdout/stderr with replace error handling.
- stdin always DEVNULL to prevent pipe hangs.
- Process-tree cleanup on timeout (Windows: taskkill /T /F; POSIX: process group kill).
- Never interpolates untrusted prompt text through a shell string.
- Supports CLI path overrides for testing with fake executables.
"""

import os
import pathlib
import re
import shutil
import subprocess
import sys


def build_args(cli_path, user_args):
    """
    Build a safe subprocess argument list for the given CLI path.

    .py paths are invoked as [sys.executable, -X, utf8, path, ...args].
    On Windows, if the path ends in .cmd, attempts to resolve the shim to
    a native .exe or [node_exe, js_entrypoint]. Raises ValueError if the shim
    cannot be resolved — cmd /c is NOT used as a fallback because cmd.exe
    reparses metacharacters and expands % variables, creating shell injection risk.

    Set overrides.claude or overrides.codex in .collaboration/local.json to a
    native executable path (e.g. claude.exe) to bypass shim resolution.
    """
    cli_path = str(cli_path)
    user_args = list(user_args)

    if cli_path.lower().endswith('.py'):
        return [sys.executable, '-X', 'utf8', cli_path] + user_args

    if os.name == 'nt' and cli_path.lower().endswith('.cmd'):
        resolved = _resolve_cmd_shim(cli_path)
        if resolved:
            return resolved + user_args
        # Fail closed: cmd /c is unsafe.
        raise ValueError(
            f"Cannot safely invoke {cli_path!r}: Windows .cmd shim could not be "
            f"resolved to a native exe or node+js entrypoint. "
            f"Set overrides.claude or overrides.codex in .collaboration/local.json "
            f"to the full path of the native executable (e.g. claude.exe or codex.exe). "
            f"Run 'where claude' or 'where codex' in a terminal to find the native path."
        )

    return [cli_path] + user_args


def run_with_timeout(args, timeout_secs, cwd=None, env=None):
    """
    Run a subprocess with a timeout.

    Returns (returncode, stdout_str, stderr_str).
    Raises subprocess.TimeoutExpired after killing the process tree.

    stdin is always DEVNULL to prevent pipe hangs from interactive prompts.
    Process-tree cleanup:
      Windows: taskkill /T /F /PID <pid>
      POSIX:   os.killpg(os.getpgid(pid), signal.SIGKILL)
    """
    kwargs = dict(
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        stdin=subprocess.DEVNULL,
        cwd=cwd,
        env=env,
    )
    if os.name != 'nt':
        kwargs['start_new_session'] = True

    proc = subprocess.Popen(args, **kwargs)
    try:
        stdout, stderr = proc.communicate(timeout=timeout_secs)
        return (
            proc.returncode,
            stdout.decode('utf-8', errors='replace'),
            stderr.decode('utf-8', errors='replace'),
        )
    except subprocess.TimeoutExpired:
        cleanup_error = _kill_tree(proc)
        # Bounded reap of pipes after kill.
        try:
            proc.communicate(timeout=5)
        except subprocess.TimeoutExpired:
            cleanup_error = cleanup_error or 'pipe reap timed out after 5s'
        if cleanup_error:
            raise RuntimeError(f'Process cleanup failed: {cleanup_error}') from None
        raise


def find_cli(name, overrides=None):
    """
    Find a CLI executable by name.

    Checks overrides dict first, then PATH via shutil.which.
    Returns the path string or None.
    """
    if overrides and name in overrides:
        override = overrides[name]
        if override:
            return str(override)
    return shutil.which(name)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _resolve_cmd_shim(cmd_path_str):
    """
    Attempt to resolve a Windows .cmd shim to [native_exe] or [node_exe, js_path].

    Resolution order:
      1. Same-named .exe in the same directory (native exe shim).
      2. JS entrypoint via %dp0%\\...\\file.js pattern (npm-style shim).
      3. Absolute JS path "C:\\...\\file.js" pattern.

    Returns a list with one or two elements, or None if resolution fails.
    """
    cmd_path = pathlib.Path(cmd_path_str)
    if not cmd_path.exists():
        return None

    # Check for same-named native exe in the same directory
    native_exe = cmd_path.with_suffix('.exe')
    if native_exe.exists():
        return [str(native_exe)]

    try:
        content = cmd_path.read_text(encoding='utf-8', errors='replace')
    except OSError:
        return None

    dp0 = cmd_path.parent

    # Pattern 1: %dp0%\relative\path.exe (native exe — e.g. npm-installed claude shim)
    m = re.search(r'%dp0%\\([^\s"\'%]+\.exe)', content, re.IGNORECASE)
    if m:
        exe_path = dp0 / m.group(1)
        if exe_path.exists():
            return [str(exe_path)]

    # Pattern 2: %dp0%\relative\path.js
    m = re.search(r'%dp0%\\([^\s"\'%]+\.js)', content, re.IGNORECASE)
    if m:
        js_path = dp0 / m.group(1)
        node_exe = _find_node(dp0)
        if js_path.exists() and node_exe:
            return [node_exe, str(js_path)]

    # Pattern 3: absolute path "C:\...\file.js"
    m = re.search(r'"([A-Za-z]:[^"]+\.js)"', content)
    if m:
        js_path = pathlib.Path(m.group(1))
        node_exe = _find_node(dp0)
        if js_path.exists() and node_exe:
            return [node_exe, str(js_path)]

    return None


def _find_node(cmd_dir):
    """Find the node executable: bundled in cmd_dir first, then PATH."""
    bundled = cmd_dir / 'node.exe'
    if bundled.exists():
        return str(bundled)
    return shutil.which('node')


def _kill_tree(proc):
    """
    Kill the process tree after a timeout.

    Returns an error string if cleanup failed, None on success.
    Caller must still reap pipes (bounded communicate) after this returns.
    """
    if os.name == 'nt':
        try:
            result = subprocess.run(
                ['taskkill', '/T', '/F', '/PID', str(proc.pid)],
                capture_output=True,
                timeout=5,
            )
            # rc 0 = killed; 128 / 255 = process already gone — both acceptable.
            if result.returncode not in (0, 128, 255):
                return f'taskkill rc={result.returncode}'
        except subprocess.TimeoutExpired:
            return 'taskkill timed out after 5s'
        except FileNotFoundError:
            return 'taskkill not found on PATH'
    else:
        import signal
        try:
            os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
        except ProcessLookupError:
            pass
    return None
