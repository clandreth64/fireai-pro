"""External RELEASE eligibility of rule content (Milestone 2.2A.1) — deterministic, per rule set.

ENGINEERING_RULE_STATUS (is the content reviewed / approved / fully sourced?) and EXTERNAL_RELEASE_STATUS
(may this product context use it?) are evaluated separately and both reported. NFPA-derived content is
release-eligible for an EXTERNAL context (beta, production, commercial API, external engineering) only
if ALL hold:

  * the rule set is approved and non-empty, and every rule's provenance passes the approval checks;
  * the source's authorization (``AuthorizationStore``, owner-supplied) is COMMERCIAL_AUTHORIZED;
  * the authorization's permitted uses cover the context;
  * the authorization is in effect on the evaluation date (effective, not expired).

INTERNAL DEVELOPMENT may use INTERNAL_R_AND_D_ONLY (or pending) content only with an explicit
``DevelopmentOverride`` — an object passed in code, never an environment flag — which is recorded in
the decision, audit-logged when a store is given, and labels everything INTERNAL R&D / NOT FOR
EXTERNAL USE. An override offered in any external context is itself a blocker (no hidden bypass,
no ``force``). Synthetic (TEST ONLY) content is never releasable as real engineering content.

This module does not interpret any agreement: it only enforces the state the owner recorded.
"""

from __future__ import annotations

import hashlib
import json
from datetime import date
from typing import Literal, Optional

from pydantic import BaseModel, Field

from fireai.rules.authorization import EXTERNAL_USES, AuthorizationStore, source_key
from fireai.rules.model import RuleSet
from fireai.rules.store import approval_problems

ReleaseContext = Literal["internal_development", "beta", "production", "commercial_api", "external_engineering"]
INTERNAL_LABELS = ("INTERNAL R&D", "NOT FOR EXTERNAL USE")
_INTERNAL_OK = {"INTERNAL_R_AND_D_ONLY", "COMMERCIAL_AUTHORIZATION_PENDING", "COMMERCIAL_AUTHORIZED"}
DEPLOYMENT_MODES: dict[str, str] = {"development": "internal_development", "beta": "beta", "production": "production"}


class DevelopmentOverride(BaseModel):
    """Explicit, visible permission to use not-yet-commercially-authorized content for INTERNAL R&D."""
    enabled_by: str
    reason: str
    labels: tuple[str, ...] = INTERNAL_LABELS


class ReleaseBlocker(BaseModel):
    code: str
    message: str
    rule_set_id: Optional[str] = None
    source_key: Optional[str] = None


class RuleSetReleaseStatus(BaseModel):
    rule_set_id: str
    version: str
    digest: str
    source_key: Optional[str]
    engineering_rule_status: Literal["approved_and_sourced", "not_approved", "provenance_incomplete", "empty",
                                     "synthetic_test_only", "draft"]
    authorization: Optional[dict] = None             # {version, status, digest, permitted_uses, ...}
    external_release_status: Literal["RELEASE_ELIGIBLE", "NOT_RELEASE_ELIGIBLE", "INTERNAL_ONLY"]


class ReleaseDecision(BaseModel):
    context: ReleaseContext
    evaluated_on: date
    eligible: bool
    rule_sets: list[RuleSetReleaseStatus]
    blockers: list[ReleaseBlocker] = Field(default_factory=list)
    override: Optional[dict] = None
    labels: list[str] = Field(default_factory=list)
    fingerprint: str = ""


class ReleaseBlocked(PermissionError):
    def __init__(self, decision: ReleaseDecision):
        self.decision = decision
        super().__init__("; ".join(f"{b.code}: {b.message}" for b in decision.blockers))


def _engineering_status(rs: RuleSet) -> str:
    if rs.content_basis == "synthetic_test_only":
        return "synthetic_test_only"
    if not rs.rules:
        return "empty"
    if rs.review_status != "approved":
        return "not_approved" if rs.review_status != "draft" else "draft"
    if any(approval_problems(r, rs) for r in rs.rules):
        return "provenance_incomplete"
    return "approved_and_sourced"


