"""Milestone 2.2A.1: branch-line orientation (S / L semantics), the NFPA 13-2019 development envelope,
source authorization and the external release gate.

SYNTHETIC (TEST ONLY) rule values and generated spaces only; expected numbers are derived by hand in
the docstrings. Authorization records are written only to temporary stores. Hypothetical
COMMERCIAL_AUTHORIZED records use fictitious editions (``TEST-HYPOTHETICAL-*``) — the owner's real
state (2019 INTERNAL_R_AND_D_ONLY, 2025 NOT_AVAILABLE) is never promoted, not even in a test."""

from __future__ import annotations

import math
from datetime import date

import pytest
from pydantic import ValidationError

from fireai.config import Settings
from fireai.engineering import run_design
from fireai.engineering.design import CandidateProposal
from fireai.engineering.envelope import envelope_blockers
from fireai.engineering.geometry import room_frame
from fireai.engineering.inputs import (CeilingFeature, EligibilityCriterion, EligibilityDecision, LayoutOrientation,
                                       Slope)
from fireai.engineering.placement import default_orientation, evaluate_proposal
from fireai.rules import ConstraintTemplate, Quantity, Rule, RuleParameter, RuleSet, RuleSource
from fireai.rules.authorization import (AuthorityActor, AuthorizationError, AuthorizationStore, SourceAuthorization,
                                        seed_owner_declared, source_key)
from fireai.rules.catalog import NFPA13_2019_BASE, NFPA13_2025_BASE, empty_draft, envelope_for, envelope_record
from fireai.rules.release import (DevelopmentOverride, ReleaseBlocked, assert_ruleset_release_eligible,
                                  evaluate_release, guard_rule_set_activation)
from fireai.rules.resolve import resolve
from fixtures import builders as B
from fixtures import commercial_2019 as C
from fixtures import synthetic_design as S

TODAY = date(2026, 9, 24)
OWNER = AuthorityActor(name="owner (test store)", role="owner")
OVERRIDE = DevelopmentOverride(enabled_by="developer (test)", reason="internal R&D of the 2019 measurement mapping")


# ── helpers ───────────────────────────────────────────────────────────────────

def _pkg(tmp_path, name="r", **kw):
    d = tmp_path / name
    d.mkdir()
    return S.verified_package(d, B.make_room(d / "room.dxf", **kw))


def _frame(pkg):
    return room_frame([tuple(p) for p in pkg.spaces[0].polygon_local_ft])


def _at(pkg, room_uv):
    fr = _frame(pkg)
    return [fr.to_xy(u, v) for u, v in room_uv]


def ori(pkg, axis="u", sign=1.0):
    """Branch lines along the room frame's u (long) or v (short) axis — stated explicitly."""
    fr = _frame(pkg)
    d = (fr.ux, fr.uy) if axis == "u" else (-fr.uy, fr.ux)
    return LayoutOrientation(branch_line_direction=(sign * d[0], sign * d[1]), strategy="explicit_design_input",
                             source=S.SYN)


def sxl(measurement, limit, unit, kinds=("wall",), rid=None):
    key = {"array_sxl_protection_area": "sprinkler.max_protection_area_sxl", "array_sxl_s_dimension": "sprinkler.max_s",
           "array_sxl_l_dimension": "sprinkler.max_l"}[measurement]
    return S.rule(rid or f"SYN-{key}", key, measurement, "max", limit, unit=unit, kinds=list(kinds),
                  category="protection_area")


def rs(*rules):
    return S.base_ruleset(extra=list(rules))


def _evaluate(pkg, positions, orientation, *rules):
    req = S.request(pkg, rule_sets=[rs(*rules)]).model_copy(update={"orientation": orientation})
    prop = CandidateProposal(proposed_by="fixture", package_content_fingerprint=pkg.content_fingerprint,
                             package_verification_fingerprint=pkg.verification_fingerprint,
                             rule_sets=[{"rule_set_id": s.rule_set_id, "version": s.version, "digest": s.digest()}
                                        for s in req.rule_sets],
                             listing={"listing_id": req.listing.listing_id, "version": req.listing.version,
                                      "digest": req.listing.digest()}, positions=_at(pkg, positions))
    ev = evaluate_proposal(req, prop)
    return ev, {e.measurement: e for e in ev.evaluations}


ALL3 = lambda: (sxl("array_sxl_protection_area", 1e6, "sf"), sxl("array_sxl_s_dimension", 1e6, "ft"),  # noqa: E731
                sxl("array_sxl_l_dimension", 1e6, "ft"))


