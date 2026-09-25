"""Milestone 2.1: exact pruned search (equivalence with the M2.0 brute force), compact valid sets,
the agent candidate interface, and the supported-envelope / ceiling refusals.
Synthetic (TEST ONLY) rule values; generated rooms only."""

from __future__ import annotations

import time

import pytest

from fireai.engineering import CeilingRegion, InputSource, run_design
from fireai.engineering.design import CandidateProposal
from fireai.engineering.inputs import CeilingFeature, Elevation, Slope, SystemCondition
from fireai.engineering.placement import _cells, _State, _validate, evaluate_proposal, iter_valid_layouts, reference_search
from fireai.rules import RuleSet
from fixtures import builders as B
from fixtures import synthetic_design as S

HUMAN = InputSource(kind="human_decision", by="fixture reviewer", note="test fixture decision on a generated drawing")


def _pkg(tmp_path, name="r", unit="ft", **kw):
    d = tmp_path / name
    d.mkdir()
    return S.verified_package(d, B.make_room(d / "room.dxf", unit, **kw))


def _positions(result):
    return sorted(tuple(tuple(round(c, 9) for c in p) for p in lay) for lay in iter_valid_layouts(result))


def _reference(req):
    valid, n = reference_search(req)
    _i, sp, reg, res = _validate(req)
    st = _State(req, sp, reg, res)
    return sorted(tuple(tuple(round(c, 9) for c in p["xy"]) for p in _cells(st, iu, iv)) for iu, iv in valid), n


# ── 20-24 search ──────────────────────────────────────────────────────────────

CASES = [
    ("rect", dict(w=10, h=10), dict(max_boundary=7.08), (1.0, 1, 1)),
    ("multi", dict(w=20, h=10), dict(max_boundary=7.08, max_spacing=10.0), (1.0, 2, 2)),
    ("dense", dict(w=12, h=9), dict(max_boundary=7.08, min_wall=0.5, max_spacing=8.0, max_cell=60.0), (1.0, 6, 3)),
    ("narrow", dict(w=12, h=3), dict(max_boundary=3.0), (1.5, 7, 7)),
    ("L", dict(w=10, h=10, notch=(5, 5)), dict(min_wall=1.5, min_spacing=3.0, max_space=7.0), (1.0, 4, 2)),
    ("door", dict(w=10, h=10, door=("right", 3, 7)), dict(min_wall=2.0, min_wall_kinds=("wall",), max_boundary=6.0), (1.0, 4, 2)),
    ("window", dict(w=11, h=8, window=("left", 2, 6)), dict(min_wall=1.0, max_cell=40.0, min_spacing=2.5), (1.0, 4, 2)),
]


@pytest.mark.parametrize("name,room,rules,srch", CASES, ids=[c[0] for c in CASES])
def test_20_22_pruned_search_returns_exactly_the_reference_valid_set(tmp_path, name, room, rules, srch):
    """Safe pruning: same candidate count, same valid set as the M2.0 brute force (nothing valid discarded)."""
    req = S.request(_pkg(tmp_path, **room), rule_sets=[S.base_ruleset(**rules)], srch=S.search(*srch))
    r = run_design(req)
    ref, n = _reference(req)
    assert r.search["candidates_generated"] == n
    assert _positions(r) == ref
    assert sum(r.search["rejected_by_first_failure"].values()) == n - len(ref)


def test_21_repeatable(tmp_path):
    req = S.request(_pkg(tmp_path, w=12, h=9), rule_sets=[S.base_ruleset(max_boundary=7.08, max_cell=60.0)],
                    srch=S.search(1.0, 6, 3))
    a, b = run_design(req), run_design(req)
    assert S.dumps(a) == S.dumps(b)


