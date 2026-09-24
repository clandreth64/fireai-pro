"""Milestone 1.8 — physical regions vs semantic spaces, and view-aware interpretation.

Human evidence (owner reviews, 2026-09-23): REAL_002's open first-floor area holds four named
spaces in one continuous physical region; REAL_004's section showed door-like content promoted to
plan doors (one door split into three objects). These tests reproduce the failure CLASSES with
generated drawings only — no real-drawing names, coordinates, layers or counts.
"""

from __future__ import annotations

import json
import math
from pathlib import Path

import ezdxf
import pytest

from conftest import run_pipeline
from fireai.contract import engineering_input_blockers
from fireai.review.store import ReviewStore
from fireai.schema import load_model
from fixtures import builders as B

FIXTURES = Path(__file__).parent / "fixtures"
FULL = {c: {"status": "CONFIRMED"} for c in ("units", "drawing_type", "view_regions", "extents", "walls", "rooms")}


def _spaces(m):
    return m.elements_of("space")


def _trig(m):
    return {t.code: t for t in m.diagnostics.review_triggers}


# ── A. physical region vs semantic spaces ────────────────────────────────────

def test_one_region_with_several_named_spaces(tmp_path):
    m = run_pipeline(B.make_walls_only(tmp_path / "w.dxf", with_door=False), tmp_path).model
    (region,) = m.elements_of("room")
    spaces = _spaces(m)
    assert region.label is None and region.subtype == "suspected_merged_region"       # the region stays unnamed
    assert sorted(s.label for s in spaces) == ["OFFICE", "SALES FLOOR"]               # ...but the names are kept
    assert region.properties["semantic_space_names"] == [s.label for s in spaces]
    assert region.properties["semantic_space_boundaries"] == "unresolved"
    for s in spaces:
        assert s.properties["boundary_state"] == "unresolved" and s.properties["region_uid"] == region.uid
        assert s.geometry.kind == "point" and len(s.geometry.points) == 1               # a label anchor, not a boundary
        assert "area_sf" not in s.properties and s.requires_verification
        assert s.source_entity_ids and all(m.entity(i).source.kind == "text" for i in s.source_entity_ids)


def test_named_spaces_invent_no_walls_or_boundaries(tmp_path):
    p = B.make_walls_only(tmp_path / "w.dxf", with_door=False)
    m = run_pipeline(p, tmp_path).model
    walls = m.elements_of("wall")
    (region,) = m.elements_of("room")
    # every wall is backed by source linework; no element is a wall/boundary derived from labels
    assert all(w.source_entity_ids and "S-SEMANTIC-SPACE" not in w.rules for w in walls)
    assert not any(s.geometry.kind == "polygon" for s in _spaces(m))
    assert region.properties["area_sf"] == pytest.approx(39 * 19 - 0.5 * 16, abs=0.5)  # unchanged physical region


def test_unresolved_space_boundaries_trigger_review_naming_the_spaces(tmp_path):
    m = run_pipeline(B.make_walls_only(tmp_path / "w.dxf", with_door=False), tmp_path).model
    t = _trig(m)["ROOM_BOUNDARY_SPANS_MULTIPLE_LABELS"]
    assert {s.id for s in _spaces(m)} <= set(t.element_ids)
    assert "UNRESOLVED" in t.message and "invented" in t.message


def test_single_label_region_has_one_space_with_known_boundary(tmp_path):
    m = run_pipeline(B.make_walls_only(tmp_path / "w.dxf", with_door=True), tmp_path).model
    rooms = {r.label: r for r in m.elements_of("room")}
    spaces = {s.label: s for s in _spaces(m)}
    assert set(rooms) == set(spaces) == {"OFFICE", "SALES FLOOR"}
    for name, s in spaces.items():
        assert s.properties["boundary_state"] == "known" and s.properties["region_uid"] == rooms[name].uid
        assert s.geometry.points == rooms[name].geometry.points
        assert s.properties["area_sf"] == rooms[name].properties["area_sf"]
    assert "ROOM_BOUNDARY_SPANS_MULTIPLE_LABELS" not in _trig(m)


def test_unlabelled_region_has_no_semantic_space(tmp_path):
    p = B.make_walls_only(tmp_path / "w.dxf", with_door=True)
    doc = ezdxf.readfile(p)
    for t in list(doc.modelspace().query("TEXT MTEXT")):
        doc.modelspace().delete_entity(t)
    doc.saveas(p)
    m = run_pipeline(p, tmp_path).model
    assert len(m.elements_of("room")) == 2 and _spaces(m) == []
    assert all(r.properties["semantic_space_boundaries"] == "none" for r in m.elements_of("room"))


