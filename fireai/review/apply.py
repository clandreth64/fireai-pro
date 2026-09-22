"""Apply stored human corrections to a freshly produced model.

Corrections are applied deterministically on every run of the same source.
Machine output is never overwritten silently: a replaced or rejected machine
element stays in the model with provenance.review.status = "rejected", and
each application is listed in ``model.human_corrections_applied``. A
correction that cannot be applied (context changed, target gone) is reported
as a review trigger — never dropped quietly.
"""

from __future__ import annotations

import hashlib
import json

from shapely.geometry import Polygon

from fireai.ingest.extract import uid_for
from fireai.interpret.regions import apply_view_type_overrides
from fireai.model import BuildingElement, Geometry, Issue, Provenance, ReviewState


def corrections_digest(applied: list[dict]) -> str | None:
    if not applied:
        return None
    ids = sorted(a["correction_id"] for a in applied if a["status"] == "applied")
    return hashlib.sha256(json.dumps(ids).encode()).hexdigest() if ids else None


def _context_ok(c: dict, xref_shas: list[str]) -> bool:
    return sorted(c.get("context", {}).get("xref_sha256") or []) == sorted(xref_shas)


def apply_view_type_corrections(regions: list[dict], corrections: list[dict], xref_shas: list[str]) -> list[dict]:
    """Before interpretation (room logic depends on view type)."""
    out = []
    todo = {}
    for c in corrections:
        if c["kind"] != "view_type":
            continue
        if not _context_ok(c, xref_shas):
            out.append({"correction_id": c["id"], "kind": c["kind"], "status": "not_applied",
                        "reason": "XREF context changed since the correction was made"})
            continue
        todo[c["data"]["region_uid"]] = c    # later corrections of the same region win
    missing = set(apply_view_type_overrides(regions, {u: c["data"]["view_type"] for u, c in todo.items()}))
    for u, c in todo.items():
        if u in missing:
            out.append({"correction_id": c["id"], "kind": c["kind"], "status": "not_applied",
                        "reason": "region no longer exists (its member entities changed)"})
        else:
            out.append({"correction_id": c["id"], "kind": c["kind"], "status": "applied", "target": u,
                        "reviewer": c["reviewer"]})
            r = next(r for r in regions if r.get("uid") == u)
            r["view_type_evidence"] = list(r.get("view_type_evidence", [])) + [
                f"view type set to {c['data']['view_type']} by {c['reviewer']} (correction {c['id']}); "
                f"FireAI proposed {r.get('machine_view_type')}"]
    return out


def apply_element_corrections(model, corrections: list[dict], xref_shas: list[str]) -> list[dict]:
    """After interpretation and room analysis."""
    out = []
    by_uid = {e.uid: e for e in model.elements if e.uid}
    t = model.transform
    n = 0
    for c in corrections:
        kind, data = c["kind"], c["data"]
        if kind == "view_type":
            continue
        if not _context_ok(c, xref_shas):
            out.append({"correction_id": c["id"], "kind": kind, "status": "not_applied",
                        "reason": "XREF context changed since the correction was made"})
            continue
        review = ReviewState(status="rejected", by=c["reviewer"], at=c["created_at"],
                             note=f"correction {c['id']}" + (f": {c['note']}" if c.get("note") else ""))
        if kind in ("element_reject", "element_confirm"):
            el = by_uid.get(data["element_uid"])
            if el is None:
                out.append({"correction_id": c["id"], "kind": kind, "status": "not_applied",
                            "reason": "target element no longer produced (interpretation changed)"})
                continue
            el.provenance.review = review if kind == "element_reject" else review.model_copy(
                update={"status": "confirmed"})
            out.append({"correction_id": c["id"], "kind": kind, "status": "applied", "target": el.uid,
                        "reviewer": c["reviewer"]})
            continue
        # room_boundary: polygon in SRC frame -> LOCAL feet
        pts_local = [((x - t.origin[0]) * t.scale, (y - t.origin[1]) * t.scale) for x, y in data["polygon_src"]]
        poly = Polygon(pts_local)
        if not poly.is_valid or poly.area <= 0:
            out.append({"correction_id": c["id"], "kind": kind, "status": "not_applied",
                        "reason": "polygon is not a valid simple polygon"})
            continue
        rep = data.get("replaces_element_uid")
        replaced = by_uid.get(rep) if rep else None
        if rep and replaced is None:
            out.append({"correction_id": c["id"], "kind": kind, "status": "not_applied",
                        "reason": "the machine room it replaces is no longer produced; re-check the boundary"})
            continue
        if replaced is not None:
            replaced.provenance.review = review
        n += 1
        label = data.get("label")
        human = BuildingElement(
            id=f"RH{n:04d}", uid=uid_for(model.source.source_uid, f"human|{c['id']}"), category="room",
            subtype="human_boundary", label=label, confidence=1.0,
            evidence=[f"room boundary drawn by {c['reviewer']} (correction {c['id']}, {c['created_at']})"],
            rules=["H-ROOM-BOUNDARY"], source_entity_ids=[], requires_verification=False,
            geometry=Geometry(kind="polygon", points=pts_local, closed=True, frame="LOCAL"),
            properties={"area_sf": round(poly.area, 2), "perimeter_ft": round(poly.length, 2), "name": label,
                        "polygon_src": data["polygon_src"], "replaces_element_uid": rep},
            provenance=Provenance(origin="human", engine="human_review", rule_ids=["H-ROOM-BOUNDARY"],
                                  derived_from=[rep] if rep else [],
                                  review=ReviewState(status="confirmed", by=c["reviewer"], at=c["created_at"])),
        )
        model.elements.append(human)
        out.append({"correction_id": c["id"], "kind": kind, "status": "applied", "target": human.uid,
                    "replaced": rep, "reviewer": c["reviewer"]})
    return out


def correction_issues(applied: list[dict]) -> list[Issue]:
    bad = [a for a in applied if a["status"] != "applied"]
    if not bad:
        return []
    return [Issue(code="HUMAN_CORRECTIONS_NOT_APPLIED", severity="error",
                  message="Stored human corrections could not be applied to this run: "
                          + "; ".join(f"{a['correction_id']} ({a['kind']}): {a['reason']}" for a in bad)
                          + ". Review them against the current drawing.")]
