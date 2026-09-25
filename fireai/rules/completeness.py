"""Rule-subset COMPLETENESS vs individual rule approval vs compliance (Milestone 2.2B.2).

Four questions, never merged:

  A. RULE APPROVAL          — is each rule reviewed, sourced and approved? (``fireai.rules.store``)
  B. SUBSET TECHNICAL STATUS — are all rules of the package approved and do they cover every family the
                               manifest lists?                                         -> VALIDATED / PARTIAL / NOT_VALIDATED
  C. ENVELOPE COMPLETENESS  — has a QUALIFIED HUMAN reviewed and approved that the manifest lists every
                               rule family the supported envelope requires?             -> ESTABLISHED / NOT_ESTABLISHED
  D. EXTERNAL RELEASE       — source authorization (``fireai.rules.release``).

``NFPA_COMPLIANCE`` has exactly one value in this codebase: NOT_CLAIMED. An approved partial catalog —
or even an approved, "complete for the envelope" manifest — never becomes a code-compliance claim:
compliance also depends on everything outside the envelope (hydraulics, water supply, other
chapters, the AHJ) that FireAI does not evaluate.

A completeness manifest is edition- and envelope-specific, versioned (write-once), authored by a person,
independently reviewed and approved by different people, with provenance. Agents, LLMs and the system
cannot author, review or approve it.
"""

from __future__ import annotations

import hashlib
import json
import re
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal, Optional

from pydantic import BaseModel, model_validator

from fireai.rules.authorization import AuthorityActor, source_key
from fireai.rules.identity import is_placeholder, placeholder_message
from fireai.rules.intake import M22B_MAPPINGS
from fireai.rules.model import RuleSet

IDENTITY_ASSURANCE = "unauthenticated_name (NOT PRODUCTION SAFE)"
HUMAN_ROLES = frozenset({"owner", "admin", "reviewer", "rule_author"})
_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.\-]{0,127}$")
_lock = threading.Lock()


class CompletenessError(ValueError):
    pass


class RequiredRuleFamily(BaseModel):
    family_id: str
    title: str                                 # neutral label, never standard text
    constraint_key: str                        # the resolved constraint that implements it
    locator: Optional[str] = None


class CompletenessManifest(BaseModel):
    manifest_id: str
    version: int = 1
    governing_standard: str
    edition: str
    envelope_id: str
    families: list[RequiredRuleFamily]
    status: Literal["DEVELOPMENT_INCOMPLETE", "UNDER_REVIEW", "APPROVED_COMPLETE_FOR_ENVELOPE", "SUPERSEDED"]
    completeness: Literal["NOT_ESTABLISHED", "ESTABLISHED"] = "NOT_ESTABLISHED"
    author: str
    authored_at: str
    provenance: str                            # what the family list is based on
    reviewer: Optional[str] = None
    approver: Optional[str] = None
    approved_at: Optional[str] = None
    qualified_review_statement: str = ""       # the qualified reviewer's own statement of the review done
    change_reason: str = ""
    identity_assurance: str = IDENTITY_ASSURANCE

    @model_validator(mode="after")
    def _completeness_needs_qualified_approval(self):
        if self.completeness == "ESTABLISHED" and any(is_placeholder(x) for x in (self.author, self.reviewer,
                                                                                    self.approver)):
            raise ValueError(placeholder_message("PLACEHOLDER_*", "an established completeness manifest"))
        if (self.completeness == "ESTABLISHED") != (self.status == "APPROVED_COMPLETE_FOR_ENVELOPE"):
            raise ValueError("completeness is ESTABLISHED exactly when the manifest is APPROVED_COMPLETE_FOR_ENVELOPE")
        if self.completeness == "ESTABLISHED":
            people = [self.author, self.reviewer, self.approver]
            if not all(people) or len(set(people)) != 3 or not self.qualified_review_statement.strip():
                raise ValueError("an established manifest needs a distinct author, reviewer and approver and the "
                                 "qualified reviewer's statement")
        return self

    def digest(self) -> str:
        return hashlib.sha256(json.dumps(self.model_dump(mode="json"), sort_keys=True,
                                         separators=(",", ":")).encode()).hexdigest()


