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
    'qualifications/requirements': 'required', 'qualifications / requirements': 'required',
    'qualifications and requirements': 'required', 'standard job requirements': 'required',
    'education, experience & soft skills': 'unspecified',
    'education, experience and soft skills': 'unspecified',
    'education experience & soft skills': 'unspecified',
    'education and experience': 'unspecified', 'skills and experience': 'unspecified',
    'what we look for': 'candidate_context', 'what we are looking for': 'candidate_context',
    'desired characteristics': 'preferred', 'desired qualifications': 'preferred',
    'preferred experience': 'preferred', 'your qualifications': 'unspecified',
}

RESPONSIBILITY_HEADINGS = {
    'responsibilities', 'key responsibilities', 'essential responsibilities',
    'key job responsibilities', 'duties', 'job duties', 'what you will do',
    "what you'll do", "what you'll be doing", 'what you will be doing',
    'your impact', 'your responsibilities',
}
OVERVIEW_HEADINGS = {
    'the role', 'about this job', 'position summary', 'role summary/purpose',
    'role summary', 'about the role', 'overview', 'job description', 'description',
    "who you'll work with", 'who you will work with',
}
CONDITION_HEADINGS = {
    'eligibility requirements', 'work arrangement', 'our way of working',
    'location', 'travel', 'work authorization', 'visa sponsorship',
    'pre-employment background check and drug test',
    'job functions and work environment',
}
# These sections explain the employer or its process, not candidate qualifications.
# A concrete role condition embedded in one is retained separately below.
BOILERPLATE_HEADING = re.compile(
    r'^(?:about us|about the company|about [\w .&-]+|who we are|what we offer|'
    r'our commitment|company|company overview|benefits|.*employee benefits.*|'
    r'compensation(?: and benefits)?|compensation and benefits|target range|'
    r'salary(?: range)?|pay range|equal employment opportunity|eeo(?: and accommodations)?|'
    r'reasonable accommodation(?: notice)?|e-verify and right to work notices)$', re.I)
BOILERPLATE_PROSE = re.compile(
    r'(?:\bis an equal (?:opportunity|employment opportunity) employer\b|'
    r'\bdoes not discriminate on the basis\b|^los angeles county applicants:|'
    r'^criminal history may|^pursuant to the .*fair chance|'
    r'^our inclusive culture|^if you have a disability and need|'
    r'^reasonable accommodations may be|^federal law requires employers to provide reasonable accommodation|'
    r'^the base salary range|^the salary range|^actual (?:starting pay|compensation)|'
    r'^(?:[-*•]\s*)?(?:salary|pay range|target range)\s*:|'
    r'^at .{0,120}our purpose is|'
    r'^salaries are adjusted|^.*prides itself on helping team members thrive|'
    r'^in addition, individuals may be eligible for an annual|'
    r'^.*(?:offers comprehensive benefits|participates in .*e-verify program)|'
    r'^.*(?:right to work poster|e-verify participation poster))', re.I)
CONDITION = re.compile(
    r'\b(?:visa|employment visas?|sponsorship|sponsor individuals|work authori[sz]ation|'
    r'authori[sz]ation to work|authorized to work|security clearance|'
    r'(?:travel|commut\w*|relocat\w*)[^.\n]{0,70}(?:required|requirement|must|ability|willing)|'
    r'(?:required|must|willing|ability)[^.\n]{0,70}(?:travel|commut\w*|relocat\w*)|'
    r'(?:onsite|on-site|in-person|hybrid|remote)[^.\n]{0,70}(?:required|requirement|not available|only)|'
    r'(?:report|work)[^.\n]{0,30}(?:onsite|on-site)|'
    r'(?:offers? (?:are|is) contingent|must be (?:able|willing)|successful completion of a background check))\b',
    re.I)
BULLET = re.compile(r'^(?:[-*•]|\d+[.)])\s+\S')
CANDIDATE_DEMAND = re.compile(
    r'\b(?:(?:you|applicants?|candidates?)\s+(?:must|need|should|shall)|'
    r'must\s+(?:have|hold|possess|demonstrate|complete|obtain)|'
    r'minimum\s+\d+\s*\+?\s*(?:years?|yrs?))\b', re.I)


