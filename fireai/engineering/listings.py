"""Durable, human-controlled sprinkler LISTING data (Milestone 2.1).

Real manufacturer / listing data enters FireAI only through this workflow — never from memory or a
"generic" sprinkler. Unknown listing facts stay unknown (None).

    create (DRAFT) → submit_for_review → review (different person; checks) → APPROVED (immutable)
    change → revise (new version, DRAFT, change reason) → … → approve (previous: SUPERSEDED)
    select_for_design: only APPROVED, authoritative listings

Approval requires: document id, revision, publication/effective date, a manufacturer-listing source
kind and access metadata; manufacturer, model, sprinkler type, orientation; units on every listed
quantity; every listing rule approvable (``fireai.rules.store.approval_problems``); a reviewer who is
not the author. Identities are free-text names — NOT PRODUCTION SAFE.
"""

from __future__ import annotations

import json
import re
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from fireai.engineering.inputs import SprinklerListing
from fireai.rules.model import Quantity
from fireai.rules.store import IDENTITY_ASSURANCE, approval_problems
from fireai.rules.units import UnitError, dimension

_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.\-]{0,127}$")
_lock = threading.Lock()
_NO_UNIT_DIMENSION = {"gpm/psi^0.5", "lpm/bar^0.5", "degF", "degC"}   # listed quantities without length/area dimension


class ListingError(ValueError):
    pass


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def listing_problems(lst: SprinklerListing) -> list[str]:
    p: list[str] = []
    d = lst.document
    if lst.content_basis != "authoritative" or d.kind != "manufacturer_listing_document":
        p.append("only authoritative manufacturer listing documents can be approved (synthetic is test-only)")
    for f, v in (("document id", d.document), ("document revision", d.revision),
                 ("publication / effective date", d.publication_date), ("access method", d.access_method),
                 ("accessed at", d.accessed_at)):
        if not (v or "").strip():
            p.append(f"{f} missing")
    for f in ("manufacturer", "model", "sprinkler_type", "orientation"):
        if not (getattr(lst, f) or "").strip():
            p.append(f"{f} missing")
    quantities = [("k_factor", lst.k_factor), ("temperature_rating", lst.temperature_rating)] + \
        [(q.name, q.value) for q in lst.parameters + lst.installation_constraints if isinstance(q.value, Quantity)]
    for name, q in quantities:
        if q is None:
            continue
        if not q.unit.strip():
            p.append(f"{name}: unit missing")
        elif q.unit not in _NO_UNIT_DIMENSION:
            try:
                dimension(q.unit)
            except UnitError as exc:
                p.append(f"{name}: {exc}")
    for r in lst.rules.rules:
        for issue in approval_problems(r, lst.rules):
            p.append(f"listing rule {r.rule_id}: {issue}")
    return p


