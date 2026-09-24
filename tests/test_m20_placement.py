"""Milestone 2.0: deterministic single-space placement — known-answer tests.

Every expected answer below is derived BY HAND (the derivation is in each docstring) from the
generated room geometry and the TEST ONLY synthetic rule values — never by running FireAI first.
Room coordinates: LOCAL minus the wall thickness (0.5 ft); lattice positions are k * step, strictly
inside the room. Synthetic values are NOT NFPA 13 requirements.
"""

from __future__ import annotations

import math

import pytest

from fireai.engineering import SYNTHETIC_DISCLAIMERS, EngineeringDesignResult, explain_layout, run_design
from fixtures import builders as B
from fixtures import synthetic_design as S


def _pkg(tmp_path, name="r", unit="ft", **kw):
    d = tmp_path / name
    d.mkdir()
    return S.verified_package(d, B.make_room(d / "room.dxf", unit, **kw))


def _run(pkg, **kw):
    return run_design(S.request(pkg, **kw))


# ── 1-7 geometry known answers ───────────────────────────────────────────────

def test_01_rectangle_with_exactly_one_valid_placement(tmp_path):
    """10x10 room, one sprinkler, worst wall point <= 7.08 ft. A point (u,v) is at most as far from a
    wall point as from the farthest corner: sqrt(max(u,10-u)^2 + max(v,10-v)^2). Only (5,5) gives
    sqrt(50)=7.071; the next best lattice point (5,6) gives sqrt(61)=7.81."""
    r = _run(_pkg(tmp_path), rule_sets=[S.base_ruleset(max_boundary=7.08)], srch=S.search(1.0, 1))
    assert r.status == "VALID_LAYOUTS_FOUND" and S.room_positions(r) == [[(5.0, 5.0)]]
    (e,) = r.valid_layouts[0].evaluations
    assert e.measured == pytest.approx(math.sqrt(50)) and e.margin == pytest.approx(7.08 - math.sqrt(50))


def test_02_rectangle_requiring_multiple_sprinklers(tmp_path):
    """20x10 room. One sprinkler: >= sqrt(10^2+5^2)=11.18 > 7.08, impossible. Two in a row (a,b),(a+s,b):
    corners need b=5 and a<=5, a+s>=15; the top-wall point midway needs sqrt((s/2)^2+25)<=7.08 -> s<=10.
    Hence a=5, s=10 only; a vertical pair cannot reach both end walls."""
    r = _run(_pkg(tmp_path, w=20, h=10), rule_sets=[S.base_ruleset(max_boundary=7.08)], srch=S.search(1.0, 2))
    assert S.room_positions(r) == [[(5.0, 5.0), (15.0, 5.0)]]


@pytest.mark.parametrize("limit,expected", [(10.0, [[(5.0, 5.0), (15.0, 5.0)]]), (9.99, [])])
def test_03_04_spacing_threshold_inside_and_outside(tmp_path, limit, expected):
    """Test 2's only layout has 10.0 ft spacing: a 10.0 limit passes (equality, tolerance 1e-9); 9.99 fails."""
    r = _run(_pkg(tmp_path, w=20, h=10), rule_sets=[S.base_ruleset(max_boundary=7.08, max_spacing=limit)],
             srch=S.search(1.0, 2))
    assert S.room_positions(r) == expected
    if not expected:
        assert r.status == "NO_VALID_LAYOUT_IN_SEARCH_SPACE"
        # spacing is checked before the boundary distance (documented order), so it is a recorded reason
        assert r.search["rejected_by_first_failure"].get("sprinkler.max_axis_spacing", 0) >= 1


@pytest.mark.parametrize("limit,count", [(4.0, 9), (4.001, 1)])
def test_05_wall_distance_threshold(tmp_path, limit, count):
    """10x10 room, one sprinkler, distance to every wall >= limit: min(u,10-u,v,10-v) >= 4 -> u,v in {4,5,6}
    (9 positions, 4.0 exactly on the limit passes); >= 4.001 leaves only (5,5)."""
    r = _run(_pkg(tmp_path), rule_sets=[S.base_ruleset(min_wall=limit)], srch=S.search(1.0, 1))
    assert len(r.valid_layouts) == count


