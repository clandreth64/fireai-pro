"""Milestone 2.0: NFPA 13-centred rules architecture (no rule values): layering, precedence,
applicability, provenance and the real-engineering policy. Synthetic data only."""

from __future__ import annotations

import pytest

from fireai.rules import Condition, Quantity, Rule, RuleApplicability, RuleException, RuleParameter, RuleSet, RuleSource, resolve
from fireai.rules.units import to_canonical
from fixtures import synthetic_design as S

FACTS = {"hazard.classification": S.CLASS, "hazard.scheme": "TEST_ONLY_SYNTHETIC_SCHEME"}
KEY = "sprinkler.max_boundary_distance"
MEAS = "boundary_point_to_nearest_sprinkler_max"


def _r(rid, v, **kw):
    return S.rule(rid, KEY, MEAS, "max", v, kinds=["wall"], **kw)


def test_most_restrictive_wins_across_layers_and_every_contribution_is_explained():
    base = S.base_ruleset(extra=[_r("B1", 10.0)])
    lst = S.layer_ruleset("listing", [_r("L1", 8.0)], "TEST_ONLY_SYNTHETIC_L")
    prj = S.layer_ruleset("project", [_r("P1", 9.0)], "TEST_ONLY_SYNTHETIC_P")
    res = resolve([prj, lst, base], FACTS, "synthetic_test")            # order supplied does not matter
    (c,) = res.constraints
    assert c.limit == 8.0 and c.governing_rule_id == "L1"
    assert [(x.rule_id, x.layer, x.action) for x in c.contributions] == [
        ("B1", "base_standard", "less_restrictive"), ("L1", "listing", "governs"), ("P1", "project", "less_restrictive")]
    assert "governed by L1" in c.explain()


def test_minimum_bounds_take_the_largest_limit():
    k, m = "sprinkler.min_boundary_distance", "point_to_boundary_min"
    base = S.base_ruleset(extra=[S.rule("B", k, m, "min", 0.3, kinds=["wall"])])
    prj = S.layer_ruleset("project", [S.rule("P", k, m, "min", 0.5, kinds=["wall"])], "TEST_ONLY_SYNTHETIC_P")
    (c,) = resolve([base, prj], FACTS, "synthetic_test").constraints
    assert c.limit == 0.5 and c.governing_rule_id == "P"


def test_replacement_only_where_the_replaced_rule_permits_it():
    permitted = S.base_ruleset(extra=[_r("B1", 7.0, replaceable_by=["jurisdiction_amendment"])])
    amend = S.layer_ruleset("jurisdiction_amendment", [_r("J1", 9.0, replaces=["B1"])], "TEST_ONLY_SYNTHETIC_J")
    (c,) = resolve([permitted, amend], FACTS, "synthetic_test").constraints
    assert c.limit == 9.0 and c.governing_rule_id == "J1"            # a permitted relaxation
    listing_try = S.layer_ruleset("listing", [_r("L1", 9.0, replaces=["B1"])], "TEST_ONLY_SYNTHETIC_L")
    res = resolve([permitted, listing_try], FACTS, "synthetic_test")
    assert res.status == "refused" and res.refusals[0].code == "REPLACEMENT_NOT_PERMITTED"
    not_permitted = S.base_ruleset(extra=[_r("B1", 7.0)])
    res = resolve([not_permitted, amend], FACTS, "synthetic_test")
    assert res.status == "refused" and "REPLACEMENT_NOT_PERMITTED" in {x.code for x in res.refusals}


def test_applicability_exceptions_and_dependencies_are_three_valued():
    cond = RuleApplicability(all_of=[Condition(fact="ceiling.slope_deg", op="le", value=0.0)])
    exc = RuleException(when=[Condition(fact="sprinkler.type", op="eq", value="SPECIAL")], reason="synthetic exception")
    rules = [_r("A", 7.0, applicability=cond), S.rule("D", "sprinkler.min_spacing", "pairwise_min_distance", "min", 1.0,
                                                      depends_on=["A"], category="spacing")]
    base = S.base_ruleset(extra=rules)
    unknown = resolve([base], FACTS, "synthetic_test")
    assert unknown.status == "refused" and {"RULE_APPLICABILITY_UNKNOWN", "RULE_DEPENDENCY_UNMET"} <= {x.code for x in unknown.refusals}
    ok = resolve([base], {**FACTS, "ceiling.slope_deg": 0.0}, "synthetic_test")
    assert ok.status == "resolved" and {c.key for c in ok.constraints} == {KEY, "sprinkler.min_spacing"}
    not_app = resolve([base], {**FACTS, "ceiling.slope_deg": 5.0}, "synthetic_test")
    assert "RULE_DEPENDENCY_UNMET" in {x.code for x in not_app.refusals}
    excepted = resolve([S.base_ruleset(extra=[_r("E", 7.0, exceptions=[exc])])],
                       {**FACTS, "sprinkler.type": "SPECIAL"}, "synthetic_test")
    assert excepted.constraints == [] and excepted.outcomes[0].outcome == "excepted"
    unknown_exc = resolve([S.base_ruleset(extra=[_r("E", 7.0, exceptions=[exc])])], FACTS, "synthetic_test")
    assert "RULE_EXCEPTION_UNKNOWN" in {x.code for x in unknown_exc.refusals}


