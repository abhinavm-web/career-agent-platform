"""Candidate-confirmed fact bank. This module does not generate career claims."""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

SECTIONS = ("experience", "projects", "skills", "education", "certifications")
ID_PATTERN = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9_-]{1,63}$")


@dataclass(frozen=True)
class VerifiedFact:
    """A statement explicitly confirmed by its owner, with private provenance."""

    fact_id: str
    section: str
    statement: str
    keywords: tuple[str, ...]
    source_note: str

    @classmethod
    def parse(cls, data: Any) -> "VerifiedFact":
        if not isinstance(data, dict):
            raise ValueError("Each career fact must be an object")
        fact_id = data.get("id")
        section = data.get("section")
        statement = data.get("statement")
        keywords = data.get("keywords", [])
        source_note = data.get("source_note")
        if not isinstance(fact_id, str) or not ID_PATTERN.fullmatch(fact_id):
            raise ValueError("Each career fact needs an ID of 2-64 letters, numbers, _ or -")
        if section not in SECTIONS:
            raise ValueError(f"Fact {fact_id}: invalid section {section!r}")
        if not isinstance(statement, str) or not (8 <= len(statement.strip()) <= 1000):
            raise ValueError(f"Fact {fact_id}: statement must contain 8-1000 characters")
        if not isinstance(source_note, str) or not source_note.strip():
            raise ValueError(f"Fact {fact_id}: source_note is required")
        if data.get("verified_by_candidate") is not True:
            raise ValueError(f"Fact {fact_id}: explicit verified_by_candidate=true is required")
        if not isinstance(keywords, list) or not all(
            isinstance(item, str) and item.strip() for item in keywords
        ):
            raise ValueError(f"Fact {fact_id}: keywords must be a list of nonempty strings")
        return cls(
            fact_id=fact_id,
            section=section,
            statement=statement.strip(),
            keywords=tuple(item.strip() for item in keywords),
            source_note=source_note.strip(),
        )


@dataclass(frozen=True)
class FactBank:
    name: str
    headline: str
    facts: tuple[VerifiedFact, ...]

    @classmethod
    def parse(cls, data: Any) -> "FactBank":
        if not isinstance(data, dict):
            raise ValueError("Fact bank must be a JSON object")
        name = data.get("name")
        headline = data.get("headline", "")
        if not isinstance(name, str) or not name.strip():
            raise ValueError("Candidate name is required")
        if not isinstance(headline, str):
            raise ValueError("Candidate headline must be a string")
        if not isinstance(data.get("facts"), list) or not data["facts"]:
            raise ValueError("Fact bank must contain at least one verified fact")
        facts = tuple(VerifiedFact.parse(item) for item in data["facts"])
        ids = [fact.fact_id for fact in facts]
        if len(ids) != len(set(ids)):
            raise ValueError("Candidate fact IDs must be unique")
        return cls(name=name.strip(), headline=headline.strip(), facts=facts)

    @classmethod
    def load(cls, path: str | Path) -> "FactBank":
        return cls.parse(json.loads(Path(path).read_text(encoding="utf-8")))

    def public_claims(self) -> dict[str, str]:
        """Only verified verbatim statements, not private evidence/source notes."""
        return {fact.fact_id: fact.statement for fact in self.facts}
