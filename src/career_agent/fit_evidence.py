"""Conservative, source-linked review of explicit requirement sections."""
from __future__ import annotations

import re

from .scoring import contains_phrase

HEADINGS = {
    'requirements': 'required', 'required qualifications': 'required',
    'minimum qualifications': 'required', 'required skills': 'required',
    'must have': 'required', 'must-have': 'required',
    'preferred qualifications': 'preferred', 'preferred skills': 'preferred',
    'nice to have': 'preferred', 'nice-to-have': 'preferred',
}


def requirement_evidence(description: str, facts: list[dict]) -> dict:
    """Extract explicit section bullets; keyword links suggest, never establish, fit."""
    rows = []
    section = None
    truncated = False
    for raw in description.splitlines():
        line = raw.strip()
        if not line:
            continue
        heading, colon, remainder = line.partition(':')
        candidate = heading.strip().casefold()
        if candidate in HEADINGS and (colon or line.casefold() in HEADINGS):
            section = HEADINGS[candidate]
            if not remainder.strip():
                continue
            quote = line
        elif section and re.match(r'^(?:[-*•]|\d+[.)])\s+\S', line):
            quote = line
        else:
            # Never carry a heading into unrecognized prose or a new section.
            section = None
            continue
        if len(quote) > 1000:
            truncated = True
            continue
        matches = []
        for fact in facts:
            keywords = [word for word in fact.get('keywords', []) if word.strip() and contains_phrase(quote, word)]
            if keywords:
                matches.append({'id': fact['id'], 'statement': fact['statement'], 'matched_keywords': keywords})
        if len(rows) == 25:
            truncated = True
            break
        rows.append({'category': section, 'quote': quote, 'suggested_facts': matches})
    return {'requirements': rows, 'truncated': truncated,
            'notice': 'Literal section excerpts and keyword links only. Review relevance and confirm requirements with the employer; missing matches do not establish a qualification gap.'}
