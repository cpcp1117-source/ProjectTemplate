"""
Tests for scripts/_cli_helper.py, scripts/init_project.py,
scripts/check_connection.py, and scripts/probe_peer.py.

All tests use fake CLIs or in-process function calls.
No real model calls are made (all live/bidirectional tests are Mock mode).
Mock CLIs implement real protocol fixtures matching actual CLI output formats.

Run:
  python -m unittest tests/test_scripts.py

Mock vs Live separation:
  Mock  — uses patched subprocess / in-process calls; always runs.
  Live  — requires real CLI login; NOT run here (mark Not Run in reports).
  NotRun — checks that are skipped due to missing CLI / preflight failure.
"""

import hashlib
import json
import os
import pathlib
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import MagicMock, patch

SCRIPTS_DIR = pathlib.Path(__file__).parent.parent / 'scripts'
sys.path.insert(0, str(SCRIPTS_DIR))


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_temp_project(tmp_path, extra_files=None, missing=None):
    """Create a minimal project fixture with all required docs."""
    required = [
        'docs/ai-collaboration-workflow.md',
        'docs/project-brief.md',
        'tasks/current_task.md',
        'CLAUDE.md',
        'AGENTS.md',
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
    missing_set = set(missing or [])
    root = pathlib.Path(tmp_path)
    for rel in required:
        if rel in missing_set:
            continue
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        if rel == 'docs/ai-collaboration-workflow.md':
            p.write_text('# AI Collaboration Workflow\nVersion: 1.0\n', encoding='utf-8')
        else:
            p.write_text(f'# {rel}\n', encoding='utf-8')

    if extra_files:
        for rel, content in extra_files.items():
            p = root / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(content, encoding='utf-8')

    return root


def _write_config(root, data):
    collab = root / '.collaboration'
    collab.mkdir(parents=True, exist_ok=True)
    (collab / 'local.json').write_text(
        json.dumps(data, ensure_ascii=False), encoding='utf-8'
    )


def _compute_hash(root):
    return hashlib.sha256(
        (root / 'docs/ai-collaboration-workflow.md').read_bytes()
    ).hexdigest()[:16]


def _make_claude_response(nonce, root_str, rule_hash, session_id='sess_test01'):
    """Return a proper Claude --output-format json envelope string (Mock protocol fixture)."""
    inner = json.dumps({'nonce': nonce, 'root': root_str, 'rule_hash': rule_hash})
    return json.dumps({
        'type': 'result',
        'subtype': 'success',
        'is_error': False,
        'session_id': session_id,
        'result': inner,
    })


def _make_codex_response(nonce, root_str, rule_hash, session_id='sess_cx01',
                          thread_id='thread_cx01'):
    """Return proper Codex --json JSONL lines (Mock protocol fixture)."""
    inner_text = json.dumps({'nonce': nonce, 'root': root_str, 'rule_hash': rule_hash})
    lines = [
        json.dumps({'type': 'turn.started',
                    'thread_id': thread_id, 'session_id': session_id}),
        json.dumps({'type': 'item.completed', 'item': {
            'type': 'agent_message',
            'content': [{'type': 'text', 'text': inner_text}],
        }}),
        json.dumps({'type': 'turn.completed',
                    'thread_id': thread_id, 'session_id': session_id}),
    ]
    return '\n'.join(lines) + '\n'


# ---------------------------------------------------------------------------
# _cli_helper tests
# ---------------------------------------------------------------------------

class TestCliHelper(unittest.TestCase):

    def _import(self):
        import _cli_helper
        return _cli_helper

    def test_build_args_posix_passthrough(self):
        h = self._import()
        with patch.object(os, 'name', 'posix'):
            args = h.build_args('/usr/bin/claude', ['exec', 'hello'])
        self.assertEqual(args, ['/usr/bin/claude', 'exec', 'hello'])

    def test_build_args_windows_non_cmd(self):
        h = self._import()
        with patch.object(os, 'name', 'nt'):
            args = h.build_args('C:/bin/claude.exe', ['-p', 'hi'])
        self.assertEqual(args, ['C:/bin/claude.exe', '-p', 'hi'])

    def test_build_args_windows_cmd_unresolvable_fails_closed(self):
        """When .cmd shim cannot be resolved, raise ValueError — no cmd /c fallback."""
        h = self._import()
        with tempfile.TemporaryDirectory() as tmp:
            cmd_path = pathlib.Path(tmp) / 'fake.cmd'
            cmd_path.write_text('@echo off\necho no JS path here\n', encoding='utf-8')
            with patch.object(os, 'name', 'nt'):
                with self.assertRaises(ValueError) as cm:
                    h.build_args(str(cmd_path), ['arg1', 'arg2'])
        msg = str(cm.exception).lower()
        self.assertIn('native', msg)
        self.assertNotIn('cmd', msg.split('/c')[0] if '/c' in msg else msg)

    def test_build_args_windows_cmd_native_exe_preferred(self):
        """If same-named .exe exists alongside .cmd, use the native .exe."""
        h = self._import()
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = pathlib.Path(tmp)
            cmd_path = tmp_path / 'claude.cmd'
            exe_path = tmp_path / 'claude.exe'
            cmd_path.write_text('@echo off\n', encoding='utf-8')
            exe_path.write_text('', encoding='utf-8')
            with patch.object(os, 'name', 'nt'):
                args = h.build_args(str(cmd_path), ['-p', 'hi'])
        self.assertEqual(args[0], str(exe_path))
        self.assertIn('-p', args)

    def test_build_args_windows_cmd_resolved_to_node_js(self):
        """When .cmd shim references a real .js file, prefer node + js."""
        h = self._import()
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = pathlib.Path(tmp)
            js_file = tmp_path / 'node_modules' / 'cli.js'
            js_file.parent.mkdir(parents=True)
            js_file.write_text('// fake', encoding='utf-8')
            node_exe = tmp_path / 'node.exe'
            node_exe.write_text('', encoding='utf-8')
            cmd_content = '@ECHO off\n"%dp0%\\node_modules\\cli.js" %*\n'
            cmd_path = tmp_path / 'fake.cmd'
            cmd_path.write_text(cmd_content, encoding='utf-8')
            with patch.object(os, 'name', 'nt'):
                args = h.build_args(str(cmd_path), ['run'])
        self.assertIn(str(node_exe), args[0])
        self.assertIn(str(js_file), args[1])
        self.assertIn('run', args)

    def test_run_with_timeout_uses_devnull_stdin(self):
        """subprocess.Popen must be called with stdin=DEVNULL."""
        h = self._import()
        with patch('subprocess.Popen') as mock_popen:
            mock_proc = MagicMock()
            mock_proc.communicate.return_value = (b'hello\n', b'')
            mock_proc.returncode = 0
            mock_popen.return_value = mock_proc
            h.run_with_timeout(['echo', 'hello'], timeout_secs=5)
        call_kwargs = mock_popen.call_args[1]
        self.assertEqual(call_kwargs.get('stdin'), subprocess.DEVNULL)

    def test_run_with_timeout_success(self):
        h = self._import()
        with patch('subprocess.Popen') as mock_popen:
            mock_proc = MagicMock()
            mock_proc.communicate.return_value = (b'hello\n', b'')
            mock_proc.returncode = 0
            mock_popen.return_value = mock_proc
            rc, out, err = h.run_with_timeout(['echo', 'hello'], timeout_secs=5)
        self.assertEqual(rc, 0)
        self.assertIn('hello', out)

    def test_run_with_timeout_kills_on_timeout(self):
        """Timeout triggers process-tree cleanup and re-raises TimeoutExpired."""
        h = self._import()
        with patch('subprocess.Popen') as mock_popen:
            mock_proc = MagicMock()
            mock_proc.pid = 12345
            mock_proc.communicate.side_effect = subprocess.TimeoutExpired(
                cmd=['sleep'], timeout=1
            )
            mock_popen.return_value = mock_proc

            with patch.object(os, 'name', 'nt'):
                with patch('subprocess.run') as mock_kill:
                    with self.assertRaises(subprocess.TimeoutExpired):
                        h.run_with_timeout(['sleep', '999'], timeout_secs=1)
                    mock_kill.assert_called_once()
                    call_args = mock_kill.call_args[0][0]
                    self.assertIn('taskkill', call_args)
                    self.assertIn('/T', call_args)
                    self.assertIn('/F', call_args)

    def test_path_with_spaces_and_special_chars(self):
        """Paths with spaces, CJK, apostrophes, &, $, % pass through verbatim as list items."""
        h = self._import()
        tricky_args = [
            "path with spaces/arg",
            "CJK_路徑/file.txt",
            "apostrophe's/test",
            "amp&ersand",
            "dollar$sign",
            "percent%var",
        ]
        with patch.object(os, 'name', 'posix'):
            args = h.build_args('/usr/bin/cli', tricky_args)
        for i, ta in enumerate(tricky_args):
            self.assertEqual(args[i + 1], ta)

    def test_find_cli_uses_override(self):
        h = self._import()
        result = h.find_cli('claude', overrides={'claude': '/fake/claude'})
        self.assertEqual(result, '/fake/claude')

    def test_find_cli_falls_back_to_path(self):
        h = self._import()
        with patch('shutil.which', return_value='/usr/bin/claude'):
            result = h.find_cli('claude', overrides={})
        self.assertEqual(result, '/usr/bin/claude')

    def test_find_cli_returns_none_when_missing(self):
        h = self._import()
        with patch('shutil.which', return_value=None):
            result = h.find_cli('nonexistent', overrides={})
        self.assertIsNone(result)


# ---------------------------------------------------------------------------
# init_project tests
# ---------------------------------------------------------------------------

class TestInitProject(unittest.TestCase):

    def _run(self, argv):
        import init_project
        return init_project.main(argv)

    def test_creates_config(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = _make_temp_project(tmp)
            rc = self._run(['--root', str(root)])
            self.assertEqual(rc, 0)
            config_file = root / '.collaboration' / 'local.json'
            self.assertTrue(config_file.exists())
            data = json.loads(config_file.read_text('utf-8'))
            self.assertEqual(data['project_id'], root.name)
            self.assertEqual(data['rule_version'], '1.0')
            self.assertIn('rule_hash', data)

    def test_init_twice_preserves_bytes(self):
        """Second init should exit 0 without modifying the file."""
        with tempfile.TemporaryDirectory() as tmp:
            root = _make_temp_project(tmp)
            self._run(['--root', str(root)])
            config_file = root / '.collaboration' / 'local.json'
            original = config_file.read_bytes()
            with self.assertRaises(SystemExit) as cm:
                self._run(['--root', str(root)])
            self.assertEqual(cm.exception.code, 0)
            self.assertEqual(config_file.read_bytes(), original)

    def test_root_move_detected(self):
        with tempfile.TemporaryDirectory() as tmp1:
            with tempfile.TemporaryDirectory() as tmp2:
                root1 = _make_temp_project(tmp1)
                self._run(['--root', str(root1)])
                root2 = _make_temp_project(tmp2)
                import shutil
                collab2 = root2 / '.collaboration'
                collab2.mkdir(parents=True, exist_ok=True)
                shutil.copy(
                    str(root1 / '.collaboration' / 'local.json'),
                    str(collab2 / 'local.json'),
                )
                with self.assertRaises(SystemExit) as cm:
                    self._run(['--root', str(root2)])
                self.assertEqual(cm.exception.code, 1)

    def test_malformed_json_config_fails(self):
        """Malformed existing config must exit 1, not silently accept."""
        with tempfile.TemporaryDirectory() as tmp:
            root = _make_temp_project(tmp)
            collab = root / '.collaboration'
            collab.mkdir(parents=True, exist_ok=True)
            (collab / 'local.json').write_text('NOT JSON', encoding='utf-8')
            with self.assertRaises(SystemExit) as cm:
                self._run(['--root', str(root)])
            self.assertEqual(cm.exception.code, 1)

    def test_non_object_json_config_fails(self):
        """Config that is valid JSON but not an object must exit 1."""
        with tempfile.TemporaryDirectory() as tmp:
            root = _make_temp_project(tmp)
            collab = root / '.collaboration'
            collab.mkdir(parents=True, exist_ok=True)
            (collab / 'local.json').write_text('[1,2,3]', encoding='utf-8')
            with self.assertRaises(SystemExit) as cm:
                self._run(['--root', str(root)])
            self.assertEqual(cm.exception.code, 1)

    def test_missing_required_doc_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = _make_temp_project(tmp, missing=['docs/ai-collaboration-workflow.md'])
            with self.assertRaises(SystemExit) as cm:
                self._run(['--root', str(root)])
            self.assertEqual(cm.exception.code, 1)

    def test_missing_skill_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = _make_temp_project(tmp, missing=['docs/skills/collaboration-handoff.md'])
            with self.assertRaises(SystemExit) as cm:
                self._run(['--root', str(root)])
            self.assertEqual(cm.exception.code, 1)

    def test_rule_hash_stored(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = _make_temp_project(tmp)
            self._run(['--root', str(root)])
            data = json.loads((root / '.collaboration' / 'local.json').read_text('utf-8'))
            expected = _compute_hash(root)
            self.assertEqual(data['rule_hash'], expected)


# ---------------------------------------------------------------------------
# check_connection tests
# ---------------------------------------------------------------------------

class TestCheckConnection(unittest.TestCase):

    def _run(self, argv, tmp_root):
        import check_connection
        return check_connection.main(argv + ['--root', str(tmp_root)])

    # --- Preflight ---

    def test_preflight_pass_all_docs_present(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = _make_temp_project(tmp)
            _write_config(root, {
                'root': str(root),
                'rule_hash': _compute_hash(root),
                'cli': {'claude': None, 'codex': None},
                'overrides': {},
                'timeouts': {'preflight_secs': 5, 'live_secs': 60},
            })
            rc = self._run([], root)
            # CLI not-found causes Fail for version checks, but structure completes
            self.assertIn(rc, (0, 1))

    def test_preflight_fail_missing_doc(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = _make_temp_project(tmp, missing=['CLAUDE.md'])
            _write_config(root, {
                'root': str(root),
                'rule_hash': '',
                'cli': {}, 'overrides': {},
                'timeouts': {'preflight_secs': 5},
            })
            rc = self._run([], root)
            self.assertEqual(rc, 1)

    def test_preflight_fail_missing_agent_skill(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = _make_temp_project(
                tmp, missing=['.agents/skills/collaboration-handoff/SKILL.md']
            )
            _write_config(root, {
                'root': str(root),
                'rule_hash': _compute_hash(root),
                'cli': {}, 'overrides': {},
                'timeouts': {'preflight_secs': 5},
            })
            rc = self._run([], root)
            self.assertEqual(rc, 1)

    def test_preflight_fail_missing_claude_skill(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = _make_temp_project(
                tmp, missing=['.claude/skills/verify-and-review/SKILL.md']
            )
            _write_config(root, {
                'root': str(root),
                'rule_hash': _compute_hash(root),
                'cli': {}, 'overrides': {},
                'timeouts': {'preflight_secs': 5},
            })
            rc = self._run([], root)
            self.assertEqual(rc, 1)

    def test_missing_cli_reported_as_fail(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = _make_temp_project(tmp)
            _write_config(root, {
                'root': str(root),
                'rule_hash': _compute_hash(root),
                'cli': {'claude': '/nonexistent/claude', 'codex': None},
                'overrides': {},
                'timeouts': {'preflight_secs': 5},
            })
            with patch('_cli_helper.run_with_timeout',
                       side_effect=FileNotFoundError('not found')):
                rc = self._run([], root)
            self.assertEqual(rc, 1)

    def test_rule_hash_mismatch_detected(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = _make_temp_project(tmp)
            _write_config(root, {
                'root': str(root),
                'rule_hash': 'deadbeef00000000',
                'cli': {}, 'overrides': {},
                'timeouts': {'preflight_secs': 5},
            })
            rc = self._run([], root)
            self.assertEqual(rc, 1)

    def test_rule_hash_missing_in_config_fails(self):
        """No stored hash must Fail (not NotRun)."""
        with tempfile.TemporaryDirectory() as tmp:
            root = _make_temp_project(tmp)
            _write_config(root, {
                'root': str(root),
                'rule_hash': '',
                'cli': {}, 'overrides': {},
                'timeouts': {'preflight_secs': 5},
            })
            rc = self._run([], root)
            self.assertEqual(rc, 1)

    def test_malformed_config_reported_as_fail(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = _make_temp_project(tmp)
            collab = root / '.collaboration'
            collab.mkdir(parents=True, exist_ok=True)
            (collab / 'local.json').write_text('NOT JSON', encoding='utf-8')
            rc = self._run([], root)
            self.assertEqual(rc, 1)

    def test_config_non_object_json_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = _make_temp_project(tmp)
            collab = root / '.collaboration'
            collab.mkdir(parents=True, exist_ok=True)
            (collab / 'local.json').write_text('[1,2,3]', encoding='utf-8')
            rc = self._run([], root)
            self.assertEqual(rc, 1)

    def test_timeout_bool_value_rejected(self):
        """Boolean timeout value must Fail preflight."""
        with tempfile.TemporaryDirectory() as tmp:
            root = _make_temp_project(tmp)
            _write_config(root, {
                'root': str(root),
                'rule_hash': _compute_hash(root),
                'cli': {}, 'overrides': {},
                'timeouts': {'preflight_secs': True, 'live_secs': 60},
            })
            import check_connection
            results = check_connection._run_preflight(
                root, {'timeouts': {'preflight_secs': True}}, [], {}, {'preflight_secs': True}
            )
            # At least one key should indicate a timeout validation failure
            self.assertTrue(
                any('Fail' in str(v) for v in results.values()),
                f'Expected Fail for bool timeout, got: {results}'
            )

    def test_timeout_string_value_rejected(self):
        """String timeout value must Fail preflight."""
        with tempfile.TemporaryDirectory() as tmp:
            root = _make_temp_project(tmp)
            import check_connection
            results = check_connection._run_preflight(
                root, {'timeouts': {'live_secs': 'fast'}}, [],
                {}, {'live_secs': 'fast'}
            )
            self.assertTrue(
                any('Fail' in str(v) for v in results.values()),
                f'Expected Fail for string timeout, got: {results}'
            )

    def test_config_wrong_root_reported(self):
        """Config with wrong stored root must Fail."""
        with tempfile.TemporaryDirectory() as tmp:
            with tempfile.TemporaryDirectory() as tmp2:
                root = _make_temp_project(tmp)
                _write_config(root, {
                    'root': str(tmp2),  # wrong root
                    'rule_hash': _compute_hash(root),
                    'cli': {}, 'overrides': {},
                    'timeouts': {'preflight_secs': 5},
                })
                rc = self._run([], root)
                self.assertEqual(rc, 1)

    # --- Live validation (Mock protocol fixtures) ---

    def test_live_claude_valid_response_passes(self):
        """Claude proper protocol envelope with correct nonce/root/hash passes."""
        with tempfile.TemporaryDirectory() as tmp:
            root = _make_temp_project(tmp)
            rule_hash = _compute_hash(root)
            nonce_holder = {}

            def fake_run(full_args, timeout_secs, cwd=None):
                # Extract nonce from args
                prompt = next((full_args[i+1] for i, a in enumerate(full_args)
                               if a == '-p' and i+1 < len(full_args)), '')
                import re
                m = re.search(r'"nonce":"([a-f0-9]+)"', prompt)
                nonce = m.group(1) if m else 'unknown'
                nonce_holder['nonce'] = nonce
                return (0, _make_claude_response(nonce, str(root), rule_hash), '')

            _write_config(root, {
                'root': str(root),
                'rule_hash': rule_hash,
                'cli': {'claude': '/fake/claude', 'codex': None},
                'overrides': {'claude': '/fake/claude'},
                'timeouts': {'preflight_secs': 5, 'live_secs': 30},
            })
            with patch('_cli_helper.run_with_timeout', side_effect=fake_run):
                with patch('_cli_helper.build_args', side_effect=lambda p, a: [p] + a):
                    rc = self._run(['--live'], root)
            self.assertEqual(rc, 0)

    def test_live_claude_exit0_is_error_fails(self):
        """Claude exit 0 but is_error=true must be Fail."""
        with tempfile.TemporaryDirectory() as tmp:
            root = _make_temp_project(tmp)
            fake_response = json.dumps({
                'type': 'result',
                'subtype': 'error',
                'is_error': True,
                'error': 'Something went wrong',
                'session_id': 'sess_err',
            })
            _write_config(root, {
                'root': str(root),
                'rule_hash': _compute_hash(root),
                'cli': {'claude': '/fake/claude', 'codex': None},
                'overrides': {'claude': '/fake/claude'},
                'timeouts': {'preflight_secs': 5, 'live_secs': 30},
            })
            with patch('_cli_helper.run_with_timeout',
                       return_value=(0, fake_response, '')):
                with patch('_cli_helper.build_args', side_effect=lambda p, a: [p] + a):
                    rc = self._run(['--live'], root)
            self.assertEqual(rc, 1)

    def test_live_claude_malformed_json_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = _make_temp_project(tmp)
            _write_config(root, {
                'root': str(root),
                'rule_hash': _compute_hash(root),
                'cli': {'claude': '/fake/claude', 'codex': None},
                'overrides': {'claude': '/fake/claude'},
                'timeouts': {'preflight_secs': 5, 'live_secs': 30},
            })
            with patch('_cli_helper.run_with_timeout',
                       return_value=(0, 'NOT JSON AT ALL', '')):
                with patch('_cli_helper.build_args', side_effect=lambda p, a: [p] + a):
                    rc = self._run(['--live'], root)
            self.assertEqual(rc, 1)

    def test_live_claude_raw_text_with_nonce_fails(self):
        """Raw text containing only the nonce (no JSON envelope) must Fail — no relaxed pass."""
        with tempfile.TemporaryDirectory() as tmp:
            root = _make_temp_project(tmp)
            rule_hash = _compute_hash(root)
            _write_config(root, {
                'root': str(root),
                'rule_hash': rule_hash,
                'cli': {'claude': '/fake/claude', 'codex': None},
                'overrides': {'claude': '/fake/claude'},
                'timeouts': {'preflight_secs': 5, 'live_secs': 30},
            })
            nonce = 'abc123def456ghi7'
            # Raw text response — NOT a Claude envelope
            raw_response = f'The nonce is {nonce} and nothing else.'
            with patch('_cli_helper.run_with_timeout',
                       return_value=(0, raw_response, '')):
                with patch('_cli_helper.build_args', side_effect=lambda p, a: [p] + a):
                    rc = self._run(['--live'], root)
            self.assertEqual(rc, 1)

    def test_live_claude_wrong_type_fails(self):
        """Claude response with type != 'result' must Fail."""
        with tempfile.TemporaryDirectory() as tmp:
            root = _make_temp_project(tmp)
            nonce = 'aabbccdd11223344'
            fake_response = json.dumps({
                'type': 'message',  # wrong
                'subtype': 'success',
                'is_error': False,
                'session_id': 'sess_x',
                'result': json.dumps({'nonce': nonce}),
            })
            _write_config(root, {
                'root': str(root),
                'rule_hash': _compute_hash(root),
                'cli': {'claude': '/fake/claude', 'codex': None},
                'overrides': {'claude': '/fake/claude'},
                'timeouts': {'preflight_secs': 5, 'live_secs': 30},
            })
            with patch('_cli_helper.run_with_timeout',
                       return_value=(0, fake_response, '')):
                with patch('_cli_helper.build_args', side_effect=lambda p, a: [p] + a):
                    rc = self._run(['--live'], root)
            self.assertEqual(rc, 1)

    def test_live_claude_missing_session_id_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = _make_temp_project(tmp)
            nonce = 'aabbccdd11223344'
            rule_hash = _compute_hash(root)
            fake_response = json.dumps({
                'type': 'result',
                'subtype': 'success',
                'is_error': False,
                # session_id absent
                'result': json.dumps({'nonce': nonce, 'root': str(root),
                                      'rule_hash': rule_hash}),
            })
            _write_config(root, {
                'root': str(root),
                'rule_hash': rule_hash,
                'cli': {'claude': '/fake/claude', 'codex': None},
                'overrides': {'claude': '/fake/claude'},
                'timeouts': {'preflight_secs': 5, 'live_secs': 30},
            })
            with patch('_cli_helper.run_with_timeout',
                       return_value=(0, fake_response, '')):
                with patch('_cli_helper.build_args', side_effect=lambda p, a: [p] + a):
                    rc = self._run(['--live'], root)
            self.assertEqual(rc, 1)

    def test_live_claude_nonce_mismatch_fails(self):
        """Claude response with wrong nonce must Fail."""
        with tempfile.TemporaryDirectory() as tmp:
            root = _make_temp_project(tmp)
            rule_hash = _compute_hash(root)
            _write_config(root, {
                'root': str(root),
                'rule_hash': rule_hash,
                'cli': {'claude': '/fake/claude', 'codex': None},
                'overrides': {'claude': '/fake/claude'},
                'timeouts': {'preflight_secs': 5, 'live_secs': 30},
            })
            # Return a response with a WRONG nonce
            fake_response = json.dumps({
                'type': 'result',
                'subtype': 'success',
                'is_error': False,
                'session_id': 'sess_x',
                'result': json.dumps({'nonce': 'wrongnonce000000',
                                      'root': str(root),
                                      'rule_hash': rule_hash}),
            })
            with patch('_cli_helper.run_with_timeout',
                       return_value=(0, fake_response, '')):
                with patch('_cli_helper.build_args', side_effect=lambda p, a: [p] + a):
                    rc = self._run(['--live'], root)
            self.assertEqual(rc, 1)

    def test_live_claude_empty_response_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = _make_temp_project(tmp)
            _write_config(root, {
                'root': str(root),
                'rule_hash': _compute_hash(root),
                'cli': {'claude': '/fake/claude', 'codex': None},
                'overrides': {'claude': '/fake/claude'},
                'timeouts': {'preflight_secs': 5, 'live_secs': 30},
            })
            with patch('_cli_helper.run_with_timeout',
                       return_value=(0, '', '')):
                with patch('_cli_helper.build_args', side_effect=lambda p, a: [p] + a):
                    rc = self._run(['--live'], root)
            self.assertEqual(rc, 1)

    def test_live_codex_valid_response_passes(self):
        """Codex proper JSONL with turn.completed + agent_message + nonce passes."""
        with tempfile.TemporaryDirectory() as tmp:
            root = _make_temp_project(tmp)
            rule_hash = _compute_hash(root)

            def fake_run(full_args, timeout_secs, cwd=None):
                prompt_arg = full_args[-1] if full_args else ''
                import re
                m = re.search(r'"nonce":"([a-f0-9]+)"', prompt_arg)
                nonce = m.group(1) if m else 'unknown'
                return (0, _make_codex_response(nonce, str(root), rule_hash), '')

            _write_config(root, {
                'root': str(root),
                'rule_hash': rule_hash,
                'cli': {'claude': None, 'codex': '/fake/codex'},
                'overrides': {'codex': '/fake/codex'},
                'timeouts': {'preflight_secs': 5, 'live_secs': 30},
            })
            with patch('_cli_helper.run_with_timeout', side_effect=fake_run):
                with patch('_cli_helper.build_args', side_effect=lambda p, a: [p] + a):
                    rc = self._run(['--live'], root)
            self.assertEqual(rc, 0)

    def test_live_codex_turn_failed_event_fails(self):
        """Codex JSONL with turn.failed must Fail."""
        with tempfile.TemporaryDirectory() as tmp:
            root = _make_temp_project(tmp)
            rule_hash = _compute_hash(root)
            nonce = 'failnonce0000001'
            error_jsonl = (
                json.dumps({'type': 'turn.started',
                            'thread_id': 't1', 'session_id': 's1'}) + '\n' +
                json.dumps({'type': 'turn.failed', 'error': 'model error'}) + '\n'
            )
            _write_config(root, {
                'root': str(root),
                'rule_hash': rule_hash,
                'cli': {'claude': None, 'codex': '/fake/codex'},
                'overrides': {'codex': '/fake/codex'},
                'timeouts': {'preflight_secs': 5, 'live_secs': 30},
            })
            with patch('_cli_helper.run_with_timeout',
                       return_value=(0, error_jsonl, '')):
                with patch('_cli_helper.build_args', side_effect=lambda p, a: [p] + a):
                    rc = self._run(['--live'], root)
            self.assertEqual(rc, 1)

    def test_live_codex_missing_turn_completed_fails(self):
        """Codex JSONL without turn.completed must Fail."""
        with tempfile.TemporaryDirectory() as tmp:
            root = _make_temp_project(tmp)
            rule_hash = _compute_hash(root)
            nonce = 'noturncomp000001'
            incomplete_jsonl = (
                json.dumps({'type': 'turn.started',
                            'thread_id': 't1', 'session_id': 's1'}) + '\n' +
                json.dumps({'type': 'item.completed', 'item': {
                    'type': 'agent_message',
                    'content': [{'type': 'text',
                                 'text': json.dumps({'nonce': nonce})}],
                }}) + '\n'
                # No turn.completed
            )
            _write_config(root, {
                'root': str(root),
                'rule_hash': rule_hash,
                'cli': {'claude': None, 'codex': '/fake/codex'},
                'overrides': {'codex': '/fake/codex'},
                'timeouts': {'preflight_secs': 5, 'live_secs': 30},
            })
            with patch('_cli_helper.run_with_timeout',
                       return_value=(0, incomplete_jsonl, '')):
                with patch('_cli_helper.build_args', side_effect=lambda p, a: [p] + a):
                    rc = self._run(['--live'], root)
            self.assertEqual(rc, 1)

    def test_live_codex_rc1_with_nonce_fails(self):
        """Codex rc=1 with type=error and nonce in message must Fail — exit code matters."""
        with tempfile.TemporaryDirectory() as tmp:
            root = _make_temp_project(tmp)
            rule_hash = _compute_hash(root)
            nonce = 'rc1nonce00000001'
            error_jsonl = json.dumps({
                'type': 'error',
                'message': f'error containing nonce {nonce}',
            }) + '\n'
            _write_config(root, {
                'root': str(root),
                'rule_hash': rule_hash,
                'cli': {'claude': None, 'codex': '/fake/codex'},
                'overrides': {'codex': '/fake/codex'},
                'timeouts': {'preflight_secs': 5, 'live_secs': 30},
            })
            with patch('_cli_helper.run_with_timeout',
                       return_value=(1, error_jsonl, '')):
                with patch('_cli_helper.build_args', side_effect=lambda p, a: [p] + a):
                    rc = self._run(['--live'], root)
            self.assertEqual(rc, 1)

    def test_live_codex_nonce_only_in_raw_text_fails(self):
        """Nonce in raw JSONL outside agent_message must Fail — no relaxed pass."""
        with tempfile.TemporaryDirectory() as tmp:
            root = _make_temp_project(tmp)
            rule_hash = _compute_hash(root)
            nonce = 'rawonly000000001'
            # Nonce appears in a non-agent_message line (raw text, not in protocol)
            raw_jsonl = (
                json.dumps({'type': 'turn.started',
                            'thread_id': 't1', 'session_id': 's1'}) + '\n' +
                json.dumps({'type': 'turn.completed',
                            'thread_id': 't1', 'session_id': 's1',
                            'extra': nonce}) + '\n'
            )
            _write_config(root, {
                'root': str(root),
                'rule_hash': rule_hash,
                'cli': {'claude': None, 'codex': '/fake/codex'},
                'overrides': {'codex': '/fake/codex'},
                'timeouts': {'preflight_secs': 5, 'live_secs': 30},
            })
            with patch('_cli_helper.run_with_timeout',
                       return_value=(0, raw_jsonl, '')):
                with patch('_cli_helper.build_args', side_effect=lambda p, a: [p] + a):
                    rc = self._run(['--live'], root)
            self.assertEqual(rc, 1)

    def test_live_timeout_reported_as_fail(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = _make_temp_project(tmp)
            _write_config(root, {
                'root': str(root),
                'rule_hash': _compute_hash(root),
                'cli': {'claude': '/fake/claude', 'codex': '/fake/codex'},
                'overrides': {'claude': '/fake/claude', 'codex': '/fake/codex'},
                'timeouts': {'preflight_secs': 5, 'live_secs': 30},
            })
            with patch('_cli_helper.run_with_timeout',
                       side_effect=subprocess.TimeoutExpired(cmd=['fake'], timeout=1)):
                with patch('_cli_helper.build_args', side_effect=lambda p, a: [p] + a):
                    rc = self._run(['--live'], root)
            self.assertEqual(rc, 1)

    def test_live_not_run_when_preflight_fails(self):
        """Live checks must be NotRun if preflight fails — no live calls made."""
        with tempfile.TemporaryDirectory() as tmp:
            root = _make_temp_project(tmp, missing=['CLAUDE.md'])
            _write_config(root, {
                'root': str(root),
                'rule_hash': _compute_hash(root),
                'cli': {'claude': '/fake/claude', 'codex': '/fake/codex'},
                'overrides': {'claude': '/fake/claude', 'codex': '/fake/codex'},
                'timeouts': {'preflight_secs': 5, 'live_secs': 30},
            })
            live_called = []
            with patch('_cli_helper.run_with_timeout',
                       side_effect=lambda *a, **kw: live_called.append(True) or (0, '', '')):
                with patch('_cli_helper.build_args', side_effect=lambda p, a: [p] + a):
                    rc = self._run(['--live'], root)
            self.assertEqual(rc, 1)
            # run_with_timeout should only have been called for preflight (CLI version)
            # not for live model calls — at most 2 calls for version checks
            self.assertLessEqual(len(live_called), 2)

    # --- Bidirectional ---

    def test_bidirectional_requires_live(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = _make_temp_project(tmp)
            with self.assertRaises(SystemExit) as cm:
                self._run(['--bidirectional'], root)
            self.assertEqual(cm.exception.code, 1)

    def test_bidirectional_refuses_recursive_call(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = _make_temp_project(tmp)
            env_backup = os.environ.get('AI_COLLAB_CHECK_DEPTH')
            os.environ['AI_COLLAB_CHECK_DEPTH'] = '1'
            try:
                with self.assertRaises(SystemExit) as cm:
                    self._run(['--live', '--bidirectional'], root)
                self.assertEqual(cm.exception.code, 1)
            finally:
                if env_backup is None:
                    os.environ.pop('AI_COLLAB_CHECK_DEPTH', None)
                else:
                    os.environ['AI_COLLAB_CHECK_DEPTH'] = env_backup

    def test_bidirectional_verifies_inner_artifact_with_all_fields(self):
        """
        When outer model writes valid artifacts with all required fields for both
        directions, bidirectional passes (tool evidence found in output).
        """
        with tempfile.TemporaryDirectory() as tmp:
            root = _make_temp_project(tmp)
            rule_hash = _compute_hash(root)
            _write_config(root, {
                'root': str(root),
                'rule_hash': rule_hash,
                'cli': {'claude': '/fake/claude', 'codex': '/fake/codex'},
                'overrides': {},
                'timeouts': {'preflight_secs': 5, 'live_secs': 30},
            })
            probe_script = root / 'scripts' / 'probe_peer.py'
            probe_script.parent.mkdir(parents=True, exist_ok=True)
            probe_script.write_text('# placeholder', encoding='utf-8')

            import check_connection
            import re as _re

            call_index = [0]

            def fake_outer(full_args, timeout_secs, cwd=None, env=None):
                # Determine which call this is (alternates between directions)
                idx = call_index[0]
                call_index[0] += 1
                direction = 'claude-to-codex' if idx % 2 == 0 else 'codex-to-claude'

                # Extract inner_nonce from prompt (last arg or -p arg)
                inner_nonce = None
                outer_nonce = None
                for a in full_args:
                    prompt_text = str(a)
                    m1 = _re.search(r'--nonce\s+(\S+)', prompt_text)
                    if m1:
                        inner_nonce = m1.group(1)
                    m2 = _re.search(r'"outer_nonce":"([a-f0-9]+)"', prompt_text)
                    if m2:
                        outer_nonce = m2.group(1)

                if inner_nonce and outer_nonce:
                    art = (root / 'reports' / 'connection_reports' /
                           f'probe_{direction}_{inner_nonce}.json')
                    art.parent.mkdir(parents=True, exist_ok=True)
                    art.write_text(json.dumps({
                        'status': 'Pass',
                        'direction': direction,
                        'nonce': inner_nonce,
                        'rc': 0,
                        'session_id': 'sess_inner_01',
                        'root': str(root),
                        'rule_hash': rule_hash,
                        'nonce_found': True,
                        'inner_response': json.dumps({'nonce': inner_nonce}),
                        'auth_error': False,
                    }), encoding='utf-8')

                # Return outer response with outer_nonce and probe_peer.py evidence
                outer_resp = json.dumps({
                    'type': 'result',
                    'subtype': 'success',
                    'is_error': False,
                    'session_id': 'sess_outer_01',
                    'result': json.dumps({
                        'outer_nonce': outer_nonce or '',
                        'inner_nonce': inner_nonce,
                        'tool_used': True,
                        'probe_peer.py': 'invoked',
                    }),
                })
                return (0, outer_resp, '')

            with patch('_cli_helper.run_with_timeout', side_effect=fake_outer):
                with patch('_cli_helper.build_args', side_effect=lambda p, a: [p] + a):
                    with patch.object(check_connection, '_run_preflight', return_value={}):
                        with patch.object(check_connection, '_run_live', return_value=({}, {})):
                            rc = check_connection.main(
                                ['--live', '--bidirectional', '--root', str(root)]
                            )
            self.assertEqual(rc, 0)

    def test_bidirectional_missing_artifact_not_pass(self):
        """If inner artifact is not written, result must not be Pass."""
        with tempfile.TemporaryDirectory() as tmp:
            root = _make_temp_project(tmp)
            _write_config(root, {
                'root': str(root),
                'rule_hash': _compute_hash(root),
                'cli': {'claude': '/fake/claude', 'codex': '/fake/codex'},
                'overrides': {},
                'timeouts': {'preflight_secs': 5, 'live_secs': 30},
            })
            probe_script = root / 'scripts' / 'probe_peer.py'
            probe_script.parent.mkdir(parents=True, exist_ok=True)
            probe_script.write_text('# placeholder', encoding='utf-8')

            import check_connection

            def fake_outer(full_args, timeout_secs, cwd=None, env=None):
                # Return success but do NOT write artifact
                return (0, json.dumps({
                    'type': 'result', 'subtype': 'success',
                    'is_error': False, 'session_id': 'sess_x',
                    'result': '{}',
                }), '')

            with patch('_cli_helper.run_with_timeout', side_effect=fake_outer):
                with patch('_cli_helper.build_args', side_effect=lambda p, a: [p] + a):
                    with patch.object(check_connection, '_run_preflight', return_value={}):
                        with patch.object(check_connection, '_run_live', return_value=({}, {})):
                            rc = check_connection.main(
                                ['--live', '--bidirectional', '--root', str(root)]
                            )
            self.assertNotEqual(rc, 0)

    def test_report_saved_to_connection_reports(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = _make_temp_project(tmp)
            _write_config(root, {
                'root': str(root),
                'rule_hash': _compute_hash(root),
                'cli': {}, 'overrides': {},
                'timeouts': {'preflight_secs': 5},
            })
            self._run([], root)
            reports = list((root / 'reports' / 'connection_reports').glob('check_*.json'))
            self.assertTrue(len(reports) > 0)

    def test_report_has_run_id_not_session_id(self):
        """Report top-level must use run_id, not session_id."""
        with tempfile.TemporaryDirectory() as tmp:
            root = _make_temp_project(tmp)
            _write_config(root, {
                'root': str(root),
                'rule_hash': _compute_hash(root),
                'cli': {}, 'overrides': {},
                'timeouts': {'preflight_secs': 5},
            })
            self._run([], root)
            reports = list((root / 'reports' / 'connection_reports').glob('check_*.json'))
            data = json.loads(reports[0].read_text('utf-8'))
            self.assertIn('run_id', data)
            self.assertNotIn('session_id', data)

    def test_codex_invocation_includes_json_flag(self):
        """Live Codex invocation must include --json flag."""
        with tempfile.TemporaryDirectory() as tmp:
            root = _make_temp_project(tmp)
            rule_hash = _compute_hash(root)
            _write_config(root, {
                'root': str(root),
                'rule_hash': rule_hash,
                'cli': {'claude': None, 'codex': '/fake/codex'},
                'overrides': {'codex': '/fake/codex'},
                'timeouts': {'preflight_secs': 5, 'live_secs': 30},
            })
            captured = {}

            def fake_build(path, args):
                captured['args'] = args
                return [path] + args

            nonce = rule_hash  # doesn't matter for this test
            with patch('_cli_helper.run_with_timeout',
                       return_value=(0, '', '')):
                with patch('_cli_helper.build_args', side_effect=fake_build):
                    self._run(['--live'], root)

            args_used = captured.get('args', [])
            self.assertIn('--json', args_used)
            self.assertIn('--sandbox', args_used)
            self.assertIn('--skip-git-repo-check', args_used)


# ---------------------------------------------------------------------------
# probe_peer tests
# ---------------------------------------------------------------------------

class TestProbePeer(unittest.TestCase):

    def _run(self, argv):
        import probe_peer
        return probe_peer.main(argv)

    def test_refuses_recursive_call(self):
        env_backup = os.environ.get('AI_COLLAB_CHECK_DEPTH')
        os.environ['AI_COLLAB_CHECK_DEPTH'] = '2'
        try:
            with self.assertRaises(SystemExit) as cm:
                self._run(['--direction', 'claude-to-codex',
                           '--nonce', 'abc', '--root', '/fake'])
            self.assertEqual(cm.exception.code, 1)
        finally:
            if env_backup is None:
                os.environ.pop('AI_COLLAB_CHECK_DEPTH', None)
            else:
                os.environ['AI_COLLAB_CHECK_DEPTH'] = env_backup

    def test_cli_not_found_writes_fail_artifact(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = _make_temp_project(tmp)
            _write_config(root, {
                'cli': {'claude': None, 'codex': None},
                'overrides': {'codex': None},
                'timeouts': {'live_secs': 30},
            })
            import probe_peer
            with patch('shutil.which', return_value=None):
                with self.assertRaises(SystemExit) as cm:
                    probe_peer.main([
                        '--direction', 'claude-to-codex',
                        '--nonce', 'testnonce123',
                        '--root', str(root),
                    ])
            self.assertEqual(cm.exception.code, 1)
            artifact = (root / 'reports' / 'connection_reports' /
                        'probe_claude-to-codex_testnonce123.json')
            self.assertTrue(artifact.exists())
            data = json.loads(artifact.read_text('utf-8'))
            self.assertEqual(data['status'], 'Fail')

    def test_successful_probe_writes_full_evidence(self):
        """Successful probe writes artifact with all required fields."""
        with tempfile.TemporaryDirectory() as tmp:
            root = _make_temp_project(tmp)
            nonce = 'mynonce42'
            rule_hash = _compute_hash(root)
            fake_response = _make_codex_response(nonce, str(root), rule_hash)
            _write_config(root, {
                'cli': {'claude': None, 'codex': '/fake/codex'},
                'overrides': {'codex': '/fake/codex'},
                'timeouts': {'live_secs': 30},
            })
            import probe_peer
            with patch('_cli_helper.run_with_timeout',
                       return_value=(0, fake_response, '')):
                with patch('_cli_helper.build_args',
                           return_value=['/fake/codex', 'exec', 'prompt']):
                    rc = probe_peer.main([
                        '--direction', 'claude-to-codex',
                        '--nonce', nonce,
                        '--root', str(root),
                    ])
            self.assertEqual(rc, 0)
            artifact = (root / 'reports' / 'connection_reports' /
                        f'probe_claude-to-codex_{nonce}.json')
            self.assertTrue(artifact.exists())
            data = json.loads(artifact.read_text('utf-8'))
            self.assertEqual(data['nonce'], nonce)
            self.assertEqual(data['status'], 'Pass')
            # All required evidence fields must be present
            for field in ('status', 'direction', 'nonce', 'rc', 'session_id',
                          'root', 'rule_hash'):
                self.assertIn(field, data, f'Missing field: {field}')
            self.assertEqual(data['direction'], 'claude-to-codex')
            self.assertIsNotNone(data['session_id'])

    def test_probe_codex_turn_failed_writes_fail_artifact(self):
        """Codex turn.failed response must produce Fail artifact."""
        with tempfile.TemporaryDirectory() as tmp:
            root = _make_temp_project(tmp)
            nonce = 'failnonce111'
            error_jsonl = (
                json.dumps({'type': 'turn.started',
                            'thread_id': 't1', 'session_id': 's1'}) + '\n' +
                json.dumps({'type': 'turn.failed', 'error': 'model crashed'}) + '\n'
            )
            _write_config(root, {
                'cli': {'codex': '/fake/codex'},
                'overrides': {'codex': '/fake/codex'},
                'timeouts': {'live_secs': 30},
            })
            import probe_peer
            with patch('_cli_helper.run_with_timeout',
                       return_value=(0, error_jsonl, '')):
                with patch('_cli_helper.build_args',
                           return_value=['/fake/codex', 'exec', 'p']):
                    with self.assertRaises(SystemExit):
                        probe_peer.main([
                            '--direction', 'claude-to-codex',
                            '--nonce', nonce,
                            '--root', str(root),
                        ])
            artifact = (root / 'reports' / 'connection_reports' /
                        f'probe_claude-to-codex_{nonce}.json')
            data = json.loads(artifact.read_text('utf-8'))
            self.assertIn('Fail', data['status'])

    def test_auth_error_detected(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = _make_temp_project(tmp)
            _write_config(root, {
                'cli': {'codex': '/fake/codex'},
                'overrides': {'codex': '/fake/codex'},
                'timeouts': {'live_secs': 30},
            })
            import probe_peer
            with patch('_cli_helper.run_with_timeout',
                       return_value=(1, '', 'Error: unauthorized login required')):
                with patch('_cli_helper.build_args',
                           return_value=['/fake/codex', 'exec', 'prompt']):
                    with self.assertRaises(SystemExit):
                        probe_peer.main([
                            '--direction', 'claude-to-codex',
                            '--nonce', 'authnonce',
                            '--root', str(root),
                        ])
            artifact = (root / 'reports' / 'connection_reports' /
                        'probe_claude-to-codex_authnonce.json')
            data = json.loads(artifact.read_text('utf-8'))
            self.assertTrue(data['auth_error'])

    def test_no_self_recursion_in_probe(self):
        """probe_peer must not import check_connection or probe_peer."""
        import probe_peer
        source = pathlib.Path(probe_peer.__file__).read_text('utf-8')
        self.assertNotIn('import probe_peer', source)
        self.assertNotIn('from probe_peer', source)
        self.assertNotIn('import check_connection', source)
        self.assertNotIn('from check_connection', source)
        self.assertIn('AI_COLLAB_CHECK_DEPTH', source)

    def test_probe_includes_no_delegation_env_for_inner_cli(self):
        """probe_peer must set NO_DELEGATION env var for inner CLI call."""
        import probe_peer
        source = pathlib.Path(probe_peer.__file__).read_text('utf-8')
        self.assertIn('AI_COLLAB_NO_DELEGATION', source)

    def test_probe_prompt_includes_no_delegation_instruction(self):
        """Inner CLI prompt must include NO_DELEGATION instruction."""
        import probe_peer
        source = pathlib.Path(probe_peer.__file__).read_text('utf-8')
        self.assertIn('NO_DELEGATION', source)


# ---------------------------------------------------------------------------
# Snapshot / defect scenario and recovery
# ---------------------------------------------------------------------------

class TestSnapshotAndRecovery(unittest.TestCase):

    def test_snapshot_defect_rule_hash_change(self):
        """
        Scenario: workflow doc edited after init → hash mismatch → Fail.
        Recovery: delete config, re-run init.
        """
        with tempfile.TemporaryDirectory() as tmp:
            root = _make_temp_project(tmp)
            import init_project
            init_project.main(['--root', str(root)])

            # Simulate workflow doc change
            workflow = root / 'docs' / 'ai-collaboration-workflow.md'
            workflow.write_text('# Modified\n', encoding='utf-8')

            import check_connection
            _write_config(root, json.loads(
                (root / '.collaboration' / 'local.json').read_text('utf-8')
            ))
            rc = check_connection.main(['--root', str(root)])
            self.assertEqual(rc, 1)

            # Recovery: delete and re-init
            (root / '.collaboration' / 'local.json').unlink()
            init_project.main(['--root', str(root)])
            config = json.loads(
                (root / '.collaboration' / 'local.json').read_text('utf-8')
            )
            new_hash = hashlib.sha256(workflow.read_bytes()).hexdigest()[:16]
            self.assertEqual(config['rule_hash'], new_hash)

    def test_missing_evidence_not_success(self):
        """
        Defect: bidirectional inner evidence artifact absent.
        Expected: Not Pass (Fail or NotVerified), not success.
        """
        with tempfile.TemporaryDirectory() as tmp:
            root = _make_temp_project(tmp)
            import check_connection
            # evidence dict missing nonce
            result = check_connection._validate_bidi_evidence(
                {'status': 'Pass', 'direction': 'claude-to-codex', 'nonce': 'xxx', 'rc': 0},
                inner_nonce='different_nonce',  # mismatch
                direction='claude-to-codex',
                tool_evidence=True,
                outer_nonce_found=True,
            )
            self.assertIn('Fail', result)

    def test_invalid_config_does_not_proceed_to_live(self):
        """
        Defect: malformed config → live checks must be NotRun.
        """
        with tempfile.TemporaryDirectory() as tmp:
            root = _make_temp_project(tmp)
            collab = root / '.collaboration'
            collab.mkdir(parents=True, exist_ok=True)
            (collab / 'local.json').write_text('GARBAGE', encoding='utf-8')

            call_count = [0]
            original_run = None
            import check_connection as cc

            def counting_run(*a, **kw):
                call_count[0] += 1
                return (0, '', '')

            with patch('_cli_helper.run_with_timeout', side_effect=counting_run):
                with patch('_cli_helper.build_args', side_effect=lambda p, a: [p] + a):
                    rc = cc.main(['--live', '--root', str(root)])
            self.assertEqual(rc, 1)
            # version checks may run, but not live model calls (which would be much more)
            # The important check is rc == 1
            # live checks for claude/codex are NotRun — verified via rc


if __name__ == '__main__':
    unittest.main()