class ListingStore:
    def __init__(self, root: Path):
        self.root = Path(root)

    def _path(self, lid: str, version: str) -> Path:
        if not (_ID.match(lid or "") and _ID.match(version or "")):
            raise ListingError("invalid listing id / version")
        return self.root / lid / version / "listing.json"

    def _write(self, lst: SprinklerListing) -> None:
        path = self._path(lst.listing_id, lst.version)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(lst.model_dump(mode="json"), indent=2), encoding="utf-8")
        tmp.replace(path)

    def _event(self, lid: str, version: str, action: str, by: str, **detail) -> None:
        d = self.root / lid
        d.mkdir(parents=True, exist_ok=True)
        with (d / "events.jsonl").open("a", encoding="utf-8") as fh:
            fh.write(json.dumps({"at": _now(), "listing_id": lid, "version": version, "action": action, "by": by,
                                 "identity_assurance": IDENTITY_ASSURANCE, **detail}, sort_keys=True) + "\n")

    def events(self, lid: str) -> list[dict]:
        p = self.root / lid / "events.jsonl"
        return [json.loads(x) for x in p.read_text(encoding="utf-8").splitlines()] if p.is_file() else []

    def get(self, lid: str, version: str) -> SprinklerListing:
        p = self._path(lid, version)
        if not p.is_file():
            raise ListingError(f"no listing {lid} v{version}")
        return SprinklerListing.model_validate_json(p.read_text(encoding="utf-8"))

    def versions(self, lid: str) -> list[str]:
        d = self.root / lid
        return sorted(x.name for x in d.iterdir() if (x / "listing.json").is_file()) if d.is_dir() else []

    @staticmethod
    def _who(name: str) -> str:
        if not (name or "").strip():
            raise ListingError("a named person is required")
        return name.strip()

    def create(self, listing: SprinklerListing, author: str) -> SprinklerListing:
        by = self._who(author)
        with _lock:
            if self._path(listing.listing_id, listing.version).exists():
                raise ListingError(f"{listing.listing_id} v{listing.version} already exists (never overwritten)")
            lst = listing.model_copy(update={"author": by, "authored_at": _now(), "review_status": "draft",
                                             "reviewer": None, "reviewed_at": None})
            self._write(lst)
            self._event(lst.listing_id, lst.version, "create", by, document=lst.document.document,
                        revision=lst.document.revision)
        return lst

    def submit_for_review(self, lid: str, version: str, by: str) -> SprinklerListing:
        who = self._who(by)
        with _lock:
            lst = self.get(lid, version)
            if lst.review_status != "draft":
                raise ListingError(f"{lid} v{version} is {lst.review_status}")
            lst = lst.model_copy(update={"review_status": "under_review"})
            self._write(lst)
            self._event(lid, version, "submit_for_review", who)
        return lst

    def review(self, lid: str, version: str, reviewer: str, decision: str, note: str = "") -> SprinklerListing:
        who = self._who(reviewer)
        with _lock:
            lst = self.get(lid, version)
            if lst.review_status != "under_review":
                raise ListingError(f"{lid} v{version} is {lst.review_status}: submit it for review first")
            if who == lst.author:
                raise ListingError("the reviewer must be a different person from the author (two-person review)")
            if decision == "approve":
                problems = listing_problems(lst)
                if problems:
                    self._event(lid, version, "review_blocked", who, problems=problems)
                    raise ListingError(f"{lid} v{version} cannot be approved: " + "; ".join(problems))
                now = _now()
                rules = lst.rules.model_copy(update={
                    "review_status": "approved", "reviewer": who, "reviewed_at": now,
                    "rules": [r.model_copy(update={"review_status": "approved", "reviewer": who, "reviewed_at": now})
                              for r in lst.rules.rules]})
                lst = lst.model_copy(update={"review_status": "approved", "reviewer": who, "reviewed_at": now,
                                             "rules": rules})
                self._write(lst)
                for v in self.versions(lid):
                    if v != version:
                        old = self.get(lid, v)
                        if old.review_status == "approved":
                            self._write(old.model_copy(update={"review_status": "superseded"}))
                            self._event(lid, v, "superseded", who, by_version=version)
                self._event(lid, version, "approve", who, digest=lst.digest(), note=note)
            elif decision == "reject":
                lst = lst.model_copy(update={"review_status": "draft"})
                self._write(lst)
                self._event(lid, version, "reject", who, note=note)
            else:
                raise ListingError("decision must be 'approve' or 'reject'")
        return lst

    def revise(self, lid: str, from_version: str, new_version: str, by: str, change_reason: str,
               **changes) -> SprinklerListing:
        who = self._who(by)
        if not change_reason.strip():
            raise ListingError("a change reason is required for a revision")
        with _lock:
            src = self.get(lid, from_version)
            if self._path(lid, new_version).exists():
                raise ListingError(f"{lid} v{new_version} already exists")
            lst = src.model_copy(update={**changes, "version": new_version, "review_status": "draft", "reviewer": None,
                                         "reviewed_at": None, "author": who, "authored_at": _now(),
                                         "change_reason": change_reason})
            self._write(lst)
            self._event(lid, new_version, "revise", who, from_version=from_version, change_reason=change_reason)
        return lst

    def select_for_design(self, lid: str, version: Optional[str] = None) -> SprinklerListing:
        """An APPROVED, authoritative listing (the latest approved one when ``version`` is None)."""
        versions = [version] if version else list(reversed(self.versions(lid)))
        for v in versions:
            lst = self.get(lid, v)
            if lst.review_status == "approved" and lst.content_basis == "authoritative":
                return lst
        raise ListingError(f"no approved listing {lid}" + (f" v{version}" if version else "")
                           + ": draft / superseded / synthetic listings cannot support real design")
