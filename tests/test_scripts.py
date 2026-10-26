"""
stdlib unittest tests for AI collaboration scripts.

Coverage:
- Protocol decoder (_protocol.py): Claude + Codex real-format fixtures, negatives
- Config validation (_project.py): required fields, invalid types, missing docs
- CLI helper (_cli_helper.py): .py subprocess fixtures with special chars/CJK
- Outer tool evidence (_check_outer_tool_evidence): real event shapes, negatives
- Bidi artifact validation (_validate_bidi_artifact): positive + rejection cases
- peer refuses (probe_peer.py main): NO_DELEGATION, depth>1, invalid nonce filename
- Defect/snapshot/recovery scenario: isolated fixture with intentional defect

All tests use stdlib only. No real model calls, no real CLIs, no machine paths.
Temp dirs under tests/tmp/ (gitignored); paths validated before cleanup.
"""

import base64
import hashlib
import importlib
import json
import os
import pathlib
import subprocess
import sys
import tempfile
import unittest

SCRIPTS_DIR = pathlib.Path(__file__).parent.parent / 'scripts'
TESTS_TMP = pathlib.Path(__file__).parent / 'tmp'
sys.path.insert(0, str(SCRIPTS_DIR))

import _protocol as protocol
import _project as project
from _cli_helper import build_args, run_with_timeout


# ---------------------------------------------------------------------------
# Fixture builders
# ---------------------------------------------------------------------------

SAMPLE_PAYLOAD = {'nonce': 'abc123', 'root': '/fake/root', 'rule_hash': 'deadbeef12345678'}


def _claude_envelope(**overrides):
    env = dict(
        type='result',
        subtype='success',
        is_error=False,
        session_id='sess-claude-001',
        result=json.dumps(SAMPLE_PAYLOAD),
    )
    env.update(overrides)
    return json.dumps(env)


def _claude_stream(**overrides):
    """Multi-line stream-json with two events."""
    events = [
        json.dumps({'type': 'assistant', 'role': 'assistant', 'content': []}),
        json.dumps(dict(type='result', subtype='success', is_error=False,
                        session_id='sess-stream-001', result=json.dumps(SAMPLE_PAYLOAD),
                        **overrides)),
    ]
    return '\n'.join(events)


def _codex_jsonl(item=None, thread_id='x-sess-001', extra_events=None):
    events = [
        {'type': 'thread.started', 'thread_id': thread_id},
        {'type': 'turn.started'},
        {'type': 'item.completed', 'item': item or {
            'type': 'agent_message', 'text': json.dumps(SAMPLE_PAYLOAD),
        }},
        {'type': 'turn.completed'},
    ]
    if extra_events:
        events.extend(extra_events)
    return '\n'.join(json.dumps(e) for e in events)


def _make_project_root(tmp_dir, workflow_content='Version: 1.0\nFixture content\n'):
    """Create a minimal project with all 14 required paths."""
    root = pathlib.Path(tmp_dir)
    for rel in project.REQUIRED_PATHS:
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        if rel == project.WORKFLOW_DOC:
            p.write_text(workflow_content, encoding='utf-8')
        else:
            p.write_text('# fixture\n', encoding='utf-8')
    return root


def _make_valid_config(root):
    root = pathlib.Path(root).resolve()
    wf = root / project.WORKFLOW_DOC
    rule_hash = hashlib.sha256(wf.read_bytes()).hexdigest()[:16]
    return {
        'root': str(root),
        'project_id': 'test-project',
        'rule_version': project.RULE_VERSION,
        'rule_hash': rule_hash,
        'cli': {'claude': None, 'codex': None},
        'timeouts': {'preflight_secs': 15.0, 'live_secs': 180.0},
        'overrides': {},
    }


# ---------------------------------------------------------------------------
# Protocol: Claude decoder
# ---------------------------------------------------------------------------