def _per(e, idx):
    return e.worst_subject["per_sprinkler"][idx]


# ── 1-15: S / L follow the explicit branch-line orientation ──────────────────

def test_01_02_03_S_follows_branch_lines_L_is_perpendicular_and_the_long_axis_does_not_define_S(tmp_path):
    """24 x 10 (long axis u), sprinklers (7, 5), (17, 5).
    Branch lines along u: S = max(2 x 7, 10) = 14, L = max(2 x 5, 2 x 5) = 10.
    Branch lines along v (the SHORT axis): S = 10, L = 14. The S x L product is 140 either way, but a
    limit on S alone (13 ft) FAILS for u-branch lines and PASSES for v-branch lines."""
    pkg = _pkg(tmp_path, w=24, h=10)
    pos = [(7, 5), (17, 5)]
    ev_u, e_u = _evaluate(pkg, pos, ori(pkg, "u"), sxl("array_sxl_s_dimension", 13.0, "ft"),
                          sxl("array_sxl_l_dimension", 13.0, "ft"), sxl("array_sxl_protection_area", 140.0, "sf"))
    ev_v, e_v = _evaluate(pkg, pos, ori(pkg, "v"), sxl("array_sxl_s_dimension", 13.0, "ft"),
                          sxl("array_sxl_l_dimension", 13.0, "ft"), sxl("array_sxl_protection_area", 140.0, "sf"))
    assert (e_u["array_sxl_s_dimension"].measured, e_u["array_sxl_l_dimension"].measured) == pytest.approx((14.0, 10.0))
    assert (e_v["array_sxl_s_dimension"].measured, e_v["array_sxl_l_dimension"].measured) == pytest.approx((10.0, 14.0))
    assert e_u["array_sxl_protection_area"].measured == e_v["array_sxl_protection_area"].measured == pytest.approx(140.0)
    assert (e_u["array_sxl_s_dimension"].outcome, e_v["array_sxl_s_dimension"].outcome) == ("fail", "pass")
    assert (e_u["array_sxl_l_dimension"].outcome, e_v["array_sxl_l_dimension"].outcome) == ("pass", "fail")
    assert e_v["array_sxl_s_dimension"].worst_subject["S_axis"] == "v"          # S on the room's SHORT axis
    assert e_v["array_sxl_s_dimension"].worst_subject["orientation"]["strategy"] == "explicit_design_input"
    assert (ev_u.verdict, ev_v.verdict) == ("FAIL", "FAIL")


def test_04_missing_ambiguous_or_misaligned_orientation_refuses(tmp_path):
    pkg = _pkg(tmp_path, w=24, h=10)
    rule = sxl("array_sxl_protection_area", 140.0, "sf")
    r = run_design(S.request(pkg, rule_sets=[rs(rule)]))
    assert r.status == "REFUSED" and "LAYOUT_ORIENTATION_MISSING" in {i.code for i in r.refusals}
    ev, _ = _evaluate(pkg, [(7, 5), (17, 5)], None, rule)
    assert ev.verdict == "REFUSED" and "LAYOUT_ORIENTATION_MISSING" in {i.code for i in ev.reasons}
    diag = LayoutOrientation(branch_line_direction=(1.0, 1.0), strategy="explicit_design_input", source=S.SYN)
    r = run_design(S.request(pkg, rule_sets=[rs(rule)]).model_copy(update={"orientation": diag}))
    assert "ORIENTATION_NOT_ALIGNED_WITH_ARRAY_FRAME" in {i.code for i in r.refusals}
    square = _pkg(tmp_path, "sq", w=10, h=10)
    assert default_orientation(square.spaces[0], S.SYN) is None                    # long axis ambiguous
    d = default_orientation(pkg.spaces[0], S.SYN)
    assert d.strategy == "ROOM-LONG-AXIS-DEFAULT/1"                               # a named strategy, not S itself


