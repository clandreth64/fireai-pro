"""Milestone 2.2A: measurement foundation (S x L, perpendicular wall distance, irregular walls,
derived limits, eligibility / design-method / construction-classification facts, vertical deflector
distance, NFPA 13-2019 / 2025 identity isolation).

SYNTHETIC (TEST ONLY) rule values and generated rooms only. Every expected number is derived by hand
in the docstring from the stated geometry; FireAI output is never the reference. No NFPA content."""

from __future__ import annotations

import math
import re
from pathlib import Path

import pytest
from pydantic import ValidationError

from fireai.engineering import run_design
from fireai.engineering.design import CandidateProposal
from fireai.engineering.geometry import room_frame
from fireai.engineering.inputs import (ConstructionClassification, DeflectorPosition, Elevation, LayoutOrientation,
                                       EligibilityCriterion, EligibilityDecision, InputSource, SystemCondition)
from fireai.engineering.measure import UVSegment, ray_hit
from fireai.engineering.placement import (_cells, _State, _validate, design_facts, evaluate_proposal,
                                          iter_valid_layouts, reference_search)
from fireai.rules import (Condition, ConstraintTemplate, Quantity, Rule, RuleApplicability, RuleParameter, RuleSet,
                          RuleSource)
from fireai.rules.catalog import NFPA13_2019_BASE, NFPA13_2025_BASE, REGISTERED_IDENTITIES, empty_draft
from fireai.rules.model import DerivedLimit, RuleException
from fireai.rules.resolve import resolve
from fixtures import builders as B
from fixtures import synthetic_design as S

ROOT = Path(__file__).resolve().parent.parent
HUMAN = InputSource(kind="human_decision", by="fixture reviewer", note="test fixture decision on a generated drawing")
DATUM = "finished floor of the space"
FACTS = {"hazard.classification": S.CLASS}


# ── helpers ───────────────────────────────────────────────────────────────────

def _pkg(tmp_path, name="r", **kw):
    d = tmp_path / name
    d.mkdir()
    return S.verified_package(d, B.make_room(d / "room.dxf", **kw))


def _poly_pkg(tmp_path, name, ring):
    d = tmp_path / name
    d.mkdir()
    return S.verified_package(d, B.make_polygon_room(d / "room.dxf", ring))


def _at(pkg, room_uv):
    """LOCAL positions of room-frame (u, v) points (u = long axis, origin at the room's min corner)."""
    fr = room_frame([tuple(p) for p in pkg.spaces[0].polygon_local_ft])
    return [fr.to_xy(u, v) for u, v in room_uv]


def _proposal(req, positions):
    return CandidateProposal(proposed_by="fixture", package_content_fingerprint=req.package.content_fingerprint,
                             package_verification_fingerprint=req.package.verification_fingerprint,
                             rule_sets=[{"rule_set_id": s.rule_set_id, "version": s.version, "digest": s.digest()}
                                        for s in req.rule_sets],
                             listing={"listing_id": req.listing.listing_id, "version": req.listing.version,
                                      "digest": req.listing.digest()},
                             positions=positions, orientation=None if req.orientation else _u_orientation(req.package))


def _u_orientation(pkg):
    """M2.2A.1: S x L needs an explicit branch-line orientation. These M2.2A cases were derived with S
    along the room-frame u axis, so the orientation is stated explicitly as that direction."""
    fr = room_frame([tuple(p) for p in pkg.spaces[0].polygon_local_ft])
    return LayoutOrientation(branch_line_direction=(fr.ux, fr.uy), strategy="explicit_design_input", source=S.SYN)


def _eval(req, positions, key):
    ev = evaluate_proposal(req, _proposal(req, positions))
    return ev, next(e for e in ev.evaluations if e.constraint_key == key)


def sxl_rule(limit, kinds=("wall",), unit="sf", rid="SYN-SXL"):
    return S.rule(rid, "sprinkler.max_protection_area_sxl", "array_sxl_protection_area", "max", limit, unit=unit,
                  kinds=list(kinds), category="protection_area")


def wall_rule(limit, kinds=("wall",), rid="SYN-PERPWALL", unit="ft"):
    return S.rule(rid, "sprinkler.max_wall_distance", "perpendicular_wall_distance", "max", limit, unit=unit,
                  kinds=list(kinds))


def rs(*rules):
    return S.base_ruleset(extra=list(rules))


def _derived_rule(rid, key, measurement, from_key, factor, kinds=("wall",), bound="max"):
    return Rule(rule_id=rid, category="distance_to_boundary", title=f"synthetic derived {key}",
                parameters=[RuleParameter(name="factor", value=factor)],
                applicability=RuleApplicability(all_of=[Condition(fact="hazard.classification", op="eq", value=S.CLASS)]),
                constraint=ConstraintTemplate(key=key, measurement=measurement, bound=bound, reference_kinds=list(kinds),
                                              derived=DerivedLimit(from_key=from_key, factor_parameter="factor")),
                source=S.SRC, author="test-suite", authored_at="2026-09-24", review_status="reviewed")


def _codes(r):
    return {i.code for i in r.refusals}


# ── 1-7: S x L protection area ────────────────────────────────────────────────

