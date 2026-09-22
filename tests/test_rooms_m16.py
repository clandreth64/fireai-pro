"""Room/space improvements (Milestone 1.6)."""

from __future__ import annotations

import ezdxf
import pytest

from conftest import run_pipeline
from fixtures import builders as B


def test_doorless_opening_merges_but_is_not_split_by_labels(tmp_path):
    m = run_pipeline(B.make_walls_only(tmp_path / "w.dxf", with_door=False), tmp_path).model
    (room,) = [r for r in m.elements_of("room")]
    # still ONE region: labels alone never split a space
    assert room.subtype == "suspected_merged_region" and room.label is None
    assert room.properties["area_sf"] == pytest.approx(39 * 19 - 0.5 * 16, abs=0.5)
    sc = room.properties["split_candidates"]
    assert sc["status"] == "suggested_not_applied" and len(sc["closed_openings"]) == 1
    labelled = sorted(tuple(f["labels"]) for f in sc["faces"] if f["labels"])
    assert len(labelled) == 2
    assert sorted(f["area_sf"] for f in sc["faces"] if f["labels"]) == pytest.approx([19.25 * 19, 19.25 * 19], abs=0.5)
    assert "ROOM_SPLIT_CANDIDATE" in {t.code for t in m.diagnostics.review_triggers}
    # the doorless opening is inside the one boundary
    (conn,) = m.wall_model["room_connections"]
    assert conn["kind"] == "doorless" and conn["opening_inside_one_room"]


def test_door_connects_two_rooms(tmp_path):
    m = run_pipeline(B.make_walls_only(tmp_path / "w.dxf", with_door=True), tmp_path).model
    rooms = {r.id: r for r in m.elements_of("room")}
    assert len(rooms) == 2
    (conn,) = m.wall_model["room_connections"]
    assert conn["kind"] == "door" and set(conn["rooms"]) == set(rooms) and not conn["opening_inside_one_room"]
    for r in rooms.values():
        (op,) = r.properties["openings"]
        assert op["kind"] == "door" and op["connects_to"] == [x for x in rooms if x != r.id]
        assert "split_candidates" not in r.properties


def _area_doc(polys, labels, label_layer="A-AREA-IDEN"):
    doc = ezdxf.new("R2018")
    doc.header["$INSUNITS"] = 2
    doc.layers.add("A-AREA"); doc.layers.add(label_layer)
    msp = doc.modelspace()
    for pts in polys:
        msp.add_lwpolyline(pts, close=True, dxfattribs={"layer": "A-AREA"})
    for txt, at in labels:
        msp.add_text(txt, height=0.75, dxfattribs={"layer": label_layer}).set_placement(at)
    return doc


def test_corridor_and_irregular_shape_hints(tmp_path):
    corridor = [(0, 0), (60, 0), (60, 6), (0, 6)]
    ell = [(0, 20), (30, 20), (30, 30), (10, 30), (10, 50), (0, 50)]
    doc = _area_doc([corridor, ell], [("CORR", (20, 3)), ("OFFICE", (3, 35))])
    doc.saveas(tmp_path / "s.dxf")
    m = run_pipeline(tmp_path / "s.dxf", tmp_path).model
    by = {r.label: r for r in m.elements_of("room")}
    assert by["CORR"].properties["shape_hints"]["corridor_like"] is True
    assert by["CORR"].properties["shape_hints"]["shape"] == "rectangular"
    assert by["OFFICE"].properties["shape_hints"]["shape"] == "irregular"
    assert "corridor_like" not in by["OFFICE"].properties["shape_hints"]
    assert by["OFFICE"].properties["area_sf"] == pytest.approx(30 * 10 + 10 * 20)


def test_label_just_outside_boundary_is_associated_with_review(tmp_path):
    doc = _area_doc([[(0, 0), (20, 0), (20, 15), (0, 15)], [(40, 0), (60, 0), (60, 15), (40, 15)]],
                    [("LOBBY", (5, 15.4)), ("STORAGE", (45, 5))])
    doc.saveas(tmp_path / "n.dxf")
    m = run_pipeline(tmp_path / "n.dxf", tmp_path).model
    by = {r.label: r for r in m.elements_of("room")}
    assert "LOBBY" in by
    lobby = by["LOBBY"]
    assert "R-LABEL-NEAR" in lobby.rules and lobby.requires_verification and lobby.confidence <= 0.5
    assert "UNASSOCIATED_ROOM_LABELS" not in {t.code for t in m.diagnostics.review_triggers}


def test_label_between_two_boundaries_stays_unassociated(tmp_path):
    doc = _area_doc([[(0, 0), (20, 0), (20, 15), (0, 15)], [(0, 15.8), (20, 15.8), (20, 30), (0, 30)]],
                    [("LOBBY", (5, 15.3))])
    doc.saveas(tmp_path / "b.dxf")
    m = run_pipeline(tmp_path / "b.dxf", tmp_path).model
    assert all(r.label is None for r in m.elements_of("room"))
    assert "UNASSOCIATED_ROOM_LABELS" in {t.code for t in m.diagnostics.review_triggers}
