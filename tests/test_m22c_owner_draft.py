"""Milestone 2.2C: the first OWNER DRAFT NFPA 13-2019 rule package (internal R&D only).

The owner supplied the structured values, titles, source metadata and candidate-case arithmetic. People
are PLACEHOLDER identities, which can author drafts but can never satisfy a review / approval control.
Nothing here is approved; nothing is releasable; nothing claims compliance."""

from __future__ import annotations

import ast
import json
import re
from datetime import date
from pathlib import Path

import pytest

from fireai.rules.authorization import AuthorityActor, AuthorizationError, AuthorizationStore, seed_owner_declared
from fireai.rules.catalog import envelope_for
from fireai.rules.completeness import NFPA13_2019_DEV_MANIFEST, CompletenessError, ManifestStore, nfpa_status
from fireai.rules.constraints import FACT_VALUES
from fireai.rules.identity import PLACEHOLDER_CODE, is_placeholder
from fireai.rules.intake import IntakeError, load_rule_package
from fireai.rules.known_answers import KnownAnswerCase, KnownAnswerError, KnownAnswerStore
from fireai.rules.model import Quantity
from fireai.rules.release import DevelopmentOverride, evaluate_release
from fireai.rules.store import RuleAuthoringError, RuleStore
from fireai.rules.units import to_canonical
from fixtures import known_answer_harness as H

ROOT = Path(__file__).resolve().parent.parent
DRAFT = json.loads((ROOT / "docs" / "m2_2b" / "NFPA13_2019_RULE_PACKAGE.owner_draft.json").read_text(encoding="utf-8"))
CASES = json.loads((ROOT / "docs" / "m2_2b" / "KNOWN_ANSWER_CASES.owner_draft.json").read_text(encoding="utf-8"))
REAL_REVIEWER, REAL_APPROVER = "real reviewer (test)", "real approver (test)"


@pytest.fixture(scope="module")
def package():
    return load_rule_package(DRAFT)


def _rule(pkg, rid):
    return next(r for r in pkg.rules if r.rule_id == rid)


def _limit(rule):
    return rule.parameter("limit").value


# ── 1-10: the owner draft parses with exactly the owner's values ─────────────

def test_01_owner_draft_parses_with_source_metadata_and_placeholders(package):
    assert len(package.rules) == 8 and package.omitted == {}
    s = package.source
    assert (s.document, s.edition, s.access_method, s.accessed_at, s.source_id) == (
        "NFPA 13", "2019", "Owner-held NFPA 13-2019 copy used for internal R&D review", "2026-09-25",
        "FIREAI-N13-2019-RD-001")
    assert (package.author, package.reviewer, package.approver) == (
        "PLACEHOLDER_INTERNAL_AUTHOR", "PLACEHOLDER_INDEPENDENT_REVIEWER", "PLACEHOLDER_INDEPENDENT_APPROVER")
    assert DRAFT["status"] == "OWNER_DRAFT_AWAITING_REAL_HUMAN_REVIEW"
    # the DATA (not the template's instructions) holds no license / customer / order / e-mail identifier
    text = json.dumps({k: DRAFT[k] for k in ("source", "people", "rules")})
    assert not re.search(r"\d{6,}|@|licen[cs]e\s*(no|number|#)|customer|order\s*(no|number)", text, re.I)


@pytest.mark.parametrize("rid,value,unit", [
    ("N13-2019-SSP-LH-MAX-AREA", 225.0, "sf"), ("N13-2019-SSP-LH-MAX-SPACING", 15.0, "ft"),
    ("N13-2019-SSP-MIN-WALL-DIST", 4.0, "in"), ("N13-2019-SSP-MIN-SPACING", 6.0, "ft"),
    ("N13-2019-SSP-MIN-DEFLECTOR", 1.0, "in"), ("N13-2019-SSP-MAX-DEFLECTOR", 12.0, "in")])
def test_02_03_05_06_07_08_values_and_units(package, rid, value, unit):
    q = _limit(_rule(package, rid))
    assert (q.value, q.unit) == (value, unit)