def test_01_sxl_interior_sprinkler_of_a_regular_array(tmp_path):
    """30 x 30 room, 3 x 3 array at u, v in {5, 15, 25}. Interior sprinkler (index 4): every side has an
    adjacent sprinkler 10 ft away -> S = L = 10 -> 100 sf. (Perimeter ones: 2 x 5 = 10 = adjacent 10.)"""
    pkg = _pkg(tmp_path, w=30, h=30)
    req = S.request(pkg, rule_sets=[rs(sxl_rule(100.0))])
    ev, e = _eval(req, _at(pkg, [(u, v) for v in (5, 15, 25) for u in (5, 15, 25)]), "sprinkler.max_protection_area_sxl")
    interior = e.worst_subject["per_sprinkler"][4]
    assert (interior["S_ft"], interior["L_ft"], interior["area_sf"]) == pytest.approx((10.0, 10.0, 100.0))
    assert interior["S_from"] in ("-u", "+u") and ev.verdict == "PASS"


def test_02_perimeter_sprinkler_twice_wall_distance_governs_S(tmp_path):
    """24 x 10 room, sprinklers (7, 5), (17, 5). Sprinkler 0: -u side has no neighbour, wall 7 ft away
    -> 2 x 7 = 14; +u side adjacent 10 -> S = 14 (from -u). L: walls 5 ft each side -> 2 x 5 = 10.
    Area 14 x 10 = 140 sf. Sprinkler 1 is symmetric (wall 24 - 17 = 7) -> 140."""
    pkg = _pkg(tmp_path, w=24, h=10)
    req = S.request(pkg, rule_sets=[rs(sxl_rule(140.0))])
    ev, e = _eval(req, _at(pkg, [(7, 5), (17, 5)]), "sprinkler.max_protection_area_sxl")
    s0 = e.worst_subject["per_sprinkler"][0]
    assert (s0["S_ft"], s0["S_from"], s0["L_ft"], s0["area_sf"]) == pytest.approx((14.0, "-u", 10.0, 140.0))
    assert e.measured == pytest.approx(140.0) and ev.verdict == "PASS"
    side = next(x for x in e.worst_subject["S"]["sides"] if x["from"] == "twice_wall_distance")
    assert side["wall"]["kind"] == "wall" and side["wall"]["distance_ft"] == pytest.approx(7.0)


def test_03_adjacent_spacing_governs_S(tmp_path):
    """20 x 10 room, sprinklers (4, 5), (14, 5). Sprinkler 0: -u 2 x 4 = 8, +u adjacent 10 -> S = 10
    (adjacent governs) -> 100 sf. Sprinkler 1: +u wall 20 - 14 = 6 -> 12 > 10 -> S = 12 -> 120 sf (worst)."""
    pkg = _pkg(tmp_path, w=20, h=10)
    req = S.request(pkg, rule_sets=[rs(sxl_rule(119.0))])
    ev, e = _eval(req, _at(pkg, [(4, 5), (14, 5)]), "sprinkler.max_protection_area_sxl")
    p = e.worst_subject["per_sprinkler"]
    assert (p[0]["S_ft"], p[0]["S_from"], p[0]["area_sf"]) == pytest.approx((10.0, "+u", 100.0))
    assert (p[1]["S_ft"], p[1]["S_from"], p[1]["area_sf"]) == pytest.approx((12.0, "+u", 120.0))
    assert e.measured == pytest.approx(120.0) and ev.verdict == "FAIL"


def test_04_same_logic_for_L(tmp_path):
    """30 x 24 room (u = the 30 ft side). Column at u = 15: S = 2 x 15 = 30 for both.
    (a) v = 7, 17: sprinkler 0 L = max(2 x 7, 10) = 14 (-v, twice wall) -> 420 sf.
    (b) v = 4, 14: sprinkler 0 L = max(2 x 4, 10) = 10 (+v, adjacent) -> 300 sf; sprinkler 1 L = 2 x 10 = 20."""
    pkg = _pkg(tmp_path, w=30, h=24)
    req = S.request(pkg, rule_sets=[rs(sxl_rule(1000.0))])
    _ev, a = _eval(req, _at(pkg, [(15, 7), (15, 17)]), "sprinkler.max_protection_area_sxl")
    a0 = a.worst_subject["per_sprinkler"][0]
    assert (a0["S_ft"], a0["L_ft"], a0["L_from"], a0["area_sf"]) == pytest.approx((30.0, 14.0, "-v", 420.0))
    _ev, b = _eval(req, _at(pkg, [(15, 4), (15, 14)]), "sprinkler.max_protection_area_sxl")
    b0, b1 = b.worst_subject["per_sprinkler"]
    assert (b0["L_ft"], b0["L_from"], b0["area_sf"]) == pytest.approx((10.0, "+v", 300.0))
    assert (b1["L_ft"], b1["L_from"]) == pytest.approx((20.0, "+v"))


