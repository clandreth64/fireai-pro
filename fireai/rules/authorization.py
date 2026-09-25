"""Source AUTHORIZATION state (Milestone 2.2A.1) — separate from engineering source PROVENANCE.

Provenance (``RuleSource``: document, edition, locator, access) says WHERE rule content came from and
is part of engineering review. Authorization says whether FireAI may USE content derived from that
source in a given product context (internal R&D, beta, production, ...). The two are never coupled:
a rule set can be fully reviewed and approved for engineering and still be ineligible for release.

FireAI never infers, interprets or promotes authorization. The state is supplied by the OWNER / an
ADMIN, versioned (write-once records) and audit-logged. Agents, LLMs, rule authors and reviewers
cannot change it. No contract text, customer id, license number, subscription id or personal data
from a source document is stored: an owner-controlled reference (``FIREAI-AUTH-...``) points to
wherever the owner keeps the agreement.

Layout: <root>/<source-key-slug>/v<NNNN>.json (write-once) ; <root>/events.jsonl (append-only).
"""

from __future__ import annotations

import hashlib
import json
import re
import threading
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, model_validator

from fireai.rules.identity import is_placeholder, placeholder_message
from fireai.rules.model import RuleSet

IDENTITY_ASSURANCE = "unauthenticated_name (NOT PRODUCTION SAFE)"

AuthorizationStatus = Literal["INTERNAL_R_AND_D_ONLY", "COMMERCIAL_AUTHORIZATION_PENDING", "COMMERCIAL_AUTHORIZED",
                              "REVOKED", "EXPIRED", "NOT_AVAILABLE", "UNKNOWN"]
ReleaseUse = Literal["internal_development", "beta", "production", "commercial_api", "external_engineering"]
EXTERNAL_USES: frozenset[str] = frozenset({"beta", "production", "commercial_api", "external_engineering"})
ActorRole = Literal["owner", "admin", "rule_author", "reviewer", "agent", "llm", "system"]
AUTHORIZING_ROLES: frozenset[str] = frozenset({"owner", "admin"})

_REFERENCE = re.compile(r"^FIREAI-AUTH-[A-Z0-9][A-Z0-9-]{0,47}$")
_IDENTIFIER_HINTS = (
    (re.compile(r"[^@\s]+@[^@\s]+\.[^@\s]+"), "an e-mail address"),
    (re.compile(r"\d{6,}"), "a long number (customer / order / license identifiers are not stored)"),
    (re.compile(r"\b(customer|licen[cs]e|subscription|order|account|invoice)\s*(no\.?|number|id|#)", re.I),
     "a customer / license / subscription / order identifier"),
)
_lock = threading.Lock()


class AuthorizationError(ValueError):
    pass


def _screen(field: str, text: Optional[str]) -> None:
    for rx, what in _IDENTIFIER_HINTS:
        if text and rx.search(text):
            raise ValueError(f"{field} appears to contain {what}; authorization records hold only owner-controlled "
                             "references, never identifiers from source documents or personal data")


class AuthorityActor(BaseModel):
    """Who is asking to change authorization. Only owner / admin roles may (names are unauthenticated)."""
    name: str
    role: ActorRole


class SourceAuthorization(BaseModel):
    """One versioned authorization record for ONE source (e.g. ``NFPA 13:2019``)."""
    model_config = ConfigDict(extra="forbid")          # no room for customer / license identifiers

    source_key: str
    version: int = Field(ge=1)
    status: AuthorizationStatus
    authorization_reference: Optional[str] = None      # owner-controlled, e.g. FIREAI-AUTH-2026-A
    effective_date: Optional[date] = None
    expiration_date: Optional[date] = None
    permitted_uses: list[ReleaseUse] = Field(default_factory=list)
    notes: str = ""
    change_reason: str
    entered_by: str
    entered_by_role: Literal["owner", "admin"]
    entered_at: str
    supersedes_version: Optional[int] = None
    identity_assurance: str = IDENTITY_ASSURANCE

    @model_validator(mode="after")
    def _consistent(self):
        _screen("notes", self.notes)
        _screen("change_reason", self.change_reason)
        if self.authorization_reference is not None:
            if not _REFERENCE.match(self.authorization_reference):
                raise ValueError("authorization_reference must be an owner-controlled reference FIREAI-AUTH-<A-Z0-9->")
            _screen("authorization_reference", self.authorization_reference.removeprefix("FIREAI-AUTH-"))
        if self.status == "COMMERCIAL_AUTHORIZED":
            if not (self.authorization_reference and self.effective_date and self.permitted_uses):
                raise ValueError("COMMERCIAL_AUTHORIZED needs an owner reference, an effective date and permitted uses")
        elif set(self.permitted_uses) & EXTERNAL_USES:
            raise ValueError(f"{self.status} cannot permit external uses {sorted(set(self.permitted_uses) & EXTERNAL_USES)}")
        if self.status in ("NOT_AVAILABLE", "UNKNOWN", "REVOKED", "EXPIRED") and self.permitted_uses:
            raise ValueError(f"{self.status} permits no use")
        if self.expiration_date and self.effective_date and self.expiration_date < self.effective_date:
            raise ValueError("expiration precedes the effective date")
        return self

    def digest(self) -> str:
        return hashlib.sha256(json.dumps(self.model_dump(mode="json"), sort_keys=True,
                                         separators=(",", ":")).encode()).hexdigest()


