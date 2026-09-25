"""Milestone 2.2B: first NFPA 13-2019 rule-package WORKFLOW (Phase 1 — stops at the owner-input boundary).

No NFPA 13-2019 value exists in this repository. The owner has not yet supplied the structured values,
reviewer / approver identities, known-answer cases or a listing, so the real package is NOT authored.

To prove the workflow end to end, these tests use a HYPOTHETICAL edition (``TEST-HYPOTHETICAL-2B``)
with FICTITIOUS placeholder numbers chosen NOT to be NFPA values, registered against a test-only copy
of the 2019 envelope. Every expected answer below is worked by hand in the case records."""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import pytest
from pydantic import ValidationError

from fireai.engineering import run_design
from fireai.engineering.envelope import envelope_blockers
from fireai.engineering.inputs import CeilingRegion, Elevation, Slope
from fireai.engineering.placement import iter_valid_layouts
from fireai.engineering.status import status_summary
from fireai.rules import catalog
from fireai.rules.authorization import AuthorityActor, AuthorizationStore, seed_owner_declared
from fireai.rules.catalog import (NFPA13_2019_BASE, NFPA13_2025_BASE, REGISTERED_IDENTITIES, empty_draft, envelope_for,
                                  envelope_record)
from fireai.rules.intake import M22B_MAPPINGS, IntakeError, load_rule_package, template
from fireai.rules.known_answers import KnownAnswerCase, KnownAnswerStore
from fireai.rules.release import DevelopmentOverride, evaluate_release
from fireai.rules.store import RuleAuthoringError, RuleStore
from fixtures import builders as B
from fixtures import commercial_2019 as C
from fixtures import known_answer_harness as H
from fixtures import synthetic_design as S

ROOT = Path(__file__).resolve().parent.parent
HYPO = "TEST-HYPOTHETICAL-2B"
TODAY = date(2026, 9, 24)
PEOPLE = {"author": "rule author (TEST)", "reviewer": "rule reviewer (TEST)", "approver": "rule approver (TEST)"}
CASE_AUTHOR, CASE_REVIEWER = "case author (TEST)", "case reviewer (TEST)"
# FICTITIOUS placeholders (deliberately not NFPA values) — mechanism only
PLACEHOLDER = {"A": (250.0, "sf"), "B": (17.0, "ft"), "C": 0.4, "D": (0.9, "ft"), "E": (5.5, "ft"),
               "F_MIN": (0.05, "ft"), "F_MAX": (0.7, "ft")}
L1_U, L1_V = [5.5, 21.0, 36.5, 52.0], [6.0, 19.0, 32.0]


def grid(us, vs):
    return [[u, v] for v in vs for u in us]


# hand-worked cases on the 57.5 x 38 ft commercial fixture, branch lines along LOCAL +x (room u)
CASES = {
    "A": [("PASS", grid(L1_U, L1_V), 201.5, 250.0,
           "S: ends max(2x5.5, 15.5)=15.5, interior 15.5; L: ends max(2x6, 13)=13 -> worst 15.5 x 13 = 201.5 <= 250"),
          ("FAIL", grid(L1_U, [6.5, 31.5]), 387.5, 250.0,
           "S 15.5; L: v 6.5, 31.5 -> max(2x6.5, 25)=25 -> 15.5 x 25 = 387.5 > 250")],
    "B": [("PASS", grid(L1_U, L1_V), 15.5, 17.0, "axis spacings 15.5 (u) and 13 (v): worst 15.5 <= 17"),
          ("FAIL", grid([6.0, 24.5, 43.0], L1_V), 18.5, 17.0, "u spacing 18.5 > 17")],
    "C": [("PASS", grid(L1_U, L1_V), 6.0, 6.8,
           "limit 0.4 x 17 = 6.8; walls -u 5.5, +u 57.5-52=5.5, -v 6, +v 38-32=6 -> max 6.0 <= 6.8"),
          ("FAIL", grid([7.5, 22.0, 36.5, 51.0], L1_V), 7.5, 6.8, "-u wall 7.5 > 6.8 (+u 6.5)")],
    "D": [("PASS", grid(L1_U, L1_V), 5.5, 0.9, "min of 5.5, 5.5, 6, 6 = 5.5 >= 0.9"),
          ("FAIL", grid([0.5, 19.5, 38.5, 57.0], L1_V), 0.5, 0.9, "walls 0.5 and 57.5-57=0.5 -> 0.5 < 0.9")],
    "E": [("PASS", grid(L1_U, L1_V), 13.0, 5.5, "closest pair = min axis spacing min(15.5, 13) = 13 >= 5.5"),
          ("FAIL", grid([26.0, 31.0], [19.0]), 5.0, 5.5, "two sprinklers 5 ft apart < 5.5")],
    "F_MIN": [("PASS", grid(L1_U, L1_V), 0.5, 0.05, "ceiling 10.75 - deflector 10.25 = 0.5 >= 0.05", 10.25),
              ("FAIL", grid(L1_U, L1_V), 0.02, 0.05, "10.75 - 10.73 = 0.02 < 0.05", 10.73)],
    "F_MAX": [("PASS", grid(L1_U, L1_V), 0.5, 0.7, "10.75 - 10.25 = 0.5 <= 0.7", 10.25),
              ("FAIL", grid(L1_U, L1_V), 0.8, 0.7, "10.75 - 9.95 = 0.8 > 0.7", 9.95)],
}