def evaluate_release(rule_sets: list[RuleSet], context: str, authorizations: AuthorizationStore, on_date: date,
                     override: Optional[DevelopmentOverride] = None) -> ReleaseDecision:
    if context not in ("internal_development", *EXTERNAL_USES):
        raise ValueError(f"unknown release context {context!r}")
    external = context in EXTERNAL_USES
    blockers: list[ReleaseBlocker] = []
    statuses: list[RuleSetReleaseStatus] = []
    labels: list[str] = []
    override_used = False
    if override is not None and external:
        blockers.append(ReleaseBlocker(code="OVERRIDE_NOT_PERMITTED",
                                       message=f"a development override can never apply to the {context} context"))
    for rs in rule_sets:
        eng = _engineering_status(rs)
        key = source_key(rs)
        auth = authorizations.current(key) if key else None
        ok = True

        def block(code, message, rs=rs, key=key):
            blockers.append(ReleaseBlocker(code=code, message=message, rule_set_id=rs.rule_set_id, source_key=key))

        if eng == "synthetic_test_only":
            if external:
                block("SYNTHETIC_CONTENT_NOT_RELEASABLE", "TEST ONLY synthetic content is never real engineering content")
                ok = False
            else:
                labels += ["TEST ONLY", "NOT FOR ENGINEERING USE"]
        elif external and eng != "approved_and_sourced":
            block({"empty": "EMPTY_RULESET", "draft": "RULESET_NOT_APPROVED", "not_approved": "RULESET_NOT_APPROVED",
                   "provenance_incomplete": "PROVENANCE_INCOMPLETE"}[eng],
                  f"rule set {rs.rule_set_id} v{rs.version} engineering status is {eng}")
            ok = False
        if key is not None:
            st = auth.status if auth else "UNKNOWN"
            if external:
                if st != "COMMERCIAL_AUTHORIZED":
                    block(f"SOURCE_AUTHORIZATION_{st}", f"{key} is {st}: not commercially authorized for {context}")
                    ok = False
                else:
                    if context not in auth.permitted_uses:
                        block("AUTHORIZATION_SCOPE_EXCLUDES_USE",
                              f"{key} authorization {auth.authorization_reference} does not permit {context}")
                        ok = False
                    if auth.effective_date and on_date < auth.effective_date:
                        block("AUTHORIZATION_NOT_YET_EFFECTIVE", f"{key} authorization is effective {auth.effective_date}")
                        ok = False
                    if auth.expiration_date and on_date > auth.expiration_date:
                        block("AUTHORIZATION_EXPIRED", f"{key} authorization expired {auth.expiration_date}")
                        ok = False
            else:
                if st not in _INTERNAL_OK:
                    block(f"SOURCE_AUTHORIZATION_{st}", f"{key} is {st}: not usable even for internal development")
                    ok = False
                elif st != "COMMERCIAL_AUTHORIZED" or "internal_development" not in (auth.permitted_uses if auth else []):
                    if override is None:
                        block("INTERNAL_DEVELOPMENT_NOT_ENABLED",
                              f"{key} is {st}: internal use requires an explicit DevelopmentOverride")
                        ok = False
                    else:
                        override_used = True
                        labels += list(override.labels)
        statuses.append(RuleSetReleaseStatus(
            rule_set_id=rs.rule_set_id, version=rs.version, digest=rs.digest(), source_key=key,
            engineering_rule_status=eng,
            authorization=({"version": auth.version, "status": auth.status, "digest": auth.digest(),
                            "permitted_uses": list(auth.permitted_uses),
                            "authorization_reference": auth.authorization_reference,
                            "effective_date": str(auth.effective_date) if auth.effective_date else None,
                            "expiration_date": str(auth.expiration_date) if auth.expiration_date else None}
                           if auth else ({"status": "UNKNOWN"} if key else None)),
            # an internal evaluation never makes anything externally releasable
            external_release_status=("NOT_RELEASE_ELIGIBLE" if not ok else
                                     "RELEASE_ELIGIBLE" if external else "INTERNAL_ONLY")))
    if override_used:
        authorizations.log_override(context, override.model_dump(mode="json"),
                                    [s.source_key for s in statuses if s.source_key])
    d = ReleaseDecision(context=context, evaluated_on=on_date, eligible=not blockers, rule_sets=statuses,
                        blockers=blockers, override=override.model_dump(mode="json") if override_used else None,
                        labels=sorted(set(labels)))
    d.fingerprint = hashlib.sha256(json.dumps(d.model_dump(mode="json", exclude={"fingerprint"}), sort_keys=True,
                                              separators=(",", ":")).encode()).hexdigest()
    return d


def assert_ruleset_release_eligible(rule_sets: list[RuleSet], context: str, authorizations: AuthorizationStore,
                                    on_date: date, override: Optional[DevelopmentOverride] = None) -> ReleaseDecision:
    """Raise ``ReleaseBlocked`` unless eligible; returns the (fingerprinted) decision otherwise."""
    d = evaluate_release(rule_sets, context, authorizations, on_date, override)
    if not d.eligible:
        raise ReleaseBlocked(d)
    return d


def guard_rule_set_activation(settings, rule_sets: list[RuleSet], authorizations: AuthorizationStore, on_date: date,
                              override: Optional[DevelopmentOverride] = None) -> ReleaseDecision:
    """Deployment guard: the context comes from the configured deployment mode (default: production,
    fail-closed). Beta / production refuse R&D-only content whatever else is passed."""
    mode = getattr(settings, "deployment_mode", "production")
    if mode not in DEPLOYMENT_MODES:
        raise ValueError(f"unknown deployment mode {mode!r} (expected one of {sorted(DEPLOYMENT_MODES)})")
    return assert_ruleset_release_eligible(rule_sets, DEPLOYMENT_MODES[mode], authorizations, on_date, override)