def source_key(rs: RuleSet) -> Optional[str]:
    """The authorization key of an NFPA-derived rule set ("<standard>:<edition>"), else None.
    Synthetic content is never NFPA-derived real content; non-NFPA layers (listings, amendments,
    project documents) are not governed by this NFPA source authorization."""
    if rs.content_basis != "authoritative" or not rs.governing_standard.upper().startswith("NFPA"):
        return None
    return f"{rs.governing_standard}:{rs.edition or rs.base_edition or 'UNSPECIFIED'}"


def _slug(key: str) -> str:
    s = re.sub(r"[^A-Za-z0-9]+", "-", key).strip("-")
    if not s:
        raise AuthorizationError(f"invalid source key {key!r}")
    return s


class AuthorizationStore:
    def __init__(self, root: Path):
        self.root = Path(root)

    def _dir(self, key: str) -> Path:
        return self.root / _slug(key)

    def history(self, key: str) -> list[SourceAuthorization]:
        d = self._dir(key)
        if not d.exists():
            return []
        recs = [SourceAuthorization.model_validate_json(p.read_text(encoding="utf-8")) for p in sorted(d.glob("v*.json"))]
        return sorted((r for r in recs if r.source_key == key), key=lambda r: r.version)

    def current(self, key: str) -> Optional[SourceAuthorization]:
        h = self.history(key)
        return h[-1] if h else None

    def status(self, key: str) -> str:
        c = self.current(key)
        return c.status if c else "UNKNOWN"

    def events(self) -> list[dict]:
        p = self.root / "events.jsonl"
        return [json.loads(line) for line in p.read_text(encoding="utf-8").splitlines()] if p.exists() else []

    def _event(self, rec: dict) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        with (self.root / "events.jsonl").open("a", encoding="utf-8") as fh:
            fh.write(json.dumps({"at": datetime.now(timezone.utc).isoformat(), **rec}, sort_keys=True) + "\n")

    def record(self, key: str, status: str, actor: AuthorityActor, change_reason: str, *,
               authorization_reference: Optional[str] = None, effective_date: Optional[date] = None,
               expiration_date: Optional[date] = None, permitted_uses: Optional[list[str]] = None,
               notes: str = "") -> SourceAuthorization:
        """Record a NEW authorization version (the previous one is kept). Owner / admin only."""
        if is_placeholder(actor.name):
            self._event({"action": "authorization_change_refused", "source_key": key, "by": actor.name,
                         "role": actor.role, "requested_status": status,
                         "reason": placeholder_message(actor.name, "a source-authorization change")})
            raise AuthorizationError(placeholder_message(actor.name, "a source-authorization change"))
        if actor.role not in AUTHORIZING_ROLES:
            self._event({"action": "authorization_change_refused", "source_key": key, "by": actor.name,
                         "role": actor.role, "requested_status": status,
                         "reason": "only the owner or an admin may change source authorization"})
            raise AuthorizationError(f"{actor.role} {actor.name!r} may not change source authorization: only the owner "
                                     "or an admin can (FireAI, agents, LLMs, rule authors and reviewers never can)")
        if not change_reason.strip():
            raise AuthorizationError("a change reason is required")
        with _lock:
            prev = self.current(key)
            rec = SourceAuthorization(
                source_key=key, version=(prev.version + 1 if prev else 1), status=status,
                authorization_reference=authorization_reference, effective_date=effective_date,
                expiration_date=expiration_date, permitted_uses=list(permitted_uses or []), notes=notes,
                change_reason=change_reason, entered_by=actor.name, entered_by_role=actor.role,
                entered_at=datetime.now(timezone.utc).isoformat(), supersedes_version=prev.version if prev else None)
            d = self._dir(key)
            d.mkdir(parents=True, exist_ok=True)
            path = d / f"v{rec.version:04d}.json"
            if path.exists():
                raise AuthorizationError("authorization records are never overwritten")
            path.write_text(json.dumps(rec.model_dump(mode="json"), indent=2), encoding="utf-8")
            self._event({"action": "authorization_changed", "source_key": key, "version": rec.version,
                         "from_status": prev.status if prev else "UNKNOWN", "to_status": rec.status,
                         "by": actor.name, "role": actor.role, "identity_assurance": IDENTITY_ASSURANCE,
                         "digest": rec.digest(), "change_reason": change_reason})
        return rec

    def log_override(self, context: str, override: dict, source_keys: list[str]) -> None:
        self._event({"action": "internal_development_override_used", "context": context, "override": override,
                     "source_keys": sorted(source_keys)})


# Owner-declared state (M2.2A.1 instruction, 2026-09-24). Neither is commercially authorized.
OWNER_DECLARED_INITIAL_STATUS: dict[str, tuple[str, list[str], str]] = {
    "NFPA 13:2019": ("INTERNAL_R_AND_D_ONLY", ["internal_development"],
                     "owner: source material licensed for personal / internal use; commercial authorization not "
                     "finalized"),
    "NFPA 13:2025": ("NOT_AVAILABLE", [], "owner: source not available / not authorized"),
}


def seed_owner_declared(store: AuthorizationStore, owner_name: str) -> list[SourceAuthorization]:
    """Record the owner-declared initial statuses where no record exists yet (never overwrites)."""
    out = []
    for key, (status, uses, reason) in OWNER_DECLARED_INITIAL_STATUS.items():
        if store.current(key) is None:
            out.append(store.record(key, status, AuthorityActor(name=owner_name, role="owner"), reason,
                                    permitted_uses=uses))
    return out
