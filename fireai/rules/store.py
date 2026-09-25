"""Durable, human-controlled rule authoring and review (Milestone 2.1).

Authoritative rule content enters FireAI ONLY through this workflow. FireAI / LLMs never author
values: every record carries a human author, a different human reviewer, and a source locator
with access metadata.

    create_rule_set (DRAFT, empty) → author_rule … → submit_for_review (UNDER_REVIEW)
      → review_rule (a different person; checks below) … → approve_rule_set (APPROVED, immutable)
    change an approved set → new_version (DRAFT copy, change reason) → … → approve (old: SUPERSEDED)

Approval is refused for: synthetic content; a missing source document / section locator / edition /
access metadata; an edition that differs from the rule set's; a missing constraint, an
UNSUPPORTED_MEASUREMENT or an unknown measurement; a limit without a unit of the right dimension;
applicability or exception facts FireAI cannot establish; dependencies outside the set; a reviewer or
approver who authored the rule. Identities are free-text names — NOT PRODUCTION SAFE.

Layout: <root>/<rule_set_id>/<version>/rule_set.json ; <root>/<rule_set_id>/events.jsonl (append-only).
"""

from __future__ import annotations

import json
import re
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from fireai.rules.constraints import (BOUNDARY_MEASUREMENTS, DECLARED_MEASUREMENTS, FACTS, MEASUREMENT_BOUNDS,
                                      MEASUREMENTS)
from fireai.rules.model import UNSUPPORTED_MEASUREMENT, Quantity, Rule, RuleSet
from fireai.rules.units import UnitError, dimension

IDENTITY_ASSURANCE = "unauthenticated_name (NOT PRODUCTION SAFE)"
BOUNDARY_KINDS = {"wall", "window", "door_opening", "open_opening"}
_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.\-]{0,127}$")
_lock = threading.Lock()


class RuleAuthoringError(ValueError):
    pass


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def approval_problems(rule: Rule, rs: RuleSet) -> list[str]:
    """Why ``rule`` cannot be approved for engineering use inside ``rs`` (empty = approvable)."""
    p: list[str] = []
    src = rule.source
    if rs.content_basis != "authoritative" or src.kind == "synthetic_test_only":
        p.append("synthetic content can never be approved as an authoritative rule")
    if not src.document.strip():
        p.append("source document missing")
    if not (src.reference or "").strip():
        p.append("source section / clause locator missing")
    if rs.layer in ("listing", "project", "engineering_decision"):
        if not (src.revision and src.publication_date):          # documents identified by revision + date
            p.append("source document revision and publication / effective date missing")
    else:
        expected_edition = rs.edition if rs.layer == "base_standard" else (rs.base_edition or rs.edition)
        if not src.edition:
            p.append("source edition missing")
        elif expected_edition and src.edition != expected_edition:
            p.append(f"source edition {src.edition!r} differs from the rule set's {expected_edition!r} "
                     "(editions are never mixed)")
    if not (src.accessed_at and src.access_method):
        p.append("source access metadata (access method, accessed at) missing")
    c = rule.constraint
    if c is None:
        p.append("no deterministic constraint: the rule cannot be evaluated by an engine")
    elif c.measurement == UNSUPPORTED_MEASUREMENT:
        p.append(f"UNSUPPORTED_MEASUREMENT: {c.unsupported_reason or 'measurement not implemented'}")
    elif c.measurement in DECLARED_MEASUREMENTS:
        p.append(f"measurement {c.measurement!r} is declared but not implemented")
    elif c.measurement not in MEASUREMENTS:
        p.append(f"unknown measurement {c.measurement!r}")
    else:
        lim = rule.parameter(c.limit_parameter) if c.derived is None else None
        if c.fact_requirement is not None:                         # M2.2B.2: structured-fact requirement
            if c.fact_requirement.fact not in FACTS:
                p.append(f"fact requirement on {c.fact_requirement.fact!r}, which FireAI cannot establish (not in FACTS)")
            allowed = rule.parameter(c.fact_requirement.allowed_parameter)
            vals = allowed.value if allowed is not None else None
            if not isinstance(vals, list) or not vals or any(isinstance(v, (dict, list, Quantity)) for v in vals):
                p.append(f"fact requirement: {c.fact_requirement.allowed_parameter!r} must be a non-empty list of "
                         "scalar allowed values")
        elif c.derived is not None:
            fac = rule.parameter(c.derived.factor_parameter)
            if fac is None or isinstance(fac.value, bool) or not isinstance(fac.value, (int, float)):
                p.append(f"derived limit: factor {c.derived.factor_parameter!r} missing or not a dimensionless number")
            if c.derived.from_key not in {r.constraint.key for r in rs.rules if r.constraint}:
                p.append(f"derived limit: {c.derived.from_key!r} is not a constraint of this rule set")
        elif lim is None or not isinstance(lim.value, Quantity):
            p.append(f"limit parameter {c.limit_parameter!r} missing or without a unit")
        else:
            try:
                if dimension(lim.value.unit) != MEASUREMENTS[c.measurement][0]:
                    p.append(f"limit unit {lim.value.unit!r} does not match a {MEASUREMENTS[c.measurement][0]} measurement")
            except UnitError as exc:
                p.append(str(exc))
        if c.bound not in MEASUREMENT_BOUNDS.get(c.measurement, {"max", "min"}):
            p.append(f"measurement {c.measurement!r} only supports bound {sorted(MEASUREMENT_BOUNDS[c.measurement])}, "
                     f"not {c.bound!r} (a rule mapped to a measurement with a different meaning)")
        if c.measurement in BOUNDARY_MEASUREMENTS:
            if not c.reference_kinds:
                p.append("boundary measurement without participating boundary kinds")
            elif set(c.reference_kinds) - BOUNDARY_KINDS:
                p.append(f"unknown boundary kinds {sorted(set(c.reference_kinds) - BOUNDARY_KINDS)}")
    for q in rule.parameters:
        if isinstance(q.value, Quantity):
            try:
                dimension(q.value.unit)
            except UnitError as exc:
                p.append(f"parameter {q.name}: {exc}")
    facts = [x.fact for x in rule.applicability.all_of] + [x.fact for e in rule.exceptions for x in e.when]
    unknown = sorted({f for f in facts if f not in FACTS})
    if unknown:
        p.append(f"applicability / exception facts FireAI cannot establish: {unknown}")
    ids = {r.rule_id for r in rs.rules}
    missing = [d for d in rule.depends_on if d not in ids]
    if missing:
        p.append(f"dependencies not in this rule set: {missing}")
    return p