def test_23_24_commercial_sized_room_is_bounded(tmp_path):
    """40 x 30 ft, up to 12 sprinklers: M2.0 did not finish in 10 minutes. Time-bounded here."""
    req = S.request(_pkg(tmp_path, w=40, h=30),
                    rule_sets=[S.base_ruleset(max_boundary=7.08, min_wall=0.5, max_spacing=12.0, max_cell=130.0)],
                    srch=S.search(1.0, 12, 4))
    t0 = time.perf_counter()
    r = run_design(req)
    elapsed = time.perf_counter() - t0
    assert r.search["candidates_generated"] == 1011391                # the whole space is accounted for
    assert r.search["fully_evaluated"] < 0.001 * r.search["candidates_generated"]
    assert elapsed < 180, elapsed
    assert r.status == "VALID_LAYOUTS_FOUND" and r.valid_set.count == r.search["valid"]


def test_valid_set_is_compact_beyond_the_explicit_limit_and_enumerable(tmp_path):
    pkg = _pkg(tmp_path)
    rs = [S.base_ruleset(min_wall=2.0)]                                        # 49 valid single positions
    small = run_design(S.request(pkg, rule_sets=rs))
    big = run_design(S.request(pkg, rule_sets=rs, srch=S.search(1.0, 1).model_copy(update={"max_explicit_layouts": 10})))
    assert len(small.valid_layouts) == 49 and small.valid_set.explicit_layouts_included
    assert big.valid_layouts == [] and not big.valid_set.explicit_layouts_included and big.valid_set.count == 49
    assert big.status == small.status == "VALID_LAYOUTS_FOUND"         # status follows the complete set
    assert _positions(big) == _positions(small)


# ── 25-30 agent candidate interface ───────────────────────────────────────────

def _proposal(req, positions, **kw):
    base = dict(proposed_by="future-agent", package_content_fingerprint=req.package.content_fingerprint,
                package_verification_fingerprint=req.package.verification_fingerprint,
                rule_sets=[{"rule_set_id": s.rule_set_id, "version": s.version, "digest": s.digest()} for s in req.rule_sets],
                listing={"listing_id": req.listing.listing_id, "version": req.listing.version, "digest": req.listing.digest()},
                positions=positions)
    base.update(kw)
    return CandidateProposal(**base)


@pytest.fixture()
def preq(tmp_path):
    return S.request(_pkg(tmp_path, w=20, h=10), rule_sets=[S.base_ruleset(max_boundary=7.08, max_spacing=10.0)],
                     srch=S.search(1.0, 2))


def test_25_valid_proposal_passes(preq):
    ev = evaluate_proposal(preq, _proposal(preq, [(5.5, 5.5), (15.5, 5.5)]))        # room (5,5),(15,5)
    assert ev.verdict == "PASS" and ev.basis == "synthetic_test_only" and "TEST ONLY" in ev.disclaimers


def test_26_invalid_proposal_fails_with_reasons(preq):
    ev = evaluate_proposal(preq, _proposal(preq, [(4.5, 5.5), (15.5, 5.5)]))
    assert ev.verdict == "FAIL"
    assert any(r.code == "CONSTRAINT_FAILED" and "sprinkler.max_axis_spacing" in r.message for r in ev.reasons)
    ev2 = evaluate_proposal(preq, _proposal(preq, [(-3.0, 5.5), (15.5, 5.5)]))
    assert ev2.verdict == "FAIL" and ev2.reasons[0].code == "OUTSIDE_SPACE"


def test_27_unknown_is_distinct_from_fail(preq):
    """Three sprinklers not on a rectangular grid: array axis spacing is undefined -> UNKNOWN, never PASS."""
    ev = evaluate_proposal(preq, _proposal(preq, [(5.5, 5.5), (10.5, 8.0), (15.5, 5.5)]))
    assert ev.verdict == "UNKNOWN" and any(r.code == "NOT_EVALUABLE" for r in ev.reasons)
    missing = preq.model_copy(update={"ceiling": None})
    ev2 = evaluate_proposal(missing, _proposal(missing, [(5.5, 5.5), (15.5, 5.5)]))
    assert ev2.verdict == "REFUSED" and "MISSING_CEILING_CONDITION" in {r.code for r in ev2.reasons}
    no_rules = preq.model_copy(update={"rule_sets": []})
    ev3 = evaluate_proposal(no_rules, _proposal(no_rules, [(5.5, 5.5), (15.5, 5.5)]))
    assert ev3.verdict == "REFUSED" and "NO_CONSTRAINTS_RESOLVED" in {r.code for r in ev3.reasons}