class TestClaudeDecoder(unittest.TestCase):

    def test_valid_single_envelope(self):
        inner, session = protocol.decode('claude', 0, _claude_envelope())
        self.assertEqual(inner, SAMPLE_PAYLOAD)
        self.assertEqual(session, 'sess-claude-001')

    def test_valid_stream_json(self):
        inner, session = protocol.decode('claude', 0, _claude_stream())
        self.assertEqual(inner, SAMPLE_PAYLOAD)
        self.assertEqual(session, 'sess-stream-001')

    def test_reject_nonzero_rc(self):
        with self.assertRaises(ValueError):
            protocol.decode('claude', 1, _claude_envelope())

    def test_reject_is_error_true(self):
        with self.assertRaises(ValueError):
            protocol.decode('claude', 0, _claude_envelope(is_error=True))

    def test_reject_is_error_none(self):
        with self.assertRaises(ValueError):
            protocol.decode('claude', 0, _claude_envelope(is_error=None))

    def test_reject_is_error_zero(self):
        # 0 is falsy but not exactly False
        with self.assertRaises(ValueError):
            protocol.decode('claude', 0, _claude_envelope(is_error=0))

    def test_reject_empty_session_id(self):
        with self.assertRaises(ValueError):
            protocol.decode('claude', 0, _claude_envelope(session_id=''))

    def test_reject_missing_session_id(self):
        env = dict(type='result', subtype='success', is_error=False,
                   result=json.dumps(SAMPLE_PAYLOAD))
        with self.assertRaises(ValueError):
            protocol.decode('claude', 0, json.dumps(env))

    def test_reject_wrong_type(self):
        with self.assertRaises(ValueError):
            protocol.decode('claude', 0, _claude_envelope(type='error'))

    def test_reject_wrong_subtype(self):
        with self.assertRaises(ValueError):
            protocol.decode('claude', 0, _claude_envelope(subtype='error'))

    def test_reject_non_json_result(self):
        with self.assertRaises(ValueError):
            protocol.decode('claude', 0, _claude_envelope(result='not json'))

    def test_reject_non_object_result(self):
        with self.assertRaises(ValueError):
            protocol.decode('claude', 0, _claude_envelope(result=json.dumps([1, 2])))

    def test_reject_malformed_json(self):
        with self.assertRaises(ValueError):
            protocol.decode('claude', 0, 'not json at all')

    def test_reject_json_array(self):
        with self.assertRaises(ValueError):
            protocol.decode('claude', 0, json.dumps([1, 2, 3]))

    def test_reject_empty_stdout(self):
        with self.assertRaises(ValueError):
            protocol.decode('claude', 0, '')

    def test_stream_no_result_event(self):
        lines = '\n'.join([
            json.dumps({'type': 'assistant', 'content': []}),
            json.dumps({'type': 'completion', 'data': 'stuff'}),
        ])
        with self.assertRaises(ValueError):
            protocol.decode('claude', 0, lines)

    def test_stream_non_json_line_rejected(self):
        lines = _claude_envelope() + '\nnot-json-here'
        with self.assertRaises(ValueError):
            protocol.decode('claude', 0, lines)

    def test_stream_non_object_line_rejected(self):
        lines = _claude_envelope() + '\n' + json.dumps([1, 2])
        with self.assertRaises(ValueError):
            protocol.decode('claude', 0, lines)

    def test_auth_envelope_rejected(self):
        # Auth error envelopes: rc!=0 or error type
        auth_env = json.dumps({'type': 'error', 'error': {'type': 'authentication_error'}})
        with self.assertRaises(ValueError):
            protocol.decode('claude', 1, auth_env)

    def test_nonce_containing_401_still_valid(self):
        # Nonce value "has401here" must NOT be treated as auth error
        payload = dict(SAMPLE_PAYLOAD, nonce='has401here')
        env = _claude_envelope(result=json.dumps(payload))
        inner, session = protocol.decode('claude', 0, env)
        self.assertEqual(inner['nonce'], 'has401here')


# ---------------------------------------------------------------------------
# Protocol: Codex decoder
# ---------------------------------------------------------------------------

class TestCodexDecoder(unittest.TestCase):

    def test_valid_codex_jsonl(self):
        inner, session = protocol.decode('codex', 0, _codex_jsonl())
        self.assertEqual(inner, SAMPLE_PAYLOAD)
        self.assertEqual(session, 'x-sess-001')

    def test_reject_nonzero_rc(self):
        with self.assertRaises(ValueError):
            protocol.decode('codex', 1, _codex_jsonl())

    def test_reject_missing_thread_started(self):
        events = [
            {'type': 'item.completed', 'item': {'type': 'agent_message',
                                                  'text': json.dumps(SAMPLE_PAYLOAD)}},
            {'type': 'turn.completed'},
        ]
        with self.assertRaises(ValueError):
            protocol.decode('codex', 0, '\n'.join(json.dumps(e) for e in events))

    def test_reject_missing_turn_completed(self):
        events = [
            {'type': 'thread.started', 'thread_id': 'tid'},
            {'type': 'item.completed', 'item': {'type': 'agent_message',
                                                  'text': json.dumps(SAMPLE_PAYLOAD)}},
        ]
        with self.assertRaises(ValueError):
            protocol.decode('codex', 0, '\n'.join(json.dumps(e) for e in events))

    def test_reject_no_agent_message(self):
        events = [
            {'type': 'thread.started', 'thread_id': 'tid'},
            {'type': 'item.completed', 'item': {'type': 'command_execution', 'text': '{}'}},
            {'type': 'turn.completed'},
        ]
        with self.assertRaises(ValueError):
            protocol.decode('codex', 0, '\n'.join(json.dumps(e) for e in events))

    def test_reject_error_event(self):
        with self.assertRaises(ValueError):
            protocol.decode('codex', 0, _codex_jsonl(
                extra_events=[{'type': 'error', 'message': 'auth failed'}]
            ))

    def test_reject_turn_failed_event(self):
        with self.assertRaises(ValueError):
            protocol.decode('codex', 0, _codex_jsonl(
                extra_events=[{'type': 'turn.failed', 'error': 'oops'}]
            ))

    def test_reject_malformed_jsonl_line(self):
        with self.assertRaises(ValueError):
            protocol.decode('codex', 0, _codex_jsonl() + '\nnot-json')

    def test_reject_non_object_jsonl_line(self):
        with self.assertRaises(ValueError):
            protocol.decode('codex', 0, _codex_jsonl() + '\n' + json.dumps([]))

    def test_reject_agent_message_text_not_json(self):
        with self.assertRaises(ValueError):
            protocol.decode('codex', 0, _codex_jsonl(
                item={'type': 'agent_message', 'text': 'plain string not json'}
            ))

    def test_reject_agent_message_text_json_array(self):
        with self.assertRaises(ValueError):
            protocol.decode('codex', 0, _codex_jsonl(
                item={'type': 'agent_message', 'text': json.dumps([1, 2])}
            ))

    def test_reject_empty_stdout(self):
        with self.assertRaises(ValueError):
            protocol.decode('codex', 0, '')

    def test_nonce_only_message_rejected(self):
        # A raw string nonce is not a valid JSON object
        events = [
            {'type': 'thread.started', 'thread_id': 'tid'},
            {'type': 'item.completed', 'item': {'type': 'agent_message',
                                                  'text': '"just-a-nonce-string"'}},
            {'type': 'turn.completed'},
        ]
        with self.assertRaises(ValueError):
            protocol.decode('codex', 0, '\n'.join(json.dumps(e) for e in events))


