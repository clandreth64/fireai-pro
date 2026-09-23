"""FireAI's MACHINE values per review category, and per-category comparison with HUMAN truth.

Kept separate from gt.py on purpose: machine output is only ever the thing being evaluated,
never the reference. Comparison uses only human truth (CONFIRMED or CORRECTED items); Claude's
draft is never used as a reference unless a person confirmed it.

There is deliberately no overall "accuracy score": results are reported per category, with
"not_comparable" wherever a fair automatic comparison is not possible.
"""

from __future__ import annotations

import json
import sys
from collections import Counter
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
OUTPUTS = REPO / "tests" / "real_drawings_outputs_local"
EXTENT_REL_TOL = 0.01
AREA_REL_TOL = 0.02


def load_model_dict(path: Path) -> dict:
    sys.path.insert(0, str(REPO))
    from fireai.schema import read_model_dict      # restores defaults of persisted entities
    return read_model_dict(path)


def latest_models(gid: str, outputs: Path = OUTPUTS) -> dict[str, Path]:
    """edition (e.g. 'dwg', 'dxf') -> newest building_model.json for this drawing id."""
    out: dict[str, Path] = {}
    for p in sorted(outputs.glob(f"*/{gid}_*/building_model.json"), key=lambda p: p.stat().st_mtime):
        out[p.parent.name.split("_")[-1]] = p
    return out


def machine_values(m: dict) -> dict:
    els = m.get("elements", [])
    by = Counter(e["category"] for e in els)
    rooms = [e for e in els if e["category"] == "room"]
    sig = [r for r in m.get("view_regions", []) if r.get("significant")]
    b = m.get("bounds_normalized")
    tb = (m.get("title_block") or {}).get("fields") or {}
    return {
        "units": m["units"].get("resolved_units") or "undeclared_or_unknown",
        "drawing_type": None,
        "view_count": len(sig),
        "view_types": dict(Counter(r.get("view_type", "UNKNOWN") for r in sig)),
        "extents": ({"width_ft": b["max"][0] - b["min"][0], "height_ft": b["max"][1] - b["min"][1]} if b else None),
        "room_count": len(rooms),
        "room_names": sorted(r["label"] for r in rooms if r.get("label")),
        "room_areas": [{"name": r["label"], "value": r["properties"].get("area_sf")} for r in rooms if r.get("label")],
        "room_boundaries": None,
        "walls": {"count": by.get("wall", 0)},
        "doors": {"count": by.get("door", 0)},
        "windows": {"count": by.get("window", 0)},
        "columns": {"count": by.get("column", 0)},
        "stairs": {"count": by.get("stair", 0)},
        "grids": {"count": by.get("grid_line", 0)},
        # FireAI does not yet separate fire alarm from sprinkler content (known limitation)
        "sprinkler_components": {"count": by.get("existing_fire_protection", 0)},
        "fire_alarm_components": None,
        "title_block": {k: v.get("value") for k, v in tb.items() if not k.startswith("_") and isinstance(v, dict)},
        "other": None,
    }


def _close(a, b, tol):
    return abs(a - b) <= tol * max(abs(a), abs(b), 1e-9)


def compare(cat: str, machine, truth: dict | None) -> str:
    """agree | disagree | not_comparable | no_human_truth"""
    if not truth:
        return "no_human_truth"
    t = truth.get("comparable")
    if t is None or machine is None:
        return "not_comparable"
    if cat in ("units", "view_count", "room_count"):
        return "agree" if machine == t else "disagree"
    if cat == "view_types":
        return "agree" if machine == t else "disagree"
    if cat == "extents":
        return "agree" if (_close(machine["width_ft"], t["width_ft"], EXTENT_REL_TOL)
                           and _close(machine["height_ft"], t["height_ft"], EXTENT_REL_TOL)) else "disagree"
    if cat == "room_names":
        norm = lambda xs: Counter(" ".join(x.upper().split()) for x in xs)  # noqa: E731
        return "agree" if norm(machine) == norm(t) else "disagree"
    if cat == "room_areas":
        mm = {" ".join(r["name"].upper().split()): r["value"] for r in machine if r["value"] is not None}
        for r in t:
            v = mm.get(" ".join(r["name"].upper().split()))
            if v is None or not _close(v, r["value"], AREA_REL_TOL):
                return "disagree"
        return "agree"
    if isinstance(t, dict) and "count" in t:
        if t["count"] is None or machine.get("count") is None:
            return "not_comparable"
        return "agree" if machine["count"] == t["count"] else "disagree"
    if cat == "title_block":
        return "agree" if all(str(machine.get(k, "")).strip() == str(v).strip() for k, v in t.items()) else "disagree"
    return "not_comparable"


def json_default(o):
    return str(o)


if __name__ == "__main__":        # debug helper: print machine values for a model file
    print(json.dumps(machine_values(load_model_dict(Path(sys.argv[1]))), indent=2, default=json_default))