def _heading_parts(line):
    """Normalize markup for recognition only; never rewrite source quotations."""
    plain = re.sub(r'^#{1,6}\s+', '', line.strip())
    bold = re.match(r'^\*\*(.+?)\*\*(.*)$', plain)
    if bold:
        plain = bold.group(1) + bold.group(2)
    heading, colon, remainder = plain.partition(':')
    key = ' '.join(heading.strip().casefold().replace('’', "'").split())
    marked = bool(bold or re.match(r'^#{1,6}\s+', line.strip()) or line.rstrip().endswith(':'))
    return key, remainder.strip() if colon else '', marked


def _level(category, quote):
    normalized = ' '.join(quote.replace('’', "'").split())
    uncertain = category in {'required', 'preferred'} and (
        NEGATED_LEVEL.search(normalized) or
        (category == 'required' and OPTIONAL_UNDER_REQUIRED.search(normalized)))
    return 'unspecified' if uncertain else category

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


def source_terms(statement):
    """Candidate terms for literal suggestions from untagged facts."""
    words = re.findall(r'[a-z][a-z0-9+#-]*', statement.casefold())
    stop = set('a an the and or of in on to for with by from at as is are was were be been being have has had that this these those which who we i my our their it its across through using used built led managed experience skills ability work working team teams support supporting years'.split())
    technologies = set('python sql java aws azure gcp rag llm llms bedrock langchain langgraph selenium docker kubernetes'.split())
    candidates = [word for word in words if word in technologies]
    candidates += [' '.join(pair) for pair in zip(words, words[1:])
                   if all(word not in stop and len(word) > 1 for word in pair)]
    return list(dict.fromkeys(term for term in candidates if contains_phrase(statement, term)))


def shared_source_terms(statement, quote):
    return [term for term in source_terms(statement) if contains_phrase(quote, term)][:8]


def board_evidence(job, facts):
    """Rank literal links by requirement level, then distinct terms. Preserve source-order ties."""
    text = ' '.join((job.title + '\n' + job.description).lower().split())
    linked = []
    for fact in facts:
        terms = [term for term in fact['keywords'] if ' '.join(term.lower().split()) in text
                 and contains_phrase(text, term)]
        if terms:
            linked.append({**fact, 'keywords': terms})
    rows = requirement_evidence(job.description, linked)['requirements']
    priorities = {'required': 3, 'preferred': 2, 'unspecified': 1, 'candidate_context': 1}
    matches = [(priorities[row['category']], len(link['matched_keywords']), link['id'], link['matched_keywords'])
               for row in rows for link in row['suggested_facts']]
    matches.extend((0, len(fact['keywords']), fact['id'], fact['keywords']) for fact in linked)
    evidence = None
    if matches:
        _, _, fact_id, terms = max(matches, key=lambda match: match[:2])
        fact = next(item for item in linked if item['id'] == fact_id)
        evidence = {key: fact[key] for key in ('id', 'statement', 'section', 'source_note')}
        evidence['matched_terms'] = terms
    # A term match is only a lead, even for a required qualification.
    gap = max((row for row in rows if row['category'] in ('required', 'preferred', 'unspecified')
               and not row.get('classification_note') and not NEGATED_LEVEL.search(row['quote'])),
              key=lambda row: (priorities[row['category']], not row['suggested_facts']), default=None)
    return {'verified_fact': evidence, 'requirement_to_verify': {
        'quote': gap['quote'], 'category': gap['category'], 'term_link': bool(gap['suggested_facts'])
    } if gap else None}


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
        if BOILERPLATE_PROSE.search(line) and not CONDITION.search(line) and not CANDIDATE_DEMAND.search(line):
            section = None
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
    return {'requirements': rows, 'truncated': truncated or not rows, 'unrecognized_headings': unrecognized_headings,
            'notice': 'Source section excerpts and literal term links only. Untagged facts use shared source phrases. Unsupported requirement headings are reported as incomplete coverage. Candidate-related sections and unspecified qualifications are not labeled required or preferred. Some conflicting wording is recognized, but other qualifiers may be missed. Review relevance and confirm requirements with the employer; missing matches do not establish a qualification gap.'}


