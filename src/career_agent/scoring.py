"""Explainable keyword matching, not a prediction of hiring outcomes."""
from __future__ import annotations

import re
from dataclasses import dataclass
from decimal import Decimal

from .models import CandidateProfile, Job

STOPWORDS = {"and", "or", "the", "of", "for", "a", "in", "to"}

_PAY_NUMBER = r"(?:\d{1,3}(?:,\d{3}){1,2}|\d{1,9}(?:\.\d{1,3})?)(?:k)?"
_ANNUAL_BASE = re.compile(
    rf"^(?=.*(?:\bUSD\b|US\$))\s*(?:annual\s+)?base\s+salary\s*:\s*(?:USD\s*|US)?\$(?P<low>{_PAY_NUMBER})"
    rf"(?:\s*(?:-|–|—|to)\s*(?:USD\s*|US)?\$(?P<high>{_PAY_NUMBER}))?"
    r"\s*(?:per\s+(?:year|annum)|/\s*(?:year|yr)|annually|a\s+year)(?:\s+USD)?\s*\.?\s*$",
    re.IGNORECASE,
)


def _annual_base_range(description: str):
    """Recognize one exact annual base salary line; never infer a pay period or currency."""
    lines = description.splitlines()
    monetary = [line for line in lines if re.search(r"[$€£¥]\s*\d|\b(?:USD|CAD|EUR|GBP)\s*\d", line, re.IGNORECASE)]
    if len(monetary) != 1:
        return None
    match = _ANNUAL_BASE.fullmatch(monetary[0])
    if not match:
        return None
    def number(raw):
        raw = raw.lower().replace(',', '')
        amount = Decimal(raw[:-1]) * 1000 if raw.endswith('k') else Decimal(raw)
        return int(amount) if amount == amount.to_integral_value() else 0
    low = number(match.group('low'))
    high = number(match.group('high') or match.group('low'))
    if not 0 < low <= high <= 100_000_000:
        return None
    return low, high, monetary[0].strip()


@dataclass(frozen=True)
class Fit:
    score: int
    matched_skills: tuple[str, ...]
    candidate_skills_not_mentioned_in_posting: tuple[str, ...]
    reasons: tuple[str, ...]


def words(value: str) -> set[str]:
    return {word for word in re.findall(r"[a-z0-9]+", value.lower()) if word not in STOPWORDS}


def contains_phrase(text: str, phrase: str) -> bool:
    escaped = re.escape(phrase.strip().lower()).replace(r"\ ", r"\s+")
    # C, C++ and C# are separate languages, not interchangeable mentions.
    name = phrase.strip().lower()
    suffix = (r"(?![a-z0-9+#])" if name == "c" else
              r"(?![a-z+#])" if name in {"c++", "c#"} else r"(?![a-z0-9])")
    return bool(escaped and re.search(
        r"(?<![a-z0-9])" + escaped + suffix, text.lower()
    ))


def contains_location(location: str, preference: str) -> bool:
    """Match literal location phrases on token boundaries, not inside words."""
    phrase = re.escape(preference.strip()).replace(r"\ ", r"\s+")
    return bool(phrase and re.search(r"(?<!\w)" + phrase + r"(?!\w)", location, re.IGNORECASE))


def title_level_evidence(job: Job, profile: dict) -> dict | None:
    """Compare candidate-supplied level phrases literally; never infer seniority."""
    phrases = profile.get("preferred_title_levels", [])
    if not phrases:
        return None
    matches = [phrase for phrase in phrases if contains_phrase(job.title, phrase)]
    return {"status": "matched" if matches else "unknown", "title": job.title,
            "matched_phrases": matches,
            "notice": "Literal title-phrase comparison only; titles and levels vary. No match does not establish seniority or suitability."}


def _arrangement_label(description: str) -> tuple[str, str]:
    """Return only exact, unambiguous labels; all other labeled text is unknown."""
    labels = [line.strip() for line in description.splitlines() if re.match(
        r"^(?:work arrangement|workplace type|work location type)\s*:", line.strip(), re.IGNORECASE)]
    if not labels:
        return "absent", ""
    match = re.fullmatch(r"(?:work arrangement|workplace type|work location type)\s*:\s*(remote|hybrid|on[- ]site)\s*\.?",
                         labels[0], re.IGNORECASE) if len(labels) == 1 else None
    if not match:
        return "ambiguous", ""
    return match.group(1).casefold(), labels[0][:200]


def remote_evidence(job: Job) -> dict[str, str]:
    """Describe conflicting saved labels without claiming current arrangement."""
    arrangement, quote = _arrangement_label(job.description)
    if job.remote and arrangement in {"hybrid", "on-site", "on site"}:
        return {"status": "conflicting", "detail": f"The saved job is marked remote, but the saved posting says {quote}. Confirm the arrangement with the employer."}
    if job.remote and arrangement == "ambiguous":
        return {"status": "ambiguous", "detail": "The saved job is marked remote, but work-arrangement labels are ambiguous or multiple. Confirm the arrangement with the employer."}
    if job.remote:
        return {"status": "source_marked", "detail": "The saved job is marked remote; confirm arrangement and geographic eligibility with the employer."}
    return {"status": "unknown", "detail": "The saved remote flag is not set. Check the posting text and confirm the work arrangement with the employer."}


