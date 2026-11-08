"""Compare delivered JSON with actual, retained tool receipts without executing code."""
from __future__ import annotations

import hashlib
import json
import math
import re

BINDING_SCHEMA = {
    'type': 'object', 'additionalProperties': False,
    'properties': {
        'path': {'type': 'string', 'description': 'JSON pointer in the saved output.'},
        'evidence_id': {'type': 'string', 'description': 'Actual successful receipt ID retained by this mission.'},
        'source_pointer': {'type': 'string', 'description': 'JSON pointer starting at the complete tool receipt.'},
        'relation': {'type': 'string', 'enum': ['equals', 'text_in']},
        'decode_json': {'type': 'boolean'},
        'value_pointer': {'type': 'string', 'description': 'Pointer within explicitly decoded source JSON text.'},
    },
    'required': ['path', 'evidence_id', 'source_pointer'],
}


def _parts(path):
    if not isinstance(path, str) or len(path) > 4096 or (path and not path.startswith('/')):
        raise ValueError('A bounded JSON pointer is required; empty means the document root')
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
                raise ValueError('JSON pointer field is absent')
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
        _parts(binding['path'])
        _parts(binding['source_pointer'])
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
        if receipt.get('tool') in {'verify', 'verify_file', 'verify_json_evidence', 'write_file', 'set_plan', 'revise_plan', 'finish'}:
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
