"""Conservative job-level and historical employer sponsorship signals."""
from __future__ import annotations

import csv
import re
from dataclasses import dataclass
from pathlib import Path

from .models import Job

EXCLUSIONS = (
    r"\bno (?:visa )?sponsorship\b",
    r"\b(?:do not|does not|cannot|can't|will not|unable to) (?:offer|provide|support)?\s*(?:visa )?sponsorship\b",
    r"\bwithout (?:current or future )?(?:visa )?sponsorship\b",
    r"\bsponsorship (?:is )?not available\b",
    r"\bmust be (?:legally )?authorized to work (?:in the (?:united states|us|u\.s\.))? without (?:visa )?sponsorship\b",
)
POSITIVES = (
    r"\bvisa sponsorship (?:is )?(?:available|provided|offered)\b",
    r"\bwe (?:will|can) sponsor\b",
    r"\bh-1b sponsorship (?:is )?(?:available|provided|offered)\b",
)


@dataclass(frozen=True)
class Sponsorship:
    status: str
    reason: str


def load_historical_evidence(path: str | Path | None) -> set[str]:
    """Accept employer names only with a cited source URL; no sponsorship inference."""
    if path is None:
        return set()
    employers = set()
    with Path(path).open(encoding="utf-8-sig", newline="") as file:
        for row in csv.DictReader(file):
            employer = (row.get("employer") or "").strip()
            source = (row.get("source_url") or "").strip()
            if employer and source.startswith(("https://", "http://")):
                employers.add(employer.casefold())
    return employers


def assess_sponsorship(job: Job, historical_employers: set[str] | None = None) -> Sponsorship:
    policy = h1b_policy(job.description)
    if policy['policy'] == 'conflicting':
        return Sponsorship('conflicting', policy['reason'])
    if policy['policy'] == 'excluded':
        return Sponsorship(
            "explicit_exclusion",
            "Posting appears to exclude sponsorship; verify details with the employer.",
        )
    if policy['policy'] in {'possible', 'transfer_only', 'transfer_supported'}:
        return Sponsorship(
            "posting_claims_sponsorship",
            "Posting mentions sponsorship; written confirmation for this job is still necessary.",
        )
    if policy['policy'] == 'conditional':
        return Sponsorship('conditional', policy['reason'])
    if job.company.casefold() in (historical_employers or set()):
        return Sponsorship(
            "historical_only",
            "Historical employer filing evidence does not establish sponsorship for this role.",
        )
    return Sponsorship("unknown", "No verified job-level sponsorship commitment found.")


