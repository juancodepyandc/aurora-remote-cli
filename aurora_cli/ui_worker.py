"""Isolated local UI execution, so cancellation can stop the entire process tree."""
from __future__ import annotations
import argparse
import json
from pathlib import Path
import sys
from uuid import uuid4


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--receipt', required=True)
    args = parser.parse_args()
    from . import display
    from .workspace import execute
    request = json.load(sys.stdin)
    history = request.get('history', [])
    try:
        ok = bool(execute(request['request'],history,local_only=True))
        result = next((m['content'] for m in reversed(history) if m.get('role')=='assistant'), '') if ok else ''
        payload = {'ok':ok,'result':result,'history':history,
                   'error':'' if ok else 'Le pipeline local n’a pas confirmé le résultat demandé. Consulte le journal.'}
    except Exception as exc:
        payload = {'ok':False,'error':str(exc),'history':history}
    target = Path(args.receipt)
    temporary = target.with_name(target.name+'.'+uuid4().hex+'.tmp')
    temporary.touch(mode=0o600,exist_ok=False)
    temporary.write_text(json.dumps(payload,ensure_ascii=False),encoding='utf-8')
    temporary.replace(target)
    return 0 if payload['ok'] else 1


if __name__=='__main__':
    raise SystemExit(main())
