import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from click.testing import CliRunner

from aurora_cli.local_mission import LocalMission, execute_local
from aurora_cli import cli, config


def plan(mission):
    mission.tool('set_plan', {'steps':['Write and inspect the output'], 'criteria':['Exact bytes']})


def test_real_write_read_verify_and_durable_resume_without_rewriting(tmp_path):
    mission = LocalMission('Deliver exact bytes', tmp_path, 'STANDARD')
    plan(mission)
    assert mission.tool('write_file', {'path':'result.txt','content':'LOCAL_OK\n'})['ok']
    assert mission.tool('read_file', {'path':'result.txt'})['content'] == 'LOCAL_OK\n'
    assert mission.tool('verify_file', {'path':'result.txt','content':'LOCAL_OK\n','criterion':'Exact bytes'})['ok']
    assert mission.finish('Verified exact bytes')
    mid, stamp = mission.id, (tmp_path/'result.txt').stat().st_mtime_ns
    mission.close()
    resumed = LocalMission('', tmp_path, 'STANDARD', resume_id=mid)
    try:
        assert resumed.run(SimpleNamespace(complete=lambda *a,**k: pytest.fail('Already completed')), 'model')
        assert (tmp_path/'result.txt').stat().st_mtime_ns == stamp
        assert len([e for e in resumed.state['evidence'] if e['tool']=='write_file']) == 1
    finally:
        resumed.close()


def test_false_completion_and_post_verification_change_are_rejected(tmp_path):
    mission = LocalMission('File', tmp_path, 'STANDARD')
    try:
        plan(mission)
        with pytest.raises(ValueError,match='preuve réelle'):
            mission.finish('Done')
        mission.tool('write_file', {'path':'result.txt','content':'correct'})
        assert not mission.tool('verify_file', {'path':'result.txt','content':'wrong','criterion':'Exact bytes'})['ok']
        assert not mission.state['verified']
        mission.tool('verify_file', {'path':'result.txt','content':'correct','criterion':'Exact bytes'})
        (tmp_path/'result.txt').write_bytes(b'tampered')
        with pytest.raises(ValueError,match='Sortie modifiée'):
            mission.finish('Done')
    finally:
        mission.close()


def test_overwrites_require_observed_hash_and_preserve_previous_bytes(tmp_path):
    source = tmp_path/'result.txt'; source.write_bytes(b'USER_DATA')
    mission = LocalMission('Edit a file', tmp_path, 'STANDARD')
    try:
        plan(mission)
        assert not mission.tool('write_file', {'path':'result.txt','content':'new'})['ok']
        assert source.read_bytes() == b'USER_DATA'
        observed = mission.tool('read_file', {'path':'result.txt'})
        assert mission.tool('write_file', {'path':'result.txt','content':'new','expected_sha256':observed['sha256']})['ok']
        assert any(p.read_bytes()==b'USER_DATA' for p in (mission.path.parent/mission.id).iterdir())
    finally:
        mission.close()


def test_safe_blocks_effects_and_paths_cannot_escape(tmp_path):
    mission = LocalMission('Inspect', tmp_path, 'SAFE')
    try:
        plan(mission)
        for name in ['write_file','run_python']:
            with pytest.raises(PermissionError):
                mission.tool(name, {'path':'result.py','content':'print(1)'})
        for path in ['../escape.txt', str(tmp_path.parent/'escape.txt'), '.env', '.git/config']:
            assert not mission.tool('read_file', {'path':path})['ok']
        assert not (tmp_path/'result.py').exists()
    finally:
        mission.close()


def test_symlink_escape_is_rejected(tmp_path):
    external = tmp_path.parent/'external.txt'; external.write_bytes(b'private')
    try:
        (tmp_path/'link.txt').symlink_to(external)
    except OSError:
        pytest.skip('Symlinks not enabled on this host')
    mission = LocalMission('Read', tmp_path)
    try:
        assert not mission.tool('read_file', {'path':'link.txt'})['ok']
    finally:
        mission.close()


def test_python_script_is_actually_executed_in_workspace_and_exit_code_matters(tmp_path):
    (tmp_path/'check.py').write_text("from pathlib import Path\nassert Path.cwd().name == "+repr(tmp_path.name)+"\nassert Path('input.txt').read_bytes() == b'expected'\n",encoding='utf-8')
    (tmp_path/'input.txt').write_bytes(b'wrong')
    mission = LocalMission('Check input', tmp_path, 'STANDARD')
    try:
        plan(mission)
        assert not mission.tool('run_python', {'path':'check.py','criterion':'Exact bytes'})['ok']
        assert not mission.state['verified']
        (tmp_path/'input.txt').write_bytes(b'expected')
        assert mission.tool('run_python', {'path':'check.py','criterion':'Exact bytes'})['ok']
        assert mission.finish('Assertions passed')
    finally:
        mission.close()


def test_uncertain_effect_never_replays_on_resume(tmp_path):
    mission = LocalMission('Work', tmp_path, 'STANDARD')
    plan(mission)
    mission.state['pending_action'] = {'tool':'run_python','args':{'path':'script.py'}}
    mission.save(); mid = mission.id; mission.close()
    resumed = LocalMission('', tmp_path, 'STANDARD', resume_id=mid)
    try:
        with pytest.raises(RuntimeError,match='sans rejeu'):
            resumed.run(SimpleNamespace(complete=lambda *a,**k: pytest.fail('No new action')), 'model')
        assert resumed.state['plan'] == ['Write and inspect the output']
    finally:
        resumed.close()