def test_imperial_and_metric_give_the_same_spaces(tmp_path):
    ft = run_pipeline(B.make_walls_only(tmp_path / "ft.dxf", with_door=False), tmp_path).model
    p = B.make_walls_only(tmp_path / "mm.dxf", with_door=False)
    doc = ezdxf.readfile(p)
    doc.header["$INSUNITS"] = 4
    s = 304.8
    for e in list(doc.modelspace()):
        e.transform(ezdxf.math.Matrix44.scale(s, s, 1))
    doc.saveas(p)
    mm = run_pipeline(p, tmp_path).model
    sig = lambda m: sorted((x.label, x.properties["boundary_state"]) for x in _spaces(m))  # noqa: E731
    assert sig(ft) == sig(mm)
    (ra,), (rb,) = ft.elements_of("room"), mm.elements_of("room")
    assert math.isclose(ra.properties["area_sf"], rb.properties["area_sf"], rel_tol=1e-6)


def test_existing_room_behaviour_unchanged(tmp_path):
    m = run_pipeline(B.make_office(tmp_path / "o.dxf", "ft"), tmp_path).model
    assert sorted(r.label for r in m.elements_of("room")) == sorted(B.OFFICE_ROOMS)
    assert sorted(s.label for s in _spaces(m)) == sorted(B.OFFICE_ROOMS)


# ── B. view-aware interpretation ─────────────────────────────────────────────

def make_door_view(path: Path, title: str | None) -> Path:
    """A door drawn as linework on a door layer — leaf, glass lite INSIDE the leaf, and a small
    handle — beside two walls; titled as a section or a plan (or untitled)."""
    doc = ezdxf.new("R2018")
    doc.header["$INSUNITS"] = 2
    for n in ("A-WALL", "A-DOOR", "A-ANNO-TTLB", "A-AREA-IDEN"):
        doc.layers.add(n)
    msp = doc.modelspace()
    for x in (0, 0.5, 30, 30.5):
        msp.add_line((x, 0), (x, 20), dxfattribs={"layer": "A-WALL"})
    for y in (0, 20):
        msp.add_line((0, y), (30.5, y), dxfattribs={"layer": "A-WALL"})
    msp.add_lwpolyline([(10, 0.2), (13, 0.2), (13, 7.2), (10, 7.2)], close=True, dxfattribs={"layer": "A-DOOR"})
    msp.add_lwpolyline([(10.6, 4.5), (12.4, 4.5), (12.4, 6.5), (10.6, 6.5)], close=True, dxfattribs={"layer": "A-DOOR"})
    msp.add_lwpolyline([(12.6, 3.5), (12.7, 3.5), (12.7, 3.6)], dxfattribs={"layer": "A-DOOR"})
    if title:
        msp.add_text(title, height=1.5, dxfattribs={"layer": "A-ANNO-TTLB"}).set_placement((2, -3))
    doc.saveas(path)
    return path


def test_section_door_content_is_a_depiction_not_a_plan_door(tmp_path):
    m = run_pipeline(make_door_view(tmp_path / "s.dxf", "BUILDING SECTION A-A"), tmp_path).model
    region = next(r for r in m.view_regions if r["significant"])
    assert region["view_type"] == "SECTION"
    assert m.elements_of("door") == [] and m.elements_of("window") == []
    (dep,) = m.elements_of("depiction")                                  # ONE object: lite + handle nested in the leaf
    assert dep.subtype == "door_in_section" and dep.properties["depicted_role"] == "door"
    assert dep.properties["plan_semantic"] is False and dep.requires_verification and dep.confidence <= 0.5
    assert "V-VIEW-AWARE-INTERPRETATION" in dep.rules
    t = _trig(m)["NON_PLAN_OPENING_DEPICTIONS"]
    assert t.element_ids == [dep.id] and "NOT as plan doors" in t.message


def test_section_door_source_geometry_is_preserved_and_traceable(tmp_path):
    m = run_pipeline(make_door_view(tmp_path / "s.dxf", "BUILDING SECTION A-A"), tmp_path).model
    (dep,) = m.elements_of("depiction")
    src = [m.entity(i) for i in dep.source_entity_ids]
    assert len(src) == 3 and {e.layer for e in src} == {"A-DOOR"}
    assert dep.provenance.derived_from == [e.uid for e in src]
    assert m.wall_model["stats"]["door_openings"] == 0                 # never feeds wall/room topology


def test_plan_door_linework_still_creates_a_door(tmp_path):
    m = run_pipeline(make_door_view(tmp_path / "p.dxf", "FIRST FLOOR PLAN"), tmp_path).model
    assert next(r for r in m.view_regions if r["significant"])["view_type"] == "FLOOR_PLAN"
    (door,) = m.elements_of("door")                                    # nested parts are grouped in plans too
    assert m.elements_of("depiction") == [] and "NON_PLAN_OPENING_DEPICTIONS" not in _trig(m)
    assert len(door.source_entity_ids) == 3
    assert door.properties["view_context"] == "plan"