def test_05_imperial_metric_equivalence(tmp_path):
    """The same 24 x 10 ft room drawn in metres, with the limit given in m2 (130 sf = 130 x 0.09290304 m2):
    the same measured 140 sf and the same FAIL as in feet."""
    res = {}
    for unit in ("ft", "m"):
        pkg = _pkg(tmp_path, unit, unit=unit, w=24, h=10)
        lim = sxl_rule(130.0) if unit == "ft" else sxl_rule(130.0 * 0.09290304, unit="m2")
        wall = wall_rule(7.0) if unit == "ft" else wall_rule(7.0 * 0.3048, unit="m")
        req = S.request(pkg, rule_sets=[rs(lim, wall)])
        ev = evaluate_proposal(req, _proposal(req, _at(pkg, [(7, 5), (17, 5)])))
        res[unit] = (ev.verdict, {e.constraint_key: (e.measured, e.limit) for e in ev.evaluations})
    assert res["ft"][0] == res["m"][0] == "FAIL"
    for key, (m, lim) in res["ft"][1].items():
        assert res["m"][1][key][0] == pytest.approx(m, abs=1e-6) and res["m"][1][key][1] == pytest.approx(lim, abs=1e-9)


@pytest.mark.parametrize("deg", [30.0, 90.0, 137.0])
def test_06_rotation_gives_the_same_engineering_result(tmp_path, deg):
    """The 24 x 10 room of test 2, rotated in plan: S x L and wall distances are identical."""
    a = math.radians(deg)
    ring = [(50 + x * math.cos(a) - y * math.sin(a), 50 + x * math.sin(a) + y * math.cos(a))
            for x, y in [(0, 0), (24, 0), (24, 10), (0, 10)]]
    pkg = _poly_pkg(tmp_path, "rot", ring)
    req = S.request(pkg, rule_sets=[rs(sxl_rule(140.0), wall_rule(7.0))])
    ev = evaluate_proposal(req, _proposal(req, _at(pkg, [(7, 5), (17, 5)])))
    got = {e.constraint_key: e.measured for e in ev.evaluations}
    assert ev.verdict == "PASS"
    assert got["sprinkler.max_protection_area_sxl"] == pytest.approx(140.0, abs=1e-6)
    assert got["sprinkler.max_wall_distance"] == pytest.approx(7.0, abs=1e-6)


def test_07_voronoi_and_sxl_differ_and_the_requested_one_is_used(tmp_path):
    """24 x 10, sprinklers (7, 5), (17, 5). Voronoi cell of sprinkler 0 = x in [0, 12] -> 120 sf;
    S x L = 140 sf (test 2). Limit 130: the Voronoi rule PASSES, the S x L rule FAILS."""
    pkg = _pkg(tmp_path, w=24, h=10)
    pos = _at(pkg, [(7, 5), (17, 5)])
    vor = S.request(pkg, rule_sets=[S.base_ruleset(max_cell=130.0)])
    sxl = S.request(pkg, rule_sets=[rs(sxl_rule(130.0))])
    ev_v, e_v = _eval(vor, pos, "sprinkler.max_cell_area")
    ev_s, e_s = _eval(sxl, pos, "sprinkler.max_protection_area_sxl")
    assert (e_v.measurement, e_v.measured, ev_v.verdict) == ("nearest_sprinkler_cell_area", pytest.approx(120.0), "PASS")
    assert (e_s.measurement, e_s.measured, ev_s.verdict) == ("array_sxl_protection_area", pytest.approx(140.0), "FAIL")


# ── 8-11: wall distance, boundary semantics, irregular walls ─────────────────

def test_08_perpendicular_wall_distance_straight_wall(tmp_path):
    """24 x 10, sprinklers (7, 5), (17, 5): perimeter directions -u 7, +u 7, +/-v 5 each -> max 7."""
    pkg = _pkg(tmp_path, w=24, h=10)
    pos = _at(pkg, [(7, 5), (17, 5)])
    ok, e = _eval(S.request(pkg, rule_sets=[rs(wall_rule(7.0))]), pos, "sprinkler.max_wall_distance")
    assert e.measured == pytest.approx(7.0) and ok.verdict == "PASS"
    dirs = {(d["index"], d["direction"]): d["distance_ft"] for d in e.worst_subject["per_direction"]}
    assert dirs == pytest.approx({(0, "-u"): 7.0, (0, "-v"): 5.0, (0, "+v"): 5.0,
                                  (1, "+u"): 7.0, (1, "-v"): 5.0, (1, "+v"): 5.0})
    bad, _ = _eval(S.request(pkg, rule_sets=[rs(wall_rule(6.9))]), pos, "sprinkler.max_wall_distance")
    assert bad.verdict == "FAIL"