def test_05_rotated_room_and_orientation(tmp_path):
    """The 24 x 10 room rotated 30 deg: branch lines along its long side give S = 14; along its short
    side S = 10 — as in tests 1-2. A direction is a line: the opposite vector is the same orientation."""
    a = math.radians(30.0)
    ring = [(50 + x * math.cos(a) - y * math.sin(a), 50 + x * math.sin(a) + y * math.cos(a))
            for x, y in [(0, 0), (24, 0), (24, 10), (0, 10)]]
    d = tmp_path / "rot"
    d.mkdir()
    pkg = S.verified_package(d, B.make_polygon_room(d / "r.dxf", ring))
    pos = [(7, 5), (17, 5)]
    for axis, s_exp in (("u", 14.0), ("v", 10.0)):
        for sign in (1.0, -1.0):
            _ev, e = _evaluate(pkg, pos, ori(pkg, axis, sign), *ALL3())
            assert e["array_sxl_s_dimension"].measured == pytest.approx(s_exp, abs=1e-6)
    assert ori(pkg, "u", -1.0).branch_line_direction == pytest.approx(ori(pkg, "u").branch_line_direction)


def test_06_to_10_interior_and_end_conditions(tmp_path):
    """42 x 32, branch lines along u: 4 x 3 array at u = 6, 16, 26, 36 and v = 6, 16, 26 (walls 6 ft away).
    index = j * 4 + i.
      interior (i=1, j=1, idx 5): S = 10, L = 10                          [test 6 / 8]
      end of a branch line (i=0, j=1, idx 4): S = max(2 x 6, 10) = 12 from -u (twice wall), L = 10   [7 / 10]
      end line (i=1, j=0, idx 1): L = max(2 x 6, 10) = 12 from -v (twice wall), S = 10              [9]"""
    pkg = _pkg(tmp_path, w=42, h=32)
    pos = [(u, v) for v in (6, 16, 26) for u in (6, 16, 26, 36)]
    _ev, e = _evaluate(pkg, pos, ori(pkg, "u"), *ALL3())
    area = e["array_sxl_protection_area"]
    p5, p4, p1 = _per(area, 5), _per(area, 4), _per(area, 1)
    assert (p5["S_ft"], p5["L_ft"], p5["area_sf"]) == pytest.approx((10.0, 10.0, 100.0))
    assert (p4["S_ft"], p4["S_from"], p4["L_ft"]) == pytest.approx((12.0, "-u", 10.0))
    assert (p1["L_ft"], p1["L_from"], p1["S_ft"]) == pytest.approx((12.0, "-v", 10.0))
    assert area.measured == pytest.approx(144.0)                                   # corners: 12 x 12
    s_side = next(x for x in area.worst_subject["S"]["sides"] if x["from"] == "twice_wall_distance")
    assert s_side["wall"]["distance_ft"] == pytest.approx(6.0) and s_side["value_ft"] == pytest.approx(12.0)


def test_11_adjacent_spacing_controls(tmp_path):
    """20 x 10, (4, 5), (14, 5), branch lines along u: sprinkler 0 S = max(2 x 4, 10) = 10 from +u (adjacent)."""
    pkg = _pkg(tmp_path, w=20, h=10)
    _ev, e = _evaluate(pkg, [(4, 5), (14, 5)], ori(pkg, "u"), *ALL3())
    p0 = _per(e["array_sxl_protection_area"], 0)
    assert (p0["S_ft"], p0["S_from"]) == pytest.approx((10.0, "+u"))


def test_12_asymmetric_spacing_selects_the_controlling_dimension(tmp_path):
    """26 x 10, branch line u = 4, 12, 22 (spacings 8 and 10), v = 5.
      idx 0: -u 2 x 4 = 8, +u 8 -> S = 8;  idx 1: -u 8, +u 10 -> S = 10 (+u);
      idx 2: -u 10, +u 2 x 4 = 8 -> S = 10 (-u). Worst S = 10."""
    pkg = _pkg(tmp_path, w=26, h=10)
    _ev, e = _evaluate(pkg, [(4, 5), (12, 5), (22, 5)], ori(pkg, "u"), *ALL3())
    per = e["array_sxl_protection_area"].worst_subject["per_sprinkler"]
    assert [(p["S_ft"], p["S_from"]) for p in per[1:]] == [(pytest.approx(10.0), "+u"), (pytest.approx(10.0), "-u")]
    assert per[0]["S_ft"] == pytest.approx(8.0)
    assert e["array_sxl_s_dimension"].measured == pytest.approx(10.0)


