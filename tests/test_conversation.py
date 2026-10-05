from types import SimpleNamespace

import pytest

from aurora_cli.core import conversation
from aurora_cli.core.providers import StreamChunk


def runtime(events):
    calls = []
    def stream(model, messages, **kwargs):
        calls.append((list(messages), kwargs))
        return iter(events.pop(0))
    return SimpleNamespace(stream=stream), calls


def test_failing_model_is_replaced_before_output_and_context_preserved(monkeypatch, tmp_path):
    from aurora_cli.core import model_selection
    history = conversation.Conversation.open(root=tmp_path)
    models = [SimpleNamespace(name='bad'), SimpleNamespace(name='good')]
    monkeypatch.setattr(model_selection, 'rank_available', lambda *a, **kw: models)
    serving, calls = runtime([[StreamChunk(error='model not found')],
                             [StreamChunk(text='Réponse complète.'), StreamChunk(done=True)]])
    result = ''.join(conversation.respond_with_fallback(serving, 'bad', history, 'Une question', models=models))
    assert result == 'Réponse complète.' and len(calls) == 2
    assert history[-2]['content'] == 'Une question'
    assert history[-1]['content'] == result
    assert conversation.Conversation.open(root=tmp_path) == history


def test_partial_answers_are_not_blended_across_models(monkeypatch, tmp_path):
    from aurora_cli.core import model_selection
    models = [SimpleNamespace(name='bad'), SimpleNamespace(name='good')]
    monkeypatch.setattr(model_selection, 'rank_available', lambda *a, **kw: models)
    history = conversation.Conversation.open(root=tmp_path)
    serving, calls = runtime([[StreamChunk(text='Début de réponse'), StreamChunk(error='interrupted')]])
    iterator = conversation.respond_with_fallback(serving, 'bad', history, 'Question', models=models)
    assert next(iterator) == 'Début de réponse'
    with pytest.raises(RuntimeError, match='interrupted'):
        next(iterator)
    assert len(calls) == 1 and history == []
    assert history.state['attempts']


def test_archive_resumes_and_new_preserves_previous(tmp_path):
    history = conversation.Conversation.open(root=tmp_path)
    history.extend([{'role': 'user', 'content': 'garder ce chemin'},
                    {'role': 'assistant', 'content': '/tmp/result.glb'}])
    history.save()
    assert conversation.Conversation.open(root=tmp_path) == history
    previous = tmp_path / f'{history.session_id}.json'
    fresh = history.new()
    assert fresh == [] and fresh.session_id != history.session_id
    assert previous.is_file()
    assert conversation.Conversation.open(root=tmp_path).session_id == fresh.session_id


def test_corrupt_archive_is_not_silently_replaced(tmp_path):
    history = conversation.Conversation.open(root=tmp_path)
    path = tmp_path / f'{history.session_id}.json'
    path.write_text('broken')
    with pytest.raises(ValueError):
        conversation.Conversation.open(root=tmp_path)
    assert path.read_text() == 'broken'


def test_stale_terminal_cannot_overwrite_newer_turns(tmp_path):
    first = conversation.Conversation.open(root=tmp_path)
    stale = conversation.Conversation.open(root=tmp_path)
    first.extend([{'role': 'user', 'content': 'new'}, {'role': 'assistant', 'content': 'reply'}])
    first.save()
    with pytest.raises(RuntimeError, match='autre terminal'):
        stale.save()
    assert conversation.Conversation.open(root=tmp_path) == first


def test_clear_archives_is_scoped_recoverable_and_blocks_stale_writes(tmp_path):
    first = conversation.Conversation.open(project=tmp_path / 'first')
    first.append({'role': 'user', 'content': 'Keep in backup'})
    first.state['pending_request'] = 'Old request'
    first.save()
    other = conversation.Conversation.open(project=tmp_path / 'other')
    original = (first.root / f'{first.session_id}.json').read_bytes()
    count, backup = conversation.clear_archives(project=tmp_path / 'first')
    assert count == 1
    assert (backup / first.root.name / f'{first.session_id}.json').read_bytes() == original
    assert conversation.Conversation.open(project=tmp_path / 'other').session_id == other.session_id
    fresh = conversation.Conversation.open(project=tmp_path / 'first')
    assert fresh == [] and 'pending_request' not in fresh.state
    with pytest.raises(RuntimeError, match='effacée'):
        first.save()
    assert conversation.Conversation.open(project=tmp_path / 'first').session_id == fresh.session_id