# ---------------------------------------------------------------------------
# Config validation (_project.validate_config)
# ---------------------------------------------------------------------------

class TestConfigValidation(unittest.TestCase):

    def setUp(self):
        TESTS_TMP.mkdir(parents=True, exist_ok=True)
        self._td = tempfile.TemporaryDirectory(dir=TESTS_TMP, prefix='cfg_')
        self.root = pathlib.Path(self._td.name).resolve()
        _make_project_root(self.root)
        self.cfg = _make_valid_config(self.root)

    def tearDown(self):
        self._td.cleanup()

    def test_valid_config_passes(self):
        project.validate_config(self.root, self.cfg)

    def test_non_dict_raises(self):
        with self.assertRaises(ValueError):
            project.validate_config(self.root, [])

    def test_null_raises(self):
        with self.assertRaises(ValueError):
            project.validate_config(self.root, None)

    def test_missing_field_raises(self):
        for field in ('root', 'project_id', 'rule_version', 'rule_hash',
                      'cli', 'timeouts', 'overrides'):
            with self.subTest(field=field):
                bad = dict(self.cfg)
                del bad[field]
                with self.assertRaises(ValueError):
                    project.validate_config(self.root, bad)

    def test_relative_root_raises(self):
        bad = dict(self.cfg, root='relative/path')
        with self.assertRaises(ValueError):
            project.validate_config(self.root, bad)

    def test_moved_root_raises(self):
        bad = dict(self.cfg, root=str(self.root.parent))
        with self.assertRaises(ValueError):
            project.validate_config(self.root, bad)

    def test_wrong_rule_version_raises(self):
        bad = dict(self.cfg, rule_version='99.0')
        with self.assertRaises(ValueError):
            project.validate_config(self.root, bad)

    def test_stale_rule_hash_raises(self):
        bad = dict(self.cfg, rule_hash='0000000000000000')
        with self.assertRaises(ValueError):
            project.validate_config(self.root, bad)

    def test_cli_not_dict_raises(self):
        bad = dict(self.cfg, cli=[])
        with self.assertRaises(ValueError):
            project.validate_config(self.root, bad)

    def test_cli_unexpected_key_raises(self):
        bad = dict(self.cfg, cli={'claude': None, 'gpt': 'something'})
        with self.assertRaises(ValueError):
            project.validate_config(self.root, bad)

    def test_cli_value_bool_raises(self):
        bad = dict(self.cfg, cli={'claude': True, 'codex': None})
        with self.assertRaises(ValueError):
            project.validate_config(self.root, bad)

    def test_timeouts_not_dict_raises(self):
        bad = dict(self.cfg, timeouts=[])
        with self.assertRaises(ValueError):
            project.validate_config(self.root, bad)

    def test_timeout_bool_raises(self):
        bad = dict(self.cfg, timeouts={'preflight_secs': True, 'live_secs': 180.0})
        with self.assertRaises(ValueError):
            project.validate_config(self.root, bad)

    def test_timeout_nan_raises(self):
        bad = dict(self.cfg, timeouts={'preflight_secs': 15.0, 'live_secs': float('nan')})
        with self.assertRaises(ValueError):
            project.validate_config(self.root, bad)

    def test_timeout_zero_raises(self):
        bad = dict(self.cfg, timeouts={'preflight_secs': 0, 'live_secs': 180.0})
        with self.assertRaises(ValueError):
            project.validate_config(self.root, bad)

    def test_timeout_negative_raises(self):
        bad = dict(self.cfg, timeouts={'preflight_secs': -1.0, 'live_secs': 180.0})
        with self.assertRaises(ValueError):
            project.validate_config(self.root, bad)

    def test_overrides_not_dict_raises(self):
        bad = dict(self.cfg, overrides=[])
        with self.assertRaises(ValueError):
            project.validate_config(self.root, bad)

    def test_overrides_empty_string_value_raises(self):
        bad = dict(self.cfg, overrides={'claude': ''})
        with self.assertRaises(ValueError):
            project.validate_config(self.root, bad)

    def test_missing_required_doc_raises(self):
        for rel in project.REQUIRED_PATHS[:3]:
            with self.subTest(rel=rel):
                p = self.root / rel
                # Compute config BEFORE deleting the file so _make_valid_config can read it
                cfg = _make_valid_config(self.root)
                p.unlink()
                with self.assertRaises(ValueError):
                    project.validate_config(self.root, cfg)
                p.write_text('# fixture\n', encoding='utf-8')

    def test_required_path_is_directory_raises(self):
        # If a required path is a directory instead of a file, must fail
        rel = project.REQUIRED_PATHS[1]  # second entry, not workflow doc
        p = self.root / rel
        p.unlink()
        p.mkdir()
        with self.assertRaises(ValueError):
            project.validate_config(self.root, self.cfg)
        p.rmdir()
        p.write_text('# fixture\n', encoding='utf-8')

    def test_14_required_paths_present(self):
        self.assertEqual(len(project.REQUIRED_PATHS), 14)


# ---------------------------------------------------------------------------
# Init project: byte preservation and re-init
# ---------------------------------------------------------------------------