def filled(edition=HYPO, units=None) -> dict:
    t = template()
    t["source"].update({"edition": edition, "access_method": "licensed copy (test placeholder)",
                        "accessed_at": "2026-09-24", "internal_document_control_reference": "DOC-CTRL-TEST-A"})
    t["people"] = dict(PEOPLE)
    for e in t["rules"]:
        mid = e["mapping_id"]
        e.update({"rule_id": f"HYPO-{mid}", "title": f"placeholder rule {mid} (fictitious values)",
                  "applicability_confirmed": True, "exceptions_outside_envelope_confirmed": True,
                  "exceptions_outside_envelope": ["listed exception paths are outside the envelope (test)"]})
        if mid.startswith("F_"):
            e["applies"] = True
        if mid == "C":
            e["derived"]["factor"] = PLACEHOLDER["C"]
        else:
            v, u = PLACEHOLDER[mid]
            if units == "metric":
                v, u = (v * 0.09290304, "m2") if u == "sf" else (v * 0.3048, "m")
            e["limit"].update({"value": v, "unit": u})
    return t


@pytest.fixture()
def hypo_envelope(monkeypatch):
    env = catalog.NFPA13_2019_FIRST_ENVELOPE.model_copy(update={"envelope_id": "TEST-HYPOTHETICAL-ENVELOPE",
                                                               "edition": HYPO})
    monkeypatch.setitem(catalog.ENVELOPES, ("NFPA 13", HYPO), env)
    return env


def _case(mid, n, spec):
    outcome, positions, measured, limit, calc, *defl = spec
    m = M22B_MAPPINGS[mid]
    unit = "sf" if m.dimension == "area" else "ft"
    return KnownAnswerCase(
        case_id=f"KA-HYPO-{mid}-{n}", rule_id=f"HYPO-{mid}", rule_version="1", constraint_key=m.key,
        fixture={"builder": "commercial_2019", "width_ft": C.WIDTH_FT, "depth_ft": C.DEPTH_FT},
        orientation={"branch_line_direction": [1.0, 0.0], "frame": "LOCAL"},
        inputs={"positions_room_ft": positions, "deflector_elevation_ft": defl[0] if defl else 10.25},
        expected_measurement=measured, expected_unit=unit, expected_limit=limit, expected_limit_unit=unit,
        expected_outcome=outcome, hand_calculation=calc, derivation="hand_calculation", author=CASE_AUTHOR,
        authored_at="2026-09-24", provenance="worked by hand from the fixture geometry (test)")