def test_28_29_30_stale_fingerprint_wrong_frame_wrong_versions_refuse(preq):
    ok = [(5.5, 5.5), (15.5, 5.5)]
    cases = {
        "STALE_SOURCE_FINGERPRINT": dict(package_content_fingerprint="0" * 64),
        "WRONG_COORDINATE_FRAME": dict(frame="PROJECT"),
        "RULESET_VERSION_MISMATCH": dict(rule_sets=[{"rule_set_id": S.BASE_ID, "version": "2",
                                                     "digest": S.base_ruleset(max_boundary=6.0, version="2").digest()}]),
        "LISTING_VERSION_MISMATCH": dict(listing={"listing_id": "TEST_ONLY_SYNTHETIC_LISTING_A", "version": "9",
                                                  "digest": "x"}),
    }
    for code, change in cases.items():
        ev = evaluate_proposal(preq, _proposal(preq, ok, **change))
        assert ev.verdict == "REFUSED" and code in {r.code for r in ev.reasons}, code
        assert ev.evaluations == []                                  # nothing evaluated, nothing to override


# ── 31-35 supported envelope and ceiling refusals (engineering mode) ─────────

def _nfpa2025_draft() -> RuleSet:
    """The EMPTY / DRAFT NFPA 13-2025 identity (as created by the catalog): carries no requirement."""
    return RuleSet(rule_set_id="NFPA13-2025-BASE", version="1", layer="base_standard", governing_standard="NFPA 13",
                   edition="2025", content_basis="authoritative", review_status="draft", rules=[])


def _eng(pkg, **kw):
    lst = S.listing().model_copy(update={"listing_id": "FIXTURE-NOT-A-REAL-PRODUCT", "content_basis": "authoritative",
                                         "sprinkler_type": "standard_spray", "orientation": "pendent",
                                         "rules": RuleSet(rule_set_id="FIXTURE-LISTING-RULES", version="1", layer="listing",
                                                          governing_standard="FIXTURE", edition=None,
                                                          content_basis="authoritative")})
    sp = pkg.semantic_spaces[0].uid
    ceil = S.ceiling(sp).model_copy(deep=True)
    for r in ceil.regions:
        r.construction, r.source, r.slope.source, r.elevation.source = "smooth_unobstructed", HUMAN, HUMAN, HUMAN
    ceil.statement_source = HUMAN
    base = dict(rule_sets=[_nfpa2025_draft()], mode="engineering", jurisdiction="amendments_not_evaluated", lst=lst, ceil=ceil,
                cls=S.classification().model_copy(update={"scheme": "NFPA 13 occupancy hazard classification",
                                                          "value": "Light Hazard", "source": HUMAN}),
                tol=S.tolerances().model_copy(update={"source": HUMAN}),
                srch=S.search().model_copy(update={"source": HUMAN}),
                system=SystemCondition(system_type="wet_pipe", storage="non_storage", source=HUMAN))
    base.update(kw)
    return run_design(S.request(pkg, **base))


@pytest.fixture()
def active_2025_envelope(monkeypatch):
    """M2.2B: the 2025 envelope is INACTIVE by owner decision. These M2.1 tests check the envelope
    MECHANISM, so they activate a copy of that historical envelope for the test only."""
    from fireai.rules import catalog
    env = catalog.NFPA13_2025_FIRST_ENVELOPE.model_copy(update={"status": "active_internal_rnd"})
    monkeypatch.setitem(catalog.ENVELOPES, ("NFPA 13", "2025"), env)
    return env


def test_2025_envelope_is_inactive_by_default(tmp_path):
    r = _eng(_pkg(tmp_path))
    assert "NO_SUPPORTED_ENVELOPE" in {i.code for i in r.refusals}


ENVELOPE_CODES = {"OUTSIDE_SUPPORTED_ENVELOPE", "MISSING_SYSTEM_CONDITION", "NO_SUPPORTED_ENVELOPE",
                  "CEILING_NOT_SUPPORTED_IN_M2_0"}