def test_04_c_is_half_the_effective_maximum_spacing(package):
    c = _rule(package, "N13-2019-SSP-MAX-WALL-DIST")
    assert c.parameter("factor").value == 0.5 and c.constraint.derived.from_key == "sprinkler.max_spacing"
    assert (c.constraint.measurement, c.constraint.bound, c.constraint.reference_kinds) == \
        ("perpendicular_wall_distance", "max", ["wall"])
    d = _rule(package, "N13-2019-SSP-MIN-WALL-DIST").constraint
    assert (d.measurement, d.bound, d.reference_kinds) == ("min_perpendicular_wall_distance", "min", ["wall"])


def test_09_10_response_vocabulary_is_single_and_canonical(package):
    g = _rule(package, "N13-2019-LH-NEW-SSP-RESPONSE")
    assert g.parameter("allowed").value == ["quick_response"] and g.constraint.fact_requirement.fact == \
        "sprinkler.response"
    assert FACT_VALUES["sprinkler.response"] == ("quick_response",)
    bad = json.loads(json.dumps(DRAFT))
    next(e for e in bad["rules"] if e["mapping_id"] == "G")["allowed_values"] = ["quick-response"]
    with pytest.raises(IntakeError, match="not canonical"):
        load_rule_package(bad)
    # no second spelling anywhere in the fireai package's string literals
    pat = re.compile(r"^(quick|standard)[_ -]?response$|^(QR|SR|quick|standard_resp)$", re.I)
    found = set()
    for f in (ROOT / "fireai").rglob("*.py"):
        for node in ast.walk(ast.parse(f.read_text(encoding="utf-8"))):
            if isinstance(node, ast.Constant) and isinstance(node.value, str) and pat.match(node.value.strip()):
                found.add(node.value)
    assert found == {"quick_response"}


# ── 11-15: placeholder identities can never satisfy a control ───────────────

@pytest.fixture()
def stores(tmp_path, package):
    ka = KnownAnswerStore(tmp_path / "ka")
    rs = RuleStore(tmp_path / "rules", known_answers=ka)
    rs.create_rule_set(rule_set_id="NFPA13-2019-BASE", version="1", layer="base_standard", governing_standard="NFPA 13",
                       edition="2019", created_by=package.author)
    for r in package.rules:
        rs.author_rule("NFPA13-2019-BASE", "1", r, package.author)
    rs.submit_for_review("NFPA13-2019-BASE", "1", package.author)
    return rs, ka


def test_11_12_placeholder_author_or_reviewer_cannot_satisfy_rule_review(stores, package):
    rs, _ka = stores
    rid = package.rules[0].rule_id
    with pytest.raises(RuleAuthoringError, match=PLACEHOLDER_CODE):              # real reviewer, placeholder author
        rs.review_rule("NFPA13-2019-BASE", "1", rid, REAL_REVIEWER, "approve")
    with pytest.raises(RuleAuthoringError, match=PLACEHOLDER_CODE):              # placeholder reviewer
        rs.review_rule("NFPA13-2019-BASE", "1", rid, package.reviewer, "approve")
    assert all(r.review_status == "draft" for r in rs.get("NFPA13-2019-BASE", "1").rules)


def test_13_placeholder_approver_cannot_approve_a_set(tmp_path, package):
    store = RuleStore(tmp_path / "rules2")
    reviewed = [r.model_copy(update={"author": "real author (test)", "reviewer": REAL_REVIEWER,
                                     "review_status": "reviewed"}) for r in package.rules]
    from fireai.rules import RuleSet
    store._write(RuleSet(rule_set_id="PLANTED-TEST", version="1", layer="base_standard", governing_standard="TEST",
                         edition="E", content_basis="authoritative", review_status="under_review", rules=reviewed))
    with pytest.raises(RuleAuthoringError, match=PLACEHOLDER_CODE):
        store.approve_rule_set("PLANTED-TEST", "1", package.approver)


def _candidates():
    return [KnownAnswerCase(**{k: v for k, v in c.items() if k != "reviewer"}, authored_at="2026-09-25")
            for c in CASES["cases"]]