def _authored(tmp_path):
    ka = KnownAnswerStore(tmp_path / "ka")
    store = RuleStore(tmp_path / "rules", known_answers=ka)
    pkg = load_rule_package(filled(), edition=HYPO)
    store.create_rule_set(rule_set_id="TEST-HYPO-BASE", version="1", layer="base_standard",
                          governing_standard="NFPA 13", edition=HYPO, created_by=PEOPLE["author"])
    for r in pkg.rules:
        store.author_rule("TEST-HYPO-BASE", "1", r, PEOPLE["author"])
    store.submit_for_review("TEST-HYPO-BASE", "1", PEOPLE["author"])
    return store, ka


def _verify_all(store, ka, tmp_path):
    rs = store.get("TEST-HYPO-BASE", "1")
    for mid, specs in CASES.items():
        for n, spec in enumerate(specs, 1):
            ka.add_case(_case(mid, n, spec))
            ka.review_case(f"KA-HYPO-{mid}-{n}", CASE_REVIEWER, "approve")
            H.verify(ka, f"KA-HYPO-{mid}-{n}", rs, tmp_path)
    return rs


@pytest.fixture()
def approved(tmp_path, hypo_envelope):
    store, ka = _authored(tmp_path)
    _verify_all(store, ka, tmp_path)
    for r in store.get("TEST-HYPO-BASE", "1").rules:
        store.review_rule("TEST-HYPO-BASE", "1", r.rule_id, PEOPLE["reviewer"], "approve")
    return store, ka, store.approve_rule_set("TEST-HYPO-BASE", "1", PEOPLE["approver"])


# ── 1-3: envelopes and identities ─────────────────────────────────────────────

def test_01_2025_envelope_inactive_not_supported(tmp_path):
    rec = envelope_record("NFPA 13", "2025")
    assert envelope_for("NFPA 13", "2025") is None and rec.status == "inactive_not_supported"
    assert [h["status"] for h in rec.status_history] == ["active_internal_rnd", "inactive_not_supported"]
    pkg = C.space(tmp_path)
    r = run_design(C.request(pkg, [empty_draft(NFPA13_2025_BASE)]))
    iss = next(i for i in r.refusals if i.code == "NO_SUPPORTED_ENVELOPE")
    assert "inactive_not_supported" in iss.message


def test_02_2025_identity_preserved_empty_draft():
    d = empty_draft(REGISTERED_IDENTITIES[("NFPA 13", "2025")])
    assert (d.rule_set_id, d.edition, d.review_status, d.rules) == ("NFPA13-2025-BASE", "2025", "draft", [])


def test_03_2019_envelope_active_for_internal_rnd():
    env = envelope_for("NFPA 13", "2019")
    assert env.status == "active_internal_rnd" and env.wall_reference_kinds == ["wall"]
    assert env.perimeter_boundary_kinds == ["wall"] and env.installation_styles == ["exposed"]


# ── owner-input boundary: templates and intake ───────────────────────────────

def test_templates_match_the_loader_and_blank_input_refuses():
    t = json.loads((ROOT / "docs" / "m2_2b" / "NFPA13_2019_RULE_PACKAGE.template.json").read_text(encoding="utf-8"))
    assert t == template()
    with pytest.raises(IntakeError) as exc:
        load_rule_package(t)
    probs = " | ".join(exc.value.problems)
    for needle in ("people.author is required", "source.access_method is required", "A: limit.value",
                   "C: derived.factor", "applicability_confirmed must be true", "F_MIN: state applies"):
        assert needle in probs
    for mid, m in M22B_MAPPINGS.items():                   # locators are exactly the owner-approved ones
        assert next(e for e in t["rules"] if e["mapping_id"] == mid)["locator"] == m.locator
        assert all(e["limit"]["value"] is None for e in t["rules"] if "limit" in e)


def test_intake_refuses_identifiers_prose_and_duplicate_people():
    t = filled()
    t["source"]["internal_document_control_reference"] = "customer id 4455667788"
    t["people"]["reviewer"] = t["people"]["author"]
    t["rules"][0]["title"] = "x" * 300
    with pytest.raises(IntakeError) as exc:
        load_rule_package(t, edition=HYPO)
    probs = " | ".join(exc.value.problems)
    assert "identifier" in probs and "three different people" in probs and "own words" in probs


# ── 4-6: nothing engineers before full review with known answers ─────────────

