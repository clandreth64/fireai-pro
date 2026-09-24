"""Resolve a layered rule stack against explicit design facts into engineering constraints.

Deterministic, three-valued and explained:

* applicability / exceptions are evaluated over explicit facts; a missing fact is UNKNOWN, and a
  rule whose applicability is unknown REFUSES resolution (it is never assumed to apply or not);
* dependencies must be satisfied;
* contributions to the same constraint key combine MOST-RESTRICTIVELY; a rule REPLACES another only
  when the replaced rule explicitly allows replacement by the replacing rule's (later) layer;
* an applicable rule that cannot be evaluated by a deterministic engine refuses resolution — a
  design is never called valid while an applicable requirement went unchecked;
* in ``engineering`` mode an approved, authoritative NFPA 13 base rule set with an explicit edition
  and an explicit jurisdiction statement are required; synthetic content is refused.
"""

from __future__ import annotations

from typing import Any, Literal, Optional

from pydantic import BaseModel, Field

from fireai.rules.constraints import MEASUREMENTS, Contribution, EngineeringConstraint
from fireai.rules.model import LAYER_ORDER, Condition, Quantity, Rule, RuleSet
from fireai.rules.units import UnitError, dimension, to_canonical

Mode = Literal["engineering", "synthetic_test"]
JurisdictionStatement = Literal["amendments_layered", "amendments_not_evaluated"]
_MISSING = object()


class RuleIssue(BaseModel):
    code: str
    message: str
    rule_id: Optional[str] = None
    rule_set_id: Optional[str] = None


class RuleOutcome(BaseModel):
    rule_id: str
    rule_set_id: str
    layer: str
    outcome: Literal["applied", "not_applicable", "excepted", "replaced", "not_effective"]
    detail: str = ""


class RuleResolution(BaseModel):
    status: Literal["resolved", "refused"]
    mode: str
    basis: Literal["authoritative", "synthetic_test_only"]
    rule_sets: list[dict]
    constraints: list[EngineeringConstraint] = Field(default_factory=list)
    outcomes: list[RuleOutcome] = Field(default_factory=list)
    refusals: list[RuleIssue] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)


def _fact(facts: dict, path: str):
    return facts.get(path, _MISSING)


def evaluate_condition(c: Condition, facts: dict) -> Optional[bool]:
    """True / False / None (unknown)."""
    v = _fact(facts, c.fact)
    if c.op == "known":
        return v is not _MISSING and v is not None
    if c.op == "unknown":
        return v is _MISSING or v is None
    if v is _MISSING or v is None:
        return None
    if c.op == "eq":
        return v == c.value
    if c.op == "ne":
        return v != c.value
    if c.op == "in":
        return v in c.value
    if c.op == "not_in":
        return v not in c.value
    if not isinstance(v, (int, float)) or not isinstance(c.value, (int, float)):
        return None
    return {"lt": v < c.value, "le": v <= c.value, "gt": v > c.value, "ge": v >= c.value}[c.op]


def evaluate_all(conds: list[Condition], facts: dict) -> tuple[Optional[bool], list[str]]:
    results = [(c, evaluate_condition(c, facts)) for c in conds]
    if any(r is False for _c, r in results):
        return False, []
    unknown = [c.fact for c, r in results if r is None]
    return (None, unknown) if unknown else (True, [])


def _layer_index(layer: str) -> int:
    return LAYER_ORDER.index(layer)


