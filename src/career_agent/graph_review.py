"""LangGraph scoring → sponsorship → draft → validation → human review.

Callers supply durable SQLite checkpoints; in-memory storage remains available
for isolated tests and the legacy standalone review command. No submissions.
"""
from __future__ import annotations

from dataclasses import asdict
from typing import Any, TypedDict

from .application import build_packet, validate_packet
from .candidate import FactBank
from .models import CandidateProfile, Job
from .scoring import score_job
from .sponsor_evidence import FilingEvidence, sponsorship_report


class CareerState(TypedDict, total=False):
    job: dict[str, Any]
    preferences: dict[str, Any]
    fact_bank: dict[str, Any]
    filing_records: list[dict[str, Any]]
    analysis: dict[str, Any]
    packet: dict[str, Any]
    review_status: str
    workspace_revision: int | None
    job_description_revision: int | None
    tailored_fact_ids: list[str] | None
    tailored_section_order: list[str] | None


def _imports():
    try:
        from langgraph.checkpoint.memory import InMemorySaver
        from langgraph.graph import END, START, StateGraph
        from langgraph.types import Command, interrupt
    except ImportError as error:
        raise RuntimeError('Install the optional graph: pip install -e ".[graph]"') from error
    return InMemorySaver, END, START, StateGraph, Command, interrupt


def _decode(state: CareerState):
    job = Job.from_mapping(state["job"])
    raw_profile = state["preferences"]
    if not isinstance(raw_profile, dict):
        raise ValueError("Workflow preferences must be an object")

    def items(key: str) -> tuple[str, ...]:
        values = raw_profile.get(key, [])
        if not isinstance(values, (tuple, list)) or not all(isinstance(x, str) for x in values):
            raise ValueError(f"Workflow preference {key} must be a list of strings")
        return tuple(values)

    remote = raw_profile.get("allow_remote", True)
    if type(remote) is not bool:
        raise ValueError("allow_remote must be a boolean")
    preferences = CandidateProfile(
        target_titles=items("target_titles"),
        skills=items("skills"),
        preferred_locations=items("preferred_locations"),
        allow_remote=remote,
    )
    bank = FactBank.parse(state["fact_bank"])
    raw_records = state.get("filing_records", [])
    if not isinstance(raw_records, list):
        raise ValueError("Workflow filing_records must be a list of evidence objects")
    records = tuple(
        FilingEvidence.from_state(item, index)
        for index, item in enumerate(raw_records)
    )
    return job, preferences, bank, records


def _review_node(state: CareerState, interrupt) -> dict[str, str]:
    packet = state["packet"]
    fingerprint = validate_packet(packet)
    # Never perform side effects before an interrupt: LangGraph replays the node.
    decision = interrupt({
        "action": "review_application_packet",
        "job_id": packet["job_id"],
        "job_url": packet["job_url"],
        "packet_sha256": fingerprint,
        "notice": "Approval permits only manual submission; no application will be sent.",
    })
    if not isinstance(decision, dict):
        raise ValueError("Structured review decision required")
    if decision.get("packet_sha256") != fingerprint:
        raise ValueError("Review decision refers to an obsolete packet")
    if decision.get("reviewed") is not True:
        raise ValueError("Explicit confirmation of packet review is required")
    if type(decision.get("approved")) is not bool:
        raise ValueError("Review decision must explicitly approve or reject")
    return {
        "review_status": (
            "approved_for_manual_submission" if decision["approved"]
            else "rejected_by_reviewer"
        )
    }


def build_career_graph(checkpointer=None):
    """A real multi-node LangGraph pipeline with explicit review interrupt."""
    InMemorySaver, END, START, StateGraph, _, interrupt = _imports()

    def analyze(state: CareerState) -> dict[str, Any]:
        job, preferences, _, records = _decode(state)
        return {
            "analysis": {
                "fit": asdict(score_job(job, preferences)),
            }
        }

    def sponsorship(state: CareerState) -> dict[str, Any]:
        job, _, _, records = _decode(state)
        return {'analysis': {**state['analysis'], 'sponsorship': sponsorship_report(job, records)}}

    def draft(state: CareerState) -> dict[str, Any]:
        job, preferences, bank, records = _decode(state)
        return {"packet": build_packet(job, preferences, bank, records,
                                       workspace_revision=state.get('workspace_revision'),
                                       job_description_revision=state.get('job_description_revision'),
                                       tailored_fact_ids=state.get('tailored_fact_ids'),
                                       tailored_section_order=state.get('tailored_section_order'))}

    def validate(state: CareerState) -> dict:
        validate_packet(state['packet'])
        _, _, bank, _ = _decode(state)
        if not set(state['packet']['selected_fact_ids']).issubset(bank.public_claims()):
            raise ValueError('Draft references unsupported facts')
        return {}

    def human_review(state: CareerState) -> dict[str, str]:
        return _review_node(state, interrupt)

    builder = StateGraph(CareerState)
    builder.add_node("analyze", analyze)
    builder.add_node("sponsorship", sponsorship)
    builder.add_node("draft", draft)
    builder.add_node("validate", validate)
    builder.add_node("human_review", human_review)
    builder.add_edge(START, "analyze")
    builder.add_edge("analyze", "sponsorship")
    builder.add_edge("sponsorship", "draft")
    builder.add_edge("draft", "validate")
    builder.add_edge("validate", "human_review")
    builder.add_edge("human_review", END)
    return builder.compile(checkpointer=checkpointer if checkpointer is not None else InMemorySaver())


def build_review_graph():
    """Review an existing fingerprinted packet, without rerunning discovery."""
    InMemorySaver, END, START, StateGraph, _, interrupt = _imports()
    builder = StateGraph(CareerState)
    builder.add_node("human_review", lambda state: _review_node(state, interrupt))
    builder.add_edge(START, "human_review")
    builder.add_edge("human_review", END)
    return builder.compile(checkpointer=InMemorySaver())


def resume_review(graph, packet: dict[str, Any], approved: bool, thread_id: str) -> str:
    """Resume a *paused* graph. The caller must display the packet first."""
    _, _, _, _, Command, _ = _imports()
    fingerprint = validate_packet(packet)
    result = graph.invoke(
        Command(resume={
            "approved": approved,
            "reviewed": True,
            "packet_sha256": fingerprint,
        }),
        config={"configurable": {"thread_id": thread_id}},
    )
    return result["review_status"]
