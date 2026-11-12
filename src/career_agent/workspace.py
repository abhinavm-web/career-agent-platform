"""Versioned candidate workspace in local SQLite (never in the public repository).

Profiles and unverified notes remain local plaintext for the single-user MVP.
Only separately confirmed, current facts can be used for application drafting.
"""
from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from typing import Any

from .candidate import FactBank, VerifiedFact
from .models import CandidateProfile
from .storage import Store

PROFILE_LISTS = ("target_titles", "skills", "preferred_locations")
FACT_FIELDS = {"id", "section", "statement", "keywords", "source_note", "verified_by_candidate", "derived_from"}
MAX_FACTS = 100


class StaleRevision(ValueError):
    """The workspace changed after the caller read it."""


def blank_workspace() -> dict[str, Any]:
    return {
        "revision": 0,
        "profile": {
            "name": "",
            "headline": "",
            "target_titles": [],
            "skills": [],
            "preferred_locations": [],
            "allow_remote": True,
            "require_remote": False,
            "excluded_companies": [],
            "minimum_annual_base_usd": None,
        },
        "facts": [],
    }


def _validate_profile(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError("profile must be an object")
    if set(value) - {"name", "headline", *PROFILE_LISTS, "allow_remote", "require_remote", "excluded_companies", "minimum_annual_base_usd"}:
        raise ValueError("Unknown profile field")
    name = value.get("name", "")
    headline = value.get("headline", "")
    if not isinstance(name, str) or len(name.strip()) > 120:
        raise ValueError("name must be text of at most 120 characters")
    if not isinstance(headline, str) or len(headline.strip()) > 240:
        raise ValueError("headline must be text of at most 240 characters")
    remote = value.get("allow_remote", True)
    if type(remote) is not bool:
        raise ValueError("allow_remote must be a boolean")
    required_remote = value.get("require_remote", False)
    if type(required_remote) is not bool:
        raise ValueError("require_remote must be a boolean")
    if required_remote and not remote:
        raise ValueError("Include fully remote roles when requiring remote work")
    profile = {"name": name.strip(), "headline": headline.strip()}
    for field in PROFILE_LISTS:
        entries = value.get(field, [])
        if (not isinstance(entries, list) or len(entries) > 40 or
                not all(isinstance(x, str) and 0 < len(x.strip()) <= 100 for x in entries)):
            raise ValueError(f"{field} must contain at most 40 nonempty strings of 100 characters")
        profile[field] = list(dict.fromkeys(item.strip() for item in entries))
    profile["allow_remote"] = remote
    profile["require_remote"] = required_remote
    exclusions = value.get("excluded_companies", [])
    if (not isinstance(exclusions, list) or len(exclusions) > 40 or
            not all(isinstance(x, str) and 0 < len(x.strip()) <= 100 for x in exclusions)):
        raise ValueError("excluded_companies must contain at most 40 nonempty names of 100 characters")
    profile["excluded_companies"] = list(dict.fromkeys(item.strip() for item in exclusions))
    minimum = value.get("minimum_annual_base_usd")
    if minimum is not None and (type(minimum) is not int or not 1 <= minimum <= 10_000_000):
        raise ValueError("Minimum annual base salary must be a whole USD amount from 1 to 10,000,000, or empty")
    profile["minimum_annual_base_usd"] = minimum
    return profile


def _validate_facts(rows: Any) -> list[dict[str, Any]]:
    if not isinstance(rows, list) or len(rows) > MAX_FACTS:
        raise ValueError(f"facts must be a list of no more than {MAX_FACTS} items")
    normalized: list[dict[str, Any]] = []
    ids: set[str] = set()
    for row in rows:
        if not isinstance(row, dict) or set(row) - FACT_FIELDS:
            raise ValueError("Each fact must be an object with recognized fields")
        if "verified_by_candidate" in row and type(row["verified_by_candidate"]) is not bool:
            raise ValueError("verified_by_candidate must be a boolean")
        fact = VerifiedFact.parse({**row, "verified_by_candidate": True})
        if len(fact.source_note) > 500:
            raise ValueError(f"{fact.fact_id}: source_note exceeds 500 characters")
        if len(fact.keywords) > 20 or any(len(keyword) > 80 for keyword in fact.keywords):
            raise ValueError(f"{fact.fact_id}: at most 20 keywords of 80 characters")
        if fact.fact_id in ids:
            raise ValueError("Fact IDs must be unique")
        ids.add(fact.fact_id)
        normalized_fact = {
            "id": fact.fact_id,
            "section": fact.section,
            "statement": fact.statement,
            "keywords": list(fact.keywords),
            "source_note": fact.source_note,
            "verified_by_candidate": row.get("verified_by_candidate", False),
        }
        if "derived_from" in row:
            provenance = row["derived_from"]
            if (not isinstance(provenance, dict) or set(provenance) != {"fact_id", "statement", "job_id", "job_quote"}
                    or not all(isinstance(value, str) and value.strip() for value in provenance.values())
                    or len(provenance["fact_id"]) > 64 or len(provenance["statement"]) > 1000
                    or len(provenance["job_id"]) > 128 or len(provenance["job_quote"]) > 2000):
                raise ValueError("Invalid wording suggestion provenance")
            normalized_fact["derived_from"] = dict(provenance)
        normalized.append(normalized_fact)
    return normalized


def _fact_content(fact: dict[str, Any]) -> tuple:
    """Compare all substantive fields; changes revoke old verification."""
    return (fact["section"], fact["statement"], tuple(fact["keywords"]), fact["source_note"], fact.get("derived_from"))


@dataclass
class CandidateWorkspace:
    store: Store

    def __post_init__(self) -> None:
        self.store.connection.execute("""
            CREATE TABLE IF NOT EXISTS candidate_workspace_versions (
                revision INTEGER PRIMARY KEY AUTOINCREMENT,
                document TEXT NOT NULL,
                change_type TEXT NOT NULL,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
        """)
        self.store.connection.commit()

    def latest(self) -> dict[str, Any]:
        row = self.store.connection.execute(
            "SELECT revision, document FROM candidate_workspace_versions ORDER BY revision DESC LIMIT 1"
        ).fetchone()
        if row is None:
            return blank_workspace()
        value = json.loads(row["document"])
        value["revision"] = row["revision"]
        return value

    def history(self, limit: int = 30) -> list[dict[str, Any]]:
        rows = self.store.connection.execute(
            "SELECT revision,change_type,created_at FROM candidate_workspace_versions "
            "ORDER BY revision DESC LIMIT ?",
            (max(0, min(limit, 100)),),
        ).fetchall()
        return [dict(row) for row in rows]

    def _write(self, expected_revision: int, transform, change_type: str) -> dict[str, Any]:
        if type(expected_revision) is not int or expected_revision < 0:
            raise ValueError("expected_revision must be a nonnegative integer")
        conn = self.store.connection
        conn.execute("BEGIN IMMEDIATE")
        try:
            current = self.latest()
            if current["revision"] != expected_revision:
                raise StaleRevision("Workspace changed; reload before saving")
            updated = transform(current)
            # Store the complete version together in one SQLite transaction.
            document = json.dumps(
                {"profile": updated["profile"], "facts": updated["facts"]},
                ensure_ascii=False, separators=(",", ":"),
            )
            cursor = conn.execute(
                "INSERT INTO candidate_workspace_versions(document,change_type) VALUES(?,?)",
                (document, change_type),
            )
            conn.commit()
            updated["revision"] = cursor.lastrowid
            return updated
        except Exception:
            conn.rollback()
            raise

    def save(self, expected_revision: int, profile: Any, facts: Any) -> dict[str, Any]:
        validated_profile = _validate_profile(profile)
        validated_facts = _validate_facts(facts)

        def transform(current):
            previous = {fact["id"]: fact for fact in current["facts"]}
            for fact in validated_facts:
                old = previous.get(fact["id"])
                if fact.get("derived_from") != (old.get("derived_from") if old else None):
                    raise ValueError("Suggestion provenance cannot be changed through the fact editor")
                # Editing, inserting or explicitly unchecking a fact revokes its verification.
                fact["verified_by_candidate"] = bool(
                    old and old["verified_by_candidate"]
                    and fact["verified_by_candidate"]
                    and _fact_content(fact) == _fact_content(old)
                )
            return {"profile": validated_profile, "facts": validated_facts}

        return self._write(expected_revision, transform, "edited")

    def stage_wording(self, expected_revision: int, job_id: str, fact_id: str, source_statement: str,
                      job_quote: str, wording: str, acknowledged: bool) -> dict[str, Any]:
        """Save a candidate-selected suggestion as an unverified fact, with immutable source snapshot."""
        if acknowledged is not True:
            raise ValueError("Explicit acknowledgement is required to save a suggestion")
        if not all(isinstance(item, str) and item.strip() for item in (job_id, fact_id, source_statement, job_quote, wording)):
            raise ValueError("A source fact, posting excerpt and wording are required")
        if len(job_quote) > 2000:
            raise ValueError("Posting excerpt exceeds 2,000 characters")
        def transform(current):
            if len(current['facts']) >= MAX_FACTS:
                raise ValueError("Career fact limit reached")
            source = next((fact for fact in current['facts'] if fact['id'] == fact_id), None)
            if not source or not source['verified_by_candidate'] or source['statement'] != source_statement:
                raise StaleRevision("Source fact changed or is no longer verified; request a new suggestion")
            job = self.store.get_job(job_id)
            if job_quote not in job.description:
                raise StaleRevision("Posting changed; request a new suggestion")
            proposal = {'id': 'fact-' + uuid.uuid4().hex[:12], 'section': source['section'],
                'statement': wording, 'keywords': source['keywords'],
                'source_note': 'AI wording suggestion from verified fact ' + fact_id + '. Review against your original evidence.',
                'verified_by_candidate': False, 'derived_from': {'fact_id': fact_id,
                    'statement': source_statement, 'job_id': job_id, 'job_quote': job_quote}}
            current['facts'].append(_validate_facts([proposal])[0])
            return current
        return self._write(expected_revision, transform, 'ai_wording_staged')

    def verify(self, expected_revision: int, fact_id: str, verified: bool,
               acknowledged: bool) -> dict[str, Any]:
        if type(verified) is not bool or acknowledged is not True:
            raise ValueError("Explicit candidate acknowledgement and boolean verification required")

        def transform(current):
            matching = next((fact for fact in current["facts"] if fact["id"] == fact_id), None)
            if matching is None:
                raise ValueError("Unknown candidate fact ID")
            if verified and (not current["profile"]["name"].strip()):
                raise ValueError("Enter your name before verifying career facts")
            matching["verified_by_candidate"] = verified
            return current

        return self._write(expected_revision, transform, "verified" if verified else "revoked")

    def import_legacy(self, profile: CandidateProfile, bank: FactBank) -> dict[str, Any]:
        """Explicit import only; original example/config files remain untouched."""
        seed = {
            "name": bank.name,
            "headline": bank.headline,
            "target_titles": list(profile.target_titles),
            "skills": list(profile.skills),
            "preferred_locations": list(profile.preferred_locations),
            "allow_remote": profile.allow_remote,
        }
        verified_facts = [
            {
                "id": fact.fact_id,
                "section": fact.section,
                "statement": fact.statement,
                "keywords": list(fact.keywords),
                "source_note": fact.source_note,
                "verified_by_candidate": True,
            }
            for fact in bank.facts
        ]
        normalized = _validate_facts(verified_facts)
        def transform(current):
            # Source facts were explicitly confirmed in the legacy FactBank schema.
            return {"profile": _validate_profile(seed), "facts": normalized}
        return self._write(0, transform, "imported_legacy")

    @staticmethod
    def scoring_profile(workspace: dict[str, Any]) -> CandidateProfile:
        profile = workspace["profile"]
        return CandidateProfile(
            target_titles=tuple(profile["target_titles"]),
            skills=tuple(profile["skills"]),
            preferred_locations=tuple(profile["preferred_locations"]),
            allow_remote=profile["allow_remote"],
        )

    @staticmethod
    def fact_bank(workspace: dict[str, Any]) -> FactBank:
        profile = workspace["profile"]
        if not profile["name"]:
            raise ValueError("Enter your name in the career profile before drafting")
        verified = [fact for fact in workspace["facts"] if fact["verified_by_candidate"]]
        if not verified:
            raise ValueError("Confirm at least one career fact before drafting an application")
        return FactBank.parse({
            "name": profile["name"],
            "headline": profile["headline"],
            "facts": verified,
        })
