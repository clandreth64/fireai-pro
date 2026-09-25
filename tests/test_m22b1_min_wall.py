"""Milestone 2.2B.1: minimum wall CLEARANCE (min_perpendicular_wall_distance) is its own measurement,
separate from the END-CONDITION wall distance (perpendicular_wall_distance).

SYNTHETIC (TEST ONLY) rule values, generated rooms. Room coordinates (u, v) are feet from the room's
min corner (LOCAL = room + 0.5 for make_room). Expected values are worked by hand in each docstring."""

from __future__ import annotations

import pytest

from fireai.engineering import run_design
from fireai.engineering.design import CandidateProposal
from fireai.engineering.envelope import envelope_blockers
from fireai.engineering.geometry import room_frame
from fireai.engineering.inputs import LayoutOrientation
from fireai.engineering.placement import evaluate_proposal
from fireai.rules.catalog import NFPA13_2019_BASE, empty_draft
from fireai.rules.constraints import MEASUREMENT_BOUNDS
from fireai.rules.intake import M22B_MAPPINGS
from fireai.rules.resolve import resolve
from fixtures import builders as B
from fixtures import commercial_2019 as C
from fixtures import synthetic_design as S

KEY = "sprinkler.min_wall_distance"


def _pkg(tmp_path, name="r", **kw):
    d = tmp_path / name
    d.mkdir()
    return S.verified_package(d, B.make_room(d / "room.dxf", **kw))


def minwall(limit=0.1, kinds=("wall",), unit="ft"):
    return S.rule("SYN-MINWALL", KEY, "min_perpendicular_wall_distance", "min", limit, unit=unit, kinds=list(kinds))


def endwall(limit=100.0, kinds=("wall",)):
    return S.rule("SYN-ENDWALL", "sprinkler.max_wall_distance", "perpendicular_wall_distance", "max", limit,
                  kinds=list(kinds))


def ori(pkg, axis):
    fr = room_frame([tuple(p) for p in pkg.spaces[0].polygon_local_ft])
    d = (fr.ux, fr.uy) if axis == "u" else (-fr.uy, fr.ux)
    return LayoutOrientation(branch_line_direction=d, strategy="explicit_design_input", source=S.SYN)


def evaluate(pkg, room_pts, *rules, orientation=None):
    fr = room_frame([tuple(p) for p in pkg.spaces[0].polygon_local_ft])
    req = S.request(pkg, rule_sets=[S.base_ruleset(extra=list(rules))]).model_copy(update={"orientation": orientation})
    prop = CandidateProposal(proposed_by="fixture", package_content_fingerprint=pkg.content_fingerprint,
                             package_verification_fingerprint=pkg.verification_fingerprint,
                             rule_sets=[{"rule_set_id": s.rule_set_id, "version": s.version, "digest": s.digest()}
                                        for s in req.rule_sets],
                             listing={"listing_id": req.listing.listing_id, "version": req.listing.version,
                                      "digest": req.listing.digest()},
                             positions=[fr.to_xy(u, v) for u, v in room_pts])
    ev = evaluate_proposal(req, prop)
    return ev, {e.measurement: e for e in ev.evaluations}


def test_01_single_sprinkler_near_one_wall(tmp_path):
    """20 x 10, one sprinkler at (3, 5): walls 3, 17, 5, 5 -> 3."""
    pkg = _pkg(tmp_path, w=20, h=10)
    _ev, e = evaluate(pkg, [(3, 5)], minwall())
    assert e["min_perpendicular_wall_distance"].measured == pytest.approx(3.0)


def test_02_a_neighbour_along_the_array_does_not_change_the_clearance(tmp_path):
    """Same room, sprinklers (3, 5) and (10, 5): sprinkler 0 is still 3 ft from the -u wall."""
    pkg = _pkg(tmp_path, w=20, h=10)
    _ev, e = evaluate(pkg, [(3, 5), (10, 5)], minwall())
    m = e["min_perpendicular_wall_distance"]
    assert m.measured == pytest.approx(3.0) and m.worst_subject["per_sprinkler"][0]["distance_ft"] == pytest.approx(3.0)