def test_09_window_and_door_opening_are_not_silently_walls(tmp_path):
    """24 x 10 with a window on the left (v 2..8) and a door on the right (v 3..7); sprinklers at
    (7, 5), (17, 5): -u reaches the window, +u the door opening. The RULE decides what participates."""
    pkg = _pkg(tmp_path, w=24, h=10, window=("left", 2, 8), door=("right", 3, 7))
    pos = _at(pkg, [(7, 5), (17, 5)])
    ev, e = _eval(S.request(pkg, rule_sets=[rs(wall_rule(7.0, kinds=("wall",)))]), pos, "sprinkler.max_wall_distance")
    assert ev.verdict == "UNKNOWN" and e.outcome == "not_evaluable" and "'window'" in e.note
    ev, e = _eval(S.request(pkg, rule_sets=[rs(wall_rule(7.0, kinds=("wall", "window")))]), pos,
                  "sprinkler.max_wall_distance")
    assert ev.verdict == "UNKNOWN" and "'door_opening'" in e.note
    ev, e = _eval(S.request(pkg, rule_sets=[rs(wall_rule(7.0, kinds=("wall", "window", "door_opening")))]), pos,
                  "sprinkler.max_wall_distance")
    assert ev.verdict == "PASS" and e.measured == pytest.approx(7.0)
    # the design search never counts a not-evaluable layout as valid
    r = run_design(S.request(pkg, rule_sets=[rs(wall_rule(12.0, kinds=("wall",)))], srch=S.search(1.0, 2)))
    for lay in iter_valid_layouts(r):
        _v, ee = _eval(S.request(pkg, rule_sets=[rs(wall_rule(12.0, kinds=("wall",)))], srch=S.search(1.0, 2)),
                       lay, "sprinkler.max_wall_distance")
        assert ee.outcome == "pass"


def test_10_unknown_boundary_refuses_when_the_rule_needs_a_wall(tmp_path):
    segs = [UVSegment((0, 0), (0, 10), "unknown", "s0", 0), UVSegment((24, 0), (24, 10), "wall", "s1", 1)]
    hit = ray_hit(segs, 7.0, 5.0, "u", -1, ["wall"], 1e-9)
    assert hit.status == "non_participating" and hit.kind == "unknown"
    pkg = _pkg(tmp_path, w=24, h=10)
    mutated = pkg.model_copy(deep=True)                      # a boundary segment of UNKNOWN kind
    mutated.spaces[0].boundary.rings[0].segments[1].kind = "unknown"
    r = run_design(S.request(mutated, rule_sets=[rs(wall_rule(7.0))]))
    assert r.status == "REFUSED" and "BOUNDARY_KIND_UNKNOWN" in _codes(r)


def test_11_angled_wall_refuses_explicitly(tmp_path):
    """Trapezoid (one angled wall): array wall-reference measurements REFUSE; the declared angled-wall
    contract refuses as not implemented; the worst-space-point measurement (defined for any polygon)
    still evaluates. A ROTATED rectangle is not irregular (test 6)."""
    pkg = _poly_pkg(tmp_path, "trap", [(0, 0), (20, 0), (16, 10), (0, 10)])
    for rule in (sxl_rule(200.0), wall_rule(10.0)):
        r = run_design(S.request(pkg, rule_sets=[rs(rule)]))
        assert r.status == "REFUSED" and "IRREGULAR_BOUNDARY_UNSUPPORTED" in _codes(r)
    declared = S.rule("SYN-ANGLED", "sprinkler.angled_wall", "angled_wall_perpendicular_distance", "max", 7.0,
                      kinds=["wall"])
    r = run_design(S.request(pkg, rule_sets=[rs(declared)]))
    assert "MEASUREMENT_NOT_IMPLEMENTED" in _codes(r)
    r = run_design(S.request(pkg, rule_sets=[S.base_ruleset(max_space=12.0)], srch=S.search(1.0, 2)))
    assert r.status == "VALID_LAYOUTS_FOUND"


# ── 12-15: derived limits ─────────────────────────────────────────────────────

SPACING = S.rule("SYN-SPACING", "sprinkler.max_axis_spacing", "array_axis_spacing", "max", 10.0, category="spacing")


def test_12_derived_limit_resolves_and_follows_its_source(tmp_path):
    """max wall distance = 0.5 x effective max spacing: 0.5 x 10 = 5 ft; with spacing 12 -> 6 ft. A
    project-layer fixed 4.5 ft is more restrictive and governs."""
    derived = _derived_rule("SYN-WALL-DERIVED", "sprinkler.max_wall_distance", "perpendicular_wall_distance",
                            "sprinkler.max_axis_spacing", 0.5)
    res = resolve([rs(SPACING, derived)], FACTS, "synthetic_test")
    c = next(c for c in res.constraints if c.key == "sprinkler.max_wall_distance")
    assert res.status == "resolved" and c.limit == pytest.approx(5.0)
    d = c.contributions[0].derived
    assert d == {"op": "scale", "from_key": "sprinkler.max_axis_spacing", "from_limit": 10.0, "from_unit": "ft",
                 "from_governing_rule_id": "SYN-SPACING", "factor": 0.5}
    assert "0.5 x sprinkler.max_axis_spacing" in c.explain()
    wider = S.rule("SYN-SPACING", "sprinkler.max_axis_spacing", "array_axis_spacing", "max", 12.0, category="spacing")
    assert next(c for c in resolve([rs(wider, derived)], FACTS, "synthetic_test").constraints
                if c.key == "sprinkler.max_wall_distance").limit == pytest.approx(6.0)
    proj = S.layer_ruleset("project", [S.rule("SYN-PROJ-WALL", "sprinkler.max_wall_distance",
                                               "perpendicular_wall_distance", "max", 4.5, kinds=["wall"])],
                           "TEST_ONLY_SYNTHETIC_PROJECT")
    c = next(c for c in resolve([rs(SPACING, derived), proj], FACTS, "synthetic_test").constraints
             if c.key == "sprinkler.max_wall_distance")
    assert (c.limit, c.governing_rule_id) == (pytest.approx(4.5), "SYN-PROJ-WALL")
    # end to end: 20 x 10, (5, 5) & (15, 5): walls 5 <= 5 PASS; 24 x 10, (7, 5) & (17, 5): 7 > 5 FAIL
    for w, pos, verdict in ((20, [(5, 5), (15, 5)], "PASS"), (24, [(7, 5), (17, 5)], "FAIL")):
        pkg = _pkg(tmp_path, f"w{w}", w=w, h=10)
        ev = evaluate_proposal(S.request(pkg, rule_sets=[rs(SPACING, derived)]),
                               _proposal(S.request(pkg, rule_sets=[rs(SPACING, derived)]), _at(pkg, pos)))
        assert ev.verdict == verdict