def test_13_branch_lines_across_the_long_axis(tmp_path):
    """40 x 20 (long axis u), branch lines along v; sprinklers u = 10, 30 and v = 5, 15.
    idx 0 (10, 5): S (along v) = max(2 x 5, 10) = 10; L (along u) = max(2 x 10, 20) = 20; area 200.
    A 15 ft S limit PASSES with v-branch lines and FAILS if S were (wrongly) the long axis (S = 20)."""
    pkg = _pkg(tmp_path, w=40, h=20)
    pos = [(10, 5), (30, 5), (10, 15), (30, 15)]
    ev_v, e_v = _evaluate(pkg, pos, ori(pkg, "v"), sxl("array_sxl_s_dimension", 15.0, "ft"))
    ev_u, e_u = _evaluate(pkg, pos, ori(pkg, "u"), sxl("array_sxl_s_dimension", 15.0, "ft"))
    s = e_v["array_sxl_s_dimension"]
    assert (s.measured, s.worst_subject["S_axis"], s.worst_subject["L_ft"]) == (pytest.approx(10.0), "v",
                                                                                 pytest.approx(20.0))
    assert (ev_v.verdict, ev_u.verdict) == ("PASS", "FAIL")


def test_14_explanation_identifies_the_controlling_geometry(tmp_path):
    pkg = _pkg(tmp_path, w=24, h=10)
    _ev, e = _evaluate(pkg, [(7, 5), (17, 5)], ori(pkg, "u"), sxl("array_sxl_protection_area", 140.0, "sf"))
    subj = e["array_sxl_protection_area"].worst_subject
    assert subj["S"]["governed_by"] == "-u" and subj["L"]["governed_by"] in ("-v", "+v")
    wall = next(x for x in subj["S"]["sides"] if x["direction"] == "-u")["wall"]
    assert wall["kind"] == "wall" and wall["segment_uid"] and wall["status"] == "ok"
    assert subj["orientation"]["branch_line_axis"] == "u"
    assert "long axis does not define S" in subj["orientation"]["semantics"]


def test_15_imperial_metric_equivalence(tmp_path):
    got = {}
    for unit in ("ft", "m"):
        pkg = _pkg(tmp_path, unit, unit=unit, w=24, h=10)
        k, a = (1.0, 1.0) if unit == "ft" else (0.3048, 0.09290304)
        ev, e = _evaluate(pkg, [(7, 5), (17, 5)], ori(pkg, "v"),
                          sxl("array_sxl_s_dimension", 13.0 * k, unit if unit == "ft" else "m"),
                          sxl("array_sxl_l_dimension", 13.0 * k, unit if unit == "ft" else "m"),
                          sxl("array_sxl_protection_area", 140.0 * a, "sf" if unit == "ft" else "m2"))
        got[unit] = (ev.verdict, {m: (x.measured, x.outcome) for m, x in e.items()})
    assert got["ft"][0] == got["m"][0]
    for m, (val, outcome) in got["ft"][1].items():
        assert got["m"][1][m][0] == pytest.approx(val, abs=1e-6) and got["m"][1][m][1] == outcome


# ── 16-23: NFPA13-2019-DEV-ENVELOPE-1 ────────────────────────────────────────

@pytest.fixture(scope="module")
def commercial(tmp_path_factory):
    return C.space(tmp_path_factory.mktemp("commercial"))


def _blockers(req, base=None):
    return envelope_blockers(req, [base or empty_draft(NFPA13_2019_BASE)])


def _conds(blockers):
    return {b.detail.get("condition") for b in blockers if b.code == "OUTSIDE_SUPPORTED_ENVELOPE"}


def test_16_the_exact_2019_envelope_combination_is_recognised(commercial):
    req = C.request(commercial, [empty_draft(NFPA13_2019_BASE)])
    # M2.2B.2: every FACT matches; the only envelope blocker left is that the (empty) rule set has no rule
    # evaluating the response type, which the envelope requires
    assert [b.code for b in _blockers(req)] == ["REQUIRED_FACT_RULE_MISSING"]
    assert envelope_for("NFPA 13", "2019").envelope_id == "NFPA13-2019-DEV-ENVELOPE-1"
    r = run_design(req)                     # the envelope does not approve the (empty, draft) rule set
    codes = {i.code for i in r.refusals}
    assert r.status == "REFUSED" and "RULESET_NOT_APPROVED" in codes
    assert not codes & {"OUTSIDE_SUPPORTED_ENVELOPE", "NO_SUPPORTED_ENVELOPE", "MISSING_DESIGN_METHOD",
                        "MISSING_CONSTRUCTION_CLASSIFICATION", "MISSING_LAYOUT_ORIENTATION"}


