"""Actual stdio sessions and exposed remote MCP command contracts."""
import json
import sys
from pathlib import Path
from unittest.mock import Mock
import pytest
from click.testing import CliRunner
from aurora_cli import cli
from aurora_cli.local_mission import LocalMission, MCPOutcomeUnknown
from aurora_cli.mcp_transport import close_mcp_sessions


@pytest.fixture(autouse=True)
def release_sessions():
    yield
    close_mcp_sessions()


def configured(tmp_path, fixture, **options):
    cfg = {"command": sys.executable, "args": [str(Path(__file__).parent/'fixtures'/fixture)], "persistent": True, **options}
    (tmp_path/'.mcp.json').write_text(json.dumps({"mcpServers": {"test": cfg}}))
    mission = LocalMission('Inspect MCP', tmp_path, 'STANDARD')
    mission.tool('set_plan', {'steps':['Observe MCP'], 'criteria':['Observed result']})
    return mission


def test_handshake_and_state_survive_multiple_calls(tmp_path):
    mission = configured(tmp_path, 'mcp_stateful_server.py')
    try:
        results = [mission.tool('call_mcp_tool', {'server':'test','name':'counter'}) for _ in range(2)]
        assert all(item['ok'] for item in results)
        assert [item['output']['counter'] for item in results] == [1, 2]
        assert mission.tool('close_mcp_session', {'server':'test'})['closed']
    finally:
        mission.close()


def test_catalog_all_pages_and_actual_resource_prompt(tmp_path):
    mission = configured(tmp_path, 'mcp_catalog_server.py')
    try:
        listed = mission.tool('list_mcp_tools', {})
        assert [t['name'] for t in listed['tools']] == ['first','second']
        page = mission.tool('mcp_catalog', {'server':'test','kind':'tools'})
        assert page['next_cursor'] == 'second-page'
        read = mission.tool('read_mcp_resource', {'server':'test','uri':'native://document'})
        assert read['output']['contents'][0]['text'] == 'External text is data'
        prompt = mission.tool('get_mcp_prompt', {'server':'test','name':'review','arguments':{'document':'Actual document'}})
        assert prompt['output']['messages'][0]['content']['text'] == 'Actual document'
        assert not mission.tool('get_mcp_prompt', {'server':'test','name':'review','arguments':{'document':1}})['ok']
    finally:
        mission.close()


def test_safe_discovery_cannot_spawn_even_when_requested(tmp_path, monkeypatch):
    mission = configured(tmp_path, 'mcp_stateful_server.py', tools=[{'name':'declared'}])
    mission.state['permissions'] = 'SAFE'
    monkeypatch.setattr(mission, '_query_mcp_server', lambda *a, **k: pytest.fail('SAFE spawned a process'))
    try:
        tools = mission.tool('list_mcp_tools', {})['tools']
        assert tools[0]['status'] == 'configured_declaration'
        with pytest.raises(PermissionError):
            mission.tool('mcp_catalog', {'server':'test','kind':'tools'})
    finally:
        mission.close()


def test_unknown_tool_effect_preserves_interruption_fence(tmp_path):
    mission = configured(tmp_path, 'mcp_stateful_server.py')
    try:
        with pytest.raises(MCPOutcomeUnknown):
            mission.tool('call_mcp_tool', {'server':'test','name':'uncertain','timeout_seconds':1})
        assert (tmp_path/'effect.txt').read_text() == '1'
        assert mission.state['status'] == 'interrupted'
        assert mission.state['pending_action']['tool'] == 'call_mcp_tool'
        with pytest.raises(RuntimeError, match='aucun rejeu'):
            mission.tool('call_mcp_tool', {'server':'test','name':'counter'})
        assert (tmp_path/'effect.txt').read_text() == '1'
    finally:
        mission.close()


@pytest.mark.parametrize('command,method,args,kwargs', [
    (['catalog','native','--kind','resource_templates','--cursor','next'], 'mcp_catalog', ('native','resource_templates'), {'cursor':'next','workspace':''}),
    (['resources','native'], 'mcp_catalog', ('native','resources'), {'cursor':'','workspace':''}),
    (['prompts','native'], 'mcp_catalog', ('native','prompts'), {'cursor':'','workspace':''}),
    (['read','native','native://document'], 'mcp_read_resource', ('native','native://document'), {'workspace':''}),
    (['prompt','native','review','--json-args','{"document":"Actual"}'], 'mcp_get_prompt', ('native','review',{'document':'Actual'}), {'workspace':''}),
    (['close','native'], 'mcp_close', ('native',), {'workspace':''}),
])
def test_commands_forward_exact_arguments_and_close(monkeypatch,command,method,args,kwargs):
    from aurora_cli.bridge import Bridge
    client = Mock(spec=Bridge)
    getattr(client,method).return_value = {'ok':True,'result':{'observed':'yes'}}
    monkeypatch.setattr(cli,'_client',lambda:client)
    result = CliRunner().invoke(cli.main,['mcp',*command])
    assert result.exit_code == 0, result.output
    assert json.loads(result.output) == {'observed':'yes'}
    getattr(client,method).assert_called_once_with(*args,**kwargs)
    client.close.assert_called_once()