def test_13_missing_dependency_refuses():
    derived = _derived_rule("SYN-WALL-DERIVED", "sprinkler.max_wall_distance", "perpendicular_wall_distance",
                            "sprinkler.max_axis_spacing", 0.5)
    res = resolve([rs(derived)], FACTS, "synthetic_test")
    assert res.status == "refused" and "DERIVED_DEPENDENCY_MISSING" in {i.code for i in res.refusals}
    assert not res.constraints


def test_14_dependency_cycle_refuses():
    a = _derived_rule("SYN-A", "k.a", "perpendicular_wall_distance", "k.b", 0.5)
    b = _derived_rule("SYN-B", "k.b", "point_to_boundary_min", "k.a", 2.0, bound="min")
    res = resolve([rs(a, b)], FACTS, "synthetic_test")
    assert res.status == "refused" and "DERIVED_DEPENDENCY_CYCLE" in {i.code for i in res.refusals}
    with pytest.raises(ValidationError):
        _derived_rule("SYN-SELF", "k.s", "perpendicular_wall_distance", "k.s", 0.5)


def test_15_unit_mismatch_refuses():
    cell = S.rule("SYN-CELL", "sprinkler.max_cell_area", "nearest_sprinkler_cell_area", "max", 100.0, unit="sf",
                  category="protection_area")
    from_area = _derived_rule("SYN-D1", "sprinkler.max_wall_distance", "perpendicular_wall_distance",
                              "sprinkler.max_cell_area", 0.5)
    res = resolve([rs(cell, from_area)], FACTS, "synthetic_test")
    assert "DERIVED_UNIT_MISMATCH" in {i.code for i in res.refusals}
    factor_with_unit = _derived_rule("SYN-D2", "sprinkler.max_wall_distance", "perpendicular_wall_distance",
                                     "sprinkler.max_axis_spacing", 0.5)
    factor_with_unit.parameters = [RuleParameter(name="factor", value=Quantity(value=0.5, unit="ft"))]
    res = resolve([rs(SPACING, factor_with_unit)], FACTS, "synthetic_test")
    assert "DERIVED_UNIT_MISMATCH" in {i.code for i in res.refusals}


# ── 16-19: explicit facts ────────────────────────────────────────────────────

def _small_room_rule():
    r = sxl_rule(100.0)
    r.exceptions = [RuleException(when=[Condition(fact="space.eligibility.small_room", op="eq", value="eligible")],
                                  reason="TEST ONLY synthetic small-room provision")]
    return r


def _outcome(res, rid):
    return next((o.outcome for o in res.outcomes if o.rule_id == rid), None)


def test_16_unknown_small_room_eligibility_never_activates_the_exception(tmp_path):
    pkg = _pkg(tmp_path, w=8, h=8)                                    # 64 sf: small by area, NOT by decision
    rset = rs(_small_room_rule())
    unknown = S.request(pkg, rule_sets=[rset]).model_copy(update={"eligibility": {
        "small_room": EligibilityDecision(status="unknown", source=HUMAN)}})
    f = design_facts(unknown, 64.0)
    assert f["space.eligibility.small_room"] == "unknown"
    assert _outcome(resolve([rset], f, "synthetic_test"), "SYN-SXL") == "applied"
    absent = design_facts(S.request(pkg, rule_sets=[rset]), 64.0)
    assert "space.eligibility.small_room" not in absent                  # area alone establishes nothing
    res = resolve([rset], absent, "synthetic_test")
    assert "RULE_EXCEPTION_UNKNOWN" in {i.code for i in res.refusals}   # cannot tell -> refuse, never activate


