"""
Codex App Server adapter (stdio JSONRPC).

Protocol (official app-server v2, schema-verified):
  -> {"id":1,"method":"initialize","params":{"clientInfo":{"name":"controller","version":"1"}}}
  <- {"id":1,"result":{...}}
  -> {"method":"initialized"}    (no id, one-way notification)
  -> {"id":2,"method":"thread/start","params":{"approvalPolicy":"never",...}}
  <- {"id":2,"result":{"thread":{"id":"...",...},...}}
  -> {"id":3,"method":"turn/start","params":{"threadId":"...","input":[{"type":"text","text":"..."}]}}
  <- notifications arrive (including turn/completed) and then response {"id":3,"result":{"turn":{...}}}
  <- turn/completed notification: {"method":"turn/completed","params":{"threadId":"...","turn":{...}}}

Completion detection: turn/completed notification OR turn/start response with turn.status != inProgress.
Terminal notifications are matched by threadId and turnId before being accepted.
Terminal statuses: completed, interrupted, failed.
Unsupported approval requests respond decline; never hang or auto-approve.
Unknown server requests (id+method) receive explicit JSONRPC error.
cancel: send turn/interrupt (threadId + turnId required), then bounded process cleanup.
"""
import json
import os
import queue
import subprocess
import threading
import time

# Official server request methods that are declined (not approved)
_DECLINE_APPROVAL_METHODS = frozenset({
    'item/commandExecution/requestApproval',
    'item/fileChange/requestApproval',
    'item/permissions/requestApproval',
    'applyPatchApproval',
    'execCommandApproval',
    'approval/request',
})


class AppServerError(Exception):
    pass