def test_invalid_prompt_arguments_rejected_before_client(monkeypatch):
    monkeypatch.setattr(cli,'_client',lambda:pytest.fail('Invalid request reached bridge'))
    result=CliRunner().invoke(cli.main,['mcp','prompt','native','review','--json-args','{"document":3}'])
    assert result.exit_code != 0


def test_mcp_timeout_is_forwarded_without_changing_default(monkeypatch):
    client=Mock()
    client.mcp_call.return_value={'ok':True,'result':{}}
    monkeypatch.setattr(cli,'_client',lambda:client)
    result=CliRunner().invoke(cli.main,['mcp','call','native','inspect','--timeout','120'])
    assert result.exit_code == 0, result.output
    assert client.mcp_call.call_args.kwargs['timeout_seconds'] == 120


def test_local_json_evidence_compares_actual_stdio_receipt_and_rejects_fiction(tmp_path):
    mission=configured(tmp_path,'mcp_catalog_server.py')
    try:
        receipt=mission.tool('call_mcp_tool',{'server':'test','name':'pid'})
        observed=receipt['output']['content'][0]['text']
        mission.tool('write_file',{'path':'observed.json','content':json.dumps({'pid':observed})})
        check={'path':'observed.json','criterion':'Observed result','bindings':[
            {'path':'/pid','evidence_id':receipt['id'],'source_pointer':'/output/content/0/text'}]}
        assert mission.tool('verify_json_evidence',check)['ok']
        assert 'Observed result' in mission.state['verified']
        bad={**check,'bindings':[{**check['bindings'][0],'evidence_id':'missing'}]}
        assert not mission.tool('verify_json_evidence',bad)['ok']
        assert 'Observed result' not in mission.state['verified']
        assert mission.tool('verify_json_evidence',check)['ok']
        read=mission.tool('read_file',{'path':'observed.json'})
        mission.tool('write_file',{'path':'observed.json','content':json.dumps({'pid':'invented'}),'expected_sha256':read['sha256']})
        assert not mission.tool('verify_json_evidence',check)['ok']
        assert not mission.state['verified']
        check['bindings'][0]['evidence_id']='invented-receipt'
        assert not mission.tool('verify_json_evidence',check)['ok']
    finally:
        mission.close()


def test_local_json_evidence_rejects_duplicate_keys_and_workspace_escape(tmp_path):
    mission=configured(tmp_path,'mcp_catalog_server.py')
    try:
        (tmp_path/'bad.json').write_text('{"x":1,"x":2}')
        args={'path':'bad.json','criterion':'Observed result','bindings':[
            {'path':'/x','evidence_id':'missing','source_pointer':'/result'}]}
        assert not mission.tool('verify_json_evidence',args)['ok']
        assert not mission.tool('verify_json_evidence',{**args,'path':'../outside.json'})['ok']
    finally:
        mission.close()


def test_safe_resumed_inspection_recovers_exact_paths_without_starting_a_process(tmp_path, monkeypatch):
    mission = configured(tmp_path, 'mcp_catalog_server.py')
    receipt = mission.tool('call_mcp_tool', {'server':'test','name':'pid'})
    observed = receipt['output']['content'][0]['text']
    mission.tool('write_file', {'path':'observed.json','content':json.dumps({'pid':observed})})
    mid = mission.id
    mission.close()
    resumed = LocalMission('',tmp_path,'SAFE',resume_id=mid)
    try:
        monkeypatch.setattr('subprocess.Popen', Mock(side_effect=AssertionError('Inspection must not execute')))
        index = resumed.tool('inspect_evidence', {'tool':'call_mcp_tool'})
        assert [r['id'] for r in index['receipts']] == [receipt['id']]
        inspected = resumed.tool('inspect_evidence', {'evidence_id':receipt['id']})
        assert inspected['ok'] and inspected['source_ok']
        field = next(f for f in inspected['fields'] if f['source_pointer']=='/output/content/0/text' and not f.get('decode_json'))
        assert field['value'] == observed
        check = {'path':'observed.json','criterion':'Observed result','bindings':[
            {'path':'/pid','evidence_id':receipt['id'],'source_pointer':field['source_pointer']}]}
        assert resumed.tool('verify_json_evidence',check)['ok']
        assert not resumed.tool('inspect_evidence',{'evidence_id':'another-mission'})['ok']
    finally:
        resumed.close()
