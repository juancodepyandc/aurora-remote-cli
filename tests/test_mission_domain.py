from aurora_cli.mission_domain import domain_label

def test_3d_domain_is_distinct_from_dialogue_tab():
    assert domain_label('générer une loutre avec un fusil en 3D dans le style animé')=='3D'
    assert domain_label('préparer une figurine STL')=='3D'
    assert domain_label('une représentation politique')=='Mission'

def test_workspace_identifies_3d_without_inventing_engine_activity():
    from prompt_toolkit.input import create_pipe_input
    from prompt_toolkit.output import DummyOutput
    from aurora_cli.ui import WorkspaceApp
    with create_pipe_input() as pipe:
        app=WorkspaceApp(input=pipe,output=DummyOutput())
        app.state.goal='Une figurine 3D';app.state.status='running';app.state.phase='inspect_tool'
        assert 'Une figurine 3D' in app.sidebar_text()
        activity = ''.join(text for _,text in app.activity_text())
        assert 'inspect_tool' in activity
        assert 'CONVERSATION' not in activity
        assert 'Aucune action observée' in app.agents_matrix_text()