# The DEVELOPMENT manifest for NFPA13-2019-DEV-ENVELOPE-1 (owner instruction M2.2B.2). It lists the FIRST
# INTENDED rule subset only; it does NOT assert these are all the requirements applicable to the envelope.
NFPA13_2019_DEV_MANIFEST = CompletenessManifest(
    manifest_id="NFPA13-2019-DEV-ENVELOPE-1-MANIFEST", version=1, governing_standard="NFPA 13", edition="2019",
    envelope_id="NFPA13-2019-DEV-ENVELOPE-1",
    families=[RequiredRuleFamily(family_id=m.mapping_id, title=t, constraint_key=m.key, locator=m.locator)
              for m, t in zip(M22B_MAPPINGS.values(), (
                  "maximum protection area", "maximum sprinkler spacing", "maximum wall distance",
                  "minimum wall distance", "minimum sprinkler spacing", "minimum deflector distance",
                  "maximum deflector distance", "response-type requirement"), strict=True)],
    status="DEVELOPMENT_INCOMPLETE", completeness="NOT_ESTABLISHED", author="owner", authored_at="2026-09-25",
    provenance="owner M2.2B.2 instruction: the first INTENDED rule subset. Not a determination of every requirement "
               "applicable to the envelope — that belongs to later qualified code review.")


class ManifestStore:
    """<root>/<manifest_id>/v<NNNN>.json (write-once) ; <root>/events.jsonl."""

    def __init__(self, root: Path):
        self.root = Path(root)

    def _dir(self, mid: str) -> Path:
        if not _ID.match(mid or ""):
            raise CompletenessError(f"invalid manifest id {mid!r}")
        return self.root / mid

    def _event(self, rec: dict) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        with (self.root / "events.jsonl").open("a", encoding="utf-8") as fh:
            fh.write(json.dumps({"at": datetime.now(timezone.utc).isoformat(), **rec}, sort_keys=True) + "\n")

    def get(self, mid: str) -> Optional[CompletenessManifest]:
        d = self._dir(mid)
        files = sorted(d.glob("v*.json")) if d.exists() else []
        return CompletenessManifest.model_validate_json(files[-1].read_text(encoding="utf-8")) if files else None

    def events(self) -> list[dict]:
        p = self.root / "events.jsonl"
        return [json.loads(x) for x in p.read_text(encoding="utf-8").splitlines()] if p.exists() else []

    def _write(self, m: CompletenessManifest) -> None:
        d = self._dir(m.manifest_id)
        d.mkdir(parents=True, exist_ok=True)
        p = d / f"v{m.version:04d}.json"
        if p.exists():
            raise CompletenessError("manifest versions are never overwritten")
        p.write_text(json.dumps(m.model_dump(mode="json"), indent=2), encoding="utf-8")

    def record(self, m: CompletenessManifest, actor: AuthorityActor) -> CompletenessManifest:
        if actor.role not in HUMAN_ROLES:
            self._event({"action": "manifest_change_refused", "manifest_id": m.manifest_id, "by": actor.name,
                         "role": actor.role})
            raise CompletenessError(f"{actor.role} may not author a completeness manifest")
        if m.completeness == "ESTABLISHED":
            raise CompletenessError("a manifest is established only through approve_complete")
        with _lock:
            prev = self.get(m.manifest_id)
            m = m.model_copy(update={"version": prev.version + 1 if prev else 1, "author": actor.name})
            self._write(m)
            self._event({"action": "manifest_recorded", "manifest_id": m.manifest_id, "version": m.version,
                         "by": actor.name, "role": actor.role, "status": m.status, "digest": m.digest()})
        return m

    def approve_complete(self, mid: str, reviewer: AuthorityActor, approver: AuthorityActor,
                         qualified_review_statement: str) -> CompletenessManifest:
        """A qualified reviewer and a separate approver declare the family list COMPLETE for its envelope."""
        with _lock:
            m = self.get(mid)
            if m is None:
                raise CompletenessError(f"no manifest {mid}")
            for name, control in ((m.author, "completeness approval (as the manifest author)"),
                                  (reviewer.name, "completeness-manifest review"),
                                  (approver.name, "completeness-manifest approval")):
                if is_placeholder(name):
                    self._event({"action": "manifest_approval_refused", "manifest_id": mid, "by": name,
                                 "reason": placeholder_message(name, control)})
                    raise CompletenessError(placeholder_message(name, control))
            for a in (reviewer, approver):
                if a.role not in HUMAN_ROLES:
                    self._event({"action": "manifest_approval_refused", "manifest_id": mid, "by": a.name,
                                 "role": a.role})
                    raise CompletenessError(f"{a.role} may not review or approve completeness")
            new = m.model_copy(update={"version": m.version + 1, "status": "APPROVED_COMPLETE_FOR_ENVELOPE",
                                       "completeness": "ESTABLISHED", "reviewer": reviewer.name,
                                       "approver": approver.name, "approved_at": datetime.now(timezone.utc).isoformat(),
                                       "qualified_review_statement": qualified_review_statement})
            new = CompletenessManifest.model_validate(new.model_dump())          # re-run the invariants
            self._write(new)
            self._event({"action": "manifest_approved_complete", "manifest_id": mid, "version": new.version,
                         "reviewer": reviewer.name, "approver": approver.name, "digest": new.digest()})
        return new