def _check_stack(rule_sets: list[RuleSet], mode: Mode, jurisdiction: Optional[JurisdictionStatement],
                 refusals: list[RuleIssue], limitations: list[str]) -> None:
    bases = [s for s in rule_sets if s.layer == "base_standard"]
    synthetic = [s for s in rule_sets if s.content_basis == "synthetic_test_only"]
    if mode == "synthetic_test":
        if len(synthetic) != len(rule_sets):
            refusals.append(RuleIssue(code="MIXED_SYNTHETIC_AND_AUTHORITATIVE_RULES",
                                      message="synthetic test runs may only use synthetic rule sets"))
        limitations.append("TEST ONLY: synthetic rule values — not NFPA 13 requirements")
        return
    if synthetic:
        refusals.append(RuleIssue(code="SYNTHETIC_RULES_IN_ENGINEERING_MODE",
                                  message="synthetic (TEST ONLY) rule sets can never support engineering: "
                                          + ", ".join(s.rule_set_id for s in synthetic)))
    bases = [s for s in bases if s.content_basis == "authoritative"]     # a synthetic base never counts
    if len(bases) != 1:
        refusals.append(RuleIssue(code="NO_APPROVED_NFPA13_RULESET" if not bases else "MULTIPLE_BASE_RULESETS",
                                  message="real engineering requires exactly one approved, authoritative NFPA 13 "
                                          f"base rule set; found {len(bases)}"))
    for b in bases:
        if b.governing_standard != "NFPA 13":
            refusals.append(RuleIssue(code="BASE_STANDARD_NOT_NFPA13", rule_set_id=b.rule_set_id,
                                      message=f"the base standard must be NFPA 13, not {b.governing_standard!r}"))
        if not b.edition or "TEST_ONLY" in b.edition:
            refusals.append(RuleIssue(code="NFPA13_EDITION_NOT_SPECIFIED", rule_set_id=b.rule_set_id,
                                      message="an explicit, owner-approved NFPA 13 edition is required"))
        for s in rule_sets:
            if s.layer == "edition" and s.edition != b.edition:
                refusals.append(RuleIssue(code="EDITION_MISMATCH", rule_set_id=s.rule_set_id,
                                          message=f"edition layer {s.edition!r} does not match base {b.edition!r}"))
    for s in rule_sets:
        if s.content_basis == "authoritative" and s.review_status != "approved":
            refusals.append(RuleIssue(code="RULESET_NOT_APPROVED", rule_set_id=s.rule_set_id,
                                      message=f"rule set {s.rule_set_id} v{s.version} is {s.review_status}, not approved"))
    has_amend = any(s.layer == "jurisdiction_amendment" for s in rule_sets)
    if jurisdiction is None and not has_amend:
        refusals.append(RuleIssue(code="JURISDICTION_NOT_SPECIFIED",
                                  message="state the jurisdiction layer: supply amendment rule sets or declare "
                                          "'amendments_not_evaluated' explicitly"))
    elif jurisdiction == "amendments_not_evaluated" and not has_amend:
        limitations.append("Jurisdiction / AHJ amendments were NOT evaluated; the result reflects the base "
                           "standard (and any other supplied layers) only")