def test_04_05_draft_or_unreviewed_rules_do_not_engineer(tmp_path, hypo_envelope):
    store, _ka = _authored(tmp_path)
    pkg = C.space(tmp_path)
    r = run_design(C.request(pkg, [store.get("TEST-HYPO-BASE", "1")], mode="rule_validation"))
    codes = {i.code for i in r.refusals}
    assert r.status == "REFUSED" and {"RULESET_NOT_APPROVED", "RULE_NOT_APPROVED"} <= codes
    forged = store.get("TEST-HYPO-BASE", "1").model_copy(update={"review_status": "approved"})   # rules unreviewed
    r = run_design(C.request(pkg, [forged], mode="rule_validation"))
    assert "RULE_NOT_APPROVED" in {i.code for i in r.refusals}


def test_06_missing_known_answers_block_rule_approval(tmp_path, hypo_envelope):
    store, _ka = _authored(tmp_path)
    with pytest.raises(RuleAuthoringError, match="known-answer"):
        store.review_rule("TEST-HYPO-BASE", "1", "HYPO-A", PEOPLE["reviewer"], "approve")
    bare = RuleStore(tmp_path / "rules")                            # no registry at all
    with pytest.raises(RuleAuthoringError, match="known-answer registry"):
        bare.review_rule("TEST-HYPO-BASE", "1", "HYPO-A", PEOPLE["reviewer"], "approve")


# ── 7-10, 14, 15: the package's mappings, verified against the hand-worked cases ─

def test_07_to_15_every_rule_reproduces_its_independent_known_answers(approved):
    store, ka, rs = approved
    assert rs.review_status == "approved"
    by = {c.case_id: c for c in ka.cases()}
    assert len(by) == 14 and all(c.verifications[-1]["match"] for c in by.values())
    keys = {r.rule_id: (r.constraint.key, r.constraint.measurement, r.constraint.bound, r.constraint.reference_kinds)
            for r in rs.rules}
    assert keys["HYPO-A"][1] == "array_sxl_protection_area"                         # 7: S x L, not Voronoi
    assert keys["HYPO-B"][:2] == ("sprinkler.max_spacing", "array_axis_spacing")    # 8: separate constraint
    c = next(r for r in rs.rules if r.rule_id == "HYPO-C").constraint                # 9: derived from B
    assert c.derived.from_key == "sprinkler.max_spacing" and by["KA-HYPO-C-1"].verifications[-1]["limit"] == \
        pytest.approx(6.8)
    assert keys["HYPO-D"][2:] == ("min", ["wall"]) and keys["HYPO-C"][3] == ["wall"]  # 10: solid walls only
    assert keys["HYPO-E"][1] == "pairwise_min_distance"                              # 14
    assert {keys["HYPO-F_MIN"][2], keys["HYPO-F_MAX"][2]} == {"min", "max"}          # 15


# ── 11-13, 16: boundary / opening scope and exception paths refuse ───────────

def _env_conds(req, base):
    return {b.detail.get("condition"): b.message for b in envelope_blockers(req, [base])
            if b.code == "OUTSIDE_SUPPORTED_ENVELOPE"}


def test_11_12_13_openings_and_windows_are_outside_the_first_envelope(tmp_path):
    d19 = empty_draft(NFPA13_2019_BASE)
    for name, kw in (("door", {"door": ("right", 10, 13)}), ("window", {"window": ("left", 10, 16)})):
        d = tmp_path / name
        d.mkdir()
        pkg = S.verified_package(d, B.make_room(d / "r.dxf", w=C.WIDTH_FT, h=C.DEPTH_FT, **kw))
        conds = _env_conds(C.request(pkg, [d19]), d19)
        assert "perimeter boundary kinds" in conds and "LIMITATION" in conds["perimeter boundary kinds"]
    base = C.space(tmp_path)
    opened = base.model_copy(deep=True)                                     # an open opening on the boundary
    opened.spaces[0].boundary.rings[0].segments[1].kind = "open_opening"
    assert "perimeter boundary kinds" in _env_conds(C.request(opened, [d19]), d19)
    # a rule that would treat windows as walls is itself outside the first envelope
    window_rule = S.rule("SYN-W", "sprinkler.max_wall_distance", "perpendicular_wall_distance", "max", 7.0,
                         kinds=["wall", "window"])
    extra = S.layer_ruleset("project", [window_rule], "TEST_ONLY_SYNTHETIC_PROJECT")
    conds = envelope_blockers(C.request(base, [d19, extra]), [d19, extra])
    assert "rule wall reference kinds" in {b.detail.get("condition") for b in conds}


