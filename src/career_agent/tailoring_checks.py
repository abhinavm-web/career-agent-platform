"""Limited review prompts for potentially changed factual claims, never an approval."""
from __future__ import annotations

import re

from .scoring import contains_phrase

NUMBER = re.compile(r'(?<![\w])[-+]?\d+(?:[,.]\d+)*(?:\s*%)?(?![\w])')
NEGATION = re.compile(r"\b(?:no|not|never|without|neither|nor|cannot|can't|couldn't|isn't|aren't|won't|didn't|doesn't|wasn't|weren't)\b", re.IGNORECASE)
HIGHER_CLAIMS = re.compile(r'\b(?:led|managed|directed|founded|certified|expert|senior|director|guaranteed|increased|decreased|reduced|eliminated|owned|architected|spearheaded)\b', re.IGNORECASE)
QUALIFIED_NUMBER = re.compile(r'\b(?:up to|at most|at least|less than|more than|approximately|roughly|about|around|under|over)\s+\$?\d+(?:[,.]\d+)*(?:\s*%)?', re.IGNORECASE)
# Deliberately limited. These are prompts for human review, not skill extraction.
TECHNOLOGIES = ('Python', 'Rust', 'Java', 'JavaScript', 'TypeScript', 'C++', 'C#', 'SQL',
                'AWS', 'Azure', 'GCP', 'Kubernetes', 'Docker', 'Terraform', 'Spark', 'Snowflake')


def _negated_clauses(text: str) -> list[str]:
    text = text.replace('’', "'")
    clauses = re.split(r'[.!?;\n]+', text)
    return [' '.join(re.findall(r"\w+(?:['’]\w+)?", part.casefold()))
            for part in clauses if NEGATION.search(part)]


def wording_review_warnings(source: str, wording: str) -> list[str]:
    """Flag selected changes for review; an empty list does not imply factual fidelity."""
    source_sequence = [re.sub(r'\s+', '', x) for x in NUMBER.findall(source)]
    proposed_sequence = [re.sub(r'\s+', '', x) for x in NUMBER.findall(wording)]
    source_numbers, proposed_numbers = set(source_sequence), set(proposed_sequence)
    warnings = []
    if proposed_numbers - source_numbers:
        warnings.append('New numeric or percentage text needs verification against the source.')
    if source_numbers == proposed_numbers and len(source_sequence) > 1 and source_sequence != proposed_sequence:
        warnings.append('The order or repetition of numeric text changed; verify dates, ranges and counts.')
    source_qualified = {re.sub(r'\s+', ' ', x.casefold()) for x in QUALIFIED_NUMBER.findall(source)}
    proposed_qualified = {re.sub(r'\s+', ' ', x.casefold()) for x in QUALIFIED_NUMBER.findall(wording)}
    if source_qualified != proposed_qualified:
        warnings.append('A numeric limit or approximation changed; verify the scope against the source.')
    if _negated_clauses(source) != _negated_clauses(wording):
        warnings.append('Negation or its surrounding wording changed; check that the meaning is unchanged.')
    added = {x.casefold() for x in HIGHER_CLAIMS.findall(wording)} - {x.casefold() for x in HIGHER_CLAIMS.findall(source)}
    if added:
        warnings.append('Potentially stronger role or outcome wording needs verification against the source.')
    new_technologies = [term for term in TECHNOLOGIES if contains_phrase(wording, term) and not contains_phrase(source, term)]
    if new_technologies:
        warnings.append('New technology terms need verification against the source: ' + ', '.join(new_technologies) + '.')
    return warnings
