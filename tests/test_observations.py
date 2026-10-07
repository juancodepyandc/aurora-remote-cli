"""Display the actual cause of failure and measured results in both CLI views."""
from aurora_cli.observations import observation_text
from aurora_cli.ui import MissionView


def test_image_prerequisite_failure_is_visible_in_activity():
    state = MissionView()
    event = {'type':'tool_result','id':'ev_plan','tool':'generate_image','ok':False,
             'result':{'error':'Set a plan and measurable acceptance criteria before executing actions'}}
    state.consume(event)
    assert 'Set a plan' in state.activity[-1][1]
    assert 'generate_image' in state.activity[-1][1]
    assert state.verified == []


def test_failed_verification_keeps_actual_and_expected_values():
    event = {'type':'tool_result','tool':'verify','ok':False,
             'result':{'passed':False,'checks':[{'kind':'json','passed':False,
                 'path':'result.json','expected':{'sum':139},'observed':{'sum':122}}]}}
    detail = observation_text(event)
    assert '139' in detail and '122' in detail and 'result.json' in detail


def test_command_error_tail_survives_bounded_rendering():
    detail = observation_text({'type':'command_output','content':'Starting job\n'+'x'*3000+'\nCUDA out of memory'}, 200)
    assert len(detail) <= 200
    assert 'Starting job' in detail and 'CUDA out of memory' in detail


def test_recovery_is_labeled_as_a_hypothesis_and_not_an_accepted_proof():
    state = MissionView()
    state.consume({'type':'recovery_proposal','hypothesis':'Missing plan',
                   'expected_observation':'Plan accepted'})
    assert 'Hypothèse' in state.activity[-1][1]
    assert not state.verified and not state.evidence


def test_tool_output_cannot_emit_terminal_control_sequences():
    detail = observation_text({'type':'command_output','content':'\x1b[31mError\x1b[0m\x00\x9b'})
    assert detail == 'Error'
    assert len(observation_text({'type':'command_output','content':'many characters'}, 3)) <= 3
