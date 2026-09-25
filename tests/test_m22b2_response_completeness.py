# ruff: noqa: F811  (pytest fixtures `approved` / `hypo_envelope` are imported from test_m22b_rule_package)
"""Milestone 2.2B.2: installation context, generic FACT requirements (Rule G: response type), and the
separation of rule approval / subset status / envelope completeness / compliance / release.

No NFPA value: Rule G's allowed values in these tests are TEST_ONLY synthetic response designations on
the HYPOTHETICAL edition from the M2.2B tests; the real 2019 template keeps them owner-input-only."""

from __future__ import annotations


import pytest
from pydantic import ValidationError

from fireai.engineering import run_design
from fireai.engineering.envelope import envelope_blockers
from fireai.engineering.inputs import InstallationContext, SystemCondition
from fireai.engineering.placement import design_facts
from fireai.engineering.status import status_summary
from fireai.project import ProjectStore, currency, dependencies_of
from fireai.rules import Condition, ConstraintTemplate, RuleException, RuleParameter
from fireai.rules.authorization import AuthorityActor
from fireai.rules.catalog import NFPA13_2019_BASE, NFPA13_2025_BASE, empty_draft, envelope_for
from fireai.rules.completeness import (NFPA13_2019_DEV_MANIFEST, CompletenessError, CompletenessManifest,
                                       ManifestStore, nfpa_status)
from fireai.rules.intake import M22B_MAPPINGS, IntakeError, load_rule_package, template
from fireai.rules.model import FactRequirement
from fireai.rules.release import DevelopmentOverride, evaluate_release
from fireai.rules.resolve import resolve
from fireai.rules.store import RuleAuthoringError, RuleStore, approval_problems
from fixtures import commercial_2019 as C
from fixtures import synthetic_design as S
from test_m22b_rule_package import HYPO, PEOPLE, TODAY, _auth_store, _run, approved, hypo_envelope  # noqa: F401

HUMAN = C.FIXTURE_SOURCE
G_KEY = M22B_MAPPINGS["G"].key


@pytest.fixture(scope="module")
def space(tmp_path_factory):
    return C.space(tmp_path_factory.mktemp("space"))


def fact_rule(fact="system.design_method", allowed=("hydraulically_calculated",), rid="SYN-FACT", key="k.fact",
              **kw):
    return S.rule(rid, key, "array_axis_spacing", "max", 1.0).model_copy(update={
        "category": "system_requirement", "parameters": [RuleParameter(name="allowed", value=list(allowed))],
        "constraint": ConstraintTemplate(key=key, measurement="fact_requirement", bound="in",
                                         fact_requirement=FactRequirement(fact=fact)), **kw})


def _codes(r):
    return {i.code for i in r.refusals}


# ── 1-4: installation context ────────────────────────────────────────────────

def test_01_new_system_is_explicit_and_attributed(space):
    req = C.request(space, [empty_draft(NFPA13_2019_BASE)])
    assert req.installation.kind == "new_system" and req.installation.source.by
    assert design_facts(req, 1.0)["installation.context"] == "new_system"
    assert run_design(req).inputs["installation"]["kind"] == "new_system"


def test_02_unknown_context_refuses_where_required(space):
    d19 = empty_draft(NFPA13_2019_BASE)
    for inst in (InstallationContext(kind="unknown", source=HUMAN), None):
        codes = {b.code for b in envelope_blockers(C.request(space, [d19], installation=inst), [d19])}
        assert "MISSING_INSTALLATION_CONTEXT" in codes
    g = fact_rule("sprinkler.response", ["X"], applicability=S.rule("x", "k", "array_axis_spacing", "max", 1.0)
                  .applicability.model_copy(update={"all_of": [Condition(fact="installation.context", op="eq",
                                                                           value="new_system")]}))
    res = resolve([S.base_ruleset(extra=[g])], {"sprinkler.response": "X"}, "synthetic_test")
    assert "RULE_APPLICABILITY_UNKNOWN" in {i.code for i in res.refusals}


def test_03_existing_system_modification_is_distinct(space):
    d19 = empty_draft(NFPA13_2019_BASE)
    inst = InstallationContext(kind="existing_system_modification", source=HUMAN)
    req = C.request(space, [d19], installation=inst)
    assert design_facts(req, 1.0)["installation.context"] == "existing_system_modification"
    conds = {b.detail.get("condition") for b in envelope_blockers(req, [d19]) if b.code == "OUTSIDE_SUPPORTED_ENVELOPE"}
    assert "installation context" in conds