def h1b_policy(description: str, need: str = 'any') -> dict:
    """Explain recognized posting language without deciding immigration eligibility.

    A generic sponsorship claim does not establish H-1B, new-cap, or transfer support.
    Mixed/conditional language is retained for human review, not turned into a yes.
    """
    import html

    cleaned = html.unescape(re.sub(r'<[^>]*>', ' ', description))
    cleaned = cleaned.translate(str.maketrans({'–': '-', '‑': '-', '’': "'"}))
    cleaned = re.sub(r'\bU\.S\.', 'US', cleaned, flags=re.I)
    sentences = [s.strip() for s in re.split(r'(?<=[.!?])\s+|\n+', cleaned) if s.strip()]
    negative, positive, conditional, transfer = [], [], [], []
    transfer_support = []
    restriction = []
    for sentence in sentences:
        value = ' '.join(sentence.lower().split())
        if re.search(r'\b(?:u\.?s\.? citizens? only|(?:must be|requires?) (?:a )?(?:u\.?s\.? citizen|citizenship)|citizenship (?:is )?required)\b', value):
            restriction.append(sentence)
        if not re.search(r'sponsor|h-?1b(?!1)', value):
            continue
        if re.search(r'h-?1b1|e-?3\b', value) and not re.search(r'h-?1b(?!1)', value):
            conditional.append(sentence)
            continue
        if value.endswith('?') or re.search(r'\b(?:do you|will you|would you) (?:now or in the future )?(?:require|need)\b', value):
            conditional.append(sentence)
            continue
        transfer_only = bool(re.search(r'h-?1b(?!1) transfers? only|only (?:support|accept|sponsor) h-?1b(?!1) transfers?', value))
        supports_transfer = bool(re.search(r'h-?1b(?!1) transfers? (?:are )?(?:accepted|supported|welcome|available)', value))
        if transfer_only:
            transfer.append(sentence)
        elif supports_transfer:
            transfer_support.append(sentence)
        transfer_exclusion = re.compile(
            r"\bh-?1b(?!1) transfers? (?:are )?(?:not (?:currently )?(?:accepted|supported|available)|unavailable)\b"
            r"|\bno h-?1b(?!1) transfers?\b"
            r"|\b(?:cannot|can't|will not|won't|does not|do not|unable to) (?:currently )?(?:sponsor|support|accept|offer|provide) h-?1b(?!1) transfers?\b")
        transfer_excluded = bool(transfer_exclusion.search(value))
        # Do not let generic patterns reclassify a transfer-specific phrase as a
        # blanket exclusion; retain other independent restrictions in the sentence.
        general_value = transfer_exclusion.sub(' ', value)
        is_negative = any(re.search(pattern, general_value) for pattern in EXCLUSIONS) or bool(re.search(
            r"\b(?:cannot|can't|will not|won't|does not|do not|unable to) (?:currently )?sponsor\b|\bnot (?:eligible|available) for (?:visa )?sponsorship\b|\bno h-?1b(?!1)(?: visa)?(?: sponsorship)?\b|\b(?:sponsorship|h-?1b(?!1)(?: sponsorship)?) (?:is )?(?:not (?:currently )?(?:offered|provided|supported|available)|unavailable)\b", general_value))
        is_negative = is_negative or bool(re.search(
            r'\b(?:not (?:currently )?(?:offering|providing) (?:visa |h-?1b )?sponsorship|(?:must|will|should) not (?:now or in the future )?(?:require|need) (?:visa )?sponsorship|(?:but|however)[, ]+not for (?:this|the) (?:role|position))\b', general_value))
        is_negative = is_negative or bool(re.search(
            r'\b(?:except|excluding|other than) h-?1b(?!1)\b|\bnot (?:currently )?sponsoring\b|\bnot able to (?:provide sponsorship|sponsor)\b', general_value))
        is_positive = any(re.search(pattern, value) for pattern in POSITIVES) or bool(re.search(
            r'\bh-?1b(?!1)(?: visa)? (?:sponsorship )?(?:is )?(?:available|provided|offered|supported|welcome)|\bsponsorship (?:is )?(?:available|provided|offered)\b', value))
        is_conditional = bool(re.search(r'\b(?:may|might|if|case.by.case|consider|considered|depending|eligible candidates|not guaranteed|cannot guarantee|can\x27t guarantee|no guarantee)\b|no (?:visa )?sponsorship (?:is )?(?:required|needed)', value))
        # "Only" can restrict support to a specific group, location, or role.
        # The recognized "H-1B transfers only" case retains its own category.
        is_conditional = is_conditional or bool(re.search(r'\bonly (?:for|if|when|to)\b|\bfor\b[^.!?]{1,120}\bonly\b', value))
        is_conditional = is_conditional or (not transfer_only and bool(re.search(r'\bonly\b', value)))
        no_requirement = bool(re.search(r'no (?:visa )?sponsorship (?:is )?(?:required|needed|restrictions)', value))
        # A transfer-specific restriction says nothing about new sponsorship.
        # Keep mixed claims conflicting, and apply the restriction to a transfer need.
        if transfer_excluded and not is_negative:
            if need == 'transfer' or (is_positive and re.search(r'\b(?:but|however)\b', value)):
                is_negative = True
            else:
                is_conditional = True
        if is_negative and not no_requirement:
            negative.append(sentence)
            if is_positive and re.search(r'\b(?:but|however)\b', value):
                positive.append(sentence)
        elif is_conditional or no_requirement:
            conditional.append(sentence)
        elif is_positive and not transfer_only:
            positive.append(sentence)
        elif not transfer_only and not supports_transfer:
            conditional.append(sentence)
    quotes = list(dict.fromkeys(negative + restriction + positive + transfer + transfer_support + conditional))
    explicit_new = any(re.search(r'\bnew (?:applicants|candidates|petitions|(?:h-?1b )?sponsorship)\b', s.lower()) for s in positive)
    if ((negative or restriction) and (positive or transfer or transfer_support)) or (transfer and explicit_new):
        policy, reason = 'conflicting', 'Posting contains both support and restriction language; obtain role-specific clarification.'
    elif negative or restriction:
        policy, reason = 'excluded', 'Posting appears to restrict sponsorship or citizenship; historical filings do not override it.'
    elif conditional:
        policy, reason = 'conditional', 'Posting wording is conditional, a question, or unclear; no sponsorship commitment established.'
    elif transfer:
        policy = 'transfer_only'
        reason = ('Posting mentions H-1B transfers, not new sponsorship. This does not meet a new-sponsorship preference.' if need == 'new'
                  else 'Posting mentions H-1B transfers; confirm applicability, conditions and current role availability.')
    elif positive:
        policy, reason = 'possible', 'Posting mentions sponsorship. Confirm H-1B category and new/transfer support for this specific role.'
    elif transfer_support:
        policy, reason = 'transfer_supported', 'Posting mentions transfer support without saying transfers only. New sponsorship is unspecified; confirm the applicable path with the employer.'
    else:
        policy, reason = 'unknown', 'No recognized role-specific H-1B sponsorship policy. Absence of a statement is not an exclusion.'
    return {'policy': policy, 'reason': reason, 'quotes': quotes[:10],
            'potential_match': policy == 'possible' or (policy in {'transfer_only', 'transfer_supported'} and need != 'new')}