def test_17_human_small_room_eligibility_is_provenance_bearing(tmp_path):
    pkg = _pkg(tmp_path, w=8, h=8)
    dec = EligibilityDecision(status="eligible", source=HUMAN, reason="fixture decision",
                              criteria=[EligibilityCriterion(fact="hazard.classification", value=S.CLASS),
                                        EligibilityCriterion(fact="space.area_sf", value=64.0),
                                        EligibilityCriterion(fact="ceiling.construction", value="smooth_unobstructed"),
                                        EligibilityCriterion(fact="walls_and_openings", note="reviewed on the plan")])
    rset = rs(_small_room_rule())
    req = S.request(pkg, rule_sets=[rset]).model_copy(update={"eligibility": {"small_room": dec}})
    assert _outcome(resolve([rset], design_facts(req, 64.0), "synthetic_test"), "SYN-SXL") == "excepted"
    r = run_design(req)
    assert r.inputs["eligibility"]["small_room"]["source"]["by"] == "fixture reviewer"
    assert r.inputs["eligibility"]["small_room"]["criteria"][1]["fact"] == "space.area_sf"
    other = req.model_copy(update={"eligibility": {"small_room": dec.model_copy(update={"status": "not_eligible"})}})
    assert run_design(other).request_fingerprint != r.request_fingerprint
    with pytest.raises(ValidationError):
        EligibilityDecision(status="eligible", source=InputSource(kind="bim_model"), criteria=dec.criteria)
    with pytest.raises(ValidationError):
        EligibilityDecision(status="eligible", source=HUMAN)                         # no criteria recorded


def test_18_system_type_and_design_method_are_independent(tmp_path):
    pkg = _pkg(tmp_path)
    sysc = SystemCondition(system_type="wet_pipe", storage="non_storage", design_method="hydraulically_calculated",
                           source=HUMAN)
    f = design_facts(S.request(pkg, rule_sets=[rs()], system=sysc), 100.0)
    assert (f["system.type"], f["system.design_method"]) == ("wet_pipe", "hydraulically_calculated")
    r = sxl_rule(100.0)
    r.applicability.all_of.append(Condition(fact="system.design_method", op="eq", value="pipe_schedule"))
    assert _outcome(resolve([rs(r)], {**FACTS, **f}, "synthetic_test"), "SYN-SXL") == "not_applicable"
    unknown = design_facts(S.request(pkg, rule_sets=[rs()], system=sysc.model_copy(update={"design_method": "unknown"})),
                           100.0)
    assert "system.design_method" not in unknown and unknown["system.type"] == "wet_pipe"
    res = resolve([rs(r)], {**FACTS, **unknown}, "synthetic_test")
    assert "RULE_APPLICABILITY_UNKNOWN" in {i.code for i in res.refusals}


def test_19_construction_classification_is_distinct_from_ceiling_geometry(tmp_path):
    pkg = _pkg(tmp_path)
    ceil = S.ceiling(pkg.semantic_spaces[0].uid).model_copy(deep=True)
    ceil.regions[0].construction = "smooth_unobstructed"
    f = design_facts(S.request(pkg, rule_sets=[rs()], ceil=ceil), 100.0)
    assert f["ceiling.construction"] == "smooth_unobstructed" and "ceiling.construction_classification" not in f
    r = sxl_rule(100.0)
    r.applicability.all_of.append(Condition(fact="ceiling.construction_classification", op="eq",
                                            value="TEST_ONLY_SYNTHETIC_CONSTRUCTION_A"))
    res = resolve([rs(r)], {**FACTS, **f}, "synthetic_test")
    assert "RULE_APPLICABILITY_UNKNOWN" in {i.code for i in res.refusals}      # geometry does not stand in for it
    ceil.regions[0].construction_classification = ConstructionClassification(
        scheme="TEST_ONLY_SYNTHETIC_SCHEME", value="TEST_ONLY_SYNTHETIC_CONSTRUCTION_A", source=HUMAN)
    f2 = design_facts(S.request(pkg, rule_sets=[rs()], ceil=ceil), 100.0)
    assert f2["ceiling.construction"] == "smooth_unobstructed"
    assert f2["ceiling.construction_classification"] == "TEST_ONLY_SYNTHETIC_CONSTRUCTION_A"
    assert _outcome(resolve([rs(r)], {**FACTS, **f2}, "synthetic_test"), "SYN-SXL") == "applied"


# ── 20-23: vertical deflector measurement ────────────────────────────────────

VERT = S.rule("SYN-VERT", "sprinkler.max_deflector_below_ceiling", "ceiling_to_deflector_vertical_distance", "max",
              0.5, category="ceiling_configuration")


def _deflector(value=8.75, datum=DATUM, frame="LOCAL"):
    return DeflectorPosition(frame=frame, elevation=Elevation(status="known", value_ft=value, datum=datum, source=S.SYN))


def test_20_ceiling_to_deflector_vertical_distance(tmp_path):
    """Ceiling 9.0 ft, deflector 8.75 ft above the same datum: 9.0 - 8.75 = 0.25 ft (below the ceiling)."""
    pkg = _pkg(tmp_path)
    req = S.request(pkg, rule_sets=[rs(VERT)]).model_copy(update={"deflector": _deflector()})
    r = run_design(req)
    assert r.status == "VALID_LAYOUTS_FOUND"
    e = next(e for e in r.valid_layouts[0].evaluations if e.constraint_key == "sprinkler.max_deflector_below_ceiling")
    assert e.measured == pytest.approx(0.25) and e.worst_subject["datum"] == DATUM
    z = r.valid_layouts[0].placements[0].position.z
    assert (z.status, z.value_ft, z.datum) == ("from_input", 8.75, DATUM)
    assert not any("Z) is not established" in x for x in r.limitations)
    low = run_design(req.model_copy(update={"deflector": _deflector(8.25)}))       # 0.75 > 0.5
    assert low.status == "NO_VALID_LAYOUT_IN_SEARCH_SPACE"
    assert low.search["pruned_by_stage"] == {"global": low.search["candidates_generated"]}


