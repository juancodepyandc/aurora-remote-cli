"""Compare delivered JSON with actual, retained tool receipts without executing code."""
from __future__ import annotations

import hashlib
from itertools import islice
import json
import math
import re

# Keep the empty-root alternative outside the regex: local Ollama 0.31.1
# silently ignored grouped optional/empty patterns in actual decoding probes.
# This grammar constrains the prefix; _parts still validates escapes and size.
POINTER_PATTERN = r'^/.*$'


def _pointer_schema(description):
    return {'description': description, 'anyOf': [
        {'const': ''}, {'type': 'string', 'pattern': POINTER_PATTERN}]}

BINDING_SCHEMA = {
    'type': 'object', 'additionalProperties': False,
    'properties': {
        'path': _pointer_schema('RFC 6901 pointer in the saved output: /field or /items/0; empty means root. Never dot or bracket syntax.'),
        'evidence_id': {'type': 'string', 'description': 'Actual successful receipt ID retained by this mission.'},
        'source_pointer': _pointer_schema('RFC 6901 pointer from the complete receipt, including /result for native receipts or /output for local CLI receipts. Follow actual nested objects; never dot syntax.'),
        'relation': {'type': 'string', 'enum': ['equals', 'text_in']},
        'decode_json': {'type': 'boolean'},
        'value_pointer': _pointer_schema('RFC 6901 pointer within explicitly decoded source JSON text, e.g. /field or /items/0.'),
    },
    'required': ['path', 'evidence_id', 'source_pointer'],
}


def _parts(path):
    if not isinstance(path, str) or len(path) > 4096 or (path and not path.startswith('/')):
        raise ValueError('Use an RFC 6901 JSON pointer: "" for root, "/field" or "/items/0"; never dot or bracket syntax (maximum 4096 characters)')
    if path == '':
        return []
    keys = []
    for component in path[1:].split('/'):
        if re.search(r'~(?![01])', component):
            raise ValueError('Invalid JSON pointer escape')
        keys.append(component.replace('~1', '/').replace('~0', '~'))
    return keys


def pointer(value, path):
    """Resolve a bounded RFC 6901 pointer; strings are never implicitly parsed."""
    for key in _parts(path):
        if isinstance(value, dict):
            if key not in value:
                keys = json.dumps(list(value)[:12], ensure_ascii=True)[:512]
                raise ValueError('JSON pointer field is absent; keys at this level: '+keys)
            value = value[key]
        elif isinstance(value, list):
            if not re.fullmatch(r'0|[1-9][0-9]*', key) or int(key) >= len(value):
                raise ValueError('JSON pointer index is absent')
            value = value[int(key)]
        else:
            raise ValueError('JSON pointer cannot traverse this value')
    return value


def strict_json_text(text):
    if not isinstance(text, str) or len(text) > 65536:
        raise ValueError('Explicit source JSON decoding requires a bounded string')
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError('Duplicate source JSON key')
            result[key] = value
        return result
    def number(text):
        value = float(text)
        if not math.isfinite(value):
            raise ValueError('Nonfinite source JSON number')
        return value
    def invalid_constant(_):
        raise ValueError('Nonfinite source JSON number')
    return json.loads(text, object_pairs_hook=pairs, parse_float=number,
                      parse_constant=invalid_constant)


def equal(actual, observed):
    if isinstance(actual, bool) or isinstance(observed, bool):
        return type(actual) is bool and type(observed) is bool and actual == observed
    if isinstance(actual, (int, float)) and isinstance(observed, (int, float)):
        return (not isinstance(actual, float) or math.isfinite(actual)) and (
            not isinstance(observed, float) or math.isfinite(observed)) and actual == observed
    if type(actual) is not type(observed):
        return False
    if isinstance(actual, dict):
        return actual.keys() == observed.keys() and all(equal(value, observed[key]) for key, value in actual.items())
    if isinstance(actual, list):
        return len(actual) == len(observed) and all(equal(a, b) for a, b in zip(actual, observed))
    return actual == observed


