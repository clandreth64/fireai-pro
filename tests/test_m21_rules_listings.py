"""Milestone 2.1: authoritative rule-set storage, two-person rule review, listing authoring.

The NFPA 13-2025 identity is created EMPTY / DRAFT and must refuse. Workflow tests use a clearly
labelled WORKFLOW FIXTURE rule set (governing standard "WORKFLOW-FIXTURE", not NFPA 13) and a
FIXTURE listing that is not a real product — no NFPA or manufacturer data exists anywhere."""

from __future__ import annotations

import pytest

from fireai.engineering.inputs import SprinklerListing
from fireai.engineering.listings import ListingError, ListingStore
from fireai.rules import Condition, ConstraintTemplate, Quantity, Rule, RuleApplicability, RuleParameter, RuleSet, RuleSource, resolve
from fireai.rules.catalog import (NFPA13_2025_BASE, NFPA13_2025_FIRST_ENVELOPE, ensure_rule_set_identity, envelope_for,
                                  envelope_record)
from fireai.rules.store import RuleAuthoringError, RuleStore
from fixtures import synthetic_design as S

FIX_STD, FIX_ED = "WORKFLOW-FIXTURE (not NFPA 13)", "FIXTURE-E1"


def _src(**kw) -> RuleSource:
    base = dict(kind="authoritative_standard", document="WORKFLOW FIXTURE — not a standard", edition=FIX_ED,
                reference="FIXTURE-1", source_id="FIX-DOC-1", access_method="fixture", accessed_at="2026-09-24")
    base.update(kw)
    return RuleSource(**base)


def _rule(rid="FIX-R1", unit="ft", measurement="pairwise_min_distance", **kw) -> Rule:
    base = dict(rule_id=rid, category="spacing", title="workflow fixture rule",
                parameters=[RuleParameter(name="limit", value=Quantity(value=1.0, unit=unit))],
                applicability=RuleApplicability(all_of=[Condition(fact="hazard.classification", op="eq", value="X")]),
                constraint=ConstraintTemplate(key="fixture.key", measurement=measurement, bound="min", limit_parameter="limit"),
                source=_src(), author="ignored", authored_at="ignored")
    base.update(kw)
    return Rule(**base)


@pytest.fixture()
def store(tmp_path):
    rs = RuleStore(tmp_path / "rules")
    rs.create_rule_set(rule_set_id="FIX-BASE", version="1", layer="base_standard", governing_standard=FIX_STD,
                       edition=FIX_ED, created_by="Alice")
    return rs


# ── NFPA 13-2025 identity ─────────────────────────────────────────────────────

def test_07_08_nfpa13_2025_identity_is_empty_draft_and_cannot_engineer(tmp_path):
    rs = RuleStore(tmp_path / "rules")
    nfpa = ensure_rule_set_identity(rs, NFPA13_2025_BASE, "owner")
    assert (nfpa.rule_set_id, nfpa.governing_standard, nfpa.edition) == ("NFPA13-2025-BASE", "NFPA 13", "2025")
    assert nfpa.review_status == "draft" and nfpa.rules == [] and nfpa.content_basis == "authoritative"
    assert ensure_rule_set_identity(rs, NFPA13_2025_BASE, "someone else") == nfpa          # never overwritten
    res = resolve([nfpa], {}, "engineering", "amendments_not_evaluated")
    assert res.status == "refused" and "RULESET_NOT_APPROVED" in {x.code for x in res.refusals}
    with pytest.raises(RuleAuthoringError, match="EMPTY"):
        rs.submit_for_review("NFPA13-2025-BASE", "1", "owner")
    # M2.2B owner decision: the 2025 envelope is kept as history but INACTIVE (not supported)
    assert envelope_record("NFPA 13", "2025") == NFPA13_2025_FIRST_ENVELOPE
    assert NFPA13_2025_FIRST_ENVELOPE.status == "inactive_not_supported" and envelope_for("NFPA 13", "2025") is None
    assert envelope_for("NFPA 13", "2022") is None


# ── approval blockers ─────────────────────────────────────────────────────────

