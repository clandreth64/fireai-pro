"""Learning-event FOUNDATION (checkpoint before Milestone 2). Nothing here learns or trains.

A ``LearningEvent`` is the structured record a future learning system would consume: what
FireAI concluded, what a person decided instead, in which context, under which engine version.
This module only DERIVES such records, on demand, from data that is already stored:

* human corrections in the review store (``fireai/review/store.py``), which carry a
  ``machine_snapshot`` of FireAI's interpretation at correction time;
* the resulting verification record, if any.

Explicitly NOT done here (see docs/AGENTIC_LEARNING_ARCHITECTURE.md): no event store, no
pattern mining, no training, and no change to any production rule. Corrections affect only the
drawing they were made on (project learning); they never modify global behaviour.
"""

from __future__ import annotations

from typing import Any, Literal, Optional

from pydantic import BaseModel, Field

LEARNING_EVENT_SCHEMA = "learning_event/0"


class LearningEvent(BaseModel):
    schema_version: Literal["learning_event/0"] = LEARNING_EVENT_SCHEMA
    event_id: str                                   # = correction id (stable)
    scope: Literal["project"] = "project"           # system-level learning does not exist yet
    kind: str                                       # view_type | room_boundary | element_reject | element_confirm
    source_sha256: str                              # project/model identity (the drawing file)
    xref_sha256: list[str] = Field(default_factory=list)
    model_id: Optional[str] = None                  # the model the person was looking at
    schema_version_of_model: Optional[str] = None
    engine_version: Optional[str] = None            # FireAI engine that produced the interpretation
    object_uid: Optional[str] = None
    object_type: Optional[str] = None
    machine_value: Any = None                       # FireAI's original interpretation/decision
    machine_confidence: Optional[float] = None
    machine_evidence: list[str] = Field(default_factory=list)
    machine_rules: list[str] = Field(default_factory=list)
    human_decision: str                             # corrected | rejected | confirmed
    human_value: Any = None
    reason: Optional[str] = None
    reviewer: str
    reviewer_identity: str = "unauthenticated_name"
    timestamp: str
    resulting_verification: Optional[str] = None    # verification status recorded for this source, if any
    downstream_outcome: Optional[dict[str, Any]] = None   # future: field/engineering outcome; unknown now


_DECISION = {"view_type": "corrected", "room_boundary": "corrected", "element_reject": "rejected",
             "element_confirm": "confirmed"}


def event_from_correction(c: dict, verification: dict | None = None) -> LearningEvent:
    snap = c.get("machine_snapshot") or {}
    ctx = c.get("context") or {}
    human_value = {"view_type": lambda d: d.get("view_type"),
                   "room_boundary": lambda d: {k: d.get(k) for k in ("label", "polygon_src")},
                   }.get(c["kind"], lambda d: None)(c.get("data") or {})
    return LearningEvent(
        event_id=c["id"], kind=c["kind"], source_sha256=ctx.get("source_sha256", ""),
        xref_sha256=ctx.get("xref_sha256") or [], model_id=ctx.get("created_on_model"),
        schema_version_of_model=ctx.get("schema_version"), engine_version=ctx.get("engine_version"),
        object_uid=snap.get("uid"), object_type=snap.get("object"), machine_value=snap.get("value"),
        machine_confidence=snap.get("confidence"), machine_evidence=snap.get("evidence") or [],
        machine_rules=snap.get("rules") or [], human_decision=_DECISION[c["kind"]], human_value=human_value,
        reason=c.get("note"), reviewer=c["reviewer"], reviewer_identity=c.get("reviewer_identity",
                                                                               "unauthenticated_name"),
        timestamp=c["created_at"], resulting_verification=(verification or {}).get("status"))


def learning_events(store, source_sha: str) -> list[LearningEvent]:
    """Derive (not store) learning events for one drawing. Read-only."""
    ver = store.verification(source_sha)
    return [event_from_correction(c, ver) for c in store.corrections(source_sha)]
