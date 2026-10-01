from aurora_cli.core.subject_research import _excerpts, _words, research_subject


def test_selects_subject_paragraph_not_first_character():
    text = 'Alice is a rabbit wearing a dress with a blue ribbon and white shoes.\n' + \
           'Boris is a red fox with a large bushy tail and wears a green shirt over brown trousers.'
    assert _excerpts(text, _words('Boris')).startswith('Boris is a red fox')


def test_offline_never_contacts_network(monkeypatch):
    monkeypatch.setenv('JOBIA_REFERENCE_WEB', '0')
    assert research_subject('known subject') == []


def test_invented_identity_does_not_need_web():
    assert research_subject('un personnage inventé avec des ailes violettes') == []


def test_hyphenated_names_match_without_character_specific_rules():
    assert _words('Spider-Man') == _words('spiderman')


def test_incidental_mention_in_unrelated_article_is_not_evidence(monkeypatch):
    from types import SimpleNamespace
    from aurora_cli.core import subject_research
    class Client:
        def __init__(self, *a, **kw): pass
        def __enter__(self): return self
        def __exit__(self, *a): pass
        def get(self, *a, **kw):
            return SimpleNamespace(raise_for_status=lambda: None, json=lambda: {'query': {'pages': {'1': {
                'title': 'An unrelated movie', 'fullurl': 'https://example.test/movie',
                'extract': 'A critic saw Spiderman costumes at parties and discussed an unrelated movie at length.'}}}})
    monkeypatch.setattr(subject_research.httpx, 'Client', Client)
    assert research_subject('figurine de spiderman version noel') == []