def test_17_18_pipe_schedule_and_storage_are_outside(commercial):
    base = C.facts(commercial.semantic_spaces[0].uid)["system"]
    ps = C.request(commercial, [], system=base.model_copy(update={"design_method": "pipe_schedule"}))
    st = C.request(commercial, [], system=base.model_copy(update={"storage": "storage"}))
    assert "design method" in _conds(_blockers(ps))
    assert "storage condition" in _conds(_blockers(st))


def test_19_small_room_eligible_or_undetermined_is_outside(commercial):
    elig = EligibilityDecision(status="eligible", source=C.FIXTURE_SOURCE,
                               criteria=[EligibilityCriterion(fact="space.area_sf", value=1.0)])
    for e in ({"small_room": elig}, {"small_room": EligibilityDecision(status="unknown", source=C.FIXTURE_SOURCE)}, {}):
        b = _blockers(C.request(commercial, [], eligibility=e))
        assert "small-room eligibility" in _conds(b)


def test_20_irregular_wall_is_outside(tmp_path):
    d = tmp_path / "trap"
    d.mkdir()
    pkg = S.verified_package(d, B.make_polygon_room(d / "r.dxf", [(0, 0), (50, 0), (44, 30), (0, 30)]))
    assert "space geometry" in _conds(_blockers(C.request(pkg, [])))


def test_21_unsupported_ceiling_refuses(commercial):
    f = C.facts(commercial.semantic_spaces[0].uid)
    beam = f["ceiling"].model_copy(deep=True)
    beam.features.append(CeilingFeature(uid="B1", kind="beam", status="known"))
    sloped = f["ceiling"].model_copy(deep=True)
    sloped.regions[0].slope = Slope(status="known", value_deg=4.0, source=C.FIXTURE_SOURCE)
    sloped.regions[0].surface = "sloped"
    assert "ceiling features" in _conds(_blockers(C.request(commercial, [], ceiling=beam)))
    assert {"ceiling slope", "ceiling surface"} <= _conds(_blockers(C.request(commercial, [], ceiling=sloped)))
    r = run_design(C.request(commercial, [empty_draft(NFPA13_2019_BASE)], ceiling=beam))
    assert "CEILING_NOT_SUPPORTED_IN_M2_0" in {i.code for i in r.refusals}


def test_22_23_missing_construction_classification_design_method_or_orientation_refuse(commercial):
    f = C.facts(commercial.semantic_spaces[0].uid)
    no_cc = f["ceiling"].model_copy(deep=True)
    no_cc.regions[0].construction_classification = None
    codes = {b.code for b in _blockers(C.request(commercial, [], ceiling=no_cc))}
    assert "MISSING_CONSTRUCTION_CLASSIFICATION" in codes
    codes = {b.code for b in _blockers(C.request(commercial, [], system=f["system"].model_copy(
        update={"design_method": "unknown"})))}
    assert "MISSING_DESIGN_METHOD" in codes
    assert "MISSING_LAYOUT_ORIENTATION" in {b.code for b in _blockers(C.request(commercial, [], orientation=None))}


def test_2019_envelope_is_never_applied_to_2025_rules(commercial):
    req = C.request(commercial, [empty_draft(NFPA13_2025_BASE)])
    b25 = envelope_blockers(req, [empty_draft(NFPA13_2025_BASE)])
    assert all(x.detail.get("envelope") != "NFPA13-2019-DEV-ENVELOPE-1" for x in b25)
    assert envelope_record("NFPA 13", "2025").envelope_id == "NFPA13-2025-DEV-ENVELOPE-1"
    assert envelope_record("NFPA 13", "2025").design_methods is None  # 2019 conditions are not inherited
    assert envelope_for("NFPA 13", "2025") is None                    # M2.2B: 2025 envelope inactive


# ── 24-35: source authorization and the external release gate ───────────────

@pytest.fixture()
def store(tmp_path):
    s = AuthorizationStore(tmp_path / "auth")
    seed_owner_declared(s, "owner (test store)")
    return s


def _placeholder_set(edition: str, approved=True, rules=True) -> RuleSet:
    """An NFPA-shaped set with PLACEHOLDER content (fictitious locator, value 1.0): no NFPA requirement."""
    src = RuleSource(kind="authoritative_standard", document="NFPA 13", edition=edition,
                     reference="TEST-LOCATOR-NOT-A-SECTION", access_method="test", accessed_at="2026-09-24")
    rule = Rule(rule_id="PLACEHOLDER-1", category="spacing", title="placeholder (no content)",
                parameters=[RuleParameter(name="limit", value=Quantity(value=1.0, unit="ft"))],
                constraint=ConstraintTemplate(key="k", measurement="array_axis_spacing", bound="max",
                                              limit_parameter="limit"),
                source=src, author="a", authored_at="2026-09-24", reviewer="b", review_status="approved")
    return RuleSet(rule_set_id=f"TEST-PLACEHOLDER-{edition}", version="1", layer="base_standard",
                   governing_standard="NFPA 13", edition=edition, content_basis="authoritative",
                   review_status="approved" if approved else "draft", rules=[rule] if rules else [])