def test_21_missing_ceiling_elevation_refuses_vertical_evaluation(tmp_path):
    pkg = _pkg(tmp_path)
    ceil = S.ceiling(pkg.semantic_spaces[0].uid).model_copy(deep=True)
    ceil.regions[0].elevation = Elevation()
    r = run_design(S.request(pkg, rule_sets=[rs(VERT)], ceil=ceil).model_copy(update={"deflector": _deflector()}))
    assert r.status == "REFUSED" and "VERTICAL_CEILING_ELEVATION_UNKNOWN" in _codes(r)


def test_22_unknown_sprinkler_z_refuses_vertical_evaluation(tmp_path):
    pkg = _pkg(tmp_path)
    r = run_design(S.request(pkg, rule_sets=[rs(VERT)]))
    assert r.status == "REFUSED" and "SPRINKLER_Z_UNKNOWN" in _codes(r)
    plan_only = run_design(S.request(pkg, rule_sets=[S.base_ruleset(max_boundary=7.08)]))   # no vertical rule
    assert plan_only.valid_layouts[0].placements[0].position.z.status == "unknown"


def test_23_frame_or_datum_mismatch_refuses(tmp_path):
    pkg = _pkg(tmp_path)
    base = S.request(pkg, rule_sets=[rs(VERT)])
    r = run_design(base.model_copy(update={"deflector": _deflector(frame="PROJECT")}))
    assert r.status == "REFUSED" and "WRONG_COORDINATE_FRAME" in _codes(r)
    r = run_design(base.model_copy(update={"deflector": _deflector(datum="structural slab top")}))
    assert r.status == "REFUSED" and "VERTICAL_DATUM_MISMATCH" in _codes(r)


# ── 24-25: repeatability, prior results unchanged ────────────────────────────

def test_24_repeatable_and_every_input_is_fingerprinted(tmp_path):
    pkg = _pkg(tmp_path, w=20, h=10)
    req = S.request(pkg, rule_sets=[rs(sxl_rule(120.0), wall_rule(6.0), VERT)], srch=S.search(1.0, 4, 2)) \
        .model_copy(update={"deflector": _deflector(), "orientation": _u_orientation(pkg)})
    a, b = run_design(req), run_design(req)
    assert S.dumps(a) == S.dumps(b) and a.status == "VALID_LAYOUTS_FOUND"
    sysc = SystemCondition(system_type="wet_pipe", storage="non_storage", source=HUMAN)
    fps = {a.request_fingerprint,
           run_design(req.model_copy(update={"deflector": _deflector(8.8)})).request_fingerprint,
           run_design(req.model_copy(update={"system": sysc})).request_fingerprint,
           run_design(req.model_copy(update={"system": sysc.model_copy(update={"design_method": "pipe_schedule"})}))
           .request_fingerprint}
    assert len(fps) == 4


PINNED = [  # (w, h, search) -> (candidates, valid): M2.0 brute force == M2.1 pruned (REAL_DRAWING_VALIDATION §M2.1)
    (10, 10, (1.0, 4, 4), 2475, 823),
    (20, 10, (1.0, 4, 4), 10213, 185),
    (30, 20, (1.0, 6, 3), 155125, 1),
]


@pytest.mark.parametrize("w,h,srch,cands,valid", PINNED)
def test_25_prior_search_results_are_unchanged(tmp_path, w, h, srch, cands, valid):
    pkg = _pkg(tmp_path, w=w, h=h)
    r = run_design(S.request(pkg, rule_sets=[S.base_ruleset(max_boundary=7.08, min_wall=0.5, max_spacing=12.0,
                                                            max_cell=130.0)], srch=S.search(*srch)))
    assert (r.search["candidates_generated"], r.search["valid"]) == (cands, valid)


def test_25b_pruned_search_equals_reference_with_the_new_measurements(tmp_path):
    pkg = _pkg(tmp_path, w=20, h=10, window=("left", 2, 8))
    req = S.request(pkg, rule_sets=[rs(sxl_rule(110.0, kinds=("wall", "window")),
                                       wall_rule(6.0, kinds=("wall", "window")), VERT)],
                    srch=S.search(1.0, 4, 2)).model_copy(update={"deflector": _deflector(),
                                                                 "orientation": _u_orientation(pkg)})
    r = run_design(req)
    valid, n = reference_search(req)
    _i, sp, reg, res = _validate(req)
    st = _State(req, sp, reg, res)
    ref = sorted(tuple(tuple(round(c, 9) for c in p["xy"]) for p in _cells(st, iu, iv)) for iu, iv in valid)
    got = sorted(tuple(tuple(round(c, 9) for c in p) for p in lay) for lay in iter_valid_layouts(r))
    assert r.search["candidates_generated"] == n and got == ref and r.search["valid"] > 0