def nfpa_status(rule_sets: list[RuleSet], manifest: Optional[CompletenessManifest],
                authorization_statuses: Optional[dict[str, str]] = None,
                external_release_eligible: bool = False) -> dict:
    """The four answers, separately. There is no code path that returns a compliance claim."""
    from fireai.rules.release import _engineering_status
    nfpa = [s for s in rule_sets if source_key(s)]
    approved = bool(nfpa) and all(_engineering_status(s) == "approved_and_sourced" for s in nfpa)
    covered = {r.constraint.key for s in nfpa if s.review_status == "approved"
               for r in s.rules if r.constraint is not None and r.review_status == "approved"}
    missing = [f.family_id for f in (manifest.families if manifest else []) if f.constraint_key not in covered]
    editions = {s.edition for s in nfpa}
    manifest_matches = manifest is not None and editions == {manifest.edition} and \
        all(s.governing_standard == manifest.governing_standard for s in nfpa)
    subset = ("NOT_VALIDATED" if not approved else
              "VALIDATED" if manifest_matches and not missing else "PARTIAL")
    completeness = ("ESTABLISHED" if manifest_matches and manifest.completeness == "ESTABLISHED" and not missing
                    else "NOT_ESTABLISHED")
    auth = authorization_statuses or {}
    internal = any(auth.get(source_key(s), "UNKNOWN") != "COMMERCIAL_AUTHORIZED" for s in nfpa)
    return {
        "RULE_APPROVAL": ("APPROVED_FOR_INTERNAL_R_AND_D_RULE_VALIDATION" if approved and internal
                          else "APPROVED" if approved else "NOT_APPROVED"),
        "NFPA_RULE_SUBSET_STATUS": subset,
        "NFPA_ENVELOPE_COMPLETENESS": completeness,
        "NFPA_COMPLIANCE": "NOT_CLAIMED",
        "EXTERNAL_RELEASE": "RELEASE_ELIGIBLE" if external_release_eligible else "NOT_RELEASE_ELIGIBLE",
        "manifest": ({"manifest_id": manifest.manifest_id, "version": manifest.version, "status": manifest.status,
                      "edition": manifest.edition, "envelope_id": manifest.envelope_id, "digest": manifest.digest()}
                     if manifest else None),
        "families_missing_from_approved_rules": missing,
        "notes": ["A validated rule SUBSET is not NFPA compliance: FireAI evaluates only the rule families listed "
                  "for its supported envelope and makes no compliance claim."]
                 + ([] if manifest_matches or manifest is None else
                    ["the completeness manifest is for another edition / standard than the rule sets"]),
    }