def test_16_unsupported_exception_paths_refuse(tmp_path):
    pkg = C.space(tmp_path)
    d19 = empty_draft(NFPA13_2019_BASE)
    concealed = C.listing_placeholder().model_copy(update={"installation_style": "concealed"})
    assert "installation style" in _env_conds(C.request(pkg, [d19], listing=concealed), d19)
    f = C.facts(pkg.semantic_spaces[0].uid)
    stepped = f["ceiling"].model_copy(deep=True)                            # ceiling elevation change
    stepped.regions.append(CeilingRegion(uid="R2", surface="flat", slope=Slope(status="known", value_deg=0.0),
                                         elevation=Elevation(status="known", value_ft=9.0, datum=C.DATUM,
                                                             source=C.FIXTURE_SOURCE)))
    assert "ceiling planes" in _env_conds(C.request(pkg, [d19], ceiling=stepped), d19)
    r = run_design(C.request(pkg, [d19], ceiling=stepped))
    assert "elevation_change" in {i.detail.get("condition") for i in r.refusals}


# ── 17-21: the reviewed package executes; release stays refused ─────────────

def _auth_store(tmp_path):
    a = AuthorizationStore(tmp_path / "auth")
    seed_owner_declared(a, "owner (test store)")
    a.record(f"NFPA 13:{HYPO}", "INTERNAL_R_AND_D_ONLY", AuthorityActor(name="owner (test store)", role="owner"),
             "test: mirrors the owner's 2019 status", permitted_uses=["internal_development"])
    return a


def _run(tmp_path, rs, **kw):
    pkg = C.space(tmp_path)
    from fireai.engineering.inputs import DeflectorPosition
    defl = DeflectorPosition(elevation=Elevation(status="known", value_ft=10.25, datum=C.DATUM, source=C.FIXTURE_SOURCE))
    req = C.request(pkg, [rs], mode="rule_validation", deflector=defl, **kw)
    return pkg, req, run_design(req)


def test_17_to_21_reviewed_package_executes_but_is_not_release_eligible(tmp_path, approved):
    _store, _ka, rs = approved
    pkg, req, r = _run(tmp_path, rs)
    assert r.status == "VALID_LAYOUTS_FOUND" and r.basis == "authoritative_rules_synthetic_listing"
    assert "SYNTHETIC LISTING DATA" in r.disclaimers and r.engineering_use == "NOT_FOR_ENGINEERING_USE"
    l1 = sorted(tuple(round(c, 6) for c in p) for p in [(u + 0.5, v + 0.5) for v in L1_V for u in L1_U])
    assert l1 in [sorted(tuple(round(c, 6) for c in p) for p in lay) for lay in iter_valid_layouts(r)]
    auth = _auth_store(tmp_path)
    override = DevelopmentOverride(enabled_by="developer (test)", reason="M2.2B internal rule validation")
    internal = evaluate_release([rs], "internal_development", auth, TODAY, override)
    assert internal.eligible and internal.override and internal.rule_sets[0].engineering_rule_status == \
        "approved_and_sourced"                                                             # 19
    assert any(e["action"] == "internal_development_override_used" for e in auth.events())
    for ctx in ("beta", "production"):                                                     # 20, 21
        d = evaluate_release([rs], ctx, auth, TODAY)
        assert not d.eligible and "SOURCE_AUTHORIZATION_INTERNAL_R_AND_D_ONLY" in {b.code for b in d.blockers}
    s = status_summary(r, internal, evaluate_release([rs], "production", auth, TODAY))
    assert s["ENGINEERING RULE STATUS"] == "INTERNAL R&D APPROVED"                        # 18
    assert s["SOURCE AUTHORIZATION"] == [f"NFPA 13:{HYPO}: INTERNAL_R_AND_D_ONLY"]
    assert s["EXTERNAL RELEASE"].startswith("NOT RELEASE ELIGIBLE (production")
    assert "NOT a product-specific" in s["LISTING"] and "production-ready" in s["THIS RESULT IS NOT"]


