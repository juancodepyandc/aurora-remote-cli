"""HTTP diagnostics and mission replay without a live bridge or inference."""
import json

import httpx
import pytest

from aurora_cli import bridge


def client_with(handler):
    client = bridge.Bridge(server_url='https://bridge.invalid', api_key='fixture', timeout=7)
    client._client.close()
    client._client = httpx.Client(transport=httpx.MockTransport(handler),
                                 base_url='https://bridge.invalid',
                                 timeout=httpx.Timeout(7, connect=10))
    return client


@pytest.mark.parametrize('method', ['get', 'post', 'delete'])
@pytest.mark.parametrize('exception,phase,seconds', [
    (httpx.ReadTimeout, 'read', 7), (httpx.ConnectTimeout, 'connect', 10),
    (httpx.WriteTimeout, 'write', 7), (httpx.PoolTimeout, 'pool', 7),
])
def test_empty_timeout_keeps_phase_and_actual_budget(method, exception, phase, seconds):
    def handler(request):
        raise exception('', request=request)
    with client_with(handler) as client:
        result = getattr(client, method)('/api/cli/doctor')
    assert not result['ok']
    assert result['error_kind'] == 'timeout'
    assert result['timeout_phase'] == phase and result['timeout_seconds'] == seconds
    assert exception.__name__ in result['error'] and '/api/cli/doctor' in result['error']


def test_timed_out_mission_start_is_not_reposted():
    requests = []
    def handler(request):
        requests.append(request)
        raise httpx.ReadTimeout('', request=request)
    with client_with(handler) as client:
        result = client.mission_start('Original task', idempotency_key='original-key')
    assert result['acceptance_unknown'] is True
    assert len(requests) == 1
    assert json.loads(requests[0].content)['idempotency_key'] == 'original-key'


class BrokenStream(httpx.SyncByteStream):
    def __init__(self, payload):
        self.payload = payload
    def __iter__(self):
        yield self.payload
        raise httpx.ReadTimeout('')


def record(cursor, event):
    prefix = f'id: {cursor}\n' if cursor is not None else ''
    return (prefix + 'data: ' + json.dumps(event) + '\n\n').encode()


def test_reconnect_replays_partial_record_without_duplicate_complete_events(monkeypatch):
    monkeypatch.setattr(bridge.time, 'sleep', lambda _: None)
    requests = []
    first = record(1, {'type': 'step_start', 'step': 'Shape'})
    partial = b'id: 2\ndata: {"type":"tool_result","tool":"mesh"}'
    def handler(request):
        requests.append(request)
        if len(requests) == 1:
            return httpx.Response(200, stream=BrokenStream(first + partial))
        return httpx.Response(200, content=first
            + record(None, {'type': 'heartbeat'})
            + record(2, {'type': 'tool_result', 'tool': 'mesh'})
            + record(3, {'type': 'mission_complete'}))
    with client_with(handler) as client:
        events = list(client.mission_stream('mis_original'))
    assert [event['type'] for event in events] == [
        'step_start', 'reconnecting', 'heartbeat', 'tool_result', 'mission_complete']
    assert [request.headers['Last-Event-ID'] for request in requests] == ['0', '1']
    assert all(request.method == 'GET' for request in requests)
    assert events[1]['error_kind'] == 'timeout' and 'ReadTimeout' in events[1]['message']


def test_reconnect_is_bounded_and_retains_last_complete_cursor(monkeypatch):
    monkeypatch.setattr(bridge.time, 'sleep', lambda _: None)
    requests = []
    def handler(request):
        requests.append(request)
        return httpx.Response(200, stream=BrokenStream(record(1, {'type': 'step_start'})))
    with client_with(handler) as client:
        events = list(client.mission_stream('mis_original'))
    assert len(requests) == 4
    assert sum(event['type'] == 'step_start' for event in events) == 1
    assert events[-1]['type'] == 'error' and events[-1]['last_event_id'] == 1
    assert events[-1]['error_kind'] == 'timeout'


@pytest.mark.parametrize('cursor', [None, 'invalid', 2])
def test_missing_invalid_or_skipped_event_id_refuses_incomplete_mission(cursor):
    requests = []
    def handler(request):
        requests.append(request)
        return httpx.Response(200, content=record(cursor, {'type': 'mission_complete'}))
    with client_with(handler) as client:
        events = list(client.mission_stream('mis_original'))
    assert len(requests) == 1
    assert len(events) == 1 and events[0]['type'] == 'error'
    assert 'Invalid SSE stream' in events[0]['error']


def test_chat_post_stream_is_never_replayed_after_timeout():
    requests = []
    def handler(request):
        requests.append(request)
        return httpx.Response(200, stream=BrokenStream(record(None, {'type': 'token', 'content': 'partial'})))
    with client_with(handler) as client:
        events = list(client.chat_stream([{'role': 'user', 'content': 'Original'}]))
    assert len(requests) == 1 and requests[0].method == 'POST'
    assert [event['type'] for event in events] == ['token', 'error']


def test_sse_refusal_preserves_server_diagnostic():
    with client_with(lambda request: httpx.Response(403, json={'error': 'Mission not authorized'})) as client:
        events = list(client.mission_stream('mis_original'))
    assert events == [{'type': 'error', 'error': 'Mission not authorized',
                       'error_kind': 'http', 'last_event_id': 0}]
