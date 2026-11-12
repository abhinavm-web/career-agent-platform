"""Limited review prompts for potentially changed factual claims, never an approval."""
from __future__ import annotations

import re

NUMBER = re.compile(r'(?<![\w])[-+]?\d+(?:[,.]\d+)*(?:\s*%)?(?![\w])')
NEGATION = re.compile(r"\b(?:no|not|never|without|neither|nor|didn't|doesn't|wasn't|weren't)\b", re.IGNORECASE)
HIGHER_CLAIMS = re.compile(r'\b(?:led|managed|directed|founded|certified|expert|senior|director|guaranteed|increased|decreased|reduced|eliminated)\b', re.IGNORECASE)


def wording_review_warnings(source: str, wording: str) -> list[str]:
    """Flag selected changes for review; an empty list does not imply factual fidelity."""
    source_numbers = {re.sub(r'\s+', '', x) for x in NUMBER.findall(source)}
    proposed_numbers = {re.sub(r'\s+', '', x) for x in NUMBER.findall(wording)}
    warnings = []
    if proposed_numbers - source_numbers:
        warnings.append('New numeric or percentage text needs verification against the source.')
    if bool(NEGATION.search(source)) != bool(NEGATION.search(wording)):
        warnings.append('Negation changed or disappeared; check that the meaning is unchanged.')
    added = {x.casefold() for x in HIGHER_CLAIMS.findall(wording)} - {x.casefold() for x in HIGHER_CLAIMS.findall(source)}
    if added:
        warnings.append('Potentially stronger role or outcome wording needs verification against the source.')
    return warnings
