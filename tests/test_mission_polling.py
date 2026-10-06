"""Finite mission replay for Quick Tunnels; HTTP responses are fixtures."""
import httpx
import pytest

from aurora_cli.bridge import Bridge


def client(handler, url='https://example.trycloudflare.com'):
    bridge = Bridge(url, 'test-key')
    bridge._client.close()
    bridge._client = httpx.Client(base_url=url, transport=httpx.MockTransport(handler))
    return bridge


def test_quick_tunnel_uses_get_polling_with_cursor_and_never_reposts():
    requests = []
    def respond(request):
        requests.append(request)
        cursor = int(request.headers['Last-Event-ID'])
        event = {'type':'tool_result'} if cursor == 0 else {'type':'mission_complete'}
        return httpx.Response(200,json={'ok':True,'events':[{'id':cursor+1,'event':event}],
                                       'cursor':cursor+1,'terminal':cursor == 1})
    bridge = client(respond)
    try:
        assert [e['type'] for e in bridge.mission_stream('mis_fixture')] == ['tool_result','mission_complete']
    finally:
        bridge.close()
    assert all(r.method == 'GET' and r.url.path.endswith('/events') for r in requests)
    assert [r.headers['Last-Event-ID'] for r in requests] == ['0','1']


def test_connection_failure_retries_only_get_from_last_delivered_event(monkeypatch):
    monkeypatch.setattr('aurora_cli.bridge.time.sleep',lambda _:None)
    observed=[]
    def respond(request):
        observed.append(request.headers['Last-Event-ID'])
        if len(observed)==2:
            raise httpx.ReadTimeout('fixture interruption',request=request)
        seq = 1 if len(observed)==1 else 2
        return httpx.Response(200,json={'ok':True,'events':[{'id':seq,'event':{
            'type':'tool_result' if seq==1 else 'mission_complete'}}], 'cursor':seq,'terminal':seq==2})
    bridge=client(respond)
    try:
        events=list(bridge.mission_stream('mis_fixture'))
    finally:
        bridge.close()
    assert [e['type'] for e in events]==['tool_result','reconnecting','mission_complete']
    assert observed==['0','1','1']


@pytest.mark.parametrize('rows,cursor,terminal',[
    ([{'id':2,'event':{'type':'mission_complete'}}],2,True),
    ([{'id':True,'event':{'type':'mission_complete'}}],True,True),
    ([{'id':1,'event':[]}],1,True),
    ([{'id':1,'event':{'type':'mission_complete'}}],9,True),
    ([],0,'true'),
])
def test_invalid_batches_cannot_deliver_a_result(rows,cursor,terminal):
    bridge=client(lambda r:httpx.Response(200,json={'ok':True,'events':rows,'cursor':cursor,'terminal':terminal}))
    try:
        events=list(bridge.mission_stream('mis_fixture'))
    finally:
        bridge.close()
    assert [e['type'] for e in events]==['error']
    assert events[0]['error_kind']=='invalid_stream'


def test_empty_terminal_without_completion_fails():
    bridge=client(lambda r:httpx.Response(200,json={'ok':True,'events':[],'cursor':0,'terminal':True}))
    try:
        assert list(bridge.mission_stream('mis_fixture'))[0]['error_kind']=='interrupted_stream'
    finally:
        bridge.close()


def test_other_servers_keep_sse(monkeypatch):
    bridge=Bridge('http://localhost:3001','fixture-key')
    calls=[]
    monkeypatch.setattr(bridge,'stream_sse',lambda *a,**k:calls.append((a,k)) or iter([{'type':'mission_complete'}]))
    try:
        assert list(bridge.mission_stream('mis_fixture',last_event_id=4))==[{'type':'mission_complete'}]
    finally:
        bridge.close()
    assert calls[0][1]['last_event_id']==4 and calls[0][1]['method']=='GET'