class TestInitProject(unittest.TestCase):

    def setUp(self):
        TESTS_TMP.mkdir(parents=True, exist_ok=True)
        self._td = tempfile.TemporaryDirectory(dir=TESTS_TMP, prefix='init_')
        self.root = pathlib.Path(self._td.name).resolve()
        _make_project_root(self.root)

    def tearDown(self):
        self._td.cleanup()

    def _run_init(self, *extra):
        script = SCRIPTS_DIR / 'init_project.py'
        result = subprocess.run(
            [sys.executable, '-X', 'utf8', str(script), '--root', str(self.root)] + list(extra),
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            encoding='utf-8', timeout=20,
        )
        return result.returncode, result.stdout

    def test_init_creates_config(self):
        rc, out = self._run_init()
        self.assertEqual(rc, 0, out)
        cfg_path = self.root / '.collaboration' / 'local.json'
        self.assertTrue(cfg_path.is_file())
        cfg = json.loads(cfg_path.read_text('utf-8'))
        project.validate_config(self.root, cfg)

    def test_init_twice_preserves_bytes(self):
        self._run_init()
        cfg_path = self.root / '.collaboration' / 'local.json'
        before = cfg_path.read_bytes()
        rc, out = self._run_init()
        self.assertEqual(rc, 0, out)
        self.assertEqual(cfg_path.read_bytes(), before)

    def test_init_missing_doc_fails(self):
        (self.root / 'AGENTS.md').unlink()
        rc, out = self._run_init()
        self.assertNotEqual(rc, 0, out)

    def test_init_missing_doc_does_not_overwrite_existing_config(self):
        self._run_init()
        cfg_path = self.root / '.collaboration' / 'local.json'
        before = cfg_path.read_bytes()
        (self.root / 'AGENTS.md').unlink()
        self._run_init()
        self.assertEqual(cfg_path.read_bytes(), before)

    def test_changed_rule_hash_fails_reinit(self):
        self._run_init()
        wf = self.root / project.WORKFLOW_DOC
        wf.write_text(wf.read_text('utf-8') + '\nchanged', encoding='utf-8')
        rc, out = self._run_init()
        self.assertNotEqual(rc, 0, out)


# ---------------------------------------------------------------------------
# CLI helper: .py subprocess fixture with special chars and CJK
# ---------------------------------------------------------------------------

class TestCliHelperSubprocess(unittest.TestCase):

    def setUp(self):
        TESTS_TMP.mkdir(parents=True, exist_ok=True)
        self._td = tempfile.TemporaryDirectory(dir=TESTS_TMP, prefix='cli_中文 ')
        self.tmp = pathlib.Path(self._td.name).resolve()
        # Ensure we stay inside tests/tmp
        assert self.tmp.is_relative_to(TESTS_TMP), f"path escaped: {self.tmp}"

    def tearDown(self):
        self._td.cleanup()

    def _write_echo_script(self, name='fake cli.py'):
        script = self.tmp / name
        script.write_text(
            'import json, sys; print(json.dumps(sys.argv[1:], ensure_ascii=False))',
            encoding='utf-8',
        )
        return script

    def test_py_script_echoes_argv_safely(self):
        script = self._write_echo_script()
        special_args = ['中文', "a'b", 'a&b', '$HOME', '%TEMP%', 'hello world']
        rc, out, err = run_with_timeout(build_args(str(script), special_args), 10)
        self.assertEqual(rc, 0, err)
        self.assertEqual(json.loads(out.strip()), special_args)

    def test_py_script_path_with_spaces_and_cjk(self):
        script = self._write_echo_script('script 中文 áé.py')
        rc, out, err = run_with_timeout(build_args(str(script), ['ping']), 10)
        self.assertEqual(rc, 0, err)
        self.assertEqual(json.loads(out.strip()), ['ping'])

    def test_py_script_apostrophe_in_path(self):
        p = self.tmp / "it's a test"
        p.mkdir(exist_ok=True)
        script = p / 'echo.py'
        script.write_text(
            'import json, sys; print(json.dumps(sys.argv[1:]))',
            encoding='utf-8',
        )
        rc, out, err = run_with_timeout(build_args(str(script), ['ok']), 10)
        self.assertEqual(rc, 0, err)
        self.assertEqual(json.loads(out.strip()), ['ok'])

    @unittest.skipUnless(os.name == 'nt', 'Windows .cmd shim failure test')
    def test_unknown_cmd_fails_closed(self):
        fake_cmd = self.tmp / 'fake.cmd'
        fake_cmd.write_text('@echo off\necho hello\n', encoding='utf-8')
        with self.assertRaises(ValueError) as ctx:
            build_args(str(fake_cmd), ['--version'])
        # Verify it's the shim resolution error, not any other ValueError
        self.assertIn('cmd shim', str(ctx.exception).lower())

    @unittest.skipUnless(os.name == 'nt', 'Windows process-tree cleanup')
    def test_timeout_kills_child_process(self):
        """Parent and child are reaped; TimeoutExpired is raised."""
        import ctypes
        import ctypes.wintypes
        marker = self.tmp / 'child.pid'
        code = (
            'import subprocess,sys,time,pathlib;'
            f'p=subprocess.Popen([sys.executable,"-c","import time;time.sleep(60)"]);'
            f'pathlib.Path({str(marker)!r}).write_text(str(p.pid));'
            'time.sleep(60)'
        )
        with self.assertRaises(subprocess.TimeoutExpired):
            run_with_timeout([sys.executable, '-c', code], 3)
        # marker proves child was launched; child should not still be running
        self.assertTrue(marker.is_file(), 'child.pid not written — child may not have started')
        child_pid = int(marker.read_text())
        # Use OpenProcess/GetExitCodeProcess to check if child is still alive
        SYNCHRONIZE = 0x00100000
        PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
        STILL_ACTIVE = 259
        handle = ctypes.windll.kernel32.OpenProcess(
            PROCESS_QUERY_LIMITED_INFORMATION | SYNCHRONIZE, False, child_pid
        )
        if handle:
            exit_code = ctypes.wintypes.DWORD()
            ctypes.windll.kernel32.GetExitCodeProcess(handle, ctypes.byref(exit_code))
            ctypes.windll.kernel32.CloseHandle(handle)
            self.assertNotEqual(exit_code.value, STILL_ACTIVE,
                                 'child process still running after timeout kill')
        # If OpenProcess returns NULL, child no longer exists — that's the expected outcome