def test_04_context_is_fingerprinted_and_stales_designs(tmp_path):
    pkg = S.verified_package(tmp_path, __import__("fixtures.builders", fromlist=["x"]).make_room(tmp_path / "r.dxf"))
    req = S.request(pkg, rule_sets=[S.base_ruleset(max_boundary=7.08)]).model_copy(
        update={"installation": InstallationContext(kind="new_system", source=S.SYN)})
    other = req.model_copy(update={"installation": InstallationContext(kind="replacement", source=S.SYN)})
    r = run_design(req)
    assert r.request_fingerprint != run_design(other).request_fingerprint
    ps = ProjectStore(tmp_path / "p")
    prj = ps.create_project("p", "a")
    rev = ps.record_design(prj.uid, ps.add_design_area(prj.uid, "a", "a", []).uid, req, r, "a")
    cur = currency(rev, dependencies_of(other))
    assert cur["status"] == "STALE" and "input 'installation'" in " ".join(cur["reasons"])


# ── 5-7: the generic fact requirement ────────────────────────────────────────

def _fact_req(pkg, method):
    sysc = SystemCondition(system_type="wet_pipe", storage="non_storage", design_method=method, source=S.SYN)
    return S.request(pkg, rule_sets=[S.base_ruleset(max_boundary=7.08, extra=[fact_rule()])], system=sysc)


def test_05_06_07_fact_requirement_pass_fail_unknown(tmp_path):
    from fixtures import builders as B
    pkg = S.verified_package(tmp_path, B.make_room(tmp_path / "r.dxf"))
    ok = run_design(_fact_req(pkg, "hydraulically_calculated"))
    assert ok.status == "VALID_LAYOUTS_FOUND"
    e = next(x for x in ok.valid_layouts[0].evaluations if x.measurement == "fact_requirement")
    assert (e.outcome, e.fact, e.fact_value, e.allowed_values) == ("pass", "system.design_method",
                                                                    "hydraulically_calculated",
                                                                    ["hydraulically_calculated"])
    assert e.governing_rule_id == "SYN-FACT"
    bad = run_design(_fact_req(pkg, "pipe_schedule"))                              # every layout fails
    assert bad.status == "NO_VALID_LAYOUT_IN_SEARCH_SPACE" and bad.search["pruned_by_stage"] == \
        {"global": bad.search["candidates_generated"]} and bad.search["rejected_by_first_failure"] == \
        {"k.fact": bad.search["candidates_generated"]}
    unknown = run_design(_fact_req(pkg, "unknown"))
    assert unknown.status == "REFUSED" and "FACT_REQUIREMENT_UNKNOWN" in _codes(unknown)
    with pytest.raises(ValidationError):                                           # no numeric limit, bound "in"
        ConstraintTemplate(key="k", measurement="fact_requirement", bound="max", limit_parameter="x",
                           fact_requirement=FactRequirement(fact="system.type"))


# ── 8-11: Rule G on the (hypothetical) approved package ──────────────────────

def test_08_09_10_response_type_null_supported_unsupported(tmp_path, approved):
    _s, ka, rs = approved
    _p, _req, ok = _run(tmp_path, rs)
    assert ok.status == "VALID_LAYOUTS_FOUND"                              # supported value: layouts exist
    v_pass, v_fail = ka.get("KA-HYPO-G-1").verifications[-1], ka.get("KA-HYPO-G-2").verifications[-1]
    assert (v_pass["match"], v_pass["outcome"], v_pass["fact_value"]) == (True, "PASS", C.SYNTHETIC_RESPONSE)
    assert (v_fail["match"], v_fail["outcome"]) == (True, "FAIL")
    assert v_pass["allowed_values"] == [C.SYNTHETIC_RESPONSE]
    null = C.listing_placeholder().model_copy(update={"response_type": None})
    _p, _r, r = _run(tmp_path, rs, listing=null)
    assert r.status == "REFUSED" and {"MISSING_RESPONSE_TYPE", "FACT_REQUIREMENT_UNKNOWN"} <= _codes(r)
    other = C.listing_placeholder().model_copy(update={"response_type": "TEST_ONLY_SYNTHETIC_RESPONSE_B"})
    _p, _r, r = _run(tmp_path, rs, listing=other)
    assert r.status == "NO_VALID_LAYOUT_IN_SEARCH_SPACE"
    assert r.search["rejected_by_first_failure"] == {G_KEY: r.search["candidates_generated"]}


