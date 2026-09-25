"""Independent KNOWN-ANSWER cases for rule approval (Milestone 2.2B).

Before an NFPA-derived rule can be approved (even for INTERNAL R&D engineering), it needs at least
``MIN_CASES`` known-answer cases that are:

  * authored by a named person as a HAND CALCULATION (or independent software) — never derived from
    FireAI output (``derivation`` cannot be FireAI; a case is refused if it claims so);
  * reviewed by a different person;
  * VERIFIED: the deterministic engine was run on the case and reproduced the expected measurement,
    limit and PASS / FAIL (a mismatch is recorded and blocks approval — the case is not "fixed" by
    copying FireAI's number into it; a corrected case is a new case version by a person).

Cases are write-once, versioned records; every action is appended to ``events.jsonl``. This module
holds data and the approval check only; the geometry harness that runs a case lives with the engine
(tests / tooling) and records its outcome here with ``record_verification``.
"""

from __future__ import annotations

import json
import re
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal, Optional

from pydantic import BaseModel, Field, model_validator

from fireai.rules.identity import is_placeholder, placeholder_message

MIN_CASES = 2
IDENTITY_ASSURANCE = "unauthenticated_name (NOT PRODUCTION SAFE)"
_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.\-]{0,127}$")
_MACHINE = re.compile(r"\b(fireai|claude|gpt|llm|agent|model output)\b", re.I)
_lock = threading.Lock()


class KnownAnswerError(ValueError):
    pass


class KnownAnswerCase(BaseModel):
    case_id: str
    version: int = 1
    rule_id: str
    rule_version: str
    constraint_key: str                       # the resolved constraint the case checks
    fixture: dict[str, Any]                   # geometry spec, e.g. {"builder": "rectangular_room", "width_ft": ..}
    orientation: Optional[dict[str, Any]] = None          # explicit branch-line orientation used
    inputs: dict[str, Any]                    # positions (room frame), deflector elevation, ...
    expected_measurement: Optional[float] = None       # geometric cases
    expected_unit: str = ""
    expected_limit: Optional[float] = None
    expected_limit_unit: str = ""
    expected_fact_value: Optional[str] = None          # M2.2B.2: fact-requirement cases
    expected_allowed_values: Optional[list] = None
    expected_outcome: Literal["PASS", "FAIL"]
    hand_calculation: str                     # the derivation, step by step
    derivation: Literal["hand_calculation", "independent_software"]
    author: str
    authored_at: str
    reviewer: Optional[str] = None
    reviewed_at: Optional[str] = None
    status: Literal["draft", "reviewed", "rejected"] = "draft"
    provenance: str = ""                      # where the inputs came from (never FireAI output)
    verifications: list[dict] = Field(default_factory=list)

    @model_validator(mode="after")
    def _independent(self):
        if (self.expected_measurement is None) == (self.expected_fact_value is None):
            raise ValueError("a case states EITHER an expected measurement (geometry) OR an expected fact value")
        if self.expected_measurement is not None and self.expected_limit is None:
            raise ValueError("a geometric case needs its expected limit")
        if self.expected_fact_value is not None and not self.expected_allowed_values:
            raise ValueError("a fact case needs the expected allowed values")
        if not self.hand_calculation.strip():
            raise ValueError("a known-answer case needs its hand calculation")
        for f in ("author", "provenance"):
            if _MACHINE.search(getattr(self, f) or ""):
                raise ValueError(f"{f} names FireAI / a model / an agent: expected answers must be independent of "
                                 "FireAI output")
        return self