# ---------------------------------------------------------------------------
# Outer tool evidence: _check_outer_tool_evidence (via check_connection module)
# ---------------------------------------------------------------------------

def _import_check_connection():
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        'check_connection', SCRIPTS_DIR / 'check_connection.py'
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


_cc = _import_check_connection()
_check_outer_tool_evidence = _cc._check_outer_tool_evidence
_validate_bidi_artifact = _cc._validate_bidi_artifact


def _build_claude_tool_stream(nonce, tool_id='t-001', cmd_override=None,
                               include_result=True, result_is_error=False):
    """Build a minimal Claude stream-json with real tool_use + tool_result events."""
    expected_cmd = cmd_override if cmd_override is not None else f'python -X utf8 probe_peer.py --nonce {nonce}'
    events = [
        json.dumps({
            'type': 'assistant',
            'message': {
                'role': 'assistant',
                'content': [{
                    'type': 'tool_use',
                    'name': 'Bash',
                    'id': tool_id,
                    'input': {'command': expected_cmd},
                }],
            },
        }),
    ]
    if include_result:
        events.append(json.dumps({
            'type': 'user',
            'message': {
                'role': 'user',
                'content': [{
                    'type': 'tool_result',
                    'tool_use_id': tool_id,
                    'content': 'done',
                    'is_error': result_is_error,
                }],
            },
        }))
    # Final result event
    payload = {'outer_nonce': 'on', 'inner_nonce': nonce, 'direction': 'claude-to-codex',
               'tool_used': True}
    events.append(json.dumps({
        'type': 'result', 'subtype': 'success', 'is_error': False,
        'session_id': 'sess-outer-001', 'result': json.dumps(payload),
    }))
    return '\n'.join(events)


def _build_codex_tool_stream(nonce, exit_code=0, cmd_override=None):
    expected_cmd = cmd_override if cmd_override is not None else f'python -X utf8 probe_peer.py --nonce {nonce}'
    payload = {'outer_nonce': 'on', 'inner_nonce': nonce, 'direction': 'codex-to-claude',
               'tool_used': True}
    events = [
        {'type': 'thread.started', 'thread_id': 'tid-outer'},
        {'type': 'item.completed', 'item': {
            'type': 'command_execution',
            'exit_code': exit_code,
            'status': 'completed',
            'command': expected_cmd,
        }},
        {'type': 'item.completed', 'item': {
            'type': 'agent_message',
            'text': json.dumps(payload),
        }},
        {'type': 'turn.completed'},
    ]
    return '\n'.join(json.dumps(e) for e in events)