def _codes(d):
    return {b.code for b in d.blockers}


def test_24_internal_rnd_content_needs_an_explicit_internal_development_override(store):
    d19 = _placeholder_set("2019", approved=False)
    assert source_key(d19) == "NFPA 13:2019" and store.status("NFPA 13:2019") == "INTERNAL_R_AND_D_ONLY"
    no = evaluate_release([d19], "internal_development", store, TODAY)
    assert not no.eligible and "INTERNAL_DEVELOPMENT_NOT_ENABLED" in _codes(no)
    yes = evaluate_release([d19], "internal_development", store, TODAY, OVERRIDE)
    assert yes.eligible and {"INTERNAL R&D", "NOT FOR EXTERNAL USE"} <= set(yes.labels)
    assert yes.rule_sets[0].external_release_status == "INTERNAL_ONLY" and yes.override["enabled_by"]
    assert any(e["action"] == "internal_development_override_used" for e in store.events())


@pytest.mark.parametrize("context", ["beta", "production", "commercial_api", "external_engineering"])
def test_25_26_internal_rnd_content_refuses_every_external_context(store, context):
    d = evaluate_release([_placeholder_set("2019")], context, store, TODAY)
    assert not d.eligible and "SOURCE_AUTHORIZATION_INTERNAL_R_AND_D_ONLY" in _codes(d)
    assert d.rule_sets[0].external_release_status == "NOT_RELEASE_ELIGIBLE"
    with_override = evaluate_release([_placeholder_set("2019")], context, store, TODAY, OVERRIDE)
    assert "OVERRIDE_NOT_PERMITTED" in _codes(with_override)          # no bypass in any external context
    with pytest.raises(ReleaseBlocked):
        assert_ruleset_release_eligible([_placeholder_set("2019")], context, store, TODAY, OVERRIDE)


def test_26_deployment_guard_is_fail_closed(store, monkeypatch):
    monkeypatch.delenv("FIREAI_DEPLOYMENT_MODE", raising=False)
    assert Settings().deployment_mode == "production"
    with pytest.raises(ReleaseBlocked) as exc:
        guard_rule_set_activation(Settings(), [_placeholder_set("2019")], store, TODAY, OVERRIDE)
    assert "OVERRIDE_NOT_PERMITTED" in {b.code for b in exc.value.decision.blockers}
    monkeypatch.setenv("FIREAI_DEPLOYMENT_MODE", "beta")
    with pytest.raises(ReleaseBlocked):
        guard_rule_set_activation(Settings(), [_placeholder_set("2019")], store, TODAY)
    monkeypatch.setenv("FIREAI_DEPLOYMENT_MODE", "development")      # the mode alone unlocks nothing
    with pytest.raises(ReleaseBlocked):
        guard_rule_set_activation(Settings(), [_placeholder_set("2019")], store, TODAY)
    assert guard_rule_set_activation(Settings(), [_placeholder_set("2019")], store, TODAY, OVERRIDE).eligible
    monkeypatch.setenv("FIREAI_DEPLOYMENT_MODE", "staging-ish")
    with pytest.raises(ValueError):
        guard_rule_set_activation(Settings(), [_placeholder_set("2019")], store, TODAY)


def test_27_28_pending_and_unknown_refuse_external_release(store):
    key = "NFPA 13:TEST-HYPOTHETICAL-P"
    store.record(key, "COMMERCIAL_AUTHORIZATION_PENDING", OWNER, "test: pending (hypothetical edition)",
                 permitted_uses=["internal_development"])
    d = evaluate_release([_placeholder_set("TEST-HYPOTHETICAL-P")], "beta", store, TODAY)
    assert "SOURCE_AUTHORIZATION_COMMERCIAL_AUTHORIZATION_PENDING" in _codes(d)
    u = evaluate_release([_placeholder_set("TEST-HYPOTHETICAL-U")], "production", store, TODAY)
    assert "SOURCE_AUTHORIZATION_UNKNOWN" in _codes(u) and u.rule_sets[0].authorization == {"status": "UNKNOWN"}