def test_plan_and_proofs_survive_identical_plan_replay(tmp_path):
    mission = LocalMission('Work', tmp_path, 'STANDARD')
    try:
        plan(mission)
        mission.tool('write_file', {'path':'result.txt','content':'ok'})
        mission.tool('verify_file', {'path':'result.txt','content':'ok','criterion':'Exact bytes'})
        before = json.dumps(mission.state,sort_keys=True)
        plan(mission)
        assert json.dumps(mission.state,sort_keys=True) == before
        with pytest.raises(ValueError,match='Plan accepté conservé'):
            mission.tool('set_plan',{'steps':['Replace'],'criteria':['New']})
    finally:
        mission.close()


def test_structured_runtime_drives_real_effects_and_evidence(tmp_path):
    replies = iter([
        {'tool':'set_plan','args':{'steps':['Deliver'],'criteria':['Exact bytes']}},
        {'tool':'write_file','args':{'path':'result.txt','content':'LOCAL_OK\n'}},
        {'tool':'verify_file','args':{'path':'result.txt','content':'LOCAL_OK\n','criterion':'Exact bytes'}},
        {'result':'Delivered'},
    ])
    state = execute_local('Deliver',workspace=tmp_path,permissions='STANDARD',model='fixture',
                          runtime=SimpleNamespace(complete=lambda *a,**k:json.dumps(next(replies))))
    assert state['status']=='completed'
    assert (tmp_path/'result.txt').read_bytes()==b'LOCAL_OK\n'
    assert state['verified'] and state['files']


def test_command_chooses_fixed_host_before_local_and_never_retries_after_remote_failure(monkeypatch,tmp_path):
    from aurora_cli import routing, mission, bridge, local_mission
    from unittest.mock import Mock
    config.set('api_key','fixture');config.set('server_url','https://bridge.test')
    monkeypatch.setattr(routing,'remote_readiness',lambda:(True,'Ready'))
    local = Mock(); monkeypatch.setattr(local_mission,'execute_local',local)
    client = Mock(); monkeypatch.setattr(bridge,'Bridge',lambda:client)
    remote = Mock(return_value=False);monkeypatch.setattr(mission,'run_mission',remote)
    result=CliRunner().invoke(cli.main,['mission','Work','--workspace',str(tmp_path)])
    assert result.exit_code==1
    remote.assert_called_once(); local.assert_not_called(); client.close.assert_called_once()


def test_command_falls_back_only_before_acceptance(monkeypatch,tmp_path):
    from aurora_cli import routing, local_mission
    from unittest.mock import Mock
    config.set('api_key','fixture');config.set('server_url','https://bridge.test')
    monkeypatch.setattr(routing,'remote_readiness',lambda:(False,'Offline'))
    local=Mock(return_value={'status':'completed','result':'Verified'})
    monkeypatch.setattr(local_mission,'execute_local',local)
    result=CliRunner().invoke(cli.main,['mission','Work','--workspace',str(tmp_path),'--permissions','STANDARD'])
    assert result.exit_code==0,result.output
    assert local.call_args.kwargs['workspace']==tmp_path
    assert local.call_args.kwargs['permissions']=='STANDARD'


def test_local_mission_lock_prevents_two_writers(tmp_path):
    mission=LocalMission('Work',tmp_path,'STANDARD')
    try:
        with pytest.raises(OSError):
            LocalMission('',tmp_path,'STANDARD',resume_id=mission.id)
    finally:
        mission.close()


def test_local_mission_engineering_tools_and_skill_discovery(tmp_path):
    from aurora_cli.local_mission import list_local_missions, needs_actions
    assert needs_actions("corrige le bug dans le module d'authentification")
    assert needs_actions("lance pytest et vérifie la fonction")
    assert not needs_actions("bonjour comment ça va ?")

    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "solver.py").write_text("def solve_matrix(x):\n    return x * 3\n", encoding="utf-8")
    skill_dir = tmp_path / ".aurora" / "skills" / "matrix-skill"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text("---\nname: matrix-skill\ndescription: Matrix helper\n---\nUse solve_matrix.", encoding="utf-8")

    mission = LocalMission("Inspect and revise", tmp_path, "SAFE")
    try:
        plan(mission)
        found = mission.tool("find_files", {"pattern": "*.py"})
        assert found["ok"] and "src/solver.py" in found["files"]

        searched = mission.tool("search_code", {"query": "solve_matrix", "path": "src"})
        assert searched["ok"] and searched["total_matches"] == 1
        assert searched["matches"][0]["path"] == "src/solver.py"

        skills = mission.tool("list_skills", {})
        assert skills["ok"] and any(s["name"] == "matrix-skill" for s in skills["skills"])

        read_sk = mission.tool("read_skill", {"name": "matrix-skill"})
        assert read_sk["ok"] and "Use solve_matrix." in read_sk["content"]

        revised = mission.tool("revise_plan", {"steps": ["Step 1", "Step 2"], "reason": "Found solver.py", "evidence_ids": [searched["id"]]})
        assert revised["ok"] and mission.state["plan"] == ["Step 1", "Step 2"]
    finally:
        mission.close()

    items = list_local_missions()
    assert any(item["id"] == mission.id for item in items)