def test_03_nearest_of_several_walls_controls(tmp_path):
    """(3, 2) in 20 x 10: walls 3 (-u), 17 (+u), 2 (-v), 8 (+v) -> 2, from the -v wall."""
    pkg = _pkg(tmp_path, w=20, h=10)
    _ev, e = evaluate(pkg, [(3, 2)], minwall())
    m = e["min_perpendicular_wall_distance"]
    assert m.measured == pytest.approx(2.0) and m.worst_subject["wall"]["kind"] == "wall"


def test_04_branch_line_orientation_does_not_change_the_clearance(tmp_path):
    pkg = _pkg(tmp_path, w=20, h=10)
    vals = [evaluate(pkg, [(3, 2), (13, 2)], minwall(), orientation=ori(pkg, a))[1]
            ["min_perpendicular_wall_distance"].measured for a in ("u", "v")]
    assert vals == [pytest.approx(2.0), pytest.approx(2.0)]


def test_05_end_condition_and_minimum_clearance_differ_on_purpose(tmp_path):
    """20 x 12, one row at v = 6 with u = 2, 15, 17 (a non-uniform but complete grid).
    Sprinkler 1 (u = 15) has neighbours on BOTH sides along u, so the END-CONDITION measurement only
    looks along v (6 ft each way). Its minimum clearance counts EVERY wall, including the +u wall that
    lies beyond sprinkler 2: min(15, 20 - 15 = 5, 6, 6) = 5.
    Overall: clearance min = 2 (sprinkler 0, -u wall); end-condition max over
    {s0: -u 2, -v 6, +v 6; s1: -v 6, +v 6; s2: +u 3, -v 6, +v 6} = 6."""
    pkg = _pkg(tmp_path, w=20, h=12)
    _ev, e = evaluate(pkg, [(2, 6), (15, 6), (17, 6)], minwall(), endwall(), orientation=ori(pkg, "u"))
    clear, end = e["min_perpendicular_wall_distance"], e["perpendicular_wall_distance"]
    assert clear.measured == pytest.approx(2.0) and end.measured == pytest.approx(6.0)
    assert clear.worst_subject["per_sprinkler"][1]["distance_ft"] == pytest.approx(5.0)
    s1_end = {d["direction"] for d in end.worst_subject["per_direction"] if d["index"] == 1}
    assert s1_end == {"-v", "+v"}                                   # the 5 ft +u wall is never examined


def test_06_a_door_opening_is_not_a_wall(tmp_path):
    """20 x 10, door on the right side from v = 1 to 9; sprinkler at (16.5, 5). The door line is 3.5 ft
    away but is NOT a wall; the jambs are sqrt(3.5^2 + 4^2) = 5.32 ft away; the -v / +v walls are 5 ft:
    clearance = 5 (never 3.5)."""
    pkg = _pkg(tmp_path, w=20, h=10, door=("right", 1, 9))
    _ev, e = evaluate(pkg, [(16.5, 5)], minwall())
    assert e["min_perpendicular_wall_distance"].measured == pytest.approx(5.0)


def test_07_window_is_outside_the_current_envelope(tmp_path):
    d = tmp_path / "win"
    d.mkdir()
    pkg = S.verified_package(d, B.make_room(d / "r.dxf", w=C.WIDTH_FT, h=C.DEPTH_FT, window=("left", 10, 16)))
    d19 = empty_draft(NFPA13_2019_BASE)
    conds = {b.detail.get("condition") for b in envelope_blockers(C.request(pkg, [d19]), [d19])
             if b.code == "OUTSIDE_SUPPORTED_ENVELOPE"}
    assert "perimeter boundary kinds" in conds


def test_08_unknown_segment_refuses(tmp_path):
    pkg = _pkg(tmp_path, w=20, h=10).model_copy(deep=True)
    pkg.spaces[0].boundary.rings[0].segments[1].kind = "unknown"
    r = run_design(S.request(pkg, rule_sets=[S.base_ruleset(extra=[minwall()])]))
    assert r.status == "REFUSED" and "BOUNDARY_KIND_UNKNOWN" in {i.code for i in r.refusals}