def _authorize(store, edition, uses=("beta", "production"), **kw):
    return store.record(f"NFPA 13:{edition}", "COMMERCIAL_AUTHORIZED", OWNER, "test: hypothetical authorization",
                        authorization_reference="FIREAI-AUTH-TEST-A", effective_date=kw.pop("eff", date(2026, 1, 1)),
                        permitted_uses=list(uses), **kw)


def test_29_commercially_authorized_can_pass_the_licensing_portion(store):
    rec = _authorize(store, "TEST-HYPOTHETICAL-A", expiration_date=date(2027, 12, 31))
    d = assert_ruleset_release_eligible([_placeholder_set("TEST-HYPOTHETICAL-A")], "production", store, TODAY)
    assert d.eligible and d.rule_sets[0].external_release_status == "RELEASE_ELIGIBLE"
    assert d.rule_sets[0].authorization["digest"] == rec.digest() and d.fingerprint
    # engineering status is still evaluated separately: an unapproved or empty set is not releasable
    for bad, code in ((_placeholder_set("TEST-HYPOTHETICAL-A", approved=False), "RULESET_NOT_APPROVED"),
                      (_placeholder_set("TEST-HYPOTHETICAL-A", rules=False), "EMPTY_RULESET")):
        assert code in _codes(evaluate_release([bad], "production", store, TODAY))
    assert "AUTHORIZATION_SCOPE_EXCLUDES_USE" in _codes(
        evaluate_release([_placeholder_set("TEST-HYPOTHETICAL-A")], "commercial_api", store, TODAY))


def test_30_expired_revoked_and_not_yet_effective_refuse(store):
    _authorize(store, "TEST-HYPOTHETICAL-E", expiration_date=date(2026, 6, 30))
    assert "AUTHORIZATION_EXPIRED" in _codes(evaluate_release([_placeholder_set("TEST-HYPOTHETICAL-E")], "beta",
                                                              store, TODAY))
    _authorize(store, "TEST-HYPOTHETICAL-F", eff=date(2027, 1, 1))
    assert "AUTHORIZATION_NOT_YET_EFFECTIVE" in _codes(evaluate_release([_placeholder_set("TEST-HYPOTHETICAL-F")],
                                                                        "beta", store, TODAY))
    _authorize(store, "TEST-HYPOTHETICAL-R")
    store.record("NFPA 13:TEST-HYPOTHETICAL-R", "REVOKED", OWNER, "test: revoked")
    d = evaluate_release([_placeholder_set("TEST-HYPOTHETICAL-R")], "beta", store, TODAY)
    assert "SOURCE_AUTHORIZATION_REVOKED" in _codes(d)
    assert "SOURCE_AUTHORIZATION_REVOKED" in _codes(evaluate_release([_placeholder_set("TEST-HYPOTHETICAL-R")],
                                                                     "internal_development", store, TODAY, OVERRIDE))


@pytest.mark.parametrize("role", ["rule_author", "reviewer", "agent", "llm", "system"])
def test_31_no_one_but_owner_or_admin_can_change_authorization(store, role):
    with pytest.raises(AuthorizationError):
        store.record("NFPA 13:2019", "COMMERCIAL_AUTHORIZED", AuthorityActor(name="x", role=role), "self-promotion",
                     authorization_reference="FIREAI-AUTH-X", effective_date=TODAY, permitted_uses=["production"])
    assert store.status("NFPA 13:2019") == "INTERNAL_R_AND_D_ONLY"
    assert store.events()[-1]["action"] == "authorization_change_refused"
    assert not {"authorization", "authorization_status", "release_status"} & set(RuleSet.model_fields)


def test_32_authorization_changes_are_versioned_and_audited(store):
    key = "NFPA 13:TEST-HYPOTHETICAL-V"
    a = store.record(key, "INTERNAL_R_AND_D_ONLY", OWNER, "test: first", permitted_uses=["internal_development"])
    b = store.record(key, "COMMERCIAL_AUTHORIZATION_PENDING", AuthorityActor(name="admin (test)", role="admin"),
                     "test: second")
    assert [r.version for r in store.history(key)] == [1, 2] and b.supersedes_version == 1 and a.digest() != b.digest()
    ev = [e for e in store.events() if e.get("source_key") == key]
    assert [(e["from_status"], e["to_status"], e["role"]) for e in ev] == [
        ("UNKNOWN", "INTERNAL_R_AND_D_ONLY", "owner"), ("INTERNAL_R_AND_D_ONLY", "COMMERCIAL_AUTHORIZATION_PENDING", "admin")]
    assert ev[1]["digest"] == b.digest() and ev[1]["identity_assurance"].startswith("unauthenticated")