# ── NFPA 13-2019 / 2025 identities: isolation, no geometry fork, empty drafts refuse ──

def test_2019_and_2025_identities_are_separate_empty_drafts():
    assert REGISTERED_IDENTITIES[("NFPA 13", "2019")].rule_set_id == "NFPA13-2019-BASE"
    assert REGISTERED_IDENTITIES[("NFPA 13", "2025")].rule_set_id == "NFPA13-2025-BASE"
    for ident in (NFPA13_2019_BASE, NFPA13_2025_BASE):
        d = empty_draft(ident)
        assert (d.review_status, d.rules, d.governing_standard) == ("draft", [], "NFPA 13")
    assert empty_draft(NFPA13_2019_BASE).digest() != empty_draft(NFPA13_2025_BASE).digest()


def test_2019_and_2025_never_mix_in_one_request():
    d19, d25 = empty_draft(NFPA13_2019_BASE), empty_draft(NFPA13_2025_BASE)
    both = resolve([d19, d25], {}, "engineering", "amendments_not_evaluated")
    assert "MULTIPLE_BASE_RULESETS" in {i.code for i in both.refusals}
    ahj = RuleSet(rule_set_id="TEST-AHJ", version="1", layer="jurisdiction_amendment", governing_standard="TEST AHJ",
                  edition=None, base_edition="2019", content_basis="authoritative", review_status="approved")
    assert "EDITION_MISMATCH" in {i.code for i in resolve([d25, ahj], {}, "engineering").refusals}
    cross = Rule(rule_id="TEST-CROSS", category="other", title="placeholder (no content)",
                 constraint=ConstraintTemplate(key="k", measurement="UNSUPPORTED_MEASUREMENT", bound="max",
                                               unsupported_reason="placeholder"),
                 source=RuleSource(kind="authoritative_standard", document="NFPA 13", edition="2019",
                                   reference="TEST-LOCATOR-NOT-A-SECTION"),
                 author="a", authored_at="2026-09-24")
    mixed = d25.model_copy(update={"rules": [cross]})
    assert "EDITION_MISMATCH" in {i.code for i in resolve([mixed], {}, "engineering",
                                                               "amendments_not_evaluated").refusals}


def test_both_editions_refuse_real_engineering_while_empty(tmp_path):
    pkg = _pkg(tmp_path)
    # M2.2A.1: the owner registered NFPA13-2019-DEV-ENVELOPE-1, so 2019 now refuses on its envelope inputs
    for ident, extra in ((NFPA13_2025_BASE, set()), (NFPA13_2019_BASE, {"MISSING_LAYOUT_ORIENTATION"})):
        r = run_design(S.request(pkg, rule_sets=[empty_draft(ident)], mode="engineering",
                                 jurisdiction="amendments_not_evaluated"))
        assert r.status == "REFUSED" and {"RULESET_NOT_APPROVED"} | extra <= _codes(r)


def test_adding_an_edition_needs_no_geometry_fork():
    """No engine or rules-mechanism module names an edition: editions exist only as catalog data."""
    mods = list((ROOT / "fireai" / "engineering").glob("*.py")) + \
        [ROOT / "fireai" / "rules" / n for n in ("model.py", "resolve.py", "constraints.py", "store.py", "units.py")]
    for m in mods:
        assert not re.search(r"\b(19|20)\d\d\b", m.read_text(encoding="utf-8")), m.name


def test_derived_rules_need_a_numeric_factor_and_an_in_set_source_to_be_approvable():
    """Approval checks on a DRAFT set with placeholder numbers (no NFPA content, never approved)."""
    from fireai.rules.store import approval_problems
    src = RuleSource(kind="authoritative_standard", document="NFPA 13", edition="2019",
                     reference="TEST-LOCATOR-NOT-A-SECTION", access_method="test", accessed_at="2026-09-24")

    def mk(rid, constraint, params):
        return Rule(rule_id=rid, category="other", title="placeholder (no content)", parameters=params,
                    constraint=constraint, source=src, author="a", authored_at="2026-09-24")
    base = mk("P-1", ConstraintTemplate(key="k.base", measurement="array_axis_spacing", bound="max",
                                        limit_parameter="limit"),
              [RuleParameter(name="limit", value=Quantity(value=1.0, unit="ft"))])
    good = mk("P-2", ConstraintTemplate(key="k.derived", measurement="perpendicular_wall_distance", bound="max",
                                        reference_kinds=["wall"], derived=DerivedLimit(from_key="k.base",
                                                                                       factor_parameter="f")),
              [RuleParameter(name="f", value=0.123)])
    orphan = mk("P-3", ConstraintTemplate(key="k.orphan", measurement="perpendicular_wall_distance", bound="max",
                                          reference_kinds=["wall"], derived=DerivedLimit(from_key="k.elsewhere",
                                                                                         factor_parameter="f")),
                [RuleParameter(name="f", value="0.5")])
    draft = empty_draft(NFPA13_2019_BASE).model_copy(update={"rules": [base, good, orphan]})
    assert approval_problems(good, draft) == []
    probs = " | ".join(approval_problems(orphan, draft))
    assert "not a constraint of this rule set" in probs and "not a dimensionless number" in probs
