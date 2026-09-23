"""Learning-event foundation: corrections are recorded with FireAI's original interpretation,
can be expressed as structured events, and never change behaviour beyond their own drawing."""

from __future__ import annotations

import json

import ezdxf

from conftest import run_pipeline
from fireai.review.learning import LearningEvent, learning_events
from fireai.review.store import ReviewStore
from test_views import make_plan_and_section


def _section(m):
    return next(r for r in m.view_regions if r["view_type"] == "SECTION")


def test_correction_becomes_structured_learning_event(tmp_path):
    store = ReviewStore(tmp_path / "reviews")
    p = make_plan_and_section(tmp_path / "ps.dxf")
    m = run_pipeline(p, tmp_path, review_store=store).model
    sec = _section(m)
    store.add_correction(m, "view_type", {"region_uid": sec["uid"], "view_type": "FLOOR_PLAN"}, "Owner",
                         "this is the second floor, not a section")
    (ev,) = learning_events(store, m.source.sha256)
    assert isinstance(ev, LearningEvent) and ev.scope == "project"
    assert ev.object_uid == sec["uid"] and ev.object_type == "view_region"
    assert ev.machine_value == "SECTION" and ev.machine_confidence == sec["view_type_confidence"]
    assert ev.machine_evidence == sec["view_type_evidence"] and ev.machine_rules == sec["view_type_rules"]
    assert ev.human_decision == "corrected" and ev.human_value == "FLOOR_PLAN"
    assert ev.reason == "this is the second floor, not a section"
    assert ev.reviewer_identity == "unauthenticated_name"
    assert ev.engine_version == m.verification.engine_version and ev.source_sha256 == m.source.sha256
    assert ev.downstream_outcome is None and ev.resulting_verification is None
    json.loads(ev.model_dump_json())                                  # serializable


def test_element_correction_snapshot_keeps_machine_confidence_and_evidence(tmp_path):
    store = ReviewStore(tmp_path / "reviews")
    from fixtures import builders as B
    m = run_pipeline(B.make_walls_only(tmp_path / "w.dxf", with_door=False), tmp_path, review_store=store).model
    (room,) = m.elements_of("room")
    store.add_correction(m, "element_reject", {"element_uid": room.uid}, "Owner", "two rooms, not one")
    (c,) = store.corrections(m.source.sha256)
    snap = c["machine_snapshot"]
    assert snap["uid"] == room.uid and snap["confidence"] == room.confidence and snap["evidence"] == room.evidence
    assert snap["subtype"] == "suspected_merged_region"
    (ev,) = learning_events(store, m.source.sha256)
    assert ev.human_decision == "rejected" and ev.machine_value == room.label


def test_corrections_never_change_other_drawings(tmp_path):
    """Project learning only: a correction on drawing A must not alter drawing B."""
    store = ReviewStore(tmp_path / "reviews")
    a = make_plan_and_section(tmp_path / "a.dxf")
    b = tmp_path / "b.dxf"
    doc = ezdxf.readfile(a)
    doc.modelspace().add_line((-50, -50), (-45, -50), dxfattribs={"layer": "A-ANNO-TTLB"})   # different bytes
    doc.saveas(b)
    ma = run_pipeline(a, tmp_path, review_store=store).model
    store.add_correction(ma, "view_type", {"region_uid": _section(ma)["uid"], "view_type": "FLOOR_PLAN"}, "Owner")
    ma2 = run_pipeline(a, tmp_path, review_store=store).model
    mb = run_pipeline(b, tmp_path, review_store=store).model
    assert not any(r["view_type"] == "SECTION" for r in ma2.view_regions if r["significant"])
    assert any(r["view_type"] == "SECTION" for r in mb.view_regions if r["significant"])   # B unaffected
    assert mb.human_corrections_applied == []


def test_learning_events_are_derived_read_only(tmp_path):
    store = ReviewStore(tmp_path / "reviews")
    m = run_pipeline(make_plan_and_section(tmp_path / "ps.dxf"), tmp_path, review_store=store).model
    store.add_correction(m, "view_type", {"region_uid": _section(m)["uid"], "view_type": "DETAIL"}, "Owner")
    before = sorted(p.read_bytes() for p in (tmp_path / "reviews").rglob("*.json"))
    learning_events(store, m.source.sha256)
    assert sorted(p.read_bytes() for p in (tmp_path / "reviews").rglob("*.json")) == before
