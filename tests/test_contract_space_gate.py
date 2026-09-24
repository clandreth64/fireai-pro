"""engineering_input/3: the single-space engineering gate and provenance/frame survival.

Engineering selects ONE space (M2.0). A package may be valid for its region while one of its spaces
still has boundary portions FireAI could not establish; engineering on that space must be refused.
Generated drawings only."""

from __future__ import annotations

import json

import ezdxf
import pytest

from conftest import run_pipeline
from fireai.contract import build_engineering_input, parse_engineering_input, space_engineering_blockers
from fireai.review.store import ReviewStore
from fixtures import builders as B

FULL = {c: {"status": "CONFIRMED"} for c in ("units", "drawing_type", "view_regions", "extents", "walls", "rooms")}


def _package(tmp_path, path):
    """A person verifies the (untitled) region as a floor plan, then the package is built."""
    store = ReviewStore(tmp_path / "r")
    m = run_pipeline(path, tmp_path, review_store=store).model
    r = next(r for r in m.view_regions if r["significant"])
    if r["view_type"] != "FLOOR_PLAN":
        store.add_correction(m, "view_type", {"region_uid": r["uid"], "view_type": "FLOOR_PLAN"}, "Owner")
        m = run_pipeline(path, tmp_path, review_store=store).model
    store.record_verification(m, "Owner", "verify", FULL, sorted({t.code for t in m.diagnostics.review_triggers}),
                              [next(r["uid"] for r in m.view_regions if r["significant"])])
    return m, build_engineering_input(m, store)


def _area_with_one_wall(path):
    """A named area; walls only along its south side -> the other three sides are unknown."""
    doc = ezdxf.new("R2018")
    doc.header["$INSUNITS"] = 2
    for n in ("A-AREA", "A-AREA-IDEN", "A-WALL"):
        doc.layers.add(n)
    msp = doc.modelspace()
    msp.add_lwpolyline([(0, 0), (20, 0), (20, 15), (0, 15)], close=True, dxfattribs={"layer": "A-AREA"})
    msp.add_line((0, 0), (20, 0), dxfattribs={"layer": "A-WALL"})
    msp.add_line((0, -0.5), (20, -0.5), dxfattribs={"layer": "A-WALL"})
    msp.add_text("STORAGE", height=1, dxfattribs={"layer": "A-AREA-IDEN"}).set_placement((8, 7))
    doc.saveas(path)
    return path


def test_a_fully_classified_space_may_be_engineered(tmp_path):
    _m, ei = _package(tmp_path, B.make_office(tmp_path / "o.dxf", "ft"))
    office = next(s for s in ei.semantic_spaces if s.label == "OFFICE 101")
    assert space_engineering_blockers(ei, office.uid) == []
    assert space_engineering_blockers(ei, office.region_uid) == []            # by physical region uid too


def test_an_unresolved_boundary_blocks_engineering_on_that_space(tmp_path):
    _m, ei = _package(tmp_path, _area_with_one_wall(tmp_path / "a.dxf"))    # the REGION passes the contract
    (space,) = ei.semantic_spaces
    region = next(s for s in ei.spaces if s.uid == space.region_uid)
    kinds = {s.kind for s in region.boundary.rings[0].segments}
    assert "wall" in kinds and "unknown" in kinds and not region.boundary.complete   # never guessed as walls
    (b,) = space_engineering_blockers(ei, space.uid)
    assert "not established" in b and "distances to walls" in b


def test_an_unnamed_or_merged_space_and_unknown_ids_are_refused(tmp_path):
    _m, ei = _package(tmp_path, B.make_office(tmp_path / "o.dxf", "ft"))
    assert space_engineering_blockers(ei, "no-such-uid")[0].startswith("space no-such-uid is not")
    reg = next(s for s in ei.spaces if s.label == "OFFICE 101")
    orphan = ei.model_copy(update={"semantic_spaces": [s for s in ei.semantic_spaces if s.region_uid != reg.uid]})
    assert "known named spaces" in space_engineering_blockers(orphan, reg.uid)[0]
    assert space_engineering_blockers(orphan, reg.uid, require_semantic_space=False) == []


def test_the_space_gate_needs_nothing_but_the_package(tmp_path):
    """Engineering can decide from a persisted v3 package alone: no model, no CAD, no store."""
    _m, ei = _package(tmp_path, B.make_office(tmp_path / "o.dxf", "mm"))
    loaded = parse_engineering_input(json.loads(ei.model_dump_json()))
    for s in loaded.semantic_spaces:
        assert space_engineering_blockers(loaded, s.uid) == space_engineering_blockers(ei, s.uid) == []


def test_frame_and_provenance_survive_into_the_package(tmp_path):
    m, ei = _package(tmp_path, B.make_office(tmp_path / "o.dxf", "in"))
    assert ei.frame == "LOCAL" and ei.units == "ft" and ei.source_units == "in" and ei.z_status == "unknown"
    s = ei.source_to_local
    assert s[0][0] == pytest.approx(1 / 12) and s[1][1] == pytest.approx(1 / 12)   # traceable back to drawing units
    uids = {e.uid for e in m.elements} | {e.uid for e in m.entities} | {w["uid"] for w in m.wall_model["walls"]}
    doors = {e.uid for e in m.elements if e.category == "door"}
    for sp in ei.spaces:
        assert sp.boundary.frame == "LOCAL" and sp.boundary.plane_z_status == "unknown"
        for seg in sp.boundary.rings[0].segments:
            assert seg.uid and seg.rules and all(u in uids for u in seg.derived_from)
            if seg.kind == "wall":
                assert seg.derived_from                                             # backed by source linework
            if seg.kind == "door_opening":
                assert seg.fill_element_uid in doors
    for o in ei.openings:
        assert o.uid and all(u in uids for u in o.derived_from) and o.vertical_extent.status == "unknown"
