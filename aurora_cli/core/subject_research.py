"""Read-only subject evidence, separate from model guesses and delivery claims."""
from __future__ import annotations

import os
import re
import unicodedata

import httpx


def _words(text):
    text = ''.join(c for c in unicodedata.normalize('NFD', text.casefold())
                   if unicodedata.category(c) != 'Mn')
    text = re.sub(r'(?<=\w)-(?=\w)', '', text)
    return set(re.findall(r'\w{3,}', text)) - {
        'dans', 'avec', 'une', 'des', 'les', 'from', 'the', 'and', 'with',
        'modele', 'model', 'personnage', 'character', 'pour', 'style', 'cree',
        'figurine', 'version', 'edition',
    }


def _excerpts(text, terms):
    # Character lists contain many nearby identities. Prefer the matching
    # section over introductory paragraphs about their companions.
    sections = re.split(r'(?m)^={2,6}\s*(.*?)\s*={2,6}\s*$', text)
    matches = [(len(_words(sections[i]) & terms), sections[i + 1])
               for i in range(1, len(sections) - 1, 2)
               if _words(sections[i]) & terms]
    if matches:
        text = max(matches, key=lambda item: item[0])[1]
    paragraphs = [p.strip() for p in text.split('\n') if len(p.strip()) > 70]
    ranked = sorted(enumerate(paragraphs),
                    key=lambda p: (-len(_words(p[1]) & terms), p[0]))
    selected = [p[:1500] for _, p in ranked[:4] if _words(p) & terms]
    return '\n'.join(selected)[:4500]


def research_subject(subject: str) -> list[dict]:
    """Search encyclopedic evidence in the user's language and its English link.

    Results are untrusted reference data. They cannot request tool execution,
    and their presence is not proof that a generated image depicts the subject.
    Set JOBIA_REFERENCE_WEB=0 for fully offline subject analysis.
    """
    if os.environ.get('JOBIA_REFERENCE_WEB', '1') == '0':
        return []
    if re.search(r'\b(invent[ée]|imaginaire|imaginary|invented|original)\b', subject, re.I):
        return []
    terms = _words(subject)
    if not terms:
        return []
    sources = []
    searches = [('fr', subject)]
    with httpx.Client(timeout=15, headers={'User-Agent':
                      'JOBIA/1.2 (https://github.com/juancodepyandc/aurora-remote-cli)'}) as client:
        for language, query in searches:
            try:
                response = client.get(f'https://{language}.wikipedia.org/w/api.php', params={
                    'action': 'query', 'format': 'json', 'generator': 'search',
                    'gsrsearch': query, 'gsrlimit': 1,
                    'prop': 'extracts|info|langlinks', 'explaintext': 1,
                    'inprop': 'url', 'lllang': 'en',
                })
                response.raise_for_status()
                for page in response.json().get('query', {}).get('pages', {}).values():
                    # An incidental mention in another article is not evidence
                    # about this subject (e.g. a superhero name in a film review).
                    # Prefer no evidence to a real quotation about the wrong topic.
                    if not terms.intersection(_words(page['title'])):
                        continue
                    focus = terms - _words(page['title'])
                    text = _excerpts(page.get('extract', ''), focus or terms)
                    if text:
                        sources.append({'title': page['title'], 'url': page.get('fullurl', ''),
                                        'extract': text})
                    if language == 'fr':
                        translated = next((x.get('*', '') for x in page.get('langlinks', [])
                                           if x.get('lang') == 'en'), '')
                        if translated:
                            name_terms = sorted(terms - _words(page['title']))
                            searches.append(('en', ' '.join(name_terms[:3]) + ' ' + translated))
            except (httpx.HTTPError, ValueError, KeyError, TypeError):
                continue
            if len(searches) == 1:
                searches.append(('en', subject))
            if language == 'en':
                break
    return sources