def _remote_constraint(job: Job) -> dict[str, str]:
    """Compare only explicit, simple arrangement labels; other prose is unknown."""
    arrangement, quote = _arrangement_label(job.description)
    prefix = {"constraint": "Fully remote required"}
    if arrangement == "ambiguous":
        return {**prefix, "status": "unknown", "detail":
                "The saved posting has ambiguous or multiple work-arrangement labels. "
                "Review the original posting and confirm the location and arrangement with the employer."}
    if arrangement == "remote":
        return {**prefix, "status": "source_marked", "detail":
                f"Saved posting: {quote}. This is a source label, not confirmation of geographic eligibility; verify with the employer."}
    if arrangement != "absent":
        conflict = " The source also marks this role remote, so the signals conflict." if job.remote else ""
        return {**prefix, "status": "flagged", "detail":
                f"Saved posting: {quote}.{conflict} This may not meet your fully remote preference; verify the current arrangement with the employer."}
    return {**prefix, "status": "source_marked" if job.remote else "unknown", "detail":
            ("The saved source marks this job remote. Confirm remote scope and geographic eligibility with the employer."
             if job.remote else "The saved source does not confirm remote work. This is not proof the role is on-site.")}


def constraint_evidence(job: Job, profile: dict) -> list[dict[str, str]]:
    """Show explicit candidate constraints without inferring a rejection from absent data."""
    results = []
    if profile.get("require_remote", False):
        results.append(_remote_constraint(job))
    exclusions = profile.get("excluded_companies", [])
    if exclusions:
        normalize = lambda value: " ".join(value.casefold().split())
        exact = next((name for name in exclusions if normalize(name) == normalize(job.company)), None)
        results.append({
            "constraint": "Excluded employer names",
            "status": "flagged" if exact else "unknown",
            "detail": (f"The saved employer name exactly matches your exclusion: {exact}. Review this role before applying."
                       if exact else "No exact excluded name matched. Affiliates, aliases and legal identities are not checked."),
        })
    minimum = profile.get("minimum_annual_base_usd")
    if minimum is not None:
        pay = _annual_base_range(job.description)
        if pay is None:
            results.append({"constraint": "Minimum annual base salary",
                            "status": "unknown",
                            "detail": "The saved posting does not contain one unambiguous annual USD base-salary line. Confirm compensation directly."})
        else:
            low, high, quote = pay
            flagged = high < minimum
            results.append({"constraint": "Minimum annual base salary",
                            "status": "flagged" if flagged else "source_marked",
                            "detail": (f"Saved posting: {quote}. " +
                                       (f"The stated maximum is below your ${minimum:,} minimum." if flagged else
                                        f"The stated amount or range reaches your ${minimum:,} minimum; actual offers and eligibility are not confirmed."))})
    return results


def score_job(job: Job, profile: CandidateProfile) -> Fit:
    """Score title (40), existing profile-skill mentions (50), and location (10).

    This does not extract job requirements or infer qualification gaps.
    """
    actual_title = words(job.title)
    overlap = max(
        (len(actual_title & words(target)) / len(words(target))
         for target in profile.target_titles if words(target)),
        default=0.0,
    )
    title_points = round(40 * overlap)
    text = f"{job.title}\n{job.description}"
    # Matching ignores capitalization and whitespace runs, so use those same
    # semantics to avoid giving repeated spellings extra weight. Keep the
    # candidate's first spelling for explanations; never rewrite their profile.
    unique_skills = {}
    for skill in profile.skills:
        normalized = re.sub(r"\s+", " ", skill.strip()).lower()
        if normalized:
            unique_skills.setdefault(normalized, skill.strip())
    skills = tuple(unique_skills.values())
    matches = tuple(label for key, label in unique_skills.items() if contains_phrase(text, key))
    unmentioned_candidate_skills = tuple(label for key, label in unique_skills.items() if not contains_phrase(text, key))
    skill_points = round(50 * len(matches) / len(skills)) if skills else 0
    arrangement, _ = _arrangement_label(job.description)
    remote_uncertain = job.remote and arrangement not in {"absent", "remote"}
    remote_match = profile.allow_remote and job.remote and not remote_uncertain
    location_match = any(contains_location(job.location, loc)
                         for loc in profile.preferred_locations if loc.strip())
    location_points = 10 if remote_match or location_match else 0
    location_note = (
        "a preferred location appears in the posting; remote signals conflict or are ambiguous, so verify the arrangement" if location_match and remote_uncertain else
        "remote signals conflict or are ambiguous; remote credit withheld, verify the arrangement" if profile.allow_remote and remote_uncertain else
        "remote is marked, but geographic eligibility still needs verification" if remote_match else
        "a preferred location appears in the posting" if location_match else
        "no location preference configured; work arrangement was not matched" if not profile.preferred_locations else
        "location is unspecified; work arrangement was not matched" if not job.location.strip() else
        "no preferred location or confirmed remote match; verify the posting"
    )
    return Fit(
        score=min(100, title_points + skill_points + location_points),
        matched_skills=matches,
        candidate_skills_not_mentioned_in_posting=unmentioned_candidate_skills,
        reasons=(
            f"Title overlap: {title_points}/40",
            f"Profile skills mentioned: {len(matches)}/{len(skills)} ({skill_points}/50)",
            f"Skills found in posting: {', '.join(matches)}" if matches else "No configured profile skills found in the posting; this does not establish a qualification gap.",
            f"Location/remote preference: {location_points}/10 — {location_note}.",
            "Keyword score only; it does not assess qualification, work authorization, or hiring likelihood.",
        ),
    )