def test_31_supported_condition_clears_the_envelope_but_still_needs_approved_authoritative_data(tmp_path,
                                                                                               active_2025_envelope):
    r = _eng(_pkg(tmp_path))
    codes = {i.code for i in r.refusals}
    assert r.status == "REFUSED" and not (codes & ENVELOPE_CODES)                  # every envelope input matched
    assert {"RULESET_NOT_APPROVED", "LISTING_NOT_APPROVED_AUTHORITATIVE"} <= codes  # but nothing is approved yet
    r2 = _eng(_pkg(tmp_path, "b"), system=None)
    assert "MISSING_SYSTEM_CONDITION" in {i.code for i in r2.refusals}


@pytest.mark.parametrize("mutate,condition", [
    (lambda c: setattr(c.regions[0], "surface", "sloped"), "surface"),
    (lambda c: setattr(c.regions[0], "slope", Slope(status="known", value_deg=5.0, source=HUMAN)), "slope"),
    (lambda c: c.features.append(CeilingFeature(uid="b1", kind="beam", status="known")), "feature:beam"),
    (lambda c: c.features.append(CeilingFeature(uid="s1", kind="soffit", status="known")), "feature:soffit"),
    (lambda c: c.features.append(CeilingFeature(uid="c1", kind="cloud", status="known")), "feature:cloud"),
    (lambda c: c.regions.append(CeilingRegion(uid="r2", surface="flat", slope=Slope(status="known", value_deg=0.0),
                                              elevation=Elevation(status="known", value_ft=11.0, datum="ff", source=HUMAN))),
     "elevation_change"),
    (lambda c: setattr(c.regions[0], "construction", "obstructed"), "construction_obstructed"),
    (lambda c: setattr(c.regions[0], "elevation", Elevation()), "elevation_unknown"),
])
def test_32_unsupported_ceiling_conditions_refuse(tmp_path, mutate, condition):
    pkg = _pkg(tmp_path)
    ceil = S.ceiling(pkg.semantic_spaces[0].uid).model_copy(deep=True)
    mutate(ceil)
    r = run_design(S.request(pkg, rule_sets=[S.base_ruleset(max_boundary=7.08)], ceil=ceil))
    assert r.status == "REFUSED"
    assert condition in {i.detail.get("condition") for i in r.refusals if i.code == "CEILING_NOT_SUPPORTED_IN_M2_0"}


def test_33_unknown_obstruction_state_refuses(tmp_path):
    pkg = _pkg(tmp_path)
    ceil = S.ceiling(pkg.semantic_spaces[0].uid).model_copy(update={"obstructions_statement": "unknown"})
    r = run_design(S.request(pkg, rule_sets=[S.base_ruleset(max_boundary=7.08)], ceil=ceil))
    assert r.status == "REFUSED" and "obstructions_unknown" in {i.detail.get("condition") for i in r.refusals}


@pytest.mark.parametrize("change,condition", [
    (dict(system=SystemCondition(system_type="wet_pipe", storage="storage", source=HUMAN)), "storage condition"),   # 34
    (dict(system=SystemCondition(system_type="dry_pipe", storage="non_storage", source=HUMAN)), "system type"),
])
def test_34_outside_envelope_system_or_storage_refuses(tmp_path, change, condition, active_2025_envelope):
    r = _eng(_pkg(tmp_path), **change)
    assert condition in {i.detail.get("condition") for i in r.refusals if i.code == "OUTSIDE_SUPPORTED_ENVELOPE"}


def test_35_unsupported_classification_refuses_without_fallback(tmp_path, active_2025_envelope):
    cls = S.classification().model_copy(update={"scheme": "NFPA 13 occupancy hazard classification",
                                               "value": "Ordinary Hazard Group 1", "source": HUMAN})
    r = _eng(_pkg(tmp_path), cls=cls)
    (i,) = [i for i in r.refusals if i.code == "OUTSIDE_SUPPORTED_ENVELOPE"]
    assert i.detail["condition"] == "classification" and "no fallback" in i.message
    r2 = _eng(_pkg(tmp_path, "b"), rule_sets=[_nfpa2025_draft().model_copy(update={"edition": "2022"})])
    assert "NO_SUPPORTED_ENVELOPE" in {i.code for i in r2.refusals}                # another edition: data, not code