def test_33_authorization_is_per_source_and_per_edition(store):
    _authorize(store, "TEST-HYPOTHETICAL-A")
    assert evaluate_release([_placeholder_set("TEST-HYPOTHETICAL-A")], "beta", store, TODAY).eligible
    assert "SOURCE_AUTHORIZATION_UNKNOWN" in _codes(evaluate_release([_placeholder_set("TEST-HYPOTHETICAL-B")], "beta",
                                                                     store, TODAY))
    # the owner's real state: 2019 internal R&D does not make 2025 usable, even internally
    mixed = evaluate_release([_placeholder_set("2019"), _placeholder_set("2025")], "internal_development", store,
                             TODAY, OVERRIDE)
    by = {s.source_key: s.external_release_status for s in mixed.rule_sets}
    assert by == {"NFPA 13:2019": "INTERNAL_ONLY", "NFPA 13:2025": "NOT_RELEASE_ELIGIBLE"}
    assert "SOURCE_AUTHORIZATION_NOT_AVAILABLE" in _codes(mixed) and not mixed.eligible


def test_34_authorization_records_hold_no_source_or_personal_identifiers():
    base = dict(source_key="NFPA 13:2019", version=1, status="INTERNAL_R_AND_D_ONLY", change_reason="x", entered_by="o",
                entered_by_role="owner", entered_at="2026-09-24", permitted_uses=["internal_development"])
    with pytest.raises(ValidationError):
        SourceAuthorization(**base, customer_id="anything")                                   # no such field
    for bad in ({"notes": "contact someone@example.com"}, {"notes": "order number 12345678"},
                {"notes": "license no. ABC"}, {"authorization_reference": "FIREAI-AUTH-12345678"},
                {"authorization_reference": "LIC-99"}):
        with pytest.raises(ValidationError):
            SourceAuthorization(**{**base, **bad})
    fields = set(SourceAuthorization.model_fields)
    assert not {f for f in fields if any(w in f for w in ("customer", "license", "licence", "subscription", "email"))}


def test_35_synthetic_rules_are_never_releasable_as_real_engineering(store):
    syn = S.base_ruleset(max_boundary=7.08)
    for ctx in ("beta", "production", "commercial_api", "external_engineering"):
        assert "SYNTHETIC_CONTENT_NOT_RELEASABLE" in _codes(evaluate_release([syn], ctx, store, TODAY))
    internal = evaluate_release([syn], "internal_development", store, TODAY)
    assert internal.eligible and "TEST ONLY" in internal.labels
    assert source_key(syn) is None


def test_39_editions_never_mix():
    d19, d25 = empty_draft(NFPA13_2019_BASE), empty_draft(NFPA13_2025_BASE)
    assert "MULTIPLE_BASE_RULESETS" in {i.code for i in resolve([d19, d25], {}, "engineering",
                                                                "amendments_not_evaluated").refusals}
    assert d19.rules == d25.rules == [] and d19.digest() != d25.digest()


def test_orientation_is_fingerprinted_and_stales_persisted_designs(tmp_path):
    from fireai.project import ProjectStore, currency, dependencies_of
    pkg = _pkg(tmp_path, w=24, h=10)
    req = S.request(pkg, rule_sets=[rs(sxl("array_sxl_protection_area", 150.0, "sf"))], srch=S.search(1.0, 2)) \
        .model_copy(update={"orientation": ori(pkg, "u")})
    r = run_design(req)
    assert r.status == "VALID_LAYOUTS_FOUND"
    assert r.valid_layouts[0].array["orientation"]["branch_line_axis"] == "u"
    other = req.model_copy(update={"orientation": ori(pkg, "v")})
    assert run_design(other).request_fingerprint != r.request_fingerprint
    ps = ProjectStore(tmp_path / "projects")
    prj = ps.create_project("p", "a")
    area = ps.add_design_area(prj.uid, "a1", "a", [])
    rev = ps.record_design(prj.uid, area.uid, req, r, "a")
    cur = currency(rev, dependencies_of(other))
    assert cur["status"] == "STALE" and "input 'orientation'" in " ".join(cur["reasons"])