class AppServerAdapter:
    """
    Manages one Codex App Server child process via stdin/out pipes.

    Usage:
        with AppServerAdapter(args, cwd, timeout_secs) as adapter:
            thread_id = adapter.start_thread(approval_policy='never', ephemeral=True)
            result_text, status = adapter.run_turn(thread_id, input_text)
    """

    def __init__(self, args, cwd, timeout_secs=120):
        self._args = args
        self._cwd = cwd
        self._timeout = timeout_secs
        self._proc = None
        self._owner = None
        self._reader_thread = None
        self._stderr_thread = None
        self._incoming = queue.Queue()
        self._next_id = 1
        self._thread_id = None
        # Cleanup outcome: set by close(); readable by caller after with-block exits.
        self.cleanup_confirmed = False
        self.cleanup_error = None
        # Child process identity: set when spawn succeeds or partially preserved on uncertain failure.
        self.child_pid = None
        self.child_identity = None
        # True when spawn raised ContainmentError with cleanup_confirmed=False.
        self._spawn_uncertain = False

    def __enter__(self):
        try:
            self._start()
        except Exception:
            self.close()
            raise
        return self

    def __exit__(self, *_):
        self.close()

    def _start(self):
        from _controller_process import ProcessOwner, ContainmentError
        from _controller_state import get_process_creation_identity
        kwargs = dict(
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            cwd=self._cwd,
        )
        self._owner = ProcessOwner()
        try:
            self._proc = self._owner.spawn(self._args, **kwargs)
        except ContainmentError as e:
            # Preserve child identity fields and uncertain spawn flag BEFORE wrapping.
            self.child_pid = e.child_pid
            self.child_identity = e.child_identity
            if not e.cleanup_confirmed:
                self._spawn_uncertain = True
                self.cleanup_confirmed = False
            raise AppServerError(f'process containment failed: {e}')
        # Successful spawn: record child PID and creation identity immediately.
        self.child_pid = self._proc.pid
        try:
            self.child_identity = get_process_creation_identity(self._proc.pid)
        except Exception:
            pass
        self._reader_thread = threading.Thread(
            target=self._read_loop, daemon=True
        )
        self._reader_thread.start()
        self._stderr_thread = threading.Thread(
            target=self._drain_stderr, daemon=True
        )
        self._stderr_thread.start()
        self._initialize()

    def _read_loop(self):
        """Background thread: read lines from child stdout, parse, enqueue."""
        try:
            for raw in self._proc.stdout:
                raw = raw.strip()
                if not raw:
                    continue
                try:
                    obj = json.loads(raw)
                except json.JSONDecodeError:
                    self._incoming.put({'_parse_error': raw.decode('utf-8', 'replace')})
                    continue
                self._incoming.put(obj)
        except OSError:
            pass
        finally:
            self._incoming.put({'_eof': True})

    def _drain_stderr(self):
        """Background thread: drain stderr to avoid pipe deadlock."""
        try:
            for _ in self._proc.stderr:
                pass
        except OSError:
            pass

    def _send(self, msg):
        line = json.dumps(msg) + '\n'
        self._proc.stdin.write(line.encode('utf-8'))
        self._proc.stdin.flush()

    def _next_rpc_id(self):
        i = self._next_id
        self._next_id += 1
        return i

    def _recv_response(self, expected_id, deadline):
        """Wait for a response matching expected_id, returning the result dict."""
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise AppServerError('timeout waiting for response')
            try:
                msg = self._incoming.get(timeout=min(remaining, 5.0))
            except queue.Empty:
                # 5s chunk expired but overall deadline may not have passed; recheck.
                continue
            if msg.get('_eof'):
                raise AppServerError('app server EOF before response')
            if msg.get('_parse_error'):
                raise AppServerError(f'app server sent malformed JSON: {msg["_parse_error"][:80]}')
            # Server-to-client request: has both 'method' and 'id'
            if 'method' in msg and 'id' in msg:
                self._handle_server_request(msg)
                continue
            # Notifications have no 'id'
            if 'id' not in msg:
                continue
            if msg.get('id') != expected_id:
                continue
            if 'error' in msg:
                raise AppServerError(f'app server error: {msg["error"]}')
            return msg.get('result', {})

    def _handle_server_request(self, msg):
        """Respond to a server-to-client request: decline approvals, error on others."""
        req_id = msg.get('id')
        method = msg.get('method', '')
        if method in _DECLINE_APPROVAL_METHODS:
            if req_id is not None:
                self._send({'id': req_id, 'result': {'decision': 'decline'}})
        else:
            if req_id is not None:
                self._send({
                    'id': req_id,
                    'error': {'code': -32601, 'message': f'Method not supported: {method}'},
                })

    def _decline_approval(self, msg):
        """Decline an unexpected approval request (legacy method name kept for callers)."""
        self._handle_server_request(msg)

    def _initialize(self):
        deadline = time.monotonic() + self._timeout
        rpc_id = self._next_rpc_id()
        self._send({
            'id': rpc_id,
            'method': 'initialize',
            'params': {'clientInfo': {'name': 'controller', 'version': '1'}},
        })
        self._recv_response(rpc_id, deadline)
        # Schema: send 'initialized' (not 'notifications/initialized')
        self._send({'method': 'initialized'})

    def start_thread(self, cwd=None, approval_policy='never',
                     sandbox='read-only', ephemeral=True):
        """Send thread/start; return thread id from result.thread.id."""
        deadline = time.monotonic() + self._timeout
        rpc_id = self._next_rpc_id()
        params = {
            'cwd': cwd or self._cwd,
            'approvalPolicy': approval_policy,
            'sandbox': sandbox,
            'ephemeral': ephemeral,
        }
        self._send({'id': rpc_id, 'method': 'thread/start', 'params': params})
        result = self._recv_response(rpc_id, deadline)
        # ThreadStartResponse: result.thread.id
        thread_obj = result.get('thread') or {}
        self._thread_id = thread_obj.get('id') or result.get('threadId') or result.get('thread_id')
        if not self._thread_id:
            raise AppServerError(f'thread/start returned no thread id: {result}')
        return self._thread_id

    def run_turn(self, thread_id, input_text, cancel_fn=None):
        """
        Send turn/start with UserInput array, collect agent text, return (text, status).

        cancel_fn: callable returning True when cancellation requested.
        status: 'completed' | 'interrupted' | 'failed'

        Result text: only agentMessage items with phase=final_answer (or null/missing phase
        for legacy compatibility).  Items are deduplicated by ID so item/completed and
        turn/completed carrying the same item ID do not double-count.
        Notification threadId and turnId are validated; unknown IDs silently ignored.
        """
        deadline = time.monotonic() + self._timeout
        rpc_id = self._next_rpc_id()
        # TurnStartParams: input is array of UserInput objects
        user_input = [{'type': 'text', 'text': input_text, 'text_elements': []}]
        self._send({
            'id': rpc_id,
            'method': 'turn/start',
            'params': {'threadId': thread_id, 'input': user_input},
        })

        # Ordered item-ID map: item_id -> text (None means seen but no text yet)
        agent_item_map = {}
        turn_id = None
        terminal_status = None
        response_received = False  # True once we have the turn/start response

        # Buffer for terminal notifications that arrived before we know turn_id.
        # Each entry: (notif_turn_id, status, turn_dict) — validated after response.
        _pending_terminal_notifs = []

        while True:
            if cancel_fn and cancel_fn():
                self._interrupt_turn(thread_id, turn_id)
                return '', 'interrupted'
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                self._interrupt_turn(thread_id, turn_id)
                return '', 'interrupted'
            try:
                msg = self._incoming.get(timeout=min(remaining, 1.0))
            except queue.Empty:
                continue
            if msg.get('_eof'):
                if terminal_status:
                    return self._join_items(agent_item_map), terminal_status
                return self._join_items(agent_item_map), 'failed'
            if msg.get('_parse_error'):
                return '', 'failed'

            msg_method = msg.get('method')
            msg_id = msg.get('id')

            # Server-to-client request: has both method and id
            if msg_method and msg_id is not None:
                self._handle_server_request(msg)
                continue

            if 'id' not in msg:
                # Notification: has method, no id
                params = msg.get('params') or {}
                notif_thread_id = params.get('threadId')

                if msg_method in ('turn/completed', 'thread/turn/completed'):
                    # Validate threadId — unrelated completions must be ignored.
                    # Unknown/missing threadId does NOT silently match.
                    if not notif_thread_id or notif_thread_id != thread_id:
                        continue
                    turn = params.get('turn') or {}
                    notif_turn_id = turn.get('id')
                    status = turn.get('status', 'completed')
                    terminal_status_candidate = status if status != 'inProgress' else 'completed'

                    if turn_id:
                        # We already know the turn_id from the response; validate.
                        if notif_turn_id and notif_turn_id != turn_id:
                            # Wrong turn: ignore (could be a stale completion for an old turn).
                            continue
                        self._collect_turn_items(turn, agent_item_map)
                        terminal_status = terminal_status_candidate
                        if response_received:
                            return self._join_items(agent_item_map), terminal_status
                    else:
                        # Response not yet received; buffer the notification.
                        # Do not set turn_id or terminal_status yet — validate after response.
                        _pending_terminal_notifs.append(
                            (notif_turn_id, terminal_status_candidate, turn)
                        )
                    continue

                if msg_method in ('turn/failed', 'thread/turn/failed'):
                    if not notif_thread_id or notif_thread_id != thread_id:
                        continue
                    if turn_id:
                        terminal_status = 'failed'
                        if response_received:
                            return self._join_items(agent_item_map), terminal_status
                    else:
                        _pending_terminal_notifs.append((None, 'failed', {}))
                    continue

                if msg_method in ('turn/interrupted', 'thread/turn/interrupted'):
                    if not notif_thread_id or notif_thread_id != thread_id:
                        continue
                    if turn_id:
                        terminal_status = 'interrupted'
                        if response_received:
                            return self._join_items(agent_item_map), terminal_status
                    else:
                        _pending_terminal_notifs.append((None, 'interrupted', {}))
                    continue

                # Partial agent message delta (schema: agentMessage; legacy: agent_message)
                item = params.get('item') or msg.get('item') or {}
                if item.get('type') in ('agentMessage', 'agent_message'):
                    self._merge_agent_item(item, agent_item_map)
                continue

            if msg_id != rpc_id:
                continue
            if 'error' in msg:
                return '', 'failed'
            result = msg.get('result', {})
            # TurnStartResponse: result.turn
            turn = result.get('turn') or result
            resp_turn_id = turn.get('id') if isinstance(turn, dict) else None
            if not turn_id and resp_turn_id:
                turn_id = resp_turn_id
            status = turn.get('status', 'inProgress') if isinstance(turn, dict) else 'inProgress'
            self._collect_turn_items(turn if isinstance(turn, dict) else {}, agent_item_map)

            # Now that we have turn_id, replay buffered notifications and validate each.
            for (notif_turn_id, cand_status, notif_turn) in _pending_terminal_notifs:
                # A notification with None turn_id (e.g. turn/failed) always matches.
                if notif_turn_id is not None and turn_id and notif_turn_id != turn_id:
                    # Wrong turn ID: stale notification from a previous turn — ignore.
                    continue
                self._collect_turn_items(notif_turn, agent_item_map)
                if terminal_status is None:
                    terminal_status = cand_status
            _pending_terminal_notifs.clear()

            if status != 'inProgress':
                return self._join_items(agent_item_map), status
            # Response arrived with inProgress; mark received and wait for completion notification.
            response_received = True
            if terminal_status:
                return self._join_items(agent_item_map), terminal_status
            continue

    def _is_final_answer_item(self, item):
        """Return True if this agentMessage item should be included in the result.

        phase=final_answer: include.
        phase=commentary: exclude (progress/narration text, not final answer).
        phase=null/missing: include for legacy compatibility (providers may not emit phase).
        """
        phase = item.get('phase')
        if phase is None:
            return True
        return phase == 'final_answer'

    def _merge_agent_item(self, item, item_map):
        """Insert or update an agentMessage item in the ordered map, respecting phase filter."""
        if not isinstance(item, dict):
            return
        item_id = item.get('id')
        if not self._is_final_answer_item(item):
            # Mark as seen-but-excluded so a later completed version doesn't re-add.
            if item_id and item_id not in item_map:
                item_map[item_id] = None
            return
        t = item.get('text') or item.get('content') or ''
        if item_id:
            # Idempotent: replace (not append) so item/completed + turn/completed = once.
            item_map[item_id] = t if t else item_map.get(item_id)
        elif t:
            # No ID: use a synthetic key to avoid collision but still include text.
            item_map[f'_noid_{len(item_map)}'] = t

    def _collect_turn_items(self, turn, item_map):
        """Extract agentMessage items from a Turn object into the ordered map."""
        if not isinstance(turn, dict):
            return
        for item in turn.get('items', []):
            if isinstance(item, dict) and item.get('type') == 'agentMessage':
                self._merge_agent_item(item, item_map)
        # Backward compat: some fake/legacy servers return flat agentMessages list.
        # These carry no phase field, so they pass the legacy-compat filter.
        for m in turn.get('agentMessages', []):
            if isinstance(m, dict):
                t = m.get('text') or m.get('content') or ''
                if t:
                    item_id = m.get('id')
                    if item_id:
                        if item_id not in item_map:
                            item_map[item_id] = t
                    else:
                        item_map[f'_legacy_{len(item_map)}'] = t

    def _join_items(self, item_map):
        """Join non-None values from item_map into a single result string."""
        parts = [v for v in item_map.values() if v is not None]
        return '\n'.join(parts)

    def _interrupt_turn(self, thread_id, turn_id=None):
        """Send turn/interrupt; requires both threadId and turnId per schema."""
        if not turn_id:
            return
        try:
            rpc_id = self._next_rpc_id()
            self._send({
                'id': rpc_id,
                'method': 'turn/interrupt',
                'params': {'threadId': thread_id, 'turnId': turn_id},
            })
        except OSError:
            pass

    def close(self):
        """Bounded shutdown: close stdin gracefully, then terminate owned group."""
        if self._proc is None:
            # Only confirm cleanup if spawn never started an uncertain child.
            # An uncertain ContainmentError leaves cleanup_confirmed=False.
            if not self._spawn_uncertain:
                self.cleanup_confirmed = True
            return
        # Close stdin first to give the server a short normal-exit window.
        try:
            self._proc.stdin.close()
        except OSError:
            pass
        # Short wait for normal server exit after stdin closes (~1s).
        _normal_exit_secs = 1.0
        _normal_deadline = time.monotonic() + _normal_exit_secs
        while time.monotonic() < _normal_deadline:
            self._proc.poll()
            if self._proc.returncode is not None:
                break
            time.sleep(0.05)
        # Terminate owned group before closing output pipes so reader threads get EOF.
        terminated = False
        uncertain = False
        try:
            if self._owner is not None:
                terminated, uncertain = self._owner.terminate(grace=5)
            else:
                from _controller_process import kill_tree
                terminated, uncertain = kill_tree(self._proc, grace=5)
        except Exception as exc:
            self.cleanup_error = str(exc)
            uncertain = True
        # If group not confirmed stopped, don't block indefinitely on pipe.
        # Mark unknown and propagate uncertainty.
        if uncertain and not terminated:
            self.cleanup_confirmed = False
            self.cleanup_error = self.cleanup_error or 'process group termination uncertain'
        else:
            self.cleanup_confirmed = not uncertain
            if uncertain:
                self.cleanup_error = self.cleanup_error or 'process group termination uncertain'
        # Close remaining pipes; reader threads drain to EOF.
        try:
            self._proc.stdout.close()
        except OSError:
            pass
        try:
            self._proc.stderr.close()
        except OSError:
            pass