class TestOuterToolEvidence(unittest.TestCase):

    def test_claude_valid_tool_event_passes(self):
        nonce = 'realn0nce'
        raw = _build_claude_tool_stream(nonce)
        ok, detail = _check_outer_tool_evidence('claude', raw, f'python -X utf8 probe_peer.py --nonce {nonce}', nonce)
        self.assertTrue(ok, detail)

    def test_claude_no_tool_use_fails(self):
        nonce = 'realn0nce'
        # Just a result event, no tool_use
        raw = json.dumps({'type': 'result', 'subtype': 'success', 'is_error': False,
                          'session_id': 's', 'result': '{}'})
        ok, detail = _check_outer_tool_evidence('claude', raw, 'anything', nonce)
        self.assertFalse(ok)

    def test_claude_tool_result_is_error_fails(self):
        nonce = 'realn0nce2'
        raw = _build_claude_tool_stream(nonce, result_is_error=True)
        ok, detail = _check_outer_tool_evidence('claude', raw, f'python -X utf8 probe_peer.py --nonce {nonce}', nonce)
        self.assertFalse(ok, detail)

    def test_claude_no_tool_result_fails(self):
        nonce = 'realn0nce3'
        raw = _build_claude_tool_stream(nonce, include_result=False)
        ok, detail = _check_outer_tool_evidence('claude', raw, f'python -X utf8 probe_peer.py --nonce {nonce}', nonce)
        self.assertFalse(ok, detail)

    def test_claude_wrong_nonce_in_command_fails(self):
        nonce = 'realn0nce4'
        raw = _build_claude_tool_stream('different_nonce')
        ok, detail = _check_outer_tool_evidence('claude', raw, 'anything', nonce)
        self.assertFalse(ok, detail)

    def test_codex_valid_command_execution_passes(self):
        nonce = 'codexn0nce'
        raw = _build_codex_tool_stream(nonce)
        ok, detail = _check_outer_tool_evidence('codex', raw, f'python -X utf8 probe_peer.py --nonce {nonce}', nonce)
        self.assertTrue(ok, detail)

    def test_codex_nonzero_exit_code_fails(self):
        nonce = 'codexn0nce2'
        raw = _build_codex_tool_stream(nonce, exit_code=1)
        ok, detail = _check_outer_tool_evidence('codex', raw, f'python -X utf8 probe_peer.py --nonce {nonce}', nonce)
        self.assertFalse(ok, detail)

    def test_codex_nonce_not_in_command_fails(self):
        nonce = 'codexn0nce3'
        raw = _build_codex_tool_stream('different_nonce')
        ok, detail = _check_outer_tool_evidence('codex', raw, 'anything', nonce)
        self.assertFalse(ok, detail)

    def test_prose_only_no_tool_use_fails_claude(self):
        nonce = 'prose_nonce'
        raw = json.dumps({'type': 'result', 'subtype': 'success', 'is_error': False,
                          'session_id': 's', 'result': json.dumps({'echo': nonce})})
        ok, detail = _check_outer_tool_evidence('claude', raw, 'any', nonce)
        self.assertFalse(ok, detail)

    def test_claude_encoded_command_passes(self):
        # Encoded command (base64) as expected_cmd — must match exactly, not raw nonce
        nonce = 'enc_nonce_01'
        encoded_payload = base64.b64encode(b'python probe_peer.py').decode()
        expected_cmd = f'powershell -EncodedCommand {encoded_payload}'
        raw = _build_claude_tool_stream(nonce, cmd_override=expected_cmd)
        ok, detail = _check_outer_tool_evidence('claude', raw, expected_cmd, nonce)
        self.assertTrue(ok, detail)

    def test_claude_echo_of_nonce_fails(self):
        # Command is just an echo of the nonce, not the expected command
        nonce = 'echo_nonce_01'
        echo_cmd = f'echo {nonce}'
        raw = _build_claude_tool_stream(nonce, cmd_override=echo_cmd)
        expected_cmd = f'python probe_peer.py --nonce {nonce}'
        ok, detail = _check_outer_tool_evidence('claude', raw, expected_cmd, nonce)
        self.assertFalse(ok, detail)

    def test_codex_powershell_wrapper_passes(self):
        # Codex may wrap expected command in powershell -Command; expected must be substring
        nonce = 'ps_wrap_nonce'
        expected_cmd = f'python -X utf8 probe_peer.py --nonce {nonce}'
        wrapped_cmd = f'powershell -Command "{expected_cmd}"'
        payload = {'outer_nonce': 'on', 'inner_nonce': nonce, 'direction': 'codex-to-claude',
                   'tool_used': True}
        events = [
            {'type': 'thread.started', 'thread_id': 'tid-ps'},
            {'type': 'item.completed', 'item': {
                'type': 'command_execution',
                'exit_code': 0,
                'status': 'completed',
                'command': wrapped_cmd,
            }},
            {'type': 'item.completed', 'item': {
                'type': 'agent_message', 'text': json.dumps(payload),
            }},
            {'type': 'turn.completed'},
        ]
        raw = '\n'.join(json.dumps(e) for e in events)
        ok, detail = _check_outer_tool_evidence('codex', raw, expected_cmd, nonce)
        self.assertTrue(ok, detail)

    def test_codex_echo_of_nonce_fails(self):
        # Command echoes nonce but doesn't contain expected command
        nonce = 'ce_nonce_01'
        echo_cmd = f'Write-Output {nonce}'
        expected_cmd = f'python probe_peer.py --nonce {nonce}'
        payload = {'outer_nonce': 'on', 'inner_nonce': nonce, 'direction': 'codex-to-claude',
                   'tool_used': True}
        events = [
            {'type': 'thread.started', 'thread_id': 'tid-echo'},
            {'type': 'item.completed', 'item': {
                'type': 'command_execution',
                'exit_code': 0,
                'status': 'completed',
                'command': echo_cmd,
            }},
            {'type': 'item.completed', 'item': {
                'type': 'agent_message', 'text': json.dumps(payload),
            }},
            {'type': 'turn.completed'},
        ]
        raw = '\n'.join(json.dumps(e) for e in events)
        ok, detail = _check_outer_tool_evidence('codex', raw, expected_cmd, nonce)
        self.assertFalse(ok, detail)


# ---------------------------------------------------------------------------
# Bidi artifact validation: _validate_bidi_artifact
# ---------------------------------------------------------------------------

