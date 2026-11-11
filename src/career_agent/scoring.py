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
    matches = tuple(skill for skill in profile.skills if contains_phrase(text, skill))
    unmentioned_candidate_skills = tuple(skill for skill in profile.skills if skill not in matches)
    skill_points = round(50 * len(matches) / len(profile.skills)) if profile.skills else 0
    place_ok = profile.allow_remote and job.remote
    place_ok = place_ok or any(
        contains_location(job.location, loc)
        for loc in profile.preferred_locations if loc.strip()
    )
    location_points = 10 if place_ok else 0
    location_note = (
        "remote is marked, but geographic eligibility still needs verification" if profile.allow_remote and job.remote else
        "a preferred location appears in the posting" if place_ok else
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
            f"Profile skills mentioned: {len(matches)}/{len(profile.skills)} ({skill_points}/50)",
            f"Skills found in posting: {', '.join(matches)}" if matches else "No configured profile skills found in the posting; this does not establish a qualification gap.",
            f"Location/remote preference: {location_points}/10 — {location_note}.",
            "Keyword score only; it does not assess qualification, work authorization, or hiring likelihood.",
        ),
    )
