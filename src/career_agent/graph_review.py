"""LangGraph human review interrupt, intentionally without an application sender.

Graph checkpoints are in memory for the local demonstration. For persistent,
multi-process workflows use a durable checkpointer and an authenticated review UI.
"""
from __future__ import annotations

from typing import Any, TypedDict

from .application import validate_packet


class ReviewState(TypedDict, total=False):
    packet: dict[str, Any]
    review_status: str


def build_review_graph():
    """Compile the optional LangGraph workflow with a real human interrupt."""
    try:
        from langgraph.checkpoint.memory import InMemorySaver
        from langgraph.graph import END, START, StateGraph
        from langgraph.types import interrupt
    except ImportError as error:
        raise RuntimeError('Install LangGraph: pip install -e ".[graph]"') from error

    def review(state: ReviewState) -> dict[str, str]:
        packet = state["packet"]
        fingerprint = validate_packet(packet)
        # Side effects must never precede interrupt(): LangGraph replays the node.
        decision = interrupt({
            "action": "review_application_packet",
            "job_id": packet["job_id"],
            "job_url": packet["job_url"],
            "packet_sha256": fingerprint,
            "notice": "Approval only marks draft ready for a manual application. Nothing is sent.",
        })
        if not isinstance(decision, dict):
            raise ValueError("A structured review decision is required")
        if decision.get("packet_sha256") != fingerprint:
            raise ValueError("Review decision refers to an obsolete packet")
        if decision.get("reviewed") is not True:
            raise ValueError("Explicit confirmation of review is required")
        if type(decision.get("approved")) is not bool:
            raise ValueError("Review decision must explicitly approve or reject")
        return {
            "review_status": (
                "approved_for_manual_submission" if decision["approved"]
                else "rejected_by_reviewer"
            )
        }

    builder = StateGraph(ReviewState)
    builder.add_node("human_review", review)
    builder.add_edge(START, "human_review")
    builder.add_edge("human_review", END)
    return builder.compile(checkpointer=InMemorySaver())


def run_review(packet: dict[str, Any], approved: bool, thread_id: str) -> str:
    """Run a same-process interrupt+resume after an explicit caller decision.

    This is appropriate for a local CLI demonstration, not a secure hosted
    approval API. The caller must show the packet to the reviewing person.
    """
    from langgraph.types import Command

    fingerprint = validate_packet(packet)
    graph = build_review_graph()
    config = {"configurable": {"thread_id": thread_id}}
    result = graph.invoke({"packet": packet}, config=config)
    if "__interrupt__" not in result:
        raise RuntimeError("Expected human review interrupt did not occur")
    resumed = graph.invoke(
        Command(resume={
            "approved": approved,
            "reviewed": True,
            "packet_sha256": fingerprint,
        }),
        config=config,
    )
    return resumed["review_status"]