def rule_content_digest(rule) -> str:
    """Digest of what a rule SAYS (parameters, applicability, exceptions, constraint, source, version) —
    excluding who reviewed it — so a verification is tied to the exact content it checked."""
    import hashlib
    body = rule.model_dump(mode="json", exclude={"author", "authored_at", "reviewer", "reviewed_at", "review_status",
                                                 "effective_status", "notes"})
    return hashlib.sha256(json.dumps(body, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


class KnownAnswerStore:
    """Layout: <root>/<case_id>/v<NNNN>.json (write-once) ; <root>/events.jsonl."""

    def __init__(self, root: Path):
        self.root = Path(root)

    def _dir(self, case_id: str) -> Path:
        if not _ID.match(case_id or ""):
            raise KnownAnswerError(f"invalid case id {case_id!r}")
        return self.root / case_id

    def _event(self, rec: dict) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        with (self.root / "events.jsonl").open("a", encoding="utf-8") as fh:
            fh.write(json.dumps({"at": datetime.now(timezone.utc).isoformat(),
                                 "identity_assurance": IDENTITY_ASSURANCE, **rec}, sort_keys=True) + "\n")

    def _write(self, case: KnownAnswerCase) -> None:
        d = self._dir(case.case_id)
        d.mkdir(parents=True, exist_ok=True)
        p = d / f"v{case.version:04d}.json"
        tmp = p.with_suffix(".tmp")
        tmp.write_text(json.dumps(case.model_dump(mode="json"), indent=2), encoding="utf-8")
        tmp.replace(p)

    def get(self, case_id: str) -> Optional[KnownAnswerCase]:
        d = self._dir(case_id)
        files = sorted(d.glob("v*.json")) if d.exists() else []
        return KnownAnswerCase.model_validate_json(files[-1].read_text(encoding="utf-8")) if files else None

    def cases(self) -> list[KnownAnswerCase]:
        if not self.root.exists():
            return []
        return [c for c in (self.get(d.name) for d in sorted(self.root.iterdir()) if d.is_dir()) if c]

    def events(self) -> list[dict]:
        p = self.root / "events.jsonl"
        return [json.loads(x) for x in p.read_text(encoding="utf-8").splitlines()] if p.exists() else []

    def add_case(self, case: KnownAnswerCase) -> KnownAnswerCase:
        with _lock:
            prev = self.get(case.case_id)
            c = case.model_copy(update={"version": (prev.version + 1) if prev else 1, "status": "draft",
                                        "reviewer": None, "reviewed_at": None, "verifications": []})
            self._write(c)
            self._event({"action": "add_case", "case_id": c.case_id, "version": c.version, "rule_id": c.rule_id,
                         "by": c.author})
        return c

    def review_case(self, case_id: str, reviewer: str, decision: str, note: str = "") -> KnownAnswerCase:
        with _lock:
            c = self.get(case_id)
            if c is None:
                raise KnownAnswerError(f"no case {case_id}")
            if reviewer.strip() == c.author.strip():
                raise KnownAnswerError("the case reviewer must be a different person from its author")
            if decision == "approve":
                for name, control in ((reviewer, "known-answer review"), (c.author, "a reviewed known-answer case "
                                                                                   "(as its author)")):
                    if is_placeholder(name):
                        self._event({"action": "review_case_refused", "case_id": case_id, "by": reviewer,
                                     "reason": placeholder_message(name, control)})
                        raise KnownAnswerError(placeholder_message(name, control))
            if decision not in ("approve", "reject"):
                raise KnownAnswerError("decision must be 'approve' or 'reject'")
            c = c.model_copy(update={"reviewer": reviewer, "reviewed_at": datetime.now(timezone.utc).isoformat(),
                                     "status": "reviewed" if decision == "approve" else "rejected"})
            self._write(c)
            self._event({"action": f"review_case_{decision}", "case_id": case_id, "version": c.version,
                         "by": reviewer, "note": note})
        return c

    def record_verification(self, case_id: str, *, measured: Optional[float], limit: Optional[float],
                            outcome: str, engine: str, rule_set_digest: str, tolerance: float,
                            rule_digest: str, fact_value=None, allowed_values=None) -> dict:
        """Record what the deterministic engine produced for the case (the expected values are NOT
        changed): match = measurement and limit within tolerance and the same PASS / FAIL."""
        with _lock:
            c = self.get(case_id)
            if c is None:
                raise KnownAnswerError(f"no case {case_id}")
            from fireai.rules.model import Quantity
            from fireai.rules.units import to_canonical

            def canon(value, unit):             # a case may state values in any supported unit (e.g. 4 in)
                return to_canonical(Quantity(value=value, unit=unit))[0] if unit else value
            if c.expected_fact_value is not None:
                match = (outcome == c.expected_outcome and fact_value == c.expected_fact_value
                         and sorted(allowed_values or []) == sorted(c.expected_allowed_values or []))
            else:
                match = (measured is not None and limit is not None and outcome == c.expected_outcome
                         and abs(measured - canon(c.expected_measurement, c.expected_unit)) <= tolerance
                         and abs(limit - canon(c.expected_limit, c.expected_limit_unit)) <= tolerance)
            rec = {"at": datetime.now(timezone.utc).isoformat(), "engine": engine, "rule_set_digest": rule_set_digest,
                   "rule_digest": rule_digest, "fact_value": fact_value, "allowed_values": allowed_values,
                   "measured": measured, "limit": limit, "outcome": outcome, "tolerance": tolerance, "match": match}
            c = c.model_copy(update={"verifications": c.verifications + [rec]})
            self._write(c)
            self._event({"action": "verification", "case_id": case_id, "version": c.version, "match": match,
                         "rule_set_digest": rule_set_digest})
        return rec

    def problems_for_rule(self, rule) -> list[str]:
        """Why ``rule`` (this exact content) lacks known-answer validation (empty = satisfied)."""
        rule_id, rule_version, rule_author = rule.rule_id, rule.version, rule.author
        digest = rule_content_digest(rule)
        cases = [c for c in self.cases() if c.rule_id == rule_id and c.rule_version == rule_version]
        good = [c for c in cases if c.status == "reviewed" and c.verifications and c.verifications[-1]["match"]
                and c.verifications[-1].get("rule_digest") == digest
                and not is_placeholder(c.author) and not is_placeholder(c.reviewer)]
        stale = [c.case_id for c in cases if c.verifications and c.verifications[-1].get("rule_digest") != digest]
        if stale:
            p_stale = [f"known-answer cases verified against DIFFERENT rule content (re-run them): {stale}"]
        else:
            p_stale = []
        p = list(p_stale)
        failing = [c.case_id for c in cases if c.verifications and not c.verifications[-1]["match"]]
        if failing:
            p.append(f"known-answer cases whose latest engine run did NOT reproduce the expected answer: {failing}")
        if len(good) < MIN_CASES:
            p.append(f"{len(good)} reviewed and verified known-answer case(s) for {rule_id} v{rule_version}; "
                     f"{MIN_CASES} independent cases are required")
        if rule_author and good and all(c.author == rule_author for c in good):
            p.append("every verified case was authored by the rule's author: at least one must be independent")
        return p