def test_14_placeholder_case_author_or_reviewer_cannot_qualify_a_case(tmp_path, package):
    ka = KnownAnswerStore(tmp_path / "ka")
    cands = _candidates()
    assert len(cands) == 15 and all(is_placeholder(c.author) for c in cands)
    ka.add_case(cands[0])
    with pytest.raises(KnownAnswerError, match=PLACEHOLDER_CODE):
        ka.review_case(cands[0].case_id, REAL_REVIEWER, "approve")                # placeholder author
    real = cands[1].model_copy(update={"author": "real case author (test)"})
    ka.add_case(real)
    with pytest.raises(KnownAnswerError, match=PLACEHOLDER_CODE):
        ka.review_case(real.case_id, "PLACEHOLDER_CASE_REVIEWER", "approve")      # placeholder reviewer
    assert all(c.status == "draft" for c in ka.cases())
    assert any("required" in p for p in ka.problems_for_rule(_rule(package, cands[0].rule_id)))


def test_15_placeholders_cannot_approve_completeness_authorization_or_overrides(tmp_path):
    ms = ManifestStore(tmp_path / "m")
    ms.record(NFPA13_2019_DEV_MANIFEST, AuthorityActor(name="PLACEHOLDER_INTERNAL_AUTHOR", role="owner"))
    with pytest.raises(CompletenessError, match=PLACEHOLDER_CODE):
        ms.approve_complete(NFPA13_2019_DEV_MANIFEST.manifest_id, AuthorityActor(name="r", role="reviewer"),
                            AuthorityActor(name="a", role="admin"), "x")
    ms2 = ManifestStore(tmp_path / "m2")
    ms2.record(NFPA13_2019_DEV_MANIFEST, AuthorityActor(name="real author (test)", role="owner"))
    with pytest.raises(CompletenessError, match=PLACEHOLDER_CODE):
        ms2.approve_complete(NFPA13_2019_DEV_MANIFEST.manifest_id,
                             AuthorityActor(name="PLACEHOLDER_INDEPENDENT_REVIEWER", role="reviewer"),
                             AuthorityActor(name="a", role="admin"), "x")
    auth = AuthorizationStore(tmp_path / "auth")
    with pytest.raises(AuthorizationError, match=PLACEHOLDER_CODE):
        auth.record("NFPA 13:2019", "COMMERCIAL_AUTHORIZED", AuthorityActor(name="PLACEHOLDER_OWNER", role="owner"),
                    "x", authorization_reference="FIREAI-AUTH-X", effective_date=date(2026, 1, 1),
                    permitted_uses=["production"])
    seed_owner_declared(auth, "owner (test store)")
    d = evaluate_release([], "internal_development", auth, date(2026, 9, 25),
                         DevelopmentOverride(enabled_by="PLACEHOLDER_DEV", reason="x"))
    assert not d.eligible and PLACEHOLDER_CODE in {b.code for b in d.blockers}


# ── 16-23: the candidate cases' arithmetic, checked by the deterministic engine ─