def resolve(rule_sets: list[RuleSet], facts: dict[str, Any], mode: Mode,
            jurisdiction: Optional[JurisdictionStatement] = None) -> RuleResolution:
    refusals: list[RuleIssue] = []
    limitations: list[str] = []
    stack = sorted(rule_sets, key=lambda s: (_layer_index(s.layer), s.rule_set_id))
    _check_stack(stack, mode, jurisdiction, refusals, limitations)
    basis = "synthetic_test_only" if any(s.content_basis == "synthetic_test_only" for s in stack) else "authoritative"

    located: dict[str, tuple[Rule, RuleSet]] = {}
    for s in stack:
        for r in s.rules:
            if r.rule_id in located:
                refusals.append(RuleIssue(code="DUPLICATE_RULE_ID", rule_id=r.rule_id, rule_set_id=s.rule_set_id,
                                          message=f"rule id {r.rule_id} appears in more than one rule set"))
            located[r.rule_id] = (r, s)

    outcomes: dict[str, RuleOutcome] = {}
    applied: list[tuple[Rule, RuleSet]] = []
    for rid, (r, s) in located.items():
        def out(kind, detail="", rid=rid, s=s):
            outcomes[rid] = RuleOutcome(rule_id=rid, rule_set_id=s.rule_set_id, layer=s.layer, outcome=kind,
                                        detail=detail)
        if r.effective_status != "effective":
            out("not_effective", r.effective_status)
            continue
        if mode == "engineering" and r.review_status != "approved":
            refusals.append(RuleIssue(code="RULE_NOT_APPROVED", rule_id=rid, rule_set_id=s.rule_set_id,
                                      message=f"rule {rid} is {r.review_status}"))
        ok, unknown = evaluate_all(r.applicability.all_of, facts)
        if ok is None:
            refusals.append(RuleIssue(code="RULE_APPLICABILITY_UNKNOWN", rule_id=rid, rule_set_id=s.rule_set_id,
                                      message=f"cannot tell whether {rid} applies: unknown {sorted(set(unknown))}"))
            continue
        if ok is False:
            out("not_applicable")
            continue
        excepted = None
        for ex in r.exceptions:
            eok, eunknown = evaluate_all(ex.when, facts)
            if eok is None:
                refusals.append(RuleIssue(code="RULE_EXCEPTION_UNKNOWN", rule_id=rid, rule_set_id=s.rule_set_id,
                                          message=f"cannot tell whether an exception to {rid} applies: unknown "
                                                  f"{sorted(set(eunknown))}"))
                excepted = "unknown"
                break
            if eok:
                excepted = ex.reason
                break
        if excepted == "unknown":
            continue
        if excepted:
            out("excepted", excepted)
            continue
        out("applied")
        applied.append((r, s))

    applied_ids = {r.rule_id for r, _s in applied}
    for r, s in applied:
        for dep in r.depends_on:
            if dep not in applied_ids:
                refusals.append(RuleIssue(code="RULE_DEPENDENCY_UNMET", rule_id=r.rule_id, rule_set_id=s.rule_set_id,
                                          message=f"{r.rule_id} depends on {dep}, which does not apply or is missing"))

    replaced: dict[str, str] = {}
    for r, s in applied:
        for tid in r.replaces:
            if tid not in located:
                refusals.append(RuleIssue(code="REPLACEMENT_TARGET_MISSING", rule_id=r.rule_id,
                                          message=f"{r.rule_id} replaces {tid}, which is not in the rule stack"))
                continue
            t, ts = located[tid]
            permitted = (s.layer in t.replaceable_by and _layer_index(s.layer) > _layer_index(ts.layer)
                         and t.constraint and r.constraint and t.constraint.key == r.constraint.key)
            if not permitted:
                refusals.append(RuleIssue(code="REPLACEMENT_NOT_PERMITTED", rule_id=r.rule_id, rule_set_id=s.rule_set_id,
                                          message=f"{r.rule_id} ({s.layer}) may not replace {tid} ({ts.layer}): the "
                                                  f"replaced rule allows replacement by {t.replaceable_by or 'no layer'}"))
                continue
            if tid in applied_ids:
                replaced[tid] = r.rule_id
                outcomes[tid] = RuleOutcome(rule_id=tid, rule_set_id=ts.rule_set_id, layer=ts.layer,
                                            outcome="replaced", detail=f"replaced by {r.rule_id}")

    groups: dict[str, list[tuple[Rule, RuleSet]]] = {}
    for r, s in applied:
        if r.constraint is None:
            refusals.append(RuleIssue(code="RULE_NOT_MACHINE_EVALUABLE", rule_id=r.rule_id, rule_set_id=s.rule_set_id,
                                      message=f"{r.rule_id} applies but no deterministic engine evaluates it; a design "
                                              "cannot be declared valid"))
            continue
        groups.setdefault(r.constraint.key, []).append((r, s))

    constraints: list[EngineeringConstraint] = []
    for key, members in groups.items():
        live = [(r, s) for r, s in members if r.rule_id not in replaced]
        c0 = live[0][0].constraint
        if c0.measurement not in MEASUREMENTS:
            refusals.append(RuleIssue(code="UNKNOWN_MEASUREMENT", rule_id=live[0][0].rule_id,
                                      message=f"measurement {c0.measurement!r} is not implemented"))
            continue
        if any((r.constraint.measurement, r.constraint.bound, sorted(r.constraint.reference_kinds))
               != (c0.measurement, c0.bound, sorted(c0.reference_kinds)) for r, _s in live):
            refusals.append(RuleIssue(code="CONSTRAINT_DEFINITION_CONFLICT",
                                      message=f"rules for {key} disagree on what is measured; only an explicit "
                                              "permitted replacement may change it"))
            continue
        want = MEASUREMENTS[c0.measurement][0]
        contribs, bad = [], False
        for r, s in members:
            p = r.parameter(r.constraint.limit_parameter)
            if p is None or not isinstance(p.value, Quantity):
                refusals.append(RuleIssue(code="RULE_PARAMETER_MISSING", rule_id=r.rule_id,
                                          message=f"{r.rule_id}: limit parameter {r.constraint.limit_parameter!r} "
                                                  "missing or without a unit"))
                bad = True
                continue
            try:
                if dimension(p.value.unit) != want:
                    raise UnitError(f"{p.value.unit} is not a {want}")
                val, unit = to_canonical(p.value)
            except UnitError as exc:
                refusals.append(RuleIssue(code="RULE_UNIT_INVALID", rule_id=r.rule_id, message=str(exc)))
                bad = True
                continue
            contribs.append((r, s, val, unit))
        if bad or not contribs:
            continue
        live_c = [c for c in contribs if c[0].rule_id not in replaced]
        pick = (min if c0.bound == "max" else max)(live_c, key=lambda c: (c[2], _layer_index(c[1].layer)))
        if c0.bound == "min":   # ties: earliest layer
            best = pick[2]
            pick = min((c for c in live_c if c[2] == best), key=lambda c: _layer_index(c[1].layer))
        out_c = []
        for r, s, val, unit in contribs:
            action = ("replaced" if r.rule_id in replaced else "governs" if r is pick[0] else
                      "replaces" if r.replaces else "less_restrictive")
            out_c.append(Contribution(rule_id=r.rule_id, rule_set_id=s.rule_set_id, rule_set_version=s.version,
                                      layer=s.layer, content_basis=s.content_basis, source_document=r.source.document,
                                      source_reference=r.source.reference, limit=val, unit=unit, action=action,
                                      note=(f"replaced by {replaced[r.rule_id]}" if r.rule_id in replaced else "")))
        constraints.append(EngineeringConstraint(key=key, measurement=c0.measurement, bound=c0.bound, limit=pick[2],
                                                 unit=pick[3], reference_kinds=list(c0.reference_kinds),
                                                 contributions=out_c, governing_rule_id=pick[0].rule_id))
    constraints.sort(key=lambda c: c.key)
    return RuleResolution(status="refused" if refusals else "resolved", mode=mode, basis=basis,
                          rule_sets=[s.identity() for s in stack], constraints=constraints if not refusals else [],
                          outcomes=sorted(outcomes.values(), key=lambda o: (_layer_index(o.layer), o.rule_id)),
                          refusals=refusals, limitations=limitations)
