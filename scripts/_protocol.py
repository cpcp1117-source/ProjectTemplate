"""
Strict shared decoder for Claude and Codex CLI responses.

decode(cli, rc, stdout) -> (payload_dict, session_id) or raises ValueError.
validate_payload(payload, expected) -> None or raises ValueError.
"""
import json
import pathlib


def decode(cli, rc, stdout):
    """
    Decode and strictly validate a CLI response.

    Returns (payload_dict, session_id) on success.
    Raises ValueError with a descriptive message on any failure.

    Claude JSON envelope requirements:
      - rc == 0
      - type == "result", subtype == "success"
      - is_error is exactly False (not merely falsy)
      - session_id is a non-empty string
      - result field parses as a JSON object
    Supports stream-json: all non-empty lines must be valid JSON objects;
    the last event with type=result is used.

    Codex JSONL requirements:
      - rc == 0
      - thread.started event carrying thread_id
      - turn.completed event present
      - final item.completed with item.type=agent_message whose item.text
        parses as a JSON object
      - error / turn.failed / malformed / non-object events raise immediately
      - No substring or fake fallback
    """
    if not stdout or not stdout.strip():
        raise ValueError('empty response')

    if cli == 'claude':
        return _decode_claude(rc, stdout)
    if cli == 'codex':
        return _decode_codex(rc, stdout)
    raise ValueError(f'unknown CLI: {cli!r}')


def validate_payload(payload, expected):
    """
    Validate that payload contains exactly the expected field values.

    'root' fields are compared as normalized absolute paths.
    Raises ValueError listing all mismatches found.
    """
    mismatches = []
    for key, exp_val in expected.items():
        got = payload.get(key)
        if key == 'root':
            # root must be a nonempty absolute string; reject '.' or relative paths
            if not isinstance(got, str) or not got:
                mismatches.append(f'root_mismatch(got={got!r} expected={exp_val!r})')
                continue
            if not pathlib.Path(got).is_absolute():
                mismatches.append(
                    f'root_mismatch(got={got!r} is not absolute; expected={exp_val!r})'
                )
                continue
            try:
                got_norm = pathlib.Path(got).resolve()
                exp_norm = pathlib.Path(str(exp_val)).resolve()
            except (TypeError, ValueError, OSError) as e:
                mismatches.append(f'root_mismatch(cannot normalize: {e})')
                continue
            if got_norm != exp_norm:
                mismatches.append(f'root_mismatch(got={got!r} expected={exp_val!r})')
        else:
            if got != exp_val:
                mismatches.append(f'{key}_mismatch(got={got!r} expected={exp_val!r})')
    if mismatches:
        raise ValueError('; '.join(mismatches))


# ---------------------------------------------------------------------------
# Claude decoder
# ---------------------------------------------------------------------------

def _decode_claude(rc, stdout):
    if rc != 0:
        raise ValueError(f'rc={rc}')

    lines = [l for l in stdout.splitlines() if l.strip()]

    if len(lines) > 1:
        # Stream-json mode: every non-empty line must be a valid JSON object.
        events = []
        for line in lines:
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                raise ValueError(f'stream-json: non-JSON line: {line[:120]!r}')
            if not isinstance(obj, dict):
                raise ValueError(f'stream-json: non-object line: {line[:120]!r}')
            events.append(obj)
        data = None
        for obj in reversed(events):
            if obj.get('type') == 'result':
                data = obj
                break
        if data is None:
            raise ValueError('stream-json: no result event found')
    else:
        try:
            data = json.loads(stdout.strip())
        except json.JSONDecodeError:
            raise ValueError('malformed JSON — not a valid Claude envelope')
        if not isinstance(data, dict):
            raise ValueError('unexpected JSON type — expected object')

    if data.get('type') != 'result':
        raise ValueError(f'type={data.get("type")!r} expected "result"')
    if data.get('subtype') != 'success':
        raise ValueError(f'subtype={data.get("subtype")!r} expected "success"')
    # is_error must be exactly False, not merely falsy
    if data.get('is_error') is not False:
        raise ValueError(f'is_error={data.get("is_error")!r} must be exactly false')

    session_id = data.get('session_id', '')
    if not isinstance(session_id, str) or not session_id:
        raise ValueError('session_id missing or empty')

    result_field = data.get('result', '')
    if not result_field:
        raise ValueError('result field missing or empty')

    try:
        inner = json.loads(result_field) if isinstance(result_field, str) else result_field
    except json.JSONDecodeError:
        raise ValueError('result field is not valid JSON')

    if not isinstance(inner, dict):
        raise ValueError('result JSON is not an object')

    return inner, session_id


# ---------------------------------------------------------------------------
# Codex decoder
# ---------------------------------------------------------------------------

def _decode_codex(rc, stdout):
    if rc != 0:
        raise ValueError(f'rc={rc}')

    lines = [l.strip() for l in stdout.splitlines() if l.strip()]
    if not lines:
        raise ValueError('empty JSONL response')

    thread_id = None
    has_turn_completed = False
    last_agent_message = None

    for line in lines:
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            raise ValueError(f'malformed JSONL line: {line[:120]!r}')

        if not isinstance(obj, dict):
            raise ValueError(f'non-object JSONL event: {line[:120]!r}')

        t = obj.get('type', '')

        if t in ('error', 'turn.failed'):
            raise ValueError(
                f'error event: type={t!r} msg={obj.get("message", obj.get("error", ""))!r}'
            )

        if t == 'thread.started':
            tid = obj.get('thread_id', '')
            if isinstance(tid, str) and tid and thread_id is None:
                thread_id = tid

        if t == 'turn.completed':
            has_turn_completed = True

        if t == 'item.completed':
            item = obj.get('item', {})
            if not isinstance(item, dict):
                raise ValueError(f'item.completed item is not an object: {line[:120]!r}')
            if item.get('type') == 'agent_message':
                last_agent_message = item

    if thread_id is None:
        raise ValueError('thread.started with thread_id not found in JSONL')
    if not has_turn_completed:
        raise ValueError('turn.completed event not found in JSONL')
    if last_agent_message is None:
        raise ValueError('no item.completed with type=agent_message found')

    text = last_agent_message.get('text', '')
    if not isinstance(text, str) or not text.strip():
        raise ValueError('agent_message item.text missing or empty')

    try:
        inner = json.loads(text)
    except json.JSONDecodeError:
        raise ValueError(f'agent_message item.text is not valid JSON: {text[:120]!r}')

    if not isinstance(inner, dict):
        raise ValueError('agent_message item.text JSON is not an object')

    return inner, thread_id
