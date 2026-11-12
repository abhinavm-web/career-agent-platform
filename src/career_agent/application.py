"""Deterministic ATS-friendly draft packets using verbatim candidate-confirmed facts.

No generative model is used. Neither this module nor the review graph submits
an application or answers a legal/work-authorization screening question.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
from pathlib import Path
from typing import Any

from .candidate import FactBank, SECTIONS, VerifiedFact
from .models import CandidateProfile, Job
from .scoring import contains_phrase, score_job
from .sponsor_evidence import FilingEvidence, sponsorship_report

HEADINGS = {
    "experience": "Relevant Experience",
    "projects": "Projects",
    "skills": "Skills",
    "education": "Education",
    "certifications": "Certifications",
}
FORMAT_VERSION = 2
MAX_TAILORED_FACTS = 40


def relevance(job: Job, fact: VerifiedFact) -> int:
    text = f"{job.title}\n{job.description}"
    return sum(1 for keyword in fact.keywords if contains_phrase(text, keyword))


def select_facts(job: Job, bank: FactBank, limit: int = 12) -> tuple[VerifiedFact, ...]:
    if not 1 <= limit <= 100:
        raise ValueError("Fact limit must be between 1 and 100")
    ranked = sorted(
        enumerate(bank.facts),
        key=lambda indexed: (-relevance(job, indexed[1]), indexed[0]),
    )
    selected = [fact for _, fact in ranked[:limit]]
    return tuple(sorted(selected, key=lambda fact: (SECTIONS.index(fact.section), -relevance(job, fact))))


def resume_blocks(bank: FactBank, facts: tuple[VerifiedFact, ...], section_order: tuple[str, ...] = SECTIONS) -> list[dict[str, str]]:
    """Separate trusted document structure from literal candidate-entered text."""
    blocks = [{"kind": "name", "text": bank.name}]
    if bank.resume_contact:
        blocks.append({"kind": "contact", "text": bank.resume_contact})
    if bank.headline:
        blocks.append({"kind": "headline", "text": bank.headline})
    for section in section_order:
        statements = [fact.statement for fact in facts if fact.section == section]
        if statements:
            blocks.append({"kind": "section", "text": HEADINGS[section]})
            blocks.extend({"kind": "fact", "text": statement} for statement in statements)
    return blocks


def _blocks_markdown(blocks: list[dict[str, str]]) -> str:
    parts = []
    for block in blocks:
        kind, value = block['kind'], block['text']
        parts.append(('# ' if kind == 'name' else '\n## ' if kind == 'section' else '- ' if kind == 'fact' else '') + value)
    return '\n'.join(parts).rstrip() + '\n'


def resume_markdown(bank: FactBank, facts: tuple[VerifiedFact, ...], section_order: tuple[str, ...] = SECTIONS) -> str:
    return _blocks_markdown(resume_blocks(bank, facts, section_order))


def export_resume_blocks(packet: dict) -> list[dict[str, str]] | None:
    """Use structured blocks only when they exactly reproduce the reviewed text.

    Legacy packets lack blocks and retain their original line-based rendering.
    """
    if 'resume_blocks' not in packet:
        return None
    blocks = packet['resume_blocks']
    if (not isinstance(blocks, list) or not 1 <= len(blocks) <= 48
            or not all(isinstance(block, dict) and set(block) == {'kind', 'text'}
                       and isinstance(block['kind'], str)
                       and block['kind'] in {'name', 'contact', 'headline', 'section', 'fact'}
                       and isinstance(block['text'], str) and block['text']
                       for block in blocks)
            or blocks[0]['kind'] != 'name'
            or any(block['kind'] == 'section' and block['text'] not in HEADINGS.values() for block in blocks)
            or _blocks_markdown(blocks) != packet['resume_markdown']):
        raise ValueError('Structured resume does not match the reviewed text; regenerate and review the draft')
    return blocks


def cover_note(job: Job, bank: FactBank, facts: tuple[VerifiedFact, ...]) -> str:
    lines = [
        "Dear Hiring Team,",
        "",
        f"I am interested in the {job.title} position at {job.company}.",
    ]
    if facts:
        lines += [
            "",
            "A few relevant examples from my background:",
            *[f"- {fact.statement}" for fact in facts[:3]],
        ]
    lines += [
        "",
        "I would welcome an opportunity to discuss the role and its requirements.",
        "",
        "Sincerely,",
        bank.name,
    ]
    return "\n".join(lines) + "\n"


def packet_fingerprint(packet: dict[str, Any]) -> str:
    payload = {key: value for key, value in packet.items() if key != "packet_sha256"}
    serialized = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


def validate_packet(packet: Any) -> str:
    if not isinstance(packet, dict) or packet.get("version") != FORMAT_VERSION:
        raise ValueError("Unsupported or malformed application packet")
    claimed = packet.get("packet_sha256")
    if not isinstance(claimed, str) or not hmac.compare_digest(claimed, packet_fingerprint(packet)):
        raise ValueError("Application packet changed; regenerate and review the new version")
    if not packet.get("job_id") or not packet.get("resume_markdown"):
        raise ValueError("Application packet is missing required review fields")
    return claimed


def build_packet(
    job: Job,
    preferences: CandidateProfile,
    bank: FactBank,
    filing_records: tuple[FilingEvidence, ...] = (),
    *,
    workspace_revision: int | None = None,
    job_description_revision: int | None = None,
    tailored_fact_ids: list[str] | None = None,
    tailored_section_order: list[str] | None = None,
) -> dict[str, Any]:
    if workspace_revision is not None and (type(workspace_revision) is not int or workspace_revision < 1):
        raise ValueError("workspace_revision must be a positive integer")
    if job_description_revision is not None and (type(job_description_revision) is not int or job_description_revision < 1):
        raise ValueError("job_description_revision must be a positive integer")
    if tailored_section_order is not None and (not isinstance(tailored_section_order, list)
            or len(tailored_section_order) != len(SECTIONS)
            or not all(isinstance(section, str) and section in SECTIONS for section in tailored_section_order)
            or len(set(tailored_section_order)) != len(SECTIONS) or tailored_fact_ids is None):
        raise ValueError('Choose each resume section exactly once')
    section_order = tuple(tailored_section_order) if tailored_section_order is not None else SECTIONS
    if tailored_fact_ids is None:
        selected = select_facts(job, bank)
    else:
        claims = {fact.fact_id: fact for fact in bank.facts}
        if (not isinstance(tailored_fact_ids, list) or not 1 <= len(tailored_fact_ids) <= MAX_TAILORED_FACTS
                or not all(isinstance(item, str) and item in claims for item in tailored_fact_ids)
                or len(set(tailored_fact_ids)) != len(tailored_fact_ids)):
            raise ValueError(f'Select 1–{MAX_TAILORED_FACTS} distinct verified career facts')
        selected = tuple(sorted((claims[item] for item in tailored_fact_ids), key=lambda fact: section_order.index(fact.section)))
    selected_ids = {fact.fact_id for fact in selected}
    fit = score_job(job, preferences)
    packet = {
        "version": FORMAT_VERSION,
        "job_id": job.id,
        "job_title": job.title,
        "employer": job.company,
        "job_url": job.url,
        "candidate_name": bank.name,
        "fit_score": fit.score,
        "fit_reasons": list(fit.reasons),
        "candidate_skills_not_mentioned_in_posting": list(fit.candidate_skills_not_mentioned_in_posting),
        "qualification_gaps_assessed": False,
        "selected_fact_ids": [fact.fact_id for fact in selected],
        "fact_evidence": [{
            "id": fact.fact_id,
            "section": fact.section,
            "statement": fact.statement,
            "matched_keywords": [keyword for keyword in fact.keywords
                                 if contains_phrase(f"{job.title}\n{job.description}", keyword)],
        } for fact in selected],
        "omitted_fact_ids": [fact.fact_id for fact in bank.facts if fact.fact_id not in selected_ids],
        "resume_markdown": resume_markdown(bank, selected, section_order),
        "resume_blocks": resume_blocks(bank, selected, section_order),
        "cover_note": cover_note(job, bank, selected),
        "sponsorship": sponsorship_report(job, filing_records),
        "review_questions": [
            "Verify that the vacancy still exists on the employer's official website.",
            "Confirm every resume and cover-note claim against your own records.",
            "Check that no important qualifying or disqualifying information was omitted.",
            "Candidate skills absent from the posting are not qualification gaps.",
            "Personally answer all work-authorization and sponsorship questions truthfully.",
            "Independently confirm sponsorship policy for this specific position.",
            "Review salary, location, application consent and document accuracy.",
        ],
        "status": "awaiting_human_review",
        "submission_sent": False,
    }
    if workspace_revision is not None:
        # Bind the draft/review fingerprint to the candidate facts and preferences
        # version that produced the packet.
        packet["candidate_workspace_revision"] = workspace_revision
    if job_description_revision is not None:
        packet["job_description_revision"] = job_description_revision
    if tailored_fact_ids is not None:
        packet["tailored_fact_ids"] = list(tailored_fact_ids)
        # This is the verified fact bank at drafting time, not the uploaded résumé.
        # It intentionally excludes private source notes and remains inside the
        # fingerprinted packet so comparisons survive subsequent profile edits.
        packet["baseline_fact_evidence"] = [
            {"id": fact.fact_id, "section": fact.section, "statement": fact.statement}
            for fact in sorted(bank.facts, key=lambda fact: SECTIONS.index(fact.section))
        ]
    if tailored_section_order is not None:
        packet["tailored_section_order"] = list(tailored_section_order)
    packet["packet_sha256"] = packet_fingerprint(packet)
    return packet


def _reject_symlink_components(destination: Path) -> None:
    """Reject symlinked files and ancestors, including parents of relative paths.

    This check is suitable for our local, single-user CLI. Do not treat it as
    protection against an attacker concurrently changing filesystem paths.
    """
    absolute_path = destination.absolute()
    for component in (absolute_path, *absolute_path.parents):
        if component.is_symlink():
            raise ValueError(f"Refusing a symbolic link in application packet path: {component}")


def save_packet(packet: dict[str, Any], path: str | Path) -> Path:
    validate_packet(packet)
    destination = Path(path)
    # Check *before* creating parent directories: pathlib.mkdir would otherwise
    # follow a preexisting directory symlink and write outside the intended tree.
    _reject_symlink_components(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    _reject_symlink_components(destination)
    contents = json.dumps(packet, indent=2, ensure_ascii=False) + "\n"
    # O_NOFOLLOW protects the final file against symlink replacement on POSIX.
    flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC | getattr(os, "O_NOFOLLOW", 0)
    fd = os.open(destination, flags, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        handle.write(contents)
    destination.chmod(0o600)
    return destination


def load_packet(path: str | Path) -> dict[str, Any]:
    packet = json.loads(Path(path).read_text(encoding="utf-8"))
    validate_packet(packet)
    return packet
