"""Explainable keyword matching, not a prediction of hiring outcomes."""
from __future__ import annotations

import re
from dataclasses import dataclass

from .models import CandidateProfile, Job

STOPWORDS = {"and", "or", "the", "of", "for", "a", "in", "to"}


@dataclass(frozen=True)
class Fit:
    score: int
    matched_skills: tuple[str, ...]
    missing_skills: tuple[str, ...]
    reasons: tuple[str, ...]


def words(value: str) -> set[str]:
    return {word for word in re.findall(r"[a-z0-9]+", value.lower()) if word not in STOPWORDS}


def contains_phrase(text: str, phrase: str) -> bool:
    escaped = re.escape(phrase.strip().lower()).replace(r"\ ", r"\s+")
    return bool(escaped and re.search(
        r"(?<![a-z0-9])" + escaped + r"(?![a-z0-9])", text.lower()
    ))


def score_job(job: Job, profile: CandidateProfile) -> Fit:
    """Score title (40), known skill mentions (50), preferred location (10)."""
    actual_title = words(job.title)
    overlap = max(
        (len(actual_title & words(target)) / len(words(target))
         for target in profile.target_titles if words(target)),
        default=0.0,
    )
    title_points = round(40 * overlap)
    text = f"{job.title}\n{job.description}"
    matches = tuple(skill for skill in profile.skills if contains_phrase(text, skill))
    missing = tuple(skill for skill in profile.skills if skill not in matches)
    skill_points = round(50 * len(matches) / len(profile.skills)) if profile.skills else 0
    place_ok = profile.allow_remote and job.remote
    place_ok = place_ok or any(
        loc.casefold() in job.location.casefold()
        for loc in profile.preferred_locations if loc.strip()
    )
    location_points = 10 if place_ok else 0
    return Fit(
        score=min(100, title_points + skill_points + location_points),
        matched_skills=matches,
        missing_skills=missing,
        reasons=(
            f"Title overlap: {title_points}/40",
            f"Profile skills mentioned: {len(matches)}/{len(profile.skills)} ({skill_points}/50)",
            f"Location/remote preference: {location_points}/10",
        ),
    )