@pytest.mark.parametrize("change,problem", [
    (dict(source=_src(reference=None)), "locator missing"),                                      # 9
    (dict(source=_src(accessed_at=None)), "access metadata"),                                     # 9
    (dict(parameters=[RuleParameter(name="limit", value=1.0)]), "without a unit"),                # 10
    (dict(parameters=[RuleParameter(name="limit", value=Quantity(value=1.0, unit="sf"))]), "does not match"),  # 10
    (dict(constraint=ConstraintTemplate(key="k", measurement="UNSUPPORTED_MEASUREMENT", bound="max", limit_parameter="limit",
                                        unsupported_reason="perpendicular wall distance per a standard figure")),
     "UNSUPPORTED_MEASUREMENT"),                                                                 # 11
    (dict(applicability=RuleApplicability(all_of=[Condition(fact="room.mood", op="eq", value="calm")])), "cannot establish"),
    (dict(source=_src(edition="FIXTURE-E2")), "editions are never mixed"),                        # 14
    (dict(constraint=None), "no deterministic constraint"),
])
def test_09_10_11_14_approval_is_refused(store, change, problem):
    store.author_rule("FIX-BASE", "1", _rule(**change), "Alice")
    store.submit_for_review("FIX-BASE", "1", "Alice")
    with pytest.raises(RuleAuthoringError, match=problem):
        store.review_rule("FIX-BASE", "1", "FIX-R1", "Bob", "approve")
    assert store.events("FIX-BASE")[-1]["action"] == "review_blocked"


def test_11b_unsupported_measurement_refuses_engineering_if_applicable():
    r = S.rule("U1", "fixture.key", "UNSUPPORTED_MEASUREMENT", "max", 1.0)
    r = r.model_copy(update={"constraint": r.constraint.model_copy(update={"unsupported_reason": "not implemented"})})
    res = resolve([S.base_ruleset(extra=[r])], {"hazard.classification": S.CLASS}, "synthetic_test")
    assert res.status == "refused" and res.refusals[0].code == "UNSUPPORTED_MEASUREMENT"


# ── lifecycle ─────────────────────────────────────────────────────────────────

def test_12_author_reviewer_approver_lifecycle(store):
    store.author_rule("FIX-BASE", "1", _rule(), "Alice")
    with pytest.raises(RuleAuthoringError, match="submit it for review"):
        store.review_rule("FIX-BASE", "1", "FIX-R1", "Bob", "approve")
    store.submit_for_review("FIX-BASE", "1", "Alice")
    with pytest.raises(RuleAuthoringError, match="different person"):
        store.review_rule("FIX-BASE", "1", "FIX-R1", "Alice", "approve")
    store.review_rule("FIX-BASE", "1", "FIX-R1", "Bob", "approve")
    with pytest.raises(RuleAuthoringError, match="must not have authored"):
        store.approve_rule_set("FIX-BASE", "1", "Alice")
    rs = store.approve_rule_set("FIX-BASE", "1", "Carol")
    assert rs.review_status == "approved" and rs.rules[0].review_status == "approved" and rs.rules[0].reviewer == "Bob"
    assert [e["action"] for e in store.events("FIX-BASE")] == ["create", "author_rule", "submit_for_review",
                                                              "review_approve", "approve_rule_set"]
    assert all(e["identity_assurance"].endswith("(NOT PRODUCTION SAFE)") for e in store.events("FIX-BASE"))


def test_13_approved_rules_change_only_through_a_new_reviewed_version(store):
    store.author_rule("FIX-BASE", "1", _rule(), "Alice")
    store.submit_for_review("FIX-BASE", "1", "Alice")
    store.review_rule("FIX-BASE", "1", "FIX-R1", "Bob", "approve")
    v1 = store.approve_rule_set("FIX-BASE", "1", "Carol")
    with pytest.raises(RuleAuthoringError, match="only DRAFT"):
        store.author_rule("FIX-BASE", "1", _rule(), "Alice")
    with pytest.raises(RuleAuthoringError, match="change reason"):
        store.new_version("FIX-BASE", "1", "2", "Alice", "")
    store.new_version("FIX-BASE", "1", "2", "Alice", "fixture correction")
    changed = _rule(parameters=[RuleParameter(name="limit", value=Quantity(value=2.0, unit="ft"))])
    with pytest.raises(RuleAuthoringError, match="new rule version"):
        store.author_rule("FIX-BASE", "2", changed, "Alice")
    store.author_rule("FIX-BASE", "2", changed.model_copy(update={"version": "2", "change_reason": "fixture"}), "Alice")
    store.submit_for_review("FIX-BASE", "2", "Alice")
    store.review_rule("FIX-BASE", "2", "FIX-R1", "Bob", "approve")
    v2 = store.approve_rule_set("FIX-BASE", "2", "Carol")
    assert store.get("FIX-BASE", "1").review_status == "superseded" and v1.digest() != v2.digest()
    assert store.latest_approved("FIX-BASE").version == "2"


def test_14b_layers_for_another_edition_are_refused():
    base = RuleSet(rule_set_id="B", version="1", layer="base_standard", governing_standard="NFPA 13", edition="E-A",
                   content_basis="authoritative", review_status="approved", rules=[])
    amend = RuleSet(rule_set_id="J", version="1", layer="jurisdiction_amendment", governing_standard="local", edition=None,
                    base_edition="E-B", content_basis="authoritative", review_status="approved", rules=[])
    res = resolve([base, amend], {}, "engineering")
    assert "EDITION_MISMATCH" in {x.code for x in res.refusals}