def test_11_existing_system_exception_is_never_applied_to_a_new_system():
    g = fact_rule("sprinkler.response", ["X"], exceptions=[RuleException(
        when=[Condition(fact="installation.context", op="eq", value="existing_system_modification")],
        reason="TEST ONLY synthetic existing-system exception path")])
    rs = S.base_ruleset(extra=[g])
    f = {"hazard.classification": S.CLASS, "sprinkler.response": "X"}
    out = {o.rule_id: o.outcome for o in resolve([rs], {**f, "installation.context": "new_system"},
                                                 "synthetic_test").outcomes}
    assert out["SYN-FACT"] == "applied"
    out = {o.rule_id: o.outcome for o in resolve([rs], {**f,
                                                        "installation.context": "existing_system_modification"},
                                                 "synthetic_test").outcomes}
    assert out["SYN-FACT"] == "excepted"


# ── 12-14: Rule G stays owner input; approval needs source + review ─────────

def test_12_rule_g_is_owner_input_only_in_the_real_template():
    g = next(e for e in template()["rules"] if e["mapping_id"] == "G")
    assert (g["locator"], g["allowed_values"], g["mapping_read_only"]["measurement"]) == \
        ("9.4.3.1", None, "fact_requirement")
    with pytest.raises(IntakeError) as exc:
        load_rule_package(template())
    assert any(p.startswith("G: allowed_values") for p in exc.value.problems)


def test_13_rule_g_needs_authoritative_source_and_review(tmp_path, hypo_envelope):
    from test_m22b_rule_package import filled
    pkg = load_rule_package(filled(), edition=HYPO)
    g = next(r for r in pkg.rules if r.rule_id == "HYPO-G")
    rs = empty_draft(NFPA13_2019_BASE).model_copy(update={"edition": HYPO, "rules": [g]})
    no_loc = g.model_copy(update={"source": g.source.model_copy(update={"reference": None})})
    assert any("locator" in p for p in approval_problems(no_loc, rs))
    from fireai.rules.known_answers import KnownAnswerStore
    store = RuleStore(tmp_path / "r", known_answers=KnownAnswerStore(tmp_path / "ka"))
    store.create_rule_set(rule_set_id="T", version="1", layer="base_standard", governing_standard="NFPA 13",
                          edition=HYPO, created_by=PEOPLE["author"])
    store.author_rule("T", "1", g, PEOPLE["author"])
    store.submit_for_review("T", "1", PEOPLE["author"])
    with pytest.raises(RuleAuthoringError, match="known-answer"):
        store.review_rule("T", "1", "HYPO-G", PEOPLE["reviewer"], "approve")
    with pytest.raises(RuleAuthoringError, match="different person"):
        store.review_rule("T", "1", "HYPO-G", PEOPLE["author"], "approve")


def test_14_synthetic_listing_stays_synthetic(space):
    lst = C.listing_placeholder()
    assert lst.content_basis == "synthetic_test_only" and "TEST_ONLY_SYNTHETIC" in lst.listing_id
    assert lst.response_type.startswith("TEST_ONLY_SYNTHETIC")
    r = run_design(C.request(space, [empty_draft(NFPA13_2019_BASE)]))          # engineering mode
    assert "LISTING_NOT_APPROVED_AUTHORITATIVE" in _codes(r)


# ── 15-18: subset vs completeness vs compliance vs release ───────────────────