def test_06_narrow_room(tmp_path):
    """12x3 room, lattice 1.5 ft: v can only be 1.5, u in {1.5..10.5}. Worst wall point <= 3.0: end corners
    need sqrt(a^2+1.5^2)<=3 -> first=1.5, last=10.5; between neighbours sqrt((s/2)^2+2.25)<=3 -> s<=5.196,
    so s in {1.5, 3, 4.5}: 7, 4 or 3 sprinklers. u (the long axis) runs along the 12 ft side."""
    r = _run(_pkg(tmp_path, w=12, h=3), rule_sets=[S.base_ruleset(max_boundary=3.0)], srch=S.search(1.5, 7))
    assert S.room_positions(r) == [[(1.5, 1.5), (6.0, 1.5), (10.5, 1.5)],
                                   [(1.5, 1.5), (4.5, 1.5), (7.5, 1.5), (10.5, 1.5)],
                                   [(x * 1.5, 1.5) for x in range(1, 8)]]
    assert r.context["room_frame"]["u_axis"] == [1.0, 0.0] and r.context["room_frame"]["extent_u_ft"] == pytest.approx(12)


def test_07_l_shaped_room(tmp_path):
    """L = 10x10 minus the top-right 5x5. One sprinkler, >= 2 ft from every wall (incl. the re-entrant
    walls): lower arm u in 5..8 needs v in {2,3} (8); u in 2..4, v in 2..4 except (4,4) whose distance to the
    inner corner (5,5) is 1.41 (8); upper arm u in {2,3}, v in 5..8 (8). Total 24. The 25 lattice points in
    or on the notch are rejected as outside the space."""
    r = _run(_pkg(tmp_path, notch=(5, 5)), rule_sets=[S.base_ruleset(min_wall=2.0, min_wall_kinds=("wall",))],
             srch=S.search(1.0, 1))
    assert len(r.valid_layouts) == 24
    assert r.search["rejected_by_first_failure"]["structural:outside_space"] == 25
    pos = {p[0] for p in S.room_positions(r)}
    assert (4.0, 4.0) not in pos and (3.0, 5.0) in pos and (6.0, 6.0) not in pos


# ── 8-10 boundary semantics ──────────────────────────────────────────────────

@pytest.mark.parametrize("kinds,count", [(("wall",), 50), (("wall", "door_opening"), 49)])
def test_08_door_opening_is_not_a_wall(tmp_path, kinds, count):
    """10x10 room, 4 ft door on the right wall (v 3..7). >= 2 ft from WALLS: the 7x7 block u,v in 2..8 (49)
    plus (9,5), whose nearest wall points are the door jambs (10,3)/(10,7) at sqrt(5)=2.24. Counting the door
    opening as a reference (a rule could) removes (9,5)."""
    pkg = _pkg(tmp_path, door=("right", 3, 7))
    reg = pkg.spaces[0]
    assert reg.boundary.length_by_kind_ft == {"door_opening": 4.0, "wall": 36.0}
    r = _run(pkg, rule_sets=[S.base_ruleset(min_wall=2.0, min_wall_kinds=kinds)], srch=S.search(1.0, 1))
    assert len(r.valid_layouts) == count
    assert ([(9.0, 5.0)] in S.room_positions(r)) is (count == 50)


@pytest.mark.parametrize("kinds,count", [(("wall",), 50), (("wall", "window"), 49)])
def test_09_window_is_distinguishable(tmp_path, kinds, count):
    """Same arithmetic mirrored: a 4 ft window in the left wall (v 3..7); (1,5) is valid only if the rule's
    reference excludes windows."""
    pkg = _pkg(tmp_path, window=("left", 3, 7))
    assert pkg.spaces[0].boundary.length_by_kind_ft == {"wall": 36.0, "window": 4.0}
    r = _run(pkg, rule_sets=[S.base_ruleset(min_wall=2.0, min_wall_kinds=kinds)], srch=S.search(1.0, 1))
    assert len(r.valid_layouts) == count


def _area_with_one_wall(path):
    import ezdxf
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


def test_10_unknown_boundary_refuses(tmp_path):
    pkg = S.verified_package(tmp_path, _area_with_one_wall(tmp_path / "a.dxf"))
    r = _run(pkg, rule_sets=[S.base_ruleset(max_boundary=20)])
    assert r.status == "REFUSED" and r.valid_layouts == [] and r.engineering_use == "NONE_REFUSED"
    assert any(i.code == "SPACE_NOT_ENGINEERABLE" and "not established" in i.message for i in r.refusals)