def test_22_2019_cannot_contaminate_2025(tmp_path, approved):
    _store, _ka, rs = approved
    with pytest.raises(IntakeError, match="editions are never mixed"):
        load_rule_package(filled(edition="2019"), edition="2025")
    ka = KnownAnswerStore(tmp_path / "ka25")
    s25 = RuleStore(tmp_path / "rules25", known_answers=ka)
    s25.create_rule_set(rule_set_id="NFPA13-2025-BASE", version="1", layer="base_standard",
                        governing_standard="NFPA 13", edition="2025", created_by="owner")
    s25.author_rule("NFPA13-2025-BASE", "1", rs.rules[0].model_copy(update={"review_status": "draft"}), "someone")
    s25.submit_for_review("NFPA13-2025-BASE", "1", "someone")
    with pytest.raises(RuleAuthoringError, match="editions are never mixed"):
        s25.review_rule("NFPA13-2025-BASE", "1", rs.rules[0].rule_id, "other", "approve")
    assert empty_draft(NFPA13_2025_BASE).rules == []


def test_23_known_answers_are_independent_of_fireai(tmp_path, hypo_envelope):
    good = _case("A", 1, CASES["A"][0])
    for bad in ({"author": "FireAI"}, {"provenance": "copied from FireAI output"}, {"author": "LLM agent"},
                {"hand_calculation": " "}):
        with pytest.raises(ValidationError):
            KnownAnswerCase(**{**good.model_dump(), **bad})
    with pytest.raises(ValidationError):
        KnownAnswerCase(**{**good.model_dump(), "derivation": "fireai_output"})
    store, ka = _authored(tmp_path)
    ka.add_case(good)
    with pytest.raises(ValueError, match="different person"):
        ka.review_case(good.case_id, CASE_AUTHOR, "approve")
    wrong = good.model_copy(update={"case_id": "KA-HYPO-A-WRONG", "expected_measurement": 200.0})
    ka.add_case(wrong)
    ka.review_case(wrong.case_id, CASE_REVIEWER, "approve")
    rec = H.verify(ka, wrong.case_id, store.get("TEST-HYPO-BASE", "1"), tmp_path)
    assert rec["match"] is False and rec["measured"] == pytest.approx(201.5)
    assert ka.get(wrong.case_id).expected_measurement == 200.0                       # never rewritten
    assert any("did NOT reproduce" in p for p in ka.problems_for_rule(store.get("TEST-HYPO-BASE", "1").rules[0]))


def test_24_rule_change_after_approval_needs_a_new_version_and_re_verification(tmp_path, approved):
    store, ka, rs = approved
    rule = next(r for r in rs.rules if r.rule_id == "HYPO-B")
    with pytest.raises(RuleAuthoringError):
        store.author_rule("TEST-HYPO-BASE", "1", rule, PEOPLE["author"])                # approved: immutable
    store.new_version("TEST-HYPO-BASE", "1", "2", PEOPLE["author"], "test: change a placeholder value")
    changed = rule.model_copy(update={"parameters": [rule.parameters[0].model_copy(
        update={"value": rule.parameters[0].value.model_copy(update={"value": 16.0})})]})
    with pytest.raises(RuleAuthoringError, match="new rule version"):
        store.author_rule("TEST-HYPO-BASE", "2", changed, PEOPLE["author"])
    store.author_rule("TEST-HYPO-BASE", "2", changed.model_copy(update={"version": "2", "change_reason": "test"}),
                      PEOPLE["author"])
    v2 = next(r for r in store.get("TEST-HYPO-BASE", "2").rules if r.rule_id == "HYPO-B")
    assert any("required" in p for p in ka.problems_for_rule(v2))                    # v2 has no verified cases
    assert store.get("TEST-HYPO-BASE", "1").review_status == "approved"               # history intact


