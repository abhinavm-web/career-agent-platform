"""Original résumé baseline and a deterministic, offline résumé comparison for one role.

Nothing here calls a model or invents wording. The recommended column contains only
candidate-verified facts, each marked as already in the original résumé or added
for this posting, plus posting requirements that no verified fact addresses yet.
"""
from __future__ import annotations

import re

from .candidate import SECTIONS
from .fit_evidence import requirement_evidence, shared_source_terms
from .scoring import contains_phrase

MAX_ORIGINAL = 20000
MAX_BULLETS_PER_SECTION = 12


class ResumeConflict(Exception):
    """The stored résumé changed after the caller loaded it."""


def _normalize(value: str) -> str:
    return re.sub(r'\s+', ' ', re.sub(r'^[\s\-*•\d.)]+', '', value)).strip().casefold()


def get_original(connection):
    row = connection.execute('SELECT filename, text, saved_at, revision FROM resume_original WHERE id=1').fetchone()
    if row is None:
        return {'text': '', 'filename': '', 'saved_at': None, 'revision': 0}
    return {'text': row['text'], 'filename': row['filename'], 'saved_at': row['saved_at'], 'revision': row['revision']}


def save_original(connection, text, filename, expected_revision):
    if not isinstance(text, str) or not text.strip() or len(text) > MAX_ORIGINAL or '\x00' in text:
        raise ValueError(f'Provide résumé text of 1–{MAX_ORIGINAL:,} characters')
    if not isinstance(filename, str) or len(filename) > 255 or '\x00' in filename:
        raise ValueError('Invalid file name')
    if type(expected_revision) is not int or expected_revision < 0:
        raise ValueError('Provide the reviewed revision')
    with connection:
        connection.execute('BEGIN IMMEDIATE')
        row = connection.execute('SELECT revision FROM resume_original WHERE id=1').fetchone()
        if expected_revision != (row['revision'] if row else 0):
            raise ResumeConflict('Your original résumé changed. Reload it before saving.')
        connection.execute(
            '''INSERT INTO resume_original(id, filename, text, revision) VALUES(1,?,?,1)
               ON CONFLICT(id) DO UPDATE SET filename=excluded.filename, text=excluded.text,
               saved_at=CURRENT_TIMESTAMP, revision=revision+1''', (filename.strip(), text.strip()))
    return get_original(connection)


def _answered_excerpt(job_id, fact):
    """Posting excerpt a verified interview answer was written for, or None.

    Only the (public) posting excerpt is read from the private source note.
    """
    marker = f'Candidate answer for job {job_id}. Posting excerpt: '
    note = fact.get('source_note') or ''
    if not note.startswith(marker):
        return None
    return note[len(marker):].removesuffix(' [truncated]')


def compare(original_text, job, facts, workspace_revision):
    """Return original vs recommended content for ``job`` from verified ``facts`` only."""
    posting = f'{job.title}\n{job.description}'
    haystack = _normalize(original_text)
    by_section = {name: [] for name in SECTIONS}
    unused = 0
    answered = []
    for fact in facts:
        statement = fact['statement']
        if fact.get('keywords'):
            matched = [word for word in fact['keywords'] if word.strip() and contains_phrase(posting, word)]
        else:  # facts added through the interview carry no saved keywords; use shared posting terms
            matched = shared_source_terms(statement, posting)
        in_original = bool(haystack) and _normalize(statement) in haystack
        excerpt = _answered_excerpt(job.id, fact)
        if excerpt:
            answered.append(excerpt)
        if not matched and not in_original and not excerpt:
            unused += 1
            continue
        by_section.setdefault(fact['section'], []).append({
            'fact_id': fact['id'], 'statement': statement, 'matched_terms': matched, 'in_original': in_original,
            'answers': excerpt})
    sections = []
    for name in SECTIONS:
        bullets = sorted(by_section.get(name, []), key=lambda b: (-len(b['matched_terms']), not b['in_original']))
        if bullets:
            sections.append({'section': name, 'bullets': bullets[:MAX_BULLETS_PER_SECTION],
                             'hidden': max(0, len(bullets) - MAX_BULLETS_PER_SECTION)})
    evidence = requirement_evidence(job.description, facts)
    gaps = [{'quote': row['quote'], 'category': row['category'], 'source_heading': row['source_heading']}
            for row in evidence['requirements']
            if not row['suggested_facts'] and not any(row['quote'].startswith(item) for item in answered)]
    return {'sections': sections, 'unused_fact_count': unused, 'gaps': gaps[:25],
            'requirements_truncated': evidence['truncated'], 'workspace_revision': workspace_revision}
