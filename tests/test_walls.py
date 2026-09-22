"""Wall analysis layer (Milestone 1.6). Ground truth from the fixture definitions."""

from __future__ import annotations

import math

import ezdxf
import pytest

from conftest import run_pipeline
from fixtures import builders as B


def _wm(path, tmp):
    r = run_pipeline(path, tmp)
    return r.model, r.model.wall_model


def _piece_near(wm, p, q, tol=0.05):
    for w in wm["walls"]:
        if w["kind"] != "straight":
            continue
        a, b = w["centerline"]
        if (math.dist(a, p) < tol and math.dist(b, q) < tol) or (math.dist(a, q) < tol and math.dist(b, p) < tol):
            return w
    return None


@pytest.mark.parametrize("closed", [False, True])
def test_double_line_walls_pair_into_pieces(tmp_path, closed):
    m, wm = _wm(B.make_walls_only(tmp_path / "w.dxf", closed_wall_pieces=closed), tmp_path)
    assert wm["derived"] is True and wm["frame"] == "LOCAL"
    straight = [w for w in wm["walls"] if w["kind"] == "straight"]
    assert len(straight) == 6
    assert all(w["thickness_ft"] == pytest.approx(0.5) for w in straight)
    # the two partition pieces: centerline x = 20.0, y 0.5..8 and 11..19.5
    lower = _piece_near(wm, (20.0, 0.5), (20.0, 8.0))
    upper = _piece_near(wm, (20.0, 11.0), (20.0, 19.5))
    assert lower and upper
    assert lower["length_ft"] == pytest.approx(7.5) and upper["length_ft"] == pytest.approx(8.5)
    # each piece cites both faces and their source entities
    for w in straight:
        assert len(w["faces"]) == 2 and all(f["entity_ids"] for f in w["faces"])
        assert w["uid"] and w["rules"][0] == "W-PAIR"
    # derived geometry never leaks into elements/entities
    assert not any("wall_analysis" in str(el.properties) for el in m.elements)


def test_exterior_and_interior_evidence(tmp_path):
    _m, wm = _wm(B.make_walls_only(tmp_path / "w.dxf"), tmp_path)
    ext = {w["id"] for w in wm["walls"] if w["exterior"] == "exterior_evidence"}
    inter = {w["id"] for w in wm["walls"] if w["exterior"] == "interior_evidence"}
    assert len(ext) == 4 and len(inter) == 2
    assert _piece_near(wm, (20.0, 0.5), (20.0, 8.0))["id"] in inter


def test_door_opening_and_junctions(tmp_path):
    m, wm = _wm(B.make_walls_only(tmp_path / "w.dxf", with_door=True), tmp_path)
    (op,) = wm["openings"]
    assert op["kind"] == "door" and op["width_ft"] == pytest.approx(3.0) and op["jambs"] == 2
    assert op["door_element_ids"] == [d.id for d in m.elements_of("door")]
    assert op["center"] == pytest.approx([20.0, 9.5])
    types = sorted(j["type"] for j in wm["junctions"])
    assert types.count("T") == 2 and types.count("L") == 4


def test_doorless_opening_is_flagged_not_assumed(tmp_path):
    _m, wm = _wm(B.make_walls_only(tmp_path / "w.dxf", with_door=False), tmp_path)
    (op,) = wm["openings"]
    assert op["kind"] == "doorless" and op["requires_verification"] and op["door_element_ids"] == []
    assert op["jambs"] == 2 and op["confidence"] == 0.5


def _doc(lines, arcs=()):
    doc = ezdxf.new("R2018")
    doc.header["$INSUNITS"] = 2
    doc.layers.add("A-WALL")
    msp = doc.modelspace()
    for a, b in lines:
        msp.add_line(a, b, dxfattribs={"layer": "A-WALL"})
    for c, r, s, e in arcs:
        msp.add_arc(c, r, s, e, dxfattribs={"layer": "A-WALL"})
    return doc


def test_single_lines_are_not_given_a_thickness(tmp_path):
    doc = _doc([((0, 0), (30, 0)), ((0, 10), (30, 10))])      # 10 ft apart: not a wall pair
    doc.saveas(tmp_path / "s.dxf")
    _m, wm = _wm(tmp_path / "s.dxf", tmp_path)
    assert wm["walls"] == [] and wm["unpaired_linework"]["segment_count"] == 2


def test_three_parallel_lines_are_ambiguous(tmp_path):
    doc = _doc([((0, 0), (30, 0)), ((0, 0.5), (30, 0.5)), ((0, 1.0), (30, 1.0))])
    doc.saveas(tmp_path / "t.dxf")
    _m, wm = _wm(tmp_path / "t.dxf", tmp_path)
    assert wm["walls"], "one pair is still reported"
    assert all(w["confidence"] <= 0.4 and w["requires_verification"] for w in wm["walls"])
    assert any("ambiguous" in e for w in wm["walls"] for e in w["evidence"])


def test_curved_wall_from_concentric_arcs(tmp_path):
    doc = _doc([], arcs=[((0, 0), 20.0, 0, 90), ((0, 0), 20.5, 0, 90)])
    doc.saveas(tmp_path / "c.dxf")
    _m, wm = _wm(tmp_path / "c.dxf", tmp_path)
    (w,) = wm["walls"]
    assert w["kind"] == "curved" and w["thickness_ft"] == pytest.approx(0.5)
    assert w["arc"]["radius"] == pytest.approx(20.25)
    assert w["length_ft"] == pytest.approx(math.pi / 2 * 20.25, rel=1e-3)


def test_unusual_thickness_lowers_confidence(tmp_path):
    doc = _doc([((0, 0), (30, 0)), ((0, 1.75), (30, 1.75))])      # 21 in
    doc.saveas(tmp_path / "u.dxf")
    _m, wm = _wm(tmp_path / "u.dxf", tmp_path)
    (w,) = wm["walls"]
    assert w["confidence"] == 0.5 and w["requires_verification"]


def test_walls_in_sections_are_not_analysed(tmp_path):
    from test_views import make_plan_and_section
    m, wm = _wm(make_plan_and_section(tmp_path / "ps.dxf"), tmp_path)
    sec = next(r["id"] for r in m.view_regions if r["view_type"] == "SECTION")
    assert wm["stats"]["wall_elements_in_non_plan_views_skipped"] > 0
    assert all(w["region"] != sec for w in wm["walls"])