def test_unknown_view_fails_conservatively(tmp_path):
    m = run_pipeline(make_door_view(tmp_path / "u.dxf", None), tmp_path).model
    region = next(r for r in m.view_regions if r["significant"])
    assert region["view_type"] == "UNKNOWN"
    (door,) = m.elements_of("door")
    assert door.properties["view_context"] == "unconfirmed"
    # the engineering boundary refuses an unconfirmed view even after verification
    store = ReviewStore(tmp_path / "r")
    store.record_verification(m, "Owner", "verify", FULL, sorted({t.code for t in m.diagnostics.review_triggers}),
                              [region["uid"]])
    assert any("not a plan view" in b for b in engineering_input_blockers(m, store))


def test_human_view_correction_turns_section_depiction_into_plan_door(tmp_path):
    store = ReviewStore(tmp_path / "r")
    p = make_door_view(tmp_path / "s.dxf", "BUILDING SECTION A-A")
    m = run_pipeline(p, tmp_path, review_store=store).model
    region = next(r for r in m.view_regions if r["significant"])
    store.add_correction(m, "view_type", {"region_uid": region["uid"], "view_type": "FLOOR_PLAN"}, "Owner",
                         "this is actually a plan")
    m2 = run_pipeline(p, tmp_path, review_store=store).model
    assert len(m2.elements_of("door")) == 1 and m2.elements_of("depiction") == []
    assert m2.verification.corrections_digest is not None


# ── corrections, verification, engineering, migration ────────────────────────

def test_unresolved_spaces_block_engineering_until_human_boundaries(tmp_path):
    store = ReviewStore(tmp_path / "r")
    p = B.make_walls_only(tmp_path / "w.dxf", with_door=False)
    m = run_pipeline(p, tmp_path, review_store=store).model
    region = next(r for r in m.view_regions if r["significant"])
    if region["view_type"] != "FLOOR_PLAN":
        store.add_correction(m, "view_type", {"region_uid": region["uid"], "view_type": "FLOOR_PLAN"}, "Owner")
        m = run_pipeline(p, tmp_path, review_store=store).model
    store.record_verification(m, "Owner", "verify", FULL, sorted({t.code for t in m.diagnostics.review_triggers}),
                              [region["uid"]])
    blockers = engineering_input_blockers(m, store)
    assert any("UNRESOLVED boundaries" in b for b in blockers) and any("merged-room" in b for b in blockers)
    # a person draws the two spaces' boundaries (replacing the region) -> spaces no longer block
    (merged,) = m.elements_of("room")
    o = m.transform.origin
    left = [[0.5 + o[0], 0.5 + o[1]], [19.75 + o[0], 0.5 + o[1]], [19.75 + o[0], 19.5 + o[1]], [0.5 + o[0], 19.5 + o[1]]]
    right = [[20.25 + o[0], 0.5 + o[1]], [39.5 + o[0], 0.5 + o[1]], [39.5 + o[0], 19.5 + o[1]], [20.25 + o[0], 19.5 + o[1]]]
    store.add_correction(m, "room_boundary", {"polygon_src": left, "label": "OFFICE", "replaces_element_uid": merged.uid}, "Owner")
    store.add_correction(m, "room_boundary", {"polygon_src": right, "label": "SALES FLOOR"}, "Owner")
    m2 = run_pipeline(p, tmp_path, review_store=store).model
    assert m2.verification.status == "INVALIDATED"                      # corrections changed the model
    store.record_verification(m2, "Owner", "verify", FULL, sorted({t.code for t in m2.diagnostics.review_triggers}),
                              [region["uid"]])
    assert not any("UNRESOLVED" in b for b in engineering_input_blockers(m2, store))


def test_engine_change_invalidates_prior_verification(tmp_path, monkeypatch):
    import fireai.pipeline as P
    store = ReviewStore(tmp_path / "r")
    p = B.make_walls_only(tmp_path / "w.dxf")
    m = run_pipeline(p, tmp_path, review_store=store).model
    uid = next(r["uid"] for r in m.view_regions if r["significant"])
    store.record_verification(m, "Owner", "verify", FULL, sorted({t.code for t in m.diagnostics.review_triggers}), [uid])
    monkeypatch.setattr(P, "ENGINE_VERSION", "0.1.0+interp.m17.1")                  # the previous engine
    assert run_pipeline(p, tmp_path, review_store=store).model.verification.status == "INVALIDATED"


def test_migrate_real_0_3_0_model_derives_nothing():
    """Fixture produced by the Milestone 1.7 code (17102ed, schema 0.3.0)."""
    data = json.loads((FIXTURES / "model_v0_3_0_open_plan.json").read_text())
    assert data["schema_version"] == "0.3.0"
    model, applied = load_model(data)
    assert applied == ["0.3.0->0.4.0", "0.4.0->0.5.0"] and model.schema_version == "0.5.0"
    assert model.elements_of("space") == [] and model.elements_of("depiction") == []   # not retro-derived
    assert [e.uid for e in model.elements] == [e["uid"] for e in data["elements"]]
    assert model.elements_of("room")[0].subtype == "suspected_merged_region"