def test_16_to_23_candidate_arithmetic_matches_the_engine_but_stays_unreviewed(tmp_path, stores, package):
    rs, ka = stores
    ruleset = rs.get("NFPA13-2019-BASE", "1")                       # DRAFT / under review: rule_review mode only
    results = {}
    for c in _candidates():
        ka.add_case(c)
        results[c.case_id] = H.verify(ka, c.case_id, ruleset, tmp_path)
    assert {k: v["match"] for k, v in results.items() if not v["match"]} == {}
    assert results["KA-2019-A-1"]["measured"] == pytest.approx(225.0)             # 16
    assert results["KA-2019-A-2"]["measured"] == pytest.approx(240.0)             # 17
    assert (results["KA-2019-B-1"]["measured"], results["KA-2019-B-2"]["measured"]) == (pytest.approx(15.0),
                                                                                       pytest.approx(16.0))   # 18
    assert results["KA-2019-C-1"]["limit"] == results["KA-2019-C-2"]["limit"] == pytest.approx(7.5)          # 19
    assert to_canonical(Quantity(value=4, unit="in"))[0] == pytest.approx(1 / 3)                              # 20
    assert results["KA-2019-D-1"]["limit"] == pytest.approx(1 / 3)
    assert (results["KA-2019-E-1"]["measured"], results["KA-2019-E-2"]["measured"]) == (pytest.approx(6.0),
                                                                                       pytest.approx(5.0))    # 21
    assert results["KA-2019-F_MIN-1"]["measured"] == pytest.approx(0.25)                                      # 22
    assert results["KA-2019-F_MIN-2"]["measured"] == pytest.approx(0.5 / 12, abs=1e-6)
    assert results["KA-2019-F_MAX-2"]["measured"] == pytest.approx(13 / 12, abs=1e-6)
    assert results["KA-2019-F_MAX-2"]["limit"] == pytest.approx(1.0)
    assert (results["KA-2019-G-1"]["fact_value"], results["KA-2019-G-1"]["outcome"]) == ("quick_response", "PASS")
    g2 = next(c for c in CASES["incomplete_cases"] if c["case_id"] == "KA-2019-G-2")                           # 23
    assert g2["status"] == "INCOMPLETE_VOCABULARY_DECISION_PENDING" and g2["listing_response_type"] is None
    for r in ruleset.rules:                        # engine agreement is NOT human review: none can count yet
        assert ka.problems_for_rule(r)


# ── 24-29: status stays draft / not established / not claimed / not releasable ─

def test_24_to_27_draft_package_statuses(tmp_path, stores):
    rs, _ka = stores
    ruleset = rs.get("NFPA13-2019-BASE", "1")
    assert ruleset.review_status == "under_review" and all(r.review_status == "draft" for r in ruleset.rules)
    auth = AuthorizationStore(tmp_path / "auth")
    seed_owner_declared(auth, "owner (test store)")
    assert auth.status("NFPA 13:2019") == "INTERNAL_R_AND_D_ONLY"
    prod = evaluate_release([ruleset], "production", auth, date(2026, 9, 25))
    st = nfpa_status([ruleset], NFPA13_2019_DEV_MANIFEST, {"NFPA 13:2019": "INTERNAL_R_AND_D_ONLY"},
                     external_release_eligible=prod.eligible)
    assert (st["RULE_APPROVAL"], st["NFPA_RULE_SUBSET_STATUS"], st["NFPA_ENVELOPE_COMPLETENESS"],
            st["NFPA_COMPLIANCE"], st["EXTERNAL_RELEASE"]) == ("NOT_APPROVED", "NOT_VALIDATED", "NOT_ESTABLISHED",
                                                               "NOT_CLAIMED", "NOT_RELEASE_ELIGIBLE")
    assert not evaluate_release([ruleset], "beta", auth, date(2026, 9, 25)).eligible


def test_29_2025_stays_inactive():
    assert envelope_for("NFPA 13", "2025") is None and envelope_for("NFPA 13", "2019") is not None


def test_10b_synthetic_response_values_never_enter_a_real_edition(package):
    """TEST_ONLY_SYNTHETIC_ values are tolerated only for hypothetical test editions, never for 2019 / 2025."""
    from fireai.rules import RuleSet
    from fireai.rules.store import approval_problems
    bad = json.loads(json.dumps(DRAFT))
    next(e for e in bad["rules"] if e["mapping_id"] == "G")["allowed_values"] = ["TEST_ONLY_SYNTHETIC_RESPONSE_A"]
    with pytest.raises(IntakeError, match="not canonical"):
        load_rule_package(bad)                                               # edition 2019 (real)
    g = _rule(package, "N13-2019-LH-NEW-SSP-RESPONSE")
    synthetic_g = g.model_copy(update={"parameters": [g.parameter("allowed").model_copy(
        update={"value": ["TEST_ONLY_SYNTHETIC_RESPONSE_A"]})]})
    rs = RuleSet(rule_set_id="NFPA13-2019-BASE", version="1", layer="base_standard", governing_standard="NFPA 13",
                 edition="2019", content_basis="authoritative", rules=[synthetic_g])
    assert any("not canonical" in p for p in approval_problems(synthetic_g, rs))
