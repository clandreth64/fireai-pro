"""Engineering gate. Future engineering stages MUST call ``require_verified_model``
before using a building model; nothing in this milestone performs engineering."""

from __future__ import annotations

from fireai.review.store import ReviewStore, verification_state

ENGINEERING_VIEW_TYPES = {"FLOOR_PLAN", "REFLECTED_CEILING_PLAN"}
_ABSENT_XREF = {"missing", "load_failed", "conversion_unavailable", "too_deep", "unresolved", "not_attempted",
                "circular", "units_unresolved"}


class ModelNotVerified(Exception):
    def __init__(self, readiness: dict):
        super().__init__("; ".join(readiness["blockers"]))
        self.readiness = readiness


def engineering_readiness(model, store: ReviewStore | None) -> dict:
    state = verification_state(model, store)
    blockers: list[str] = []
    if state["status"] != "HUMAN_VERIFIED":
        blockers.append(f"model is {state['status']}" + (f" ({'; '.join(state['reasons'])})" if state["reasons"] else ""))
    if not model.units.resolved:
        blockers.append("units unresolved")
    audits = [("source DWG", model.source.conversion_audit)] + [
        (f"XREF {x.name}", {"significance": x.conversion_significance})
        for x in model.xrefs if x.status == "resolved" and x.conversion_significance]
    for what, a in audits:
        if a and (a.get("significance") == "material" or ("significance" not in a and a.get("lost"))):
            blockers.append(f"{what}: DWG conversion lost or changed plan content (material loss)")
    absent = [x.name for x in model.xrefs if x.status in _ABSENT_XREF]
    if absent:
        blockers.append(f"external references not loaded: {absent}")
    selected = state.get("selected_region_uids") or []
    by_uid = {r.get("uid"): r for r in model.view_regions}
    for u in selected:
        r = by_uid.get(u)
        if r is None:
            blockers.append(f"selected region {u} not in model")
        elif r.get("view_type") not in ENGINEERING_VIEW_TYPES:
            blockers.append(f"selected region {r['id']} is {r.get('view_type')}, not a plan view")
    return {"ready": not blockers, "verification": state, "blockers": blockers,
            "note": "Readiness to START engineering on a verified model. No engineering is performed by FireAI in "
                    "this milestone."}


def require_verified_model(model, store: ReviewStore | None) -> dict:
    r = engineering_readiness(model, store)
    if not r["ready"]:
        raise ModelNotVerified(r)
    return r