# ── 11-14, 20 missing / unknown inputs, impossibility ────────────────────────

@pytest.mark.parametrize("missing,code", [("ceil", "MISSING_CEILING_CONDITION"), ("cls", "MISSING_DESIGN_CLASSIFICATION"),
                                          ("lst", "MISSING_SPRINKLER_LISTING"), ("tol", "MISSING_TOLERANCES")])
def test_11_12_13_missing_inputs_refuse(tmp_path, missing, code):
    r = _run(_pkg(tmp_path), rule_sets=[S.base_ruleset(max_boundary=7.08)], **{missing: None})
    assert r.status == "REFUSED" and code in {i.code for i in r.refusals} and not r.valid_layouts
    if missing == "cls":                          # rules conditioned on the classification cannot be resolved
        assert "RULE_APPLICABILITY_UNKNOWN" in {i.code for i in r.refusals}


def test_11b_unsupported_ceiling_refuses(tmp_path):
    pkg = _pkg(tmp_path)
    c = S.ceiling(pkg.semantic_spaces[0].uid)
    c.regions[0].surface, c.obstructions_statement = "sloped", "unknown"
    r = _run(pkg, rule_sets=[S.base_ruleset(max_boundary=7.08)], ceil=c)
    msgs = [i.message for i in r.refusals if i.code == "CEILING_NOT_SUPPORTED_IN_M2_0"]
    assert r.status == "REFUSED" and any("sloped" in m for m in msgs) and any("obstructions" in m for m in msgs)


def test_14_impossible_design(tmp_path):
    """20x10 room with ONE sprinkler cannot meet 7.08 ft (best 11.18, see test 2): no layout, reasons counted."""
    r = _run(_pkg(tmp_path, w=20, h=10), rule_sets=[S.base_ruleset(max_boundary=7.08)], srch=S.search(1.0, 1))
    assert r.status == "NO_VALID_LAYOUT_IN_SEARCH_SPACE" and r.valid_layouts == []
    assert r.search["rejected_by_first_failure"] == {"sprinkler.max_boundary_distance": 19 * 9}
    assert r.rejected_examples and not r.rejected_examples[0].valid


def test_20_unknown_critical_fact_refuses_instead_of_assuming(tmp_path):
    """A rule conditioned on the sprinkler's response type, and a listing that does not state it."""
    from fireai.rules import Condition, RuleApplicability
    extra = S.rule("SYN-B-RESP", "sprinkler.min_spacing", "pairwise_min_distance", "min", 1.0, category="spacing",
                   applicability=RuleApplicability(all_of=[Condition(fact="sprinkler.response", op="eq", value="X")]))
    r = _run(_pkg(tmp_path), rule_sets=[S.base_ruleset(max_boundary=7.08, extra=[extra])], lst=S.listing(response=None))
    assert r.status == "REFUSED"
    (i,) = [i for i in r.refusals if i.code == "RULE_APPLICABILITY_UNKNOWN"]
    assert "sprinkler.response" in i.message


# ── 15-19 equivalence, determinism, rule layering without geometry changes ───

@pytest.mark.parametrize("unit", ["in", "mm", "m"])
def test_15_imperial_metric_equivalence(tmp_path, unit):
    rs = [S.base_ruleset(max_boundary=7.08, min_wall=2.0)]
    a = _run(_pkg(tmp_path, "ft", "ft", w=20, h=10, door=("top", 8, 11)), rule_sets=rs, srch=S.search(1.0, 2))
    b = _run(_pkg(tmp_path, unit, unit, w=20, h=10, door=("top", 8, 11)), rule_sets=rs, srch=S.search(1.0, 2))
    assert a.status == b.status == "VALID_LAYOUTS_FOUND"
    pa, pb = S.room_positions(a), S.room_positions(b)
    assert len(pa) == len(pb) and all(abs(x - y) < 1e-6 for la, lb in zip(pa, pb, strict=True)
                                      for p, q in zip(la, lb, strict=True) for x, y in zip(p, q, strict=True))
    assert a.search["rejected_by_first_failure"] == b.search["rejected_by_first_failure"]