class RuleStore:
    def __init__(self, root: Path, known_answers=None):
        self.root = Path(root)
        # M2.2B: NFPA-derived rules need verified independent known-answer cases before approval
        self.known_answers = known_answers

    def _known_answer_problems(self, rule: Rule, rs: RuleSet) -> list[str]:
        if not rs.governing_standard.upper().startswith("NFPA"):
            return []
        if self.known_answers is None:
            return ["NFPA-derived rules need a known-answer registry (KnownAnswerStore) to be approved"]
        return self.known_answers.problems_for_rule(rule)

    # ── io ────────────────────────────────────────────────────────────────────
    def _dir(self, rid: str) -> Path:
        if not _ID.match(rid or ""):
            raise RuleAuthoringError(f"invalid rule set id {rid!r}")
        return self.root / rid

    def _path(self, rid: str, version: str) -> Path:
        if not _ID.match(version or ""):
            raise RuleAuthoringError(f"invalid version {version!r}")
        return self._dir(rid) / version / "rule_set.json"

    def _write(self, rs: RuleSet) -> None:
        path = self._path(rs.rule_set_id, rs.version)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(rs.model_dump(mode="json"), indent=2), encoding="utf-8")
        tmp.replace(path)

    def _event(self, rid: str, version: str, action: str, by: str, **detail) -> None:
        d = self._dir(rid)
        d.mkdir(parents=True, exist_ok=True)
        rec = {"at": _now(), "rule_set_id": rid, "version": version, "action": action, "by": by,
               "identity_assurance": IDENTITY_ASSURANCE, **detail}
        with (d / "events.jsonl").open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(rec, sort_keys=True) + "\n")

    def events(self, rid: str) -> list[dict]:
        p = self._dir(rid) / "events.jsonl"
        return [json.loads(x) for x in p.read_text(encoding="utf-8").splitlines()] if p.is_file() else []

    def get(self, rid: str, version: str) -> RuleSet:
        path = self._path(rid, version)
        if not path.is_file():
            raise RuleAuthoringError(f"no rule set {rid} v{version}")
        return RuleSet.model_validate_json(path.read_text(encoding="utf-8"))

    def versions(self, rid: str) -> list[str]:
        d = self._dir(rid)
        return sorted(p.name for p in d.iterdir() if (p / "rule_set.json").is_file()) if d.is_dir() else []

    def latest_approved(self, rid: str) -> Optional[RuleSet]:
        ok = [self.get(rid, v) for v in self.versions(rid)]
        ok = [r for r in ok if r.review_status == "approved"]
        return ok[-1] if ok else None

    @staticmethod
    def _who(name: str) -> str:
        if not (name or "").strip():
            raise RuleAuthoringError("a named person is required")
        return name.strip()

    # ── workflow ──────────────────────────────────────────────────────────────
    def create_rule_set(self, *, rule_set_id: str, version: str, layer: str, governing_standard: str,
                        edition: Optional[str], created_by: str, base_edition: Optional[str] = None,
                        jurisdiction: Optional[str] = None, description: str = "") -> RuleSet:
        by = self._who(created_by)
        with _lock:
            if self._path(rule_set_id, version).exists():
                raise RuleAuthoringError(f"{rule_set_id} v{version} already exists (never overwritten)")
            rs = RuleSet(rule_set_id=rule_set_id, version=version, layer=layer, governing_standard=governing_standard,
                         edition=edition, base_edition=base_edition, jurisdiction=jurisdiction,
                         content_basis="authoritative", review_status="draft", description=description, rules=[])
            self._write(rs)
            self._event(rule_set_id, version, "create", by, layer=layer, standard=governing_standard, edition=edition)
        return rs

    def author_rule(self, rid: str, version: str, rule: Rule, author: str) -> Rule:
        by = self._who(author)
        with _lock:
            rs = self.get(rid, version)
            if rs.review_status != "draft":
                raise RuleAuthoringError(f"{rid} v{version} is {rs.review_status}: only DRAFT rule sets can be edited; "
                                         "an approved set changes only through new_version")
            if rule.source.kind == "synthetic_test_only":
                raise RuleAuthoringError("synthetic rules cannot be authored into an authoritative rule set")
            prev = self.latest_approved(rid)
            before = next((x for x in (prev.rules if prev else []) if x.rule_id == rule.rule_id), None)
            if before is not None:
                strip = {"author", "authored_at", "reviewer", "reviewed_at", "review_status"}
                changed = rule.model_dump(exclude=strip) != before.model_dump(exclude=strip)
                if changed and (rule.version == before.version or not rule.change_reason.strip()):
                    raise RuleAuthoringError(f"{rule.rule_id} differs from its approved version {before.version}: give "
                                             "it a new rule version and a change reason")
            r = rule.model_copy(update={"author": by, "authored_at": _now(), "reviewer": None, "reviewed_at": None,
                                        "review_status": "draft"})
            rules = [x for x in rs.rules if x.rule_id != r.rule_id] + [r]
            self._write(rs.model_copy(update={"rules": rules}))
            self._event(rid, version, "author_rule", by, rule_id=r.rule_id, rule_version=r.version)
        return r

    def submit_for_review(self, rid: str, version: str, by: str) -> RuleSet:
        who = self._who(by)
        with _lock:
            rs = self.get(rid, version)
            if rs.review_status != "draft":
                raise RuleAuthoringError(f"{rid} v{version} is {rs.review_status}")
            if not rs.rules:
                raise RuleAuthoringError(f"{rid} v{version} is EMPTY: nothing to review")
            rs = rs.model_copy(update={"review_status": "under_review"})
            self._write(rs)
            self._event(rid, version, "submit_for_review", who, rules=len(rs.rules))
        return rs

    def return_to_draft(self, rid: str, version: str, by: str, reason: str) -> RuleSet:
        who = self._who(by)
        with _lock:
            rs = self.get(rid, version)
            if rs.review_status != "under_review":
                raise RuleAuthoringError(f"{rid} v{version} is {rs.review_status}")
            rules = [r.model_copy(update={"review_status": "draft", "reviewer": None, "reviewed_at": None}) for r in rs.rules]
            rs = rs.model_copy(update={"review_status": "draft", "rules": rules})
            self._write(rs)
            self._event(rid, version, "return_to_draft", who, reason=reason)
        return rs

    def review_rule(self, rid: str, version: str, rule_id: str, reviewer: str, decision: str, note: str = "") -> Rule:
        who = self._who(reviewer)
        with _lock:
            rs = self.get(rid, version)
            if rs.review_status != "under_review":
                raise RuleAuthoringError(f"{rid} v{version} is {rs.review_status}: submit it for review first")
            rule = next((r for r in rs.rules if r.rule_id == rule_id), None)
            if rule is None:
                raise RuleAuthoringError(f"no rule {rule_id} in {rid} v{version}")
            if who == rule.author:
                raise RuleAuthoringError("the reviewer must be a different person from the author (two-person review)")
            if decision == "approve":
                problems = approval_problems(rule, rs) + self._known_answer_problems(rule, rs)
                if problems:
                    self._event(rid, version, "review_blocked", who, rule_id=rule_id, problems=problems)
                    raise RuleAuthoringError(f"{rule_id} cannot be approved: " + "; ".join(problems))
                new = rule.model_copy(update={"reviewer": who, "reviewed_at": _now(), "review_status": "reviewed"})
            elif decision == "reject":
                new = rule.model_copy(update={"reviewer": who, "reviewed_at": _now(), "review_status": "draft"})
            else:
                raise RuleAuthoringError("decision must be 'approve' or 'reject'")
            self._write(rs.model_copy(update={"rules": [new if r.rule_id == rule_id else r for r in rs.rules]}))
            self._event(rid, version, f"review_{decision}", who, rule_id=rule_id, note=note)
        return new

    def approve_rule_set(self, rid: str, version: str, approver: str) -> RuleSet:
        who = self._who(approver)
        with _lock:
            rs = self.get(rid, version)
            if rs.review_status != "under_review":
                raise RuleAuthoringError(f"{rid} v{version} is {rs.review_status}")
            if not rs.rules:
                raise RuleAuthoringError("an EMPTY rule set cannot be approved")
            pending = [r.rule_id for r in rs.rules if r.review_status != "reviewed" or not r.reviewer]
            if pending:
                raise RuleAuthoringError(f"rules not reviewed and accepted by a second person: {pending}")
            authored = sorted({r.author for r in rs.rules if r.author == who})
            if authored:
                raise RuleAuthoringError("the approver must not have authored rules in this set (two-person review)")
            problems = {r.rule_id: approval_problems(r, rs) + self._known_answer_problems(r, rs) for r in rs.rules}
            problems = {k: v for k, v in problems.items() if v}
            if problems:
                raise RuleAuthoringError(f"rules not approvable: {problems}")
            now = _now()
            rules = [r.model_copy(update={"review_status": "approved"}) for r in rs.rules]
            rs = rs.model_copy(update={"review_status": "approved", "reviewer": who, "reviewed_at": now, "rules": rules})
            self._write(rs)
            for v in self.versions(rid):
                if v != version:
                    old = self.get(rid, v)
                    if old.review_status == "approved":
                        self._write(old.model_copy(update={"review_status": "superseded"}))
                        self._event(rid, v, "superseded", who, by_version=version)
            self._event(rid, version, "approve_rule_set", who, digest=rs.digest())
        return rs

    def new_version(self, rid: str, from_version: str, new_version: str, by: str, change_reason: str) -> RuleSet:
        who = self._who(by)
        if not change_reason.strip():
            raise RuleAuthoringError("a change reason is required for a new version")
        with _lock:
            src = self.get(rid, from_version)
            if self._path(rid, new_version).exists():
                raise RuleAuthoringError(f"{rid} v{new_version} already exists")
            rules = [r.model_copy(update={"review_status": "draft", "reviewer": None, "reviewed_at": None}) for r in src.rules]
            rs = src.model_copy(update={"version": new_version, "review_status": "draft", "reviewer": None,
                                        "reviewed_at": None, "rules": rules})
            self._write(rs)
            self._event(rid, new_version, "new_version", who, from_version=from_version, change_reason=change_reason)
        return rs

    def retire(self, rid: str, version: str, by: str, reason: str) -> RuleSet:
        who = self._who(by)
        with _lock:
            rs = self.get(rid, version).model_copy(update={"review_status": "retired"})
            self._write(rs)
            self._event(rid, version, "retire", who, reason=reason)
        return rs
