"""Semantic equivalence of two drawings of the same architecture (e.g. imperial vs metric CAD).

Compares ENGINEERING MEANING, not raw CAD: view types, physical-region topology, semantic spaces
(known vs unresolved), openings / stairs / columns per view, room connectivity through openings,
classified region boundaries (the cyclic order of wall / window / door / open / unknown along each
uniquely named region, and which regions are incomplete), plan openings by kind, engineering
blockers, and normalized geometry (feet) within a tolerance. Geometry that legitimately
differs between two drafts (wall thickness, drafting technique) is compared with tolerances or not
at all; entity counts, layers, handles and block names are never compared.

Used by ``tests/test_unit_system_equivalence.py``: on generated drawings in every supported unit
(always), and on the real pair declared in ``equivalence_pairs.json`` (when the local corpus exists).
No production code depends on this module.
"""

from __future__ import annotations

import tempfile
from collections import Counter
from pathlib import Path

UNNAMED = "<unnamed>"
OUTSIDE = "<outside>"
FULL_CHECKLIST = {c: {"status": "CONFIRMED"}
                  for c in ("units", "drawing_type", "view_regions", "extents", "walls", "rooms")}
# Default tolerances for normalized geometry (feet). Two drafts of one building differ in wall
# thickness, so areas/lengths are compared relatively; a unit error is orders of magnitude.
TOLERANCES = {"extent_rel": 0.03, "area_rel": 0.08, "wall_length_rel": 0.10}


def _plan_views(model) -> list[dict]:
    regs = [r for r in model.view_regions if r.get("significant")]
    return sorted(regs, key=lambda r: (round(r["bbox_ft"][0]), round(r["bbox_ft"][1])))


def _region_key(room, spaces_by_region) -> str:
    names = sorted(s.label for s in spaces_by_region.get(room.uid, []) if s.label)
    return "|".join(names) if names else UNNAMED


def semantic_signature(model) -> dict:
    """Unit-independent description of what FireAI understood (LOCAL feet, names, states, counts)."""
    spaces = [s for s in model.elements_of("space")]
    by_region: dict[str, list] = {}
    for s in spaces:
        by_region.setdefault(s.properties.get("region_uid"), []).append(s)
    rooms = model.elements_of("room")
    key_of = {r.id: _region_key(r, by_region) for r in rooms}
    views = []
    for v in _plan_views(model):
        vid = v["id"]
        in_v = [e for e in model.elements if e.properties.get("view_region") == vid]
        rooms_v = [r for r in rooms if r.properties.get("view_region") == vid]
        spaces_v = [s for s in spaces if s.properties.get("view_region") == vid
                    or any(s.properties.get("region_uid") == r.uid for r in rooms_v)]
        keys = Counter(key_of[r.id] for r in rooms_v)
        cats = Counter(e.category for e in in_v)
        links = Counter()
        for c in (model.wall_model or {}).get("room_connections", []):
            ids = [x for x in c.get("rooms", []) if x in key_of]
            if not ids or not any(r.id == ids[0] for r in rooms_v):
                continue
            pair = sorted({key_of[x] for x in ids}) if not c.get("opening_inside_one_room") else [key_of[ids[0]]] * 2
            if len(pair) == 1:
                pair = [pair[0], OUTSIDE]
            links[f"{c.get('kind')}:{pair[0]}~{pair[1]}"] += 1
        walls_v = [w for w in (model.wall_model or {}).get("walls", []) if w.get("region") == vid]
        x0, y0, x1, y1 = v["bbox_ft"]
        views.append({
            "view_type": v.get("view_type"),
            "regions": sorted(keys.elements()),
            "merged_regions": sorted(key_of[r.id] for r in rooms_v if r.subtype == "suspected_merged_region"),
            "known_spaces": sorted(s.label for s in spaces_v if s.properties.get("boundary_state") == "known"),
            "unresolved_spaces": sorted(s.label for s in spaces_v if s.properties.get("boundary_state") != "known"),
            "counts": {k: cats.get(k, 0) for k in ("door", "window", "stair", "column", "depiction")},
            # which spaces connect through openings (a set: one door can yield several overlapping
            # wall-gap openings, a known wall-analysis limitation; multiplicity is not topology)
            "connections": sorted(links),
            # geometry (feet): compared with tolerances
            "extent_ft": [round(x1 - x0, 3), round(y1 - y0, 3)],
            "area_sf": {k: round(r.properties["area_sf"], 2) for r in rooms_v
                        if keys[(k := key_of[r.id])] == 1 and k != UNNAMED},
            "wall_length_ft": round(sum(w.get("length_ft") or 0.0 for w in walls_v), 2),
            # M1.9: classified boundaries and plan openings
            "boundary_kinds": sorted(f"{key_of[r.id]}: {' > '.join(_cyclic(r))}" for r in rooms_v
                                     if r.boundary and keys[key_of[r.id]] == 1 and key_of[r.id] != UNNAMED),
            "incomplete_boundaries": sorted(key_of[r.id] for r in rooms_v if r.boundary and not r.boundary.complete),
            "unclassified_regions": sorted(key_of[r.id] for r in rooms_v if r.boundary is None),
            "openings": sorted(f"{o.subtype}" for o in model.elements_of("opening")
                               if o.properties.get("view_region") == vid),
        })
    return {"units_resolved": bool(model.units.resolved), "view_count": len(views), "views": views}