def test_15_16_17_approved_subset_is_not_compliance_and_not_releasable(tmp_path, approved):
    _s, _k, rs = approved
    _p, _req, r = _run(tmp_path, rs)
    auth = _auth_store(tmp_path)
    internal = evaluate_release([rs], "internal_development", auth, TODAY,
                                DevelopmentOverride(enabled_by="dev (test)", reason="M2.2B.2 test"))
    beta, prod = (evaluate_release([rs], c, auth, TODAY) for c in ("beta", "production"))
    assert not beta.eligible and not prod.eligible                                    # 17
    dev_manifest = NFPA13_2019_DEV_MANIFEST.model_copy(update={"edition": HYPO})
    s = status_summary(r, internal, prod, rule_sets=[rs], manifest=dev_manifest)
    assert (s["RULE_APPROVAL"], s["NFPA_RULE_SUBSET_STATUS"], s["NFPA_ENVELOPE_COMPLETENESS"], s["NFPA_COMPLIANCE"],
            s["EXTERNAL_RELEASE"]) == ("APPROVED_FOR_INTERNAL_R_AND_D_RULE_VALIDATION", "VALIDATED", "NOT_ESTABLISHED",
                                       "NOT_CLAIMED", "NOT_RELEASE_ELIGIBLE")
    # even a qualified-human-approved "complete for the envelope" manifest is NOT a compliance claim
    ms = ManifestStore(tmp_path / "manifests")
    ms.record(dev_manifest, AuthorityActor(name="manifest author (test)", role="owner"))
    est = ms.approve_complete(dev_manifest.manifest_id, AuthorityActor(name="qualified reviewer (test)", role="reviewer"),
                              AuthorityActor(name="approver (test)", role="admin"), "test: reviewed family list")
    st = nfpa_status([rs], est, {f"NFPA 13:{HYPO}": "INTERNAL_R_AND_D_ONLY"})
    assert st["NFPA_ENVELOPE_COMPLETENESS"] == "ESTABLISHED" and st["NFPA_COMPLIANCE"] == "NOT_CLAIMED"
    # a package missing a manifest family is PARTIAL (16)
    no_g = rs.model_copy(update={"rules": [x for x in rs.rules if x.rule_id != "HYPO-G"]})
    st = nfpa_status([no_g], dev_manifest)
    assert st["NFPA_RULE_SUBSET_STATUS"] == "PARTIAL" and st["families_missing_from_approved_rules"] == ["G"]


def test_16_development_manifest_is_incomplete_and_human_only(tmp_path):
    m = NFPA13_2019_DEV_MANIFEST
    assert (m.status, m.completeness, m.edition, m.envelope_id) == ("DEVELOPMENT_INCOMPLETE", "NOT_ESTABLISHED", "2019",
                                                                    "NFPA13-2019-DEV-ENVELOPE-1")
    assert [f.family_id for f in m.families] == ["A", "B", "C", "D", "E", "F_MIN", "F_MAX", "G"]
    with pytest.raises(ValidationError):                                   # cannot simply declare it established
        CompletenessManifest(**{**m.model_dump(), "completeness": "ESTABLISHED", "status": "APPROVED_COMPLETE_FOR_ENVELOPE"})
    ms = ManifestStore(tmp_path / "m")
    for role in ("agent", "llm", "system"):
        with pytest.raises(CompletenessError):
            ms.record(m, AuthorityActor(name="x", role=role))
    ms.record(m, AuthorityActor(name="author (test)", role="owner"))
    with pytest.raises(CompletenessError):
        ms.approve_complete(m.manifest_id, AuthorityActor(name="bot", role="agent"),
                            AuthorityActor(name="approver (test)", role="admin"), "x")
    with pytest.raises(ValidationError):                                   # reviewer == author
        ms.approve_complete(m.manifest_id, AuthorityActor(name="author (test)", role="reviewer"),
                            AuthorityActor(name="approver (test)", role="admin"), "x")
    assert ms.get(m.manifest_id).completeness == "NOT_ESTABLISHED"
    assert [e["action"] for e in ms.events()][:3] == ["manifest_change_refused"] * 3


def test_18_2019_and_2025_stay_isolated():
    st = nfpa_status([empty_draft(NFPA13_2025_BASE)], NFPA13_2019_DEV_MANIFEST)
    assert st["NFPA_ENVELOPE_COMPLETENESS"] == "NOT_ESTABLISHED" and st["NFPA_RULE_SUBSET_STATUS"] == "NOT_VALIDATED"
    assert envelope_for("NFPA 13", "2025") is None
    assert envelope_for("NFPA 13", "2019").installation_contexts == ["new_system"]
    assert empty_draft(NFPA13_2025_BASE).rules == []