def test_26_imperial_metric_intake_gives_identical_constraints():
    from fireai.rules.resolve import resolve
    imp = load_rule_package(filled(), edition=HYPO).rules
    met = load_rule_package(filled(units="metric"), edition=HYPO).rules

    def limits(rules):
        from fireai.rules import RuleSet
        rs_ = RuleSet(rule_set_id="X", version="1", layer="base_standard", governing_standard="NFPA 13", edition=HYPO,
                      content_basis="authoritative", review_status="approved",
                      rules=[r.model_copy(update={"review_status": "approved"}) for r in rules])
        f = C.facts("x")
        facts = {"hazard.scheme": f["classification"].scheme, "hazard.classification": "Light Hazard",
                 "sprinkler.type": "standard_spray", "sprinkler.orientation": "pendent",
                 "system.design_method": "hydraulically_calculated", "ceiling.surface": "flat",
                 "ceiling.construction_classification.scheme": catalog.NFPA13_2019_CONSTRUCTION_SCHEME,
                 "ceiling.construction_classification": "noncombustible_unobstructed"}
        return {c.key: (c.limit, c.unit) for c in resolve([rs_], facts, "engineering", "amendments_not_evaluated")
                .constraints}
    a, b = limits(imp), limits(met)
    assert a.keys() == b.keys() and len(a) == 7
    for k in a:
        assert b[k][0] == pytest.approx(a[k][0], abs=1e-9) and a[k][1] == b[k][1]


def test_27_repeatable(tmp_path, approved):
    _s, _k, rs = approved
    _p, req, a = _run(tmp_path, rs)
    assert S.dumps(run_design(req)) == S.dumps(a)


@pytest.mark.parametrize("axis", ["u", "v"])
def test_rectangle_fast_path_prunes_safely(tmp_path, axis):
    """The M2.2B axis-stage fast path (perpendicular wall distance, S / L dimensions) returns exactly the
    brute-force reference's valid set and candidate count."""
    from fireai.engineering.geometry import room_frame
    from fireai.engineering.inputs import LayoutOrientation
    from fireai.engineering.placement import _cells, _State, _validate, reference_search
    d = tmp_path / "rect"
    d.mkdir()
    pkg = S.verified_package(d, B.make_room(d / "r.dxf", w=14, h=9))
    fr = room_frame([tuple(p) for p in pkg.spaces[0].polygon_local_ft])
    o = LayoutOrientation(branch_line_direction=(fr.ux, fr.uy) if axis == "u" else (-fr.uy, fr.ux),
                          strategy="explicit_design_input", source=S.SYN)
    rules = [S.rule("SYN-PW-MAX", "k.pwmax", "perpendicular_wall_distance", "max", 3.5, kinds=["wall"]),
             S.rule("SYN-PW-MIN", "k.pwmin", "min_perpendicular_wall_distance", "min", 1.0, kinds=["wall"]),
             S.rule("SYN-S", "k.s", "array_sxl_s_dimension", "max", 6.0, kinds=["wall"], category="spacing"),
             S.rule("SYN-L", "k.l", "array_sxl_l_dimension", "max", 7.0, kinds=["wall"], category="spacing"),
             S.rule("SYN-AREA", "k.a", "array_sxl_protection_area", "max", 40.0, unit="sf", kinds=["wall"],
                    category="protection_area")]
    req = S.request(pkg, rule_sets=[S.base_ruleset(extra=rules)], srch=S.search(1.0, 9, 4)) \
        .model_copy(update={"orientation": o})
    r = run_design(req)
    valid, n = reference_search(req)
    _i, sp, reg, res = _validate(req)
    st = _State(req, sp, reg, res)
    ref = sorted(tuple(tuple(round(c, 9) for c in p["xy"]) for p in _cells(st, iu, iv)) for iu, iv in valid)
    got = sorted(tuple(tuple(round(c, 9) for c in p) for p in lay) for lay in iter_valid_layouts(r))
    assert r.search["candidates_generated"] == n and got == ref and r.search["valid"] > 0
    assert r.search["pruned_by_stage"].get("axis", 0) > 0