def test_16_repeated_runs_are_identical(tmp_path):
    pkg = _pkg(tmp_path, w=20, h=10)
    req = S.request(pkg, rule_sets=[S.base_ruleset(max_boundary=7.08)], srch=S.search(1.0, 2))
    r1, r2 = run_design(req), run_design(req)
    assert S.dumps(r1) == S.dumps(r2) and r1.result_fingerprint == r2.result_fingerprint
    assert [p.uid for p in r1.valid_layouts[0].placements] == [p.uid for p in r2.valid_layouts[0].placements]


def test_17_ruleset_version_changes_result_not_geometry_code(tmp_path):
    """v1 (7.08 ft) admits (5,5) (test 1); v2 (6.0 ft) admits nothing (best is 7.07)."""
    pkg = _pkg(tmp_path)
    v1, v2 = S.base_ruleset(max_boundary=7.08, version="1"), S.base_ruleset(max_boundary=6.0, version="2")
    r1, r2 = _run(pkg, rule_sets=[v1]), _run(pkg, rule_sets=[v2])
    assert len(r1.valid_layouts) == 1 and r2.status == "NO_VALID_LAYOUT_IN_SEARCH_SPACE"
    assert r1.rules["rule_sets"][0]["digest"] != r2.rules["rule_sets"][0]["digest"]
    assert r1.request_fingerprint != r2.request_fingerprint


def test_18_jurisdiction_modifier_changes_a_constraint(tmp_path):
    """Replacement (permitted by the base rule) to 8.0 ft: valid iff max(u,10-u)^2+max(v,10-v)^2 <= 64 ->
    (5,5), (5,4), (5,6), (4,5), (6,5) = 5. Tightening (no replacement) to 6.9 ft: most restrictive wins -> 0."""
    pkg = _pkg(tmp_path)
    base = S.base_ruleset(max_boundary=7.08, boundary_replaceable_by=["jurisdiction_amendment"])
    relax = S.layer_ruleset("jurisdiction_amendment", [S.rule(
        "SYN-J-MAXBOUND", "sprinkler.max_boundary_distance", "boundary_point_to_nearest_sprinkler_max", "max", 8.0,
        kinds=["wall", "window"], replaces=["SYN-B-MAXBOUND"])], "TEST_ONLY_SYNTHETIC_JURISDICTION_X")
    r = _run(pkg, rule_sets=[base, relax])
    assert sorted(p[0] for p in S.room_positions(r)) == [(4.0, 5.0), (5.0, 4.0), (5.0, 5.0), (5.0, 6.0), (6.0, 5.0)]
    (c,) = r.rules["constraints"]
    assert c["governing_rule_id"] == "SYN-J-MAXBOUND"
    assert {x["rule_id"]: x["action"] for x in c["contributions"]} == {"SYN-B-MAXBOUND": "replaced", "SYN-J-MAXBOUND": "governs"}
    tighten = S.layer_ruleset("jurisdiction_amendment", [S.rule(
        "SYN-J-TIGHT", "sprinkler.max_boundary_distance", "boundary_point_to_nearest_sprinkler_max", "max", 6.9,
        kinds=["wall", "window"])], "TEST_ONLY_SYNTHETIC_JURISDICTION_Y")
    r2 = _run(pkg, rule_sets=[S.base_ruleset(max_boundary=7.08), tighten])
    assert r2.status == "NO_VALID_LAYOUT_IN_SEARCH_SPACE" and r2.rules["constraints"][0]["limit"] == 6.9


def test_19_listing_constraint_stricter_than_base(tmp_path):
    """Base: >= 2 ft from walls; listing: >= 4.001 ft. Most restrictive governs -> only (5,5) (see test 5)."""
    lst = S.listing(rules=[S.rule("SYN-L-MINWALL", "sprinkler.min_boundary_distance", "point_to_boundary_min",
                                  "min", 4.001, kinds=["wall", "window"])])
    r = _run(_pkg(tmp_path), rule_sets=[S.base_ruleset(min_wall=2.0)], lst=lst)
    assert S.room_positions(r) == [[(5.0, 5.0)]]
    (c,) = r.rules["constraints"]
    assert c["governing_rule_id"] == "SYN-L-MINWALL"
    assert {x["rule_id"]: x["action"] for x in c["contributions"]} == {"SYN-B-MINWALL": "less_restrictive",
                                                                       "SYN-L-MINWALL": "governs"}