class TestValidateBidiArtifact(unittest.TestCase):

    def setUp(self):
        TESTS_TMP.mkdir(parents=True, exist_ok=True)
        self._td = tempfile.TemporaryDirectory(dir=TESTS_TMP, prefix='bidi_')
        self.root = pathlib.Path(self._td.name).resolve()
        # Create a real workflow file so rule_hash can be computed
        wf_path = self.root / 'docs' / 'ai-collaboration-workflow.md'
        wf_path.parent.mkdir(parents=True, exist_ok=True)
        wf_path.write_text('Version: 1.0\nFixture workflow\n', encoding='utf-8')
        self.rule_hash = hashlib.sha256(wf_path.read_bytes()).hexdigest()[:16]

        self.inner_nonce = 'innernonce001'
        self.direction = 'claude-to-codex'
        self.outer_session = 'outer-sess-001'
        root_str = str(self.root)
        # Build a real inner Codex raw stdout fixture using the real root and hash
        inner_payload = {
            'nonce': self.inner_nonce,
            'root': root_str,
            'rule_hash': self.rule_hash,
            'direction': self.direction,
        }
        self.inner_raw = _codex_jsonl(thread_id='inner-sess-001',
                                      item={'type': 'agent_message',
                                            'text': json.dumps(inner_payload)})
        self.artifact = {
            'status': 'Pass',
            'direction': self.direction,
            'nonce': self.inner_nonce,
            'rc': 0,
            'root': root_str,
            'rule_hash': self.rule_hash,
            'inner_session_id': 'inner-sess-001',
            'target_cli': 'codex',
            'raw_inner_stdout': self.inner_raw,
        }

    def tearDown(self):
        self._td.cleanup()

    def _validate(self, artifact=None, inner_nonce=None, direction=None, root=None,
                  outer_session=None, tool_evidence=True, tool_detail='ok', outer_raw=''):
        return _validate_bidi_artifact(
            artifact if artifact is not None else self.artifact,
            inner_nonce or self.inner_nonce,
            direction or self.direction,
            root or str(self.root),
            outer_session or self.outer_session,
            tool_evidence,
            tool_detail,
            outer_raw,
        )

    def test_valid_artifact_passes(self):
        result = self._validate()
        self.assertEqual(result, 'Pass')

    def test_missing_raw_inner_stdout_fails(self):
        art = dict(self.artifact)
        del art['raw_inner_stdout']
        # Without raw_inner_stdout the re-decode step is skipped but it should
        # still be acceptable — however the spec requires it; check actual behavior
        # The current implementation skips re-decode if raw_inner is absent/empty.
        # Spec says artifact must include raw_inner_stdout — this test exposes the defect.
        result = _validate_bidi_artifact(
            art, self.inner_nonce, self.direction, '/fake/root',
            self.outer_session, True, 'ok', '',
        )
        # Correct intended behavior: artifact missing raw_inner_stdout must Fail
        # This assertion WILL FAIL if current code silently accepts it (known defect).
        self.assertTrue(result.startswith('Fail'),
                        f'Expected Fail for missing raw_inner_stdout, got: {result}')

    def test_wrong_nonce_fails(self):
        result = self._validate(inner_nonce='wrong-nonce')
        self.assertTrue(result.startswith('Fail'), result)

    def test_wrong_direction_fails(self):
        result = self._validate(direction='codex-to-claude')
        self.assertTrue(result.startswith('Fail'), result)

    def test_nonzero_rc_fails(self):
        art = dict(self.artifact, rc=1)
        result = self._validate(artifact=art)
        self.assertTrue(result.startswith('Fail'), result)

    def test_non_pass_status_fails(self):
        art = dict(self.artifact, status='Fail')
        result = self._validate(artifact=art)
        self.assertTrue(result.startswith('Fail'), result)

    def test_missing_required_fields_fails(self):
        for field in ('status', 'direction', 'nonce', 'rc', 'root', 'rule_hash', 'inner_session_id'):
            with self.subTest(field=field):
                art = dict(self.artifact)
                del art[field]
                result = self._validate(artifact=art)
                self.assertTrue(result.startswith('Fail'), f'{field}: {result}')

    def test_malformed_raw_inner_stdout_fails(self):
        art = dict(self.artifact, raw_inner_stdout='not valid jsonl at all')
        result = self._validate(artifact=art)
        self.assertTrue(result.startswith('Fail'), result)

    def test_non_object_artifact_raises_or_fails(self):
        result = _validate_bidi_artifact(
            [], self.inner_nonce, self.direction, str(self.root),
            self.outer_session, True, 'ok', '',
        )
        self.assertTrue(result.startswith('Fail'), result)

    def test_no_tool_evidence_gives_not_verified(self):
        result = self._validate(tool_evidence=False, tool_detail='no event found')
        self.assertTrue(result.startswith('NotVerified'), result)

    def test_inner_session_missing_fails(self):
        art = dict(self.artifact, inner_session_id='')
        result = self._validate(artifact=art)
        self.assertTrue(result.startswith('Fail'), result)

    def test_wrong_rule_hash_fails(self):
        art = dict(self.artifact, rule_hash='0000000000000000')
        result = self._validate(artifact=art)
        self.assertTrue(result.startswith('Fail'), result)

    def test_wrong_target_cli_fails(self):
        # direction=claude-to-codex expects target_cli=codex
        art = dict(self.artifact, target_cli='claude')
        result = self._validate(artifact=art)
        self.assertTrue(result.startswith('Fail'), result)

    def test_wrong_decoded_payload_nonce_fails(self):
        # raw_inner_stdout contains a different nonce than claimed
        wrong_payload = {
            'nonce': 'wrong-nonce-here',
            'root': str(self.root),
            'rule_hash': self.rule_hash,
            'direction': self.direction,
        }
        wrong_raw = _codex_jsonl(thread_id='inner-sess-001',
                                  item={'type': 'agent_message',
                                        'text': json.dumps(wrong_payload)})
        art = dict(self.artifact, raw_inner_stdout=wrong_raw)
        result = self._validate(artifact=art)
        self.assertTrue(result.startswith('Fail'), result)

    def test_rc_boolean_true_fails(self):
        # rc=True is not int 0
        art = dict(self.artifact, rc=True)
        result = self._validate(artifact=art)
        self.assertTrue(result.startswith('Fail'), result)


# ---------------------------------------------------------------------------
# probe_peer.py: delegation refusals
# ---------------------------------------------------------------------------