def test_applicable_rule_without_a_deterministic_check_refuses():
    r = Rule(rule_id="N1", category="hydraulic_criteria", title="synthetic, not machine-evaluable yet", source=S.SRC,
             author="t", authored_at="2026-09-24",
             applicability=RuleApplicability(all_of=[Condition(fact="hazard.classification", op="eq", value=S.CLASS)]))
    res = resolve([S.base_ruleset(extra=[_r("A", 7.0), r])], FACTS, "synthetic_test")
    assert res.status == "refused" and res.refusals[0].code == "RULE_NOT_MACHINE_EVALUABLE"


def test_metric_rule_parameters_convert_exactly():
    assert to_canonical(Quantity(value=304.8, unit="mm")) == (1.0, "ft")
    assert to_canonical(Quantity(value=1.0, unit="m2"))[0] == pytest.approx(10.763910416709722)
    base = S.base_ruleset(extra=[S.rule("M", KEY, MEAS, "max", 2133.6, unit="mm", kinds=["wall"])])
    (c,) = resolve([base], FACTS, "synthetic_test").constraints
    assert c.limit == pytest.approx(7.0) and c.unit == "ft"
    bad = S.base_ruleset(extra=[S.rule("M", KEY, MEAS, "max", 10.0, unit="sf", kinds=["wall"])])
    assert resolve([bad], FACTS, "synthetic_test").refusals[0].code == "RULE_UNIT_INVALID"


def _authoritative(**kw) -> RuleSet:
    """Policy fixture: an authoritative-looking rule set with NO rules and no edition / approval — used only to
    prove the policy refuses it. It contains no requirement."""
    return RuleSet(rule_set_id="POLICY_TEST_EMPTY", version="0", layer="base_standard", governing_standard="NFPA 13",
                   content_basis="authoritative", rules=[], **{"edition": None, "review_status": "draft", **kw})


def test_real_engineering_policy():
    codes = lambda res: {x.code for x in res.refusals}                        # noqa: E731
    assert {"NO_APPROVED_NFPA13_RULESET", "JURISDICTION_NOT_SPECIFIED"} <= codes(resolve([], FACTS, "engineering"))
    res = resolve([_authoritative()], FACTS, "engineering", "amendments_not_evaluated")
    assert {"NFPA13_EDITION_NOT_SPECIFIED", "RULESET_NOT_APPROVED"} <= codes(res)
    assert any("NOT evaluated" in lim for lim in res.limitations)
    res = resolve([S.base_ruleset(max_boundary=7.0)], FACTS, "engineering", "amendments_not_evaluated")
    assert {"SYNTHETIC_RULES_IN_ENGINEERING_MODE", "NO_APPROVED_NFPA13_RULESET"} <= codes(res)
    res = resolve([_authoritative(), S.base_ruleset(max_boundary=7.0)], FACTS, "synthetic_test")
    assert "MIXED_SYNTHETIC_AND_AUTHORITATIVE_RULES" in codes(res)


def test_synthetic_content_cannot_pose_as_real():
    with pytest.raises(ValueError):                    # no standard reference on synthetic sources
        RuleSource(kind="synthetic_test_only", document="TEST_ONLY_SYNTHETIC x", reference="X.Y.Z-PLACEHOLDER")
    with pytest.raises(ValueError):                    # synthetic sources must be marked
        RuleSource(kind="synthetic_test_only", document="looks official")
    with pytest.raises(ValueError):                    # synthetic rule sets must be marked and edition-free
        RuleSet(rule_set_id="LOOKS-LIKE-A-REAL-SET", version="1", layer="base_standard", governing_standard="NFPA 13",
                edition="EDITION-PLACEHOLDER", content_basis="synthetic_test_only", rules=[])
    real_src = RuleSource(kind="authoritative_standard", document="NFPA 13", edition="E", reference="x")
    with pytest.raises(ValueError):                    # a synthetic rule set cannot hold authoritative-sourced rules
        S.base_ruleset(extra=[Rule(rule_id="X", category="spacing", title="t", source=real_src, author="a",
                                   authored_at="d", parameters=[RuleParameter(name="limit", value=1.0)])])


def test_rule_set_digest_pins_content():
    a, b = S.base_ruleset(max_boundary=7.08), S.base_ruleset(max_boundary=7.08)
    assert a.digest() == b.digest() and S.base_ruleset(max_boundary=7.09).digest() != a.digest()