# ── outputs: 3D-capable objects, provenance, explanation, TEST ONLY marking ──

def test_placements_are_3d_capable_design_objects_with_provenance(tmp_path):
    pkg = _pkg(tmp_path, w=20, h=10)
    r = _run(pkg, rule_sets=[S.base_ruleset(max_boundary=7.08, max_cell=100.0)], srch=S.search(1.0, 2))
    lay = r.valid_layouts[0]
    for p in lay.placements:
        assert p.position.frame == "LOCAL" and p.position.units == "ft"
        assert p.position.z.status == "unknown" and p.position.z.value_ft is None      # never invented
        assert p.position.z.reference == "ceiling_region:TEST_ONLY_SYNTHETIC_CEILING_R1"
        prov = p.provenance
        assert prov.origin == "design" and prov.package_content_fingerprint == pkg.content_fingerprint
        assert prov.package_verification_fingerprint == pkg.verification_fingerprint
        assert prov.rule_sets[0]["digest"] and prov.listing["listing_id"].startswith("TEST_ONLY_SYNTHETIC")
    assert [p.index for p in lay.placements] == [0, 1] and lay.placements[0].position.room_uv[0] < lay.placements[1].position.room_uv[0]
    cell = next(e for e in lay.evaluations if e.constraint_key == "sprinkler.max_cell_area")
    assert cell.measured == pytest.approx(100.0) and cell.passed          # each half of the 20x10 room: 10 x 10


def test_synthetic_results_are_marked_in_structured_fields(tmp_path):
    r = _run(_pkg(tmp_path), rule_sets=[S.base_ruleset(max_boundary=7.08)])
    assert r.basis == "synthetic_test_only" and r.engineering_use == "NOT_FOR_ENGINEERING_USE"
    assert all(d in r.disclaimers for d in SYNTHETIC_DISCLAIMERS)
    ev = r.valid_layouts[0].evaluations[0]
    assert all("TEST_ONLY_SYNTHETIC" in ref for ref in ev.references) and ev.governing_rule_id.startswith("SYN-")
    data = r.model_dump(mode="json", by_alias=True)
    data["disclaimers"], data["engineering_use"] = [], "REQUIRES_QUALIFIED_HUMAN_APPROVAL"
    with pytest.raises(ValueError):                      # cannot be re-labelled as engineering output
        EngineeringDesignResult.model_validate(data)


def test_explain_any_candidate(tmp_path):
    pkg = _pkg(tmp_path)
    req = S.request(pkg, rule_sets=[S.base_ruleset(max_boundary=7.08, min_wall=1.0)])
    r = run_design(req)
    rej = r.rejected_examples[0]
    full = explain_layout(req, rej.layout_uid)
    assert full.layout_uid == rej.layout_uid and not full.valid and len(full.evaluations) == 2
    assert all(e.measured is not None and e.limit is not None and e.governing_rule_id for e in full.evaluations)


def test_engineering_mode_refuses_synthetic_or_missing_nfpa13_rules(tmp_path):
    pkg = _pkg(tmp_path)
    r = _run(pkg, rule_sets=[S.base_ruleset(max_boundary=7.08)], mode="engineering")
    codes = {i.code for i in r.refusals}
    assert r.status == "REFUSED" and {"SYNTHETIC_RULES_IN_ENGINEERING_MODE", "LISTING_NOT_APPROVED_AUTHORITATIVE",
                                      "SYNTHETIC_INPUT_IN_ENGINEERING_MODE", "NO_APPROVED_NFPA13_RULESET",
                                      "JURISDICTION_NOT_SPECIFIED"} <= codes
    r2 = _run(pkg, rule_sets=[], mode="engineering", lst=None, ceil=None, cls=None, tol=None)
    assert {"NO_APPROVED_NFPA13_RULESET", "MISSING_SPRINKLER_LISTING", "MISSING_CEILING_CONDITION",
            "MISSING_DESIGN_CLASSIFICATION", "MISSING_TOLERANCES"} <= {i.code for i in r2.refusals}
