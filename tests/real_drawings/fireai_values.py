"""FireAI's MACHINE output for the review tool: fact values, the identity of the output being
judged, and simplified geometry for the clickable review canvas.

Machine output is only ever the thing being evaluated, never the reference. Fact comparison uses
human truth only (see gt.py); evaluations and visual flags are judgements about one FireAI output.
"""

from __future__ import annotations

import hashlib
import sys
from collections import Counter
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
OUTPUTS = REPO / "tests" / "real_drawings_outputs_local"
MAX_CANVAS_POINTS = 60_000


def load_model_dict(path: Path) -> dict:
    sys.path.insert(0, str(REPO))
    from fireai.schema import read_model_dict      # restores defaults of persisted entities
    return read_model_dict(path)


def model_sha(path: Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def latest_models(gid: str, outputs: Path = OUTPUTS) -> dict[str, Path]:
    """edition ('dwg' / 'dxf') -> newest building_model.json for this drawing id."""
    out: dict[str, Path] = {}
    for p in sorted(outputs.glob(f"*/{gid}_*/building_model.json"), key=lambda p: p.stat().st_mtime):
        out[p.parent.name.split("_")[-1]] = p
    return out


def preferred_model(gid: str, outputs: Path = OUTPUTS) -> tuple[str | None, Path | None]:
    models = latest_models(gid, outputs)
    for ed in ("dwg", "dxf"):
        if ed in models:
            return ed, models[ed]
    return None, None


def evaluated_model_ref(path: Path, outputs: Path = OUTPUTS) -> dict:
    path, outputs = Path(path).resolve(), Path(outputs).resolve()
    m = load_model_dict(path)
    rel = str(path.relative_to(outputs)) if path.is_relative_to(outputs) else path.name
    return {"output": rel, "model_sha256": model_sha(path),
            "engine_version": (m.get("verification") or {}).get("engine_version"),
            "model_id": m.get("model_id")}


def machine_facts(m: dict) -> dict:
    sig = [r for r in m.get("view_regions", []) if r.get("significant")]
    return {"units": m["units"].get("resolved_units") or "cannot_tell",
            "drawing_type": None,                         # FireAI does not state a drawing type
            "view_count": len(sig),
            "view_types": dict(Counter(r.get("view_type", "UNKNOWN") for r in sig))}


def compare_fact(q: str, machine, truth: dict | None) -> str:
    """agree | disagree | not_comparable | no_human_truth"""
    if not truth:
        return "no_human_truth"
    t = truth.get("comparable")
    if t is None or machine is None:
        return "not_comparable"
    return "agree" if machine == t else "disagree"


def _pts(g):
    if not g:
        return []
    if g.get("points"):
        return [list(p) for p in g["points"]]
    return []


def canvas(m: dict) -> dict:
    """Simplified, LOCAL-frame geometry for the review canvas: grey source linework, FireAI items
    (clickable, with uid), and text. Nothing here is written back to the model."""
    flagged = {i for t in m["diagnostics"]["review_triggers"] for i in t.get("element_ids", [])}
    budget = MAX_CANVAS_POINTS
    lines, texts = [], []
    for e in m["entities"]:
        if e["space"] != "model" or not e["visible"] or not e["supported"]:
            continue
        g = e.get("normalized")
        if not g:
            continue
        if g["kind"] == "text" and g.get("insert") and g.get("text") and not e.get("parent_id"):
            texts.append({"xy": g["insert"], "t": g["text"].split("\n")[0][:40], "h": g.get("height") or 0.5})
            continue
        p = _pts(g)
        if len(p) >= 2 and budget > 0:
            if g.get("closed"):
                p = p + [p[0]]
            lines.append(p)
            budget -= len(p)
    items = []
    for el in m["elements"]:
        if el["category"] in ("text_annotation", "dimension") or not el.get("geometry"):
            continue
        p = _pts(el["geometry"])
        if len(p) < 2:
            continue
        items.append({"uid": el.get("uid"), "id": el["id"], "category": el["category"], "subtype": el.get("subtype"),
                      "label": el.get("label"), "points": p, "closed": el["geometry"].get("closed", False),
                      "contains": el["properties"].get("semantic_space_names") if el["category"] == "room" else None,
                      "confident": not el["requires_verification"],
                      "flagged_by_fireai": el["requires_verification"] or el["id"] in flagged,
                      "region": el["properties"].get("view_region")})
    b = m.get("bounds_normalized") or {"min": [0, 0], "max": [1, 1]}
    return {"bounds": [b["min"][0], b["min"][1], b["max"][0], b["max"][1]], "lines": lines, "texts": texts[:3000],
            "items": items, "truncated": budget <= 0,
            "regions": [{"id": r["id"], "bbox": r["bbox_ft"], "view_type": r.get("view_type")}
                        for r in m.get("view_regions", []) if r.get("significant")]}