def validate_bindings(bindings):
    """Validate before any delegation/effect, without reading a file or receipt."""
    if not isinstance(bindings, list) or not 1 <= len(bindings) <= 64:
        raise ValueError('Provide between one and 64 JSON evidence bindings')
    seen = set()
    allowed = set(BINDING_SCHEMA['properties'])
    for binding in bindings:
        if not isinstance(binding, dict) or set(binding) - allowed:
            raise ValueError('Unsupported JSON evidence binding fields')
        if not {'path', 'evidence_id', 'source_pointer'} <= set(binding):
            raise ValueError('Binding requires path, evidence_id and source_pointer')
        identity = (binding['path'], binding['evidence_id'], binding['source_pointer'])
        if not all(isinstance(part, str) for part in identity) or identity in seen:
            raise ValueError('Evidence bindings must use distinct string references')
        seen.add(identity)
        relation = binding.get('relation', 'equals')
        if relation not in {'equals', 'text_in'} or type(binding.get('decode_json', False)) is not bool:
            raise ValueError('Unsupported evidence relation or decode flag')
        if not binding['evidence_id'] or len(binding['evidence_id']) > 256:
            raise ValueError('A bounded actual evidence ID is required')
        for field in ('path', 'source_pointer'):
            try:
                _parts(binding[field])
            except ValueError as exc:
                raise ValueError(f'Binding {field}: {exc}') from exc
        if 'value_pointer' in binding:
            if not binding.get('decode_json'):
                raise ValueError('value_pointer requires explicit source JSON decoding')
            _parts(binding['value_pointer'])


def compare_evidence(actual, bindings, receipts):
    """A receipt binding proves correspondence, never external truth or freshness."""
    validate_bindings(bindings)
    results = []
    for binding in bindings:
        relation = binding.get('relation', 'equals')
        receipt = receipts.get(binding['evidence_id'])
        if not isinstance(receipt, dict) or receipt.get('ok') is not True:
            raise ValueError('Source must be an actual successful tool receipt in this mission')
        if receipt.get('tool') in {'verify', 'verify_file', 'verify_json_evidence', 'inspect_evidence', 'write_file', 'set_plan', 'revise_plan', 'finish'}:
            raise ValueError('A plan, assertion or saved output is not an observation source')
        observed = pointer(receipt, binding['source_pointer'])
        if binding.get('decode_json'):
            observed = strict_json_text(observed)
        if 'value_pointer' in binding:
            observed = pointer(observed, binding['value_pointer'])
        saved = pointer(actual, binding['path'])
        if relation == 'equals':
            passed = equal(saved, observed)
        else:
            values = saved if isinstance(saved, list) else [saved]
            if not isinstance(observed, str) or not values or any(not isinstance(v, str) or not v for v in values):
                raise ValueError('text_in requires nonempty saved text(s) and observed source text')
            passed = all(value in observed for value in values)
        canonical = json.dumps(observed, ensure_ascii=False, sort_keys=True, allow_nan=False).encode()
        results.append({**binding, 'relation': relation, 'passed': passed,
                        'observed_sha256': hashlib.sha256(canonical).hexdigest(),
                        'source_tool': receipt.get('tool')})
    return {'passed': all(item['passed'] for item in results), 'bindings': results,
            'scope': 'Saved JSON compared with retained successful tool receipts; not a fresh query or independent truth certificate'}