def test_clear_all_ignores_backups_and_unrelated_files(tmp_path):
    first = conversation.Conversation.open(project=tmp_path / 'first')
    second = first.new()
    other = conversation.Conversation.open(project=tmp_path / 'other')
    parent = first.root.parent
    unrelated = parent / 'notes.txt'
    unrelated.write_text('keep')
    count, backup = conversation.clear_archives(all_projects=True)
    assert count == 3
    assert (backup / first.root.name / f'{second.session_id}.json').exists()
    assert (backup / other.root.name / 'active.json').exists()
    assert unrelated.read_text() == 'keep'
    assert conversation.clear_archives(all_projects=True) == (0, None)


def test_clear_refuses_symlink_backup_directory(tmp_path):
    history = conversation.Conversation.open()
    (history.root.parent / '.cleared').symlink_to(tmp_path, target_is_directory=True)
    with pytest.raises(OSError, match='symbolique'):
        conversation.clear_archives()
    assert (history.root / 'active.json').exists()


def test_all_turns_are_sent_when_context_fits():
    history = [{'role': role, 'content': f'{i}'} for i in range(15) for role in ('user', 'assistant')]
    rt, calls = runtime([[StreamChunk(text='ok'), StreamChunk(done=True)]])
    assert ''.join(conversation.respond(rt, 'model', history, 'continue')) == 'ok'
    assert len(calls[0][0]) == 31  # Previously only twenty old messages survived.
    assert len(history) == 32
    assert calls[0][1]['context_length'] == 4096


def test_token_limit_continues_without_duplicate_history():
    rt, calls = runtime([[StreamChunk(text='first'), StreamChunk(done=True, meta={'finish': 'length'})],
                         [StreamChunk(text=' second'), StreamChunk(done=True, meta={'done_reason': 'stop'})]])
    history = []
    assert ''.join(conversation.respond(rt, 'model', history, 'full answer')) == 'first second'
    assert len(history) == 2
    assert calls[1][0][-2] == {'role': 'assistant', 'content': 'first'}


def test_interrupted_reply_is_archived_but_not_presented_as_complete(tmp_path):
    rt, _ = runtime([[StreamChunk(text='unfinished')]])
    history = conversation.Conversation.open(root=tmp_path)
    with pytest.raises(RuntimeError, match='incomplète'):
        list(conversation.respond(rt, 'model', history, 'question'))
    reopened = conversation.Conversation.open(root=tmp_path)
    assert reopened == []
    assert reopened.state['attempts'][0]['partial_answer'] == 'unfinished'
    assert reopened.state['attempts'][0]['status'] == 'incomplete'


def test_summary_keeps_full_archive_and_retrieves_original_constraint(tmp_path):
    history = conversation.Conversation.open(root=tmp_path)
    history.extend([{'role': 'user', 'content': 'texture spéciale : conserver les couleurs violettes.'},
                    {'role': 'assistant', 'content': 'Compris.'}])
    for i in range(60):
        history.extend([{'role': 'user', 'content': f'{i}: ' + 'détails ordinaires ' * 12},
                        {'role': 'assistant', 'content': 'réponse ordinaire ' * 12}])
    rt, calls = runtime([[StreamChunk(text='violet'), StreamChunk(done=True)]])
    rt.complete = lambda *a, **kw: 'Contraintes initiales : couleurs violettes, demande de texture.'
    before = list(history)
    list(conversation.respond(rt, 'model', history, 'quelles couleurs pour cette texture spéciale ?'))
    assert history[:len(before)] == before
    assert len(calls[0][0]) < len(before)
    assert any('Original archived turn 0' in m['content'] for m in calls[0][0])
    assert conversation.Conversation.open(root=tmp_path) == history


def test_summary_failure_never_drops_original_turns(tmp_path):
    history = conversation.Conversation.open(root=tmp_path)
    history.extend([{'role': role, 'content': 'word ' * 150}
                    for i in range(40) for role in ('user', 'assistant')])
    rt, calls = runtime([])
    rt.complete = lambda *a, **kw: ''
    with pytest.raises(RuntimeError, match='Résumé'):
        list(conversation.respond(rt, 'model', history, 'continue'))
    assert len(history) == 80 and not calls
    assert len(conversation.Conversation.open(root=tmp_path)) == 80
