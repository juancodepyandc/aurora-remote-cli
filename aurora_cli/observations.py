"""Bounded descriptions of facts received from the mission event stream."""
from __future__ import annotations

import json
import re


def terminal_text(value):
    """Remove terminal controls from untrusted model and tool output."""
    text = re.sub(r'\x1b(?:\[[0-?]*[ -/]*[@-~]|\][^\x07]*(?:\x07|\x1b\\))', '', str(value))
    return ''.join(c for c in text if c in '\n\t' or (ord(c)>=32 and not 127<=ord(c)<=159))


def _text(value):
    return value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, default=str)


def observation_text(event, limit=600):
    """Show actual errors/outputs, rather than only the name of an action."""
    kind = event.get('type')
    result = event.get('result')
    detail = ''
    if kind in {'tool_result', 'recovery_observation', 'completion_observation'}:
        if isinstance(result, dict):
            if result.get('error'):
                detail = _text(result['error'])
            elif isinstance(result.get('checks'), list):
                failed = [c for c in result['checks'] if isinstance(c, dict) and c.get('passed') is False]
                details = []
                for check in failed[:2]:
                    fields = {key: check[key] for key in ('criterion', 'path', 'error', 'expected', 'observed', 'missing_keys', 'extra_keys') if key in check}
                    details.append(_text(fields or check))
                detail = ' ; '.join(details) if failed else f"{len(result['checks'])} contrôles exécutés · résultat : {result.get('passed', 'non précisé')}"
            elif result.get('output'):
                detail = _text(result['output']).strip()
            elif result.get('path'):
                detail = _text(result['path'])
                if 'bytes' in result:
                    detail += f" · {result['bytes']} octets"
            else:
                detail = _text(result)
        elif result is not None:
            detail = _text(result)
    elif kind == 'command_output':
        detail = event.get('content', '')
    elif kind == 'stagnation_notice':
        detail = f"Résultat répété pour {event.get('tool', 'outil')} · {event.get('repeated_observations', '?')} répétitions sans nouvelle preuve"
    elif kind == 'recovery_start':
        detail = f"Diagnostic · tentative {event.get('attempt', '?')} · hypothèse à vérifier"
    elif kind == 'recovery_proposal':
        detail = f"Hypothèse : {event.get('hypothesis', '')} · observation attendue : {event.get('expected_observation', '')}"
    elif kind == 'recovery_rejected':
        detail = 'Diagnostic refusé : '+str(event.get('error', 'cause non précisée'))
    elif kind == 'review_result' and isinstance(event.get('review'), dict):
        review = event['review']
        if not review.get('approved') or review.get('unmet'):
            detail = 'Revue refusée : '+_text(review.get('unmet') or review.get('reason', 'preuve insuffisante'))
    detail = terminal_text(detail).strip()
    if len(detail) <= limit:
        return detail
    marker = '\n[… détails dans le journal …]\n'
    if limit <= len(marker):
        return detail[:max(0, limit)]
    remaining = max(0, limit-len(marker))
    head = (remaining+1)//2
    tail = remaining//2
    return detail[:head]+marker+(detail[-tail:] if tail else '')