def test_09_wall_ends_and_corners_are_explicit_and_deterministic(tmp_path):
    """(a) Corner (1, 1) of 20 x 10: two walls tie at 1 ft -> 1, the same segment every time.
    (b) Door (right, v 3..7), sprinkler (18, 5): the jamb at (20, 3) is sqrt(8) = 2.83 ft away, nearer than
        every perpendicular wall distance (5) -> NOT EVALUABLE, never a substituted number.
    (c) L room 20 x 10 without its top-right 8 x 5: at (11, 4) the re-entrant corner (12, 5) is
        sqrt(2) = 1.41 ft away, nearer than any perpendicular distance (4) -> NOT EVALUABLE; at (11, 6)
        the notch wall x = 12 is 1 ft away (its foot (12, 6) is on the segment) -> 1."""
    pkg = _pkg(tmp_path, w=20, h=10)
    a = [evaluate(pkg, [(1, 1)], minwall())[1]["min_perpendicular_wall_distance"] for _ in range(2)]
    assert a[0].measured == pytest.approx(1.0) and a[0].worst_subject["wall"] == a[1].worst_subject["wall"]
    door = _pkg(tmp_path, "door", w=20, h=10, door=("right", 3, 7))
    ev, e = evaluate(door, [(18, 5)], minwall())
    assert e["min_perpendicular_wall_distance"].outcome == "not_evaluable" and ev.verdict == "UNKNOWN"
    assert "wall END" in e["min_perpendicular_wall_distance"].note
    ell = _pkg(tmp_path, "ell", w=20, h=10, notch=(8, 5))
    _ev, e = evaluate(ell, [(11, 4)], minwall())
    assert e["min_perpendicular_wall_distance"].outcome == "not_evaluable"
    _ev, e = evaluate(ell, [(11, 6)], minwall())
    assert e["min_perpendicular_wall_distance"].measured == pytest.approx(1.0)


def test_10_imperial_metric_equivalence(tmp_path):
    got = {}
    for unit in ("ft", "m"):
        pkg = _pkg(tmp_path, unit, unit=unit, w=20, h=10)
        rule = minwall(2.5) if unit == "ft" else minwall(2.5 * 0.3048, unit="m")
        ev, e = evaluate(pkg, [(3, 2), (13, 2)], rule)
        got[unit] = (ev.verdict, e["min_perpendicular_wall_distance"].measured, e["min_perpendicular_wall_distance"].limit)
    assert got["ft"][0] == got["m"][0] == "FAIL"                     # 2.0 < 2.5
    assert got["m"][1] == pytest.approx(got["ft"][1], abs=1e-6) and got["m"][2] == pytest.approx(got["ft"][2], abs=1e-9)


def test_11_a_rule_mapped_to_the_wrong_measurement_is_refused():
    from fireai.rules import RuleSet
    from fireai.rules.store import approval_problems
    wrong = S.rule("SYN-D-WRONG", KEY, "perpendicular_wall_distance", "min", 1.0, kinds=["wall"])
    res = resolve([S.base_ruleset(extra=[wrong])], {"hazard.classification": S.CLASS}, "synthetic_test")
    assert "MEASUREMENT_BOUND_MISMATCH" in {i.code for i in res.refusals}
    wrong_max = S.rule("SYN-D-WRONG2", KEY, "min_perpendicular_wall_distance", "max", 1.0, kinds=["wall"])
    res = resolve([S.base_ruleset(extra=[wrong_max])], {"hazard.classification": S.CLASS}, "synthetic_test")
    assert "MEASUREMENT_BOUND_MISMATCH" in {i.code for i in res.refusals}
    rs = RuleSet(rule_set_id="X", version="1", layer="base_standard", governing_standard="NFPA 13", edition="E",
                 content_basis="authoritative", rules=[])
    assert any("only supports bound" in p for p in approval_problems(wrong.model_copy(), rs))
    assert MEASUREMENT_BOUNDS == {"perpendicular_wall_distance": {"max"}, "min_perpendicular_wall_distance": {"min"},
                                  "fact_requirement": {"in"}}
    assert (M22B_MAPPINGS["D"].measurement, M22B_MAPPINGS["D"].bound) == ("min_perpendicular_wall_distance", "min")


def test_12_13_end_condition_and_derived_max_wall_architecture_unchanged():
    c = M22B_MAPPINGS["C"]
    assert (c.measurement, c.bound, c.derived_from_key) == ("perpendicular_wall_distance", "max", "sprinkler.max_spacing")
    assert M22B_MAPPINGS["A"].measurement == "array_sxl_protection_area"
    for mid in ("A", "B", "C", "E", "F_MIN", "F_MAX"):
        assert M22B_MAPPINGS[mid].measurement != "min_perpendicular_wall_distance"