def inspect_receipt(receipts, evidence_id='', pointer_path='', *, tool=None, offset=0, limit=16, max_chars=12000):
    """Read only this mission's retained receipt, with real paths for bindings.

    Traversal and response size are bounded. JSON text fields are indexed only
    after strict decoding; returned hints still refer to the original receipt.
    """
    budget = max(512, min(int(max_chars), 16384))
    if not isinstance(evidence_id, str) or len(evidence_id)>256:
        raise ValueError('An actual bounded evidence ID is required')
    if not evidence_id:
        if pointer_path:
            raise ValueError('Selecting a pointer requires an evidence_id')
        if tool is not None and (not isinstance(tool,str) or not tool or len(tool)>256):
            raise ValueError('Receipt tool filter must be bounded text')
        if type(offset) is not int or offset<0 or type(limit) is not int or not 1<=limit<=64:
            raise ValueError('Receipt index requires nonnegative offset and limit between 1 and 64')
        # Excluding inspector calls makes pagination stable while the model
        # inspects successive pages: these reads do not extend their own index.
        matches = [r for r in receipts.values() if isinstance(r,dict)
                   and r.get('tool')!='inspect_evidence' and (tool is None or r.get('tool')==tool)]
        result = {'receipts':[], 'total':len(matches), 'offset':offset, 'next_offset':None,
                  'scope':'Retained mission receipts in original order, excluding inspector reads. Historical, not fresh operations.'}
        for receipt in matches[offset:offset+limit]:
            item = {key:receipt.get(key) for key in ('id','tool','ok','action_number','elapsed_seconds') if key in receipt}
            if len(json.dumps({**result,'receipts':result['receipts']+[item]},ensure_ascii=False))>budget-64:
                if not result['receipts']:
                    raise ValueError('Receipt metadata exceeds the inspection budget')
                break
            result['receipts'].append(item)
        next_offset = offset+len(result['receipts'])
        if next_offset<len(matches):
            result['next_offset'] = next_offset
        return result
    receipt = receipts.get(evidence_id)
    if not isinstance(receipt, dict):
        raise ValueError('Evidence ID is not retained in this mission')
    selected = pointer(receipt, pointer_path)
    result = {'evidence_id':evidence_id, 'source_tool':receipt.get('tool'),
              'source_ok':receipt.get('ok') is True, 'pointer':pointer_path,
              'scope':'Historical retained receipt, not a fresh query. Bind the original evidence_id and field pointers, never this inspector receipt.',
              'fields':[], 'truncated':False}

    # iterencode stops after the excerpt budget instead of materializing an
    # arbitrarily large serialization just to discover that it does not fit.
    excerpt = ''
    complete = True
    for chunk in json.JSONEncoder(ensure_ascii=False, allow_nan=False).iterencode(selected):
        room = budget//6 - len(excerpt)
        if len(chunk)>room:
            excerpt += chunk[:max(0,room)]
            complete = False
            break
        excerpt += chunk
    if complete:
        result['value'] = json.loads(excerpt)
    else:
        result.update(value_excerpt=excerpt, value_complete=False, truncated=True)

    def encoded_size(value):
        return len(json.dumps(value, ensure_ascii=False, allow_nan=False))

    stack = [(selected, pointer_path, None)]
    visited = 0
    while stack and visited<256 and len(result['fields'])<64:
        value, source_path, decoded_path = stack.pop()
        visited += 1
        hint = {'source_pointer':source_path}
        if decoded_path is not None:
            hint.update(decode_json=True, value_pointer=decoded_path)
        if isinstance(value, dict):
            hint.update(type='object', keys=list(islice(value,12)))
            children = list(islice(value.items(),64))
        elif isinstance(value, list):
            hint.update(type='array', length=len(value))
            children = list(enumerate(value[:64]))
        else:
            hint['type'] = ('null' if value is None else 'boolean' if isinstance(value,bool)
                            else 'number' if isinstance(value,(int,float)) else 'string')
            hint['value'] = value[:160] if isinstance(value,str) else value
            if isinstance(value,str) and len(value)>160:
                hint['value_truncated'] = True
            children = []
            if (decoded_path is None and isinstance(value,str) and len(value)<=65536
                    and value.lstrip().startswith(('{','['))):
                try:
                    decoded = strict_json_text(value)
                except (ValueError, RecursionError):
                    pass
                else:
                    stack.append((decoded, source_path, ''))
        candidate = {**result, 'fields':result['fields']+[hint]}
        if encoded_size(candidate)>budget:
            result['truncated'] = True
            break
        result['fields'].append(hint)
        for key, child in reversed(children):
            token = str(key).replace('~','~0').replace('/','~1')
            if decoded_path is None:
                child_path = source_path+'/'+token
                if len(child_path)<=4096:
                    stack.append((child,child_path,None))
            else:
                child_path = decoded_path+'/'+token
                if len(child_path)<=4096:
                    stack.append((child,source_path,child_path))
        if len(children)==64 and ((isinstance(value,dict) and len(value)>64)
                                  or (isinstance(value,list) and len(value)>64)):
            result['truncated'] = True
    result['truncated'] = result['truncated'] or bool(stack)
    if encoded_size(result)>budget:
        raise ValueError('Selected receipt metadata exceeds the inspection budget')
    return result
