"""Inspection helpers for engineering input packages (debugging and review; no engineering).

``perimeter_rows`` turns a space's classified boundary into ordered rows
(segment -> classification -> geometry -> provenance) that a person can read or diff.
"""

from __future__ import annotations

from fireai.contract.engineering_input import ContractSpace, EngineeringInput


def perimeter_rows(space: ContractSpace, package: EngineeringInput | None = None, ndigits: int = 3) -> list[dict]:
    openings = {o.uid: o for o in (package.openings if package else [])}
    rows = []
    for ring in space.boundary.rings:
        for s in ring.segments:
            o = openings.get(s.opening_uid) if s.opening_uid else None
            rows.append({
                "ring": ring.role, "index": s.index, "kind": s.kind, "encloses": s.encloses,
                "length_ft": round(s.length_ft, ndigits),
                "start_local_ft": [round(c, ndigits) for c in s.start_local_ft],
                "end_local_ft": [round(c, ndigits) for c in s.end_local_ft],
                "rules": s.rules, "confidence": s.confidence, "requires_verification": s.requires_verification,
                "opening": ({"uid": o.uid, "kind": o.kind, "width_ft": round(o.width_ft, ndigits),
                             "fill_category": o.fill_category, "spaces": len(o.space_uids)} if o else
                            ({"uid": s.opening_uid} if s.opening_uid else None)),
                "derived_from": s.derived_from, "uid": s.uid,
            })
    return rows


def cyclic_kinds(space: ContractSpace) -> list[str]:
    """Kinds around the outer ring with consecutive repeats collapsed, rotated to a canonical start
    (for comparing two drafts of the same room, e.g. imperial vs metric)."""
    seq = [s.kind for r in space.boundary.rings if r.role == "outer" for s in r.segments]
    col = [k for i, k in enumerate(seq) if i == 0 or k != seq[i - 1]]
    if len(col) > 1 and col[0] == col[-1]:
        col = col[:-1]
    if not col:
        return []
    rots = [col[i:] + col[:i] for i in range(len(col))]
    return min(rots)