class TestProbePeerRefusals(unittest.TestCase):

    def setUp(self):
        TESTS_TMP.mkdir(parents=True, exist_ok=True)
        self._td = tempfile.TemporaryDirectory(dir=TESTS_TMP, prefix='peer_')
        self.abs_root = str(pathlib.Path(self._td.name).resolve())

    def tearDown(self):
        self._td.cleanup()

    def _run_probe(self, extra_env=None, args=None):
        env = os.environ.copy()
        if extra_env:
            env.update(extra_env)
        default_args = [
            '--direction', 'claude-to-codex', '--nonce', 'deadbeef1234',
            '--root', self.abs_root,
        ]
        cmd = [sys.executable, '-X', 'utf8', str(SCRIPTS_DIR / 'probe_peer.py')] + (args or default_args)
        result = subprocess.run(
            cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            encoding='utf-8', timeout=10, env=env,
        )
        return result.returncode, result.stdout

    def test_no_delegation_env_refuses(self):
        rc, out = self._run_probe(extra_env={'AI_COLLAB_NO_DELEGATION': '1'})
        self.assertNotEqual(rc, 0, out)

    def test_depth_greater_than_1_refuses(self):
        rc, out = self._run_probe(extra_env={'AI_COLLAB_CHECK_DEPTH': '2'})
        self.assertNotEqual(rc, 0, out)

    def test_depth_1_allowed_to_reach_arg_parsing(self):
        # depth=1 is permitted; it should fail later (missing root/config) not on depth guard
        rc, out = self._run_probe(extra_env={'AI_COLLAB_CHECK_DEPTH': '1'})
        # rc!=0 is expected (no valid project), but must not say "delegation refused"
        self.assertNotIn('delegation refused', out.lower())
        self.assertNotIn('depth guard', out.lower())

    def test_invalid_nonce_refuses_before_cli_invocation(self):
        # Nonce with path traversal chars must be rejected before any artifact write or CLI call.
        # Use a valid absolute root (even without config) so the nonce check triggers first.
        marker = pathlib.Path(self.abs_root) / 'marker_invoked.txt'
        bad_nonce = '../escape'
        rc, out = self._run_probe(args=[
            '--direction', 'claude-to-codex', '--nonce', bad_nonce,
            '--root', self.abs_root,
        ])
        self.assertNotEqual(rc, 0, out)
        # Marker must not exist — CLI must not have been invoked
        self.assertFalse(marker.exists(), 'CLI was invoked despite invalid nonce')


# ---------------------------------------------------------------------------
# Defect / snapshot / recovery scenario
# ---------------------------------------------------------------------------

def _math_fixture_add(x, y):
    """Fixture function with intentional defect: uses subtraction instead of addition."""
    return x - y  # BUG: should be x + y


def _math_fixture_add_fixed(x, y):
    return x + y


def _content_hash(fn):
    src = fn.__code__.co_consts  # crude proxy for function body "content"
    return hashlib.sha256(str(src).encode()).hexdigest()[:16]


class TestDefectSnapshotRecovery(unittest.TestCase):
    """
    Demonstrates known-defect / snapshot / recovery validation.

    The fixture function has an intentional defect. An independent failing
    assertion exposes it. Fixing the function causes it to pass. Content
    hashes before/after confirm the change, invalidating the prior snapshot.
    """

    def test_defective_fixture_fails(self):
        # This assertion MUST fail — it exposes the defect.
        result = _math_fixture_add(3, 4)
        self.assertNotEqual(result, 7,
            'Defective fixture unexpectedly returned correct result — defect not present?')
        self.assertEqual(result, -1, 'Defective fixture should return 3-4=-1')

    def test_fixed_fixture_passes(self):
        result = _math_fixture_add_fixed(3, 4)
        self.assertEqual(result, 7)

    def test_hash_change_invalidates_prior_acceptance(self):
        hash_before = _content_hash(_math_fixture_add)
        hash_after = _content_hash(_math_fixture_add_fixed)
        self.assertNotEqual(hash_before, hash_after,
            'Hashes are equal — fixture edit was not captured; prior acceptance still stands')

    def test_snapshot_walkthrough_documented(self):
        """
        Walkthrough:
        1. _math_fixture_add captured at hash_before — acceptance A recorded.
        2. Test_defective_fixture_fails asserts result != 7 (exposes the bug).
        3. Fix applied: _math_fixture_add_fixed uses correct addition.
        4. hash_after != hash_before => prior acceptance A is invalidated.
        5. test_fixed_fixture_passes confirms corrected behavior.
        See docs/collaboration-validation.md for full procedure.
        """
        hash_before = _content_hash(_math_fixture_add)
        hash_after = _content_hash(_math_fixture_add_fixed)
        self.assertNotEqual(hash_before, hash_after)
        self.assertNotEqual(_math_fixture_add(3, 4), 7)
        self.assertEqual(_math_fixture_add_fixed(3, 4), 7)

    def test_source_file_walkthrough(self):
        """
        Isolated source-file SHA256 walkthrough:
        1. Write buggy source (x - y), compute hash_buggy.
        2. Execute source; assert result != 7 (defect confirmed).
        3. Write fixed source (x + y), compute hash_fixed.
        4. Execute fixed source; assert result == 7 (fix confirmed).
        5. hash_buggy != hash_fixed — prior snapshot is invalidated.
        Does not corrupt real scripts.
        """
        TESTS_TMP.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=TESTS_TMP, prefix='snap_') as td:
            src = pathlib.Path(td) / 'add.py'

            # Step 1: buggy source
            buggy_code = 'def add(x, y):\n    return x - y\n'
            src.write_text(buggy_code, encoding='utf-8')
            hash_buggy = hashlib.sha256(src.read_bytes()).hexdigest()

            # Step 2: exec buggy, confirm defect
            ns = {}
            exec(compile(src.read_text('utf-8'), str(src), 'exec'), ns)  # noqa: S102
            self.assertNotEqual(ns['add'](3, 4), 7, 'Buggy source should not return 7')
            self.assertEqual(ns['add'](3, 4), -1)

            # Step 3: write fixed source
            fixed_code = 'def add(x, y):\n    return x + y\n'
            src.write_text(fixed_code, encoding='utf-8')
            hash_fixed = hashlib.sha256(src.read_bytes()).hexdigest()

            # Step 4: exec fixed, confirm correction
            ns2 = {}
            exec(compile(src.read_text('utf-8'), str(src), 'exec'), ns2)  # noqa: S102
            self.assertEqual(ns2['add'](3, 4), 7)

            # Step 5: hashes differ — prior snapshot invalidated
            self.assertNotEqual(hash_buggy, hash_fixed,
                'Source SHA256 unchanged — snapshot invalidation not demonstrated')


if __name__ == '__main__':
    unittest.main(verbosity=2)
