"""Conservative, source-linked review of explicit requirement sections."""
from __future__ import annotations

import re

from .scoring import contains_phrase

HEADINGS = {
    'requirements': 'required', 'required qualifications': 'required',
    'minimum qualifications': 'required', 'required skills': 'required',
    'minimum requirements': 'required',
    'required skills & experience': 'required', 'required skills and experience': 'required',
    'skills & experience required': 'required', 'skills and experience required': 'required',
    'required experience': 'required', 'required knowledge, skills and abilities': 'required',
    'preferred skills & experience': 'preferred', 'preferred skills and experience': 'preferred',
    'must have': 'required', 'must-have': 'required',
    'preferred qualifications': 'preferred', 'preferred skills': 'preferred',
    'preferred': 'preferred',
    'nice to have': 'preferred', 'nice-to-have': 'preferred',
    'qualifications': 'unspecified', 'basic qualifications': 'unspecified',
    'about you': 'candidate_context', "what you'll bring": 'candidate_context',
}

NEGATED_LEVEL = re.compile(
    r"\b(?:not|never|isn't|aren't|no longer)\s+(?:strictly\s+|necessarily\s+|absolutely\s+)?"
    r"(?:required|mandatory|essential|necessary|preferred)\b"
    r"|\bnot\s+(?:a\s+)?(?:requirement|prerequisite)\b"
    r"|\bno\s+(?:prior\s+)?(?:experience|degree|certification|education)\s+(?:is\s+)?(?:required|necessary)\b",
    re.IGNORECASE,
)
OPTIONAL_UNDER_REQUIRED = re.compile(
    r'\b(?:optional|preferred|nice[ -]to[ -]have|a plus|may be waived|may be substituted|or equivalent)\b',
    re.IGNORECASE,
)


def shared_source_terms(statement, quote):
    """Literal suggestions for untagged facts; never establish qualification equivalence."""
    words = re.findall(r'[a-z][a-z0-9+#-]*', statement.casefold())
    stop = set('a an the and or of in on to for with by from at as is are was were be been being have has had that this these those which who we i my our their it its across through using used built led managed experience skills ability work working team teams support supporting years'.split())
    technologies = set('python sql java aws azure gcp rag llm llms bedrock langchain langgraph selenium docker kubernetes'.split())
    candidates = [word for word in words if word in technologies]
    candidates += [' '.join(pair) for pair in zip(words, words[1:])
                   if all(word not in stop and len(word) > 1 for word in pair)]
    return list(dict.fromkeys(term for term in candidates if contains_phrase(statement, term) and contains_phrase(quote, term)))[:8]


def requirement_evidence(description: str, facts: list[dict]) -> dict:
    """Extract explicit section bullets; keyword links suggest, never establish, fit."""
    rows = []
    section = None
    source_heading = None
    truncated = False
    unrecognized_headings = []
    lines = description.splitlines()
    index = 0
    while index < len(lines):
        raw = lines[index]
        index += 1
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
            oversized = len(quote) > 1000
            # Keep indented wrapping with its bullet, including qualifications
            # such as "not required". Never absorb another bullet or heading.
            indent = len(raw.expandtabs(4)) - len(raw.expandtabs(4).lstrip())
            while index < len(lines):
                following = lines[index]
                text = following.strip()
                following_indent = len(following.expandtabs(4)) - len(following.expandtabs(4).lstrip())
                if (not text or following_indent <= indent
                        or re.match(r'^(?:[-*•]|\d+[.)])\s+\S', text)
                        or re.match(r'^#{1,6}\s+', text)
                        or re.fullmatch(r'\*\*[^*]+\*\*\s*:?', text)
                        or text.casefold().replace('’', "'") in HEADINGS
                        or text.endswith(':')):
                    break
                continuation = following.rstrip()
                if len(quote) + 1 + len(continuation) > 1000:
                    oversized = True
                elif not oversized:
                    quote += '\n' + continuation
                index += 1
            if oversized:
                truncated = True
                continue
        elif section and not bold and not line.endswith(':') and len(line.split()) >= 8 and line.endswith(('.', ';')):
            quote = line
        else:
            if (bold or line.endswith(':') or re.match(r'^#{1,6}\s+', line)) and re.search(r'requir|qualif|skills|experience', candidate):
                unrecognized_headings.append(line)
                truncated = True
            # Never carry a heading into unrecognized prose or a new section.
            section = None
            continue
        if len(quote) > 1000 or len(source_heading) > 1000:
            truncated = True
            continue
        matches = []
        for fact in facts:
            keywords = [word for word in fact.get('keywords', []) if word.strip() and contains_phrase(quote, word)]
            derived = not fact.get('keywords')
            if derived:
                keywords = shared_source_terms(fact['statement'], quote)
            if keywords:
                matches.append({'id': fact['id'], 'statement': fact['statement'], 'matched_keywords': keywords,
                                'match_basis': 'shared_source_phrase' if derived else 'saved_keywords'})
        if len(rows) == 25:
            truncated = True
            break
        normalized_quote = ' '.join(quote.replace('’', "'").split())
        uncertain = section in {'required', 'preferred'} and (
            bool(NEGATED_LEVEL.search(normalized_quote)) or
            (section == 'required' and bool(OPTIONAL_UNDER_REQUIRED.search(normalized_quote))))
        row = {'category': 'unspecified' if uncertain else section, 'source_heading': source_heading,
               'quote': quote, 'suggested_facts': matches}
        if uncertain:
            row['classification_note'] = 'The excerpt contains optional or negated wording; confirm its requirement level with the employer.'
        rows.append(row)
    return {'requirements': rows, 'truncated': truncated, 'unrecognized_headings': unrecognized_headings,
            'notice': 'Source section excerpts and literal term links only. Untagged facts use shared source phrases. Unsupported requirement headings are reported as incomplete coverage. Candidate-related sections and unspecified qualifications are not labeled required or preferred. Some conflicting wording is recognized, but other qualifiers may be missed. Review relevance and confirm requirements with the employer; missing matches do not establish a qualification gap.'}
