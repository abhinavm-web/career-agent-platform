"""Conservative, source-linked review of explicit requirement sections."""
from __future__ import annotations

import re

from .scoring import contains_phrase

HEADINGS = {
    'requirements': 'required', 'required qualifications': 'required',
    'minimum qualifications': 'required', 'required skills': 'required',
    'minimum requirements': 'required',
    'must have': 'required', 'must-have': 'required',
    'preferred qualifications': 'preferred', 'preferred skills': 'preferred',
    'preferred': 'preferred',
    'nice to have': 'preferred', 'nice-to-have': 'preferred',
    'qualifications': 'unspecified', 'basic qualifications': 'unspecified',
    'about you': 'candidate_context', "what you'll bring": 'candidate_context',
}


def requirement_evidence(description: str, facts: list[dict]) -> dict:
    """Extract explicit section bullets; keyword links suggest, never establish, fit."""
    rows = []
    section = None
    source_heading = None
    truncated = False
    for raw in description.splitlines():
        line = raw.strip()
        if not line:
            continue
        # Normalize only recognized heading markup; preserve the source line
        # unchanged for display and matching. Do not strip markup from bullets.
        heading_line = re.sub(r'^#{1,6}\s+', '', line)
        bold = re.match(r'^\*\*(.+?)\*\*(.*)$', heading_line)
        if bold:
            heading_line = bold.group(1) + bold.group(2)
        heading, colon, remainder = heading_line.partition(':')
        candidate = heading.strip().casefold().replace('’', "'")
        normalized_line = heading_line.strip().casefold().replace('’', "'")
        if candidate in HEADINGS and (colon or normalized_line in HEADINGS):
            section = HEADINGS[candidate]
            source_heading = line
            if not remainder.strip():
                continue
            quote = line
        elif section and re.match(r'^(?:[-*•]|\d+[.)])\s+\S', line):
            quote = line
        else:
            # Never carry a heading into unrecognized prose or a new section.
            section = None
            continue
        if len(quote) > 1000 or len(source_heading) > 1000:
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
        rows.append({'category': section, 'source_heading': source_heading, 'quote': quote, 'suggested_facts': matches})
    return {'requirements': rows, 'truncated': truncated,
            'notice': 'Literal section excerpts and keyword links only. Candidate-related sections and unspecified qualifications are not labeled required or preferred. Review relevance and confirm requirements with the employer; missing matches do not establish a qualification gap.'}