def _cyclic(room) -> list[str]:
    seq = [s.kind for ring in room.boundary.rings if ring.role == "outer" for s in ring.segments]
    col = [k for i, k in enumerate(seq) if i == 0 or k != seq[i - 1]]
    col = col[:-1] if len(col) > 1 and col[0] == col[-1] else col
    return min((col[i:] + col[:i] for i in range(len(col))), default=[])


def engineering_blockers_by_view(model) -> list[list[str]]:
    """For each plan view: the engineering-contract blockers if a person verified the model with ONLY
    that view selected. Uses the real gate and contract in a throw-away store (a simulation for
    comparison; it never touches a real review store). Ids are removed so drafts compare."""
    import re

    from fireai.contract import engineering_input_blockers
    from fireai.review.store import ReviewStore
    out = []
    for v in _plan_views(model):
        with tempfile.TemporaryDirectory() as d:
            store = ReviewStore(Path(d))
            store.record_verification(model, "simulation", "verify", FULL_CHECKLIST,
                                      sorted({t.code for t in model.diagnostics.review_triggers}), [v["uid"]])
            b = engineering_input_blockers(model, store)
        out.append(sorted(re.sub(r"\b[A-Z]{1,2}\d{3,}\b", "#", re.sub(r"\(\d+ sf\)", "", x)) for x in b))
    return out


def _multiset_diff(a: list, b: list) -> tuple[list, list]:
    ca, cb = Counter(a), Counter(b)
    return sorted((ca - cb).elements()), sorted((cb - ca).elements())


def compare(sig_a: dict, sig_b: dict, tol: dict | None = None) -> list[dict]:
    """Every semantic difference, as {view, field, a, b} (list fields: a_only / b_only)."""
    tol = {**TOLERANCES, **(tol or {})}
    diffs: list[dict] = []
    for f in ("units_resolved", "view_count"):   # views beyond the shorter list are covered by view_count
        if sig_a[f] != sig_b[f]:
            diffs.append({"view": None, "field": f, "a": sig_a[f], "b": sig_b[f]})
    for i, (va, vb) in enumerate(zip(sig_a["views"], sig_b["views"], strict=False)):
        for f in ("view_type",):
            if va[f] != vb[f]:
                diffs.append({"view": i, "field": f, "a": va[f], "b": vb[f]})
        for f in ("regions", "merged_regions", "known_spaces", "unresolved_spaces", "connections", "blockers",
                  "boundary_kinds", "incomplete_boundaries", "unclassified_regions", "openings"):
            if f not in va:
                continue
            a_only, b_only = _multiset_diff(va[f], vb[f])
            if a_only or b_only:
                diffs.append({"view": i, "field": f, "a_only": a_only, "b_only": b_only})
        for k in va["counts"]:
            if va["counts"][k] != vb["counts"][k]:
                diffs.append({"view": i, "field": f"counts.{k}", "a": va["counts"][k], "b": vb["counts"][k]})
        for j in range(2):
            ea, eb = va["extent_ft"][j], vb["extent_ft"][j]
            if abs(ea - eb) > tol["extent_rel"] * max(ea, eb, 1e-9):
                diffs.append({"view": i, "field": f"extent_ft[{j}]", "a": ea, "b": eb})
        for k in sorted(set(va["area_sf"]) & set(vb["area_sf"])):
            aa, ab = va["area_sf"][k], vb["area_sf"][k]
            if abs(aa - ab) > tol["area_rel"] * max(aa, ab):
                diffs.append({"view": i, "field": f"area_sf[{k}]", "a": aa, "b": ab})
        la, lb = va["wall_length_ft"], vb["wall_length_ft"]
        if abs(la - lb) > tol["wall_length_rel"] * max(la, lb, 1e-9):
            diffs.append({"view": i, "field": "wall_length_ft", "a": la, "b": lb})
    return diffs


def full_signature(model) -> dict:
    sig = semantic_signature(model)
    for v, b in zip(sig["views"], engineering_blockers_by_view(model), strict=True):
        v["blockers"] = b
    return sig


def undeclared(diffs: list[dict], declared: list[dict]) -> tuple[list[dict], list[dict]]:
    """(differences not explained by a declaration, declarations no longer observed).

    A declaration matches a difference exactly (same view, field and values), so it can never hide
    a different, new divergence in the same field; a declaration that stops matching is STALE and
    must be removed (e.g. after a fix)."""
    strip = lambda d: {k: v for k, v in d.items() if k in ("view", "field", "a", "b", "a_only", "b_only")}  # noqa: E731
    decl = [strip(d) for d in declared]
    new = [d for d in diffs if strip(d) not in decl]
    stale = [d for d, s in zip(declared, decl, strict=True) if s not in [strip(x) for x in diffs]]
    return new, stale