def requirement_inventory(description: str) -> dict:
    """Account for the whole posting, not just the first recognizable bullets.

    Every substantive source block is retained, including ambiguous prose for an
    explicit contextual/not-a-requirement judgment by the validator. ``complete``
    describes source processing, not semantic certainty or a qualification match.
    Quotes are exact contiguous spans, preserving wrapped bullets and qualifiers.
    """
    if not isinstance(description, str) or not description.strip():
        return {'requirements': [], 'complete': False, 'notices': ['A nonempty saved posting is required.']}
    if len(description) > 100000:
        return {'requirements': [], 'complete': False,
                'notices': ['The posting exceeds 100,000 characters; no complete inventory was produced.']}
    rows = []
    notices = []
    category = 'unspecified'
    heading = ''
    excluded_section = False
    pending = []
    pending_category = category
    pending_heading = heading
    pending_excluded = False

    def flush():
        nonlocal pending, excluded_section, category
        if not pending:
            return
        quote = ''.join(pending).strip()
        pending = []
        if not quote:
            return
        condition = bool(CONDITION.search(quote))
        # Preserve explicit conditions even inside boilerplate (for example a
        # sponsorship exclusion in an equal-opportunity paragraph).
        if not condition and not CANDIDATE_DEMAND.search(quote) and BOILERPLATE_PROSE.search(quote):
            # An unheaded legal/pay paragraph also closes the previous section;
            # trailing salary figures must not inherit "preferred".
            excluded_section = True
            category = 'unspecified'
            return
        if pending_excluded and not condition:
            # A company/benefits section can still hide a real candidate demand.
            # Keep such text for review instead of trusting the heading alone.
            if not CANDIDATE_DEMAND.search(quote) and not re.search(r'\b(?:you (?:must|will|need)|candidates? (?:must|need)|'
                             r'required|requirements?|qualifications?|experience|degree|'
                             r'eligible to apply|eligible to post)\b', quote, re.I):
                return
        if len(rows) >= 300:
            if 'The posting exceeds 300 source items; the remaining content was not inventoried.' not in notices:
                notices.append('The posting exceeds 300 source items; the remaining content was not inventoried.')
            return
        level = 'condition' if condition else pending_category
        level = 'unspecified' if level == 'candidate_context' else level
        level = _level(level, quote)
        rows.append({'id': 'r' + str(len(rows) + 1), 'quote': quote,
                     'category': level, 'source_heading': pending_heading})

    for raw in description.splitlines(keepends=True):
        line = raw.strip()
        if not line:
            flush()
            continue
        key, remainder, marked = _heading_parts(line)
        known = key in HEADINGS or key in RESPONSIBILITY_HEADINGS or key in CONDITION_HEADINGS or key in OVERVIEW_HEADINGS
        is_heading = not BULLET.match(line) and (marked or known or BOILERPLATE_HEADING.fullmatch(key))
        if is_heading:
            flush()
            heading = line
            if key in HEADINGS:
                category = HEADINGS[key]
                excluded_section = False
            elif key in RESPONSIBILITY_HEADINGS:
                category = 'responsibility'
                excluded_section = False
            elif key in CONDITION_HEADINGS:
                category = 'condition'
                excluded_section = False
            elif key in OVERVIEW_HEADINGS:
                # Overview prose can describe either the employer or the role.
                # Retain it for assessment without forcing it into a criterion.
                category = 'unspecified'
                excluded_section = False
            elif BOILERPLATE_HEADING.fullmatch(key):
                category = 'unspecified'
                excluded_section = True
            else:
                # Unknown section labels cannot hide their body. Inherit a
                # responsibility subheading; otherwise keep classification open.
                category = 'responsibility' if category == 'responsibility' and not excluded_section else 'unspecified'
                excluded_section = False
            if not remainder:
                # An unrecognized marked sentence may itself be substantive,
                # e.g. a bold sponsorship condition or a one-line requirement.
                if not known and not excluded_section:
                    pending = [raw]
                    pending_category, pending_heading, pending_excluded = category, heading, False
                continue
        if BULLET.match(line):
            flush()
        elif pending and BULLET.match(pending[0].strip()):
            first_indent = len(pending[0].expandtabs(4)) - len(pending[0].expandtabs(4).lstrip())
            indent = len(raw.expandtabs(4)) - len(raw.expandtabs(4).lstrip())
            # Unindented prose following a bullet is separately accountable.
            if indent <= first_indent:
                flush()
        if not pending:
            pending_category, pending_heading, pending_excluded = category, heading, excluded_section
        pending.append(raw)
    flush()
    if not rows:
        notices.append('No substantive requirement or contextual source items were identified; coverage is unavailable.')
    notices.append('All retained source items require assessment; contextual items must be identified separately and must not inflate qualification coverage.')
    return {'requirements': rows, 'complete': bool(rows) and len(notices) == 1, 'notices': notices}
