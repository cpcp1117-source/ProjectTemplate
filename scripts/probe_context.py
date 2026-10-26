#!/usr/bin/env python3
"""
Tiny context helper for connectivity probes.

Outputs JSON: {"nonce": <arg>, "root": <cwd resolved>, "rule_hash": <first16 SHA256>, "direction": <arg or null>}

Usage:
  python scripts/probe_context.py --nonce HEX [--direction DIR]

Validates nonce is hex and filename-safe. Reads workflow doc from cwd.
Does NOT accept expected values in arguments — computed locally only.
"""

import argparse
import hashlib
import json
import pathlib
import re
import sys

WORKFLOW_DOC = 'docs/ai-collaboration-workflow.md'
_HEX_RE = re.compile(r'^[0-9a-fA-F]+$')


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument('--nonce', required=True)
    parser.add_argument('--direction', default=None)
    args = parser.parse_args(argv)

    nonce = args.nonce
    if not nonce or not _HEX_RE.match(nonce):
        print(json.dumps({'error': f'nonce must be hex, got {nonce!r}'}))
        sys.exit(1)
    # Filename-safe: only hex chars, so guaranteed safe — extra guard
    if any(c in nonce for c in '/\\:*?"<>|'):
        print(json.dumps({'error': f'nonce contains unsafe chars: {nonce!r}'}))
        sys.exit(1)

    root = pathlib.Path.cwd().resolve()
    workflow = root / WORKFLOW_DOC
    if not workflow.is_file():
        print(json.dumps({'error': f'workflow doc not found: {workflow}'}))
        sys.exit(1)

    rule_hash = hashlib.sha256(workflow.read_bytes()).hexdigest()[:16]

    out = {
        'nonce': nonce,
        'root': str(root),
        'rule_hash': rule_hash,
    }
    if args.direction is not None:
        out['direction'] = args.direction

    print(json.dumps(out))


if __name__ == '__main__':
    sys.exit(main())