def test_15_synthetic_never_enters_authoritative_sets(store):
    with pytest.raises(RuleAuthoringError, match="synthetic"):
        store.author_rule("FIX-BASE", "1", S.rule("S1", "k", "pairwise_min_distance", "min", 1.0), "Alice")
    res = resolve([store.get("FIX-BASE", "1"), S.base_ruleset(max_boundary=7.0)], {}, "synthetic_test")
    assert "MIXED_SYNTHETIC_AND_AUTHORITATIVE_RULES" in {x.code for x in res.refusals}


# ── listings ──────────────────────────────────────────────────────────────────

def _fixture_listing(version="1", **kw) -> SprinklerListing:
    doc = RuleSource(kind="manufacturer_listing_document", document="FIXTURE-DATASHEET (not a real product)",
                     revision="A", publication_date="2026-01-01", access_method="fixture", accessed_at="2026-09-24")
    base = dict(listing_id="FIXTURE-NOT-A-REAL-PRODUCT", version=version, content_basis="authoritative",
                manufacturer="FIXTURE (not a real manufacturer)", model="FIX-0", sprinkler_type="standard_spray",
                orientation="pendent", document=doc,
                rules=RuleSet(rule_set_id="FIXTURE-LISTING-RULES", version=version, layer="listing",
                              governing_standard="FIXTURE listing", edition=None, content_basis="authoritative"))
    base.update(kw)
    return SprinklerListing(**base)


def test_16_17_19_listing_lifecycle_selection_and_revision(tmp_path):
    ls = ListingStore(tmp_path / "listings")
    ls.create(_fixture_listing(), "Alice")
    with pytest.raises(ListingError, match="no approved"):
        ls.select_for_design("FIXTURE-NOT-A-REAL-PRODUCT")                          # 16: draft cannot be used
    ls.submit_for_review("FIXTURE-NOT-A-REAL-PRODUCT", "1", "Alice")
    with pytest.raises(ListingError, match="different person"):
        ls.review("FIXTURE-NOT-A-REAL-PRODUCT", "1", "Alice", "approve")
    ls.review("FIXTURE-NOT-A-REAL-PRODUCT", "1", "Bob", "approve")
    v1 = ls.select_for_design("FIXTURE-NOT-A-REAL-PRODUCT")                        # 17
    assert v1.version == "1" and v1.review_status == "approved" and v1.k_factor is None   # unknown stays unknown
    with pytest.raises(ListingError, match="change reason"):
        ls.revise("FIXTURE-NOT-A-REAL-PRODUCT", "1", "2", "Alice", "")
    ls.revise("FIXTURE-NOT-A-REAL-PRODUCT", "1", "2", "Alice", "new data sheet revision",
              document=v1.document.model_copy(update={"revision": "B"}))
    assert ls.select_for_design("FIXTURE-NOT-A-REAL-PRODUCT").version == "1"        # draft v2 not yet usable
    ls.submit_for_review("FIXTURE-NOT-A-REAL-PRODUCT", "2", "Alice")
    ls.review("FIXTURE-NOT-A-REAL-PRODUCT", "2", "Bob", "approve")
    v2 = ls.select_for_design("FIXTURE-NOT-A-REAL-PRODUCT")
    assert v2.version == "2" and v2.digest() != v1.digest()                        # 19: traceable version
    assert ls.get("FIXTURE-NOT-A-REAL-PRODUCT", "1").review_status == "superseded"
    assert [e["action"] for e in ls.events("FIXTURE-NOT-A-REAL-PRODUCT")][-3:] == ["submit_for_review", "superseded", "approve"]


@pytest.mark.parametrize("change,problem", [
    (dict(document=RuleSource(kind="manufacturer_listing_document", document="FIXTURE-DATASHEET")), "revision missing"),
    (dict(content_basis="synthetic_test_only", listing_id="TEST_ONLY_SYNTHETIC_X",
          rules=S.layer_ruleset("listing", [], "TEST_ONLY_SYNTHETIC_R")), "synthetic"),
    (dict(model=" "), "model missing"),
    (dict(k_factor=Quantity(value=1.0, unit="")), "unit missing"),
])
def test_18_listing_without_provenance_or_units_cannot_be_approved(tmp_path, change, problem):
    ls = ListingStore(tmp_path / "listings")
    lst = _fixture_listing(**change)
    ls.create(lst, "Alice")
    ls.submit_for_review(lst.listing_id, "1", "Alice")
    with pytest.raises(ListingError, match=problem):
        ls.review(lst.listing_id, "1", "Bob", "approve")
