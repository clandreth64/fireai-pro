"""Compare the FireAI models of the same drawing ingested as DWG (via converter) and as DXF.

Usage: python scripts/compare_dwg_dxf.py <dwg building_model.json> <dxf building_model.json> [--tol 1e-6]

Entities are matched by top-level DXF handle (plus handle_path for block contents when present).
Reports: type/layer agreement, geometry deviation (source units), entities present in only one
side, unit/bounds/element-count differences. Exit code 0 = equivalent within tolerance.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from collections import Counter
from pathlib import Path as _P

sys.path.insert(0, str(_P(__file__).resolve().parent.parent))


def _key(e):
    hp = e.get("handle_path")
    if hp:
        return "/".join(hp)
    return e.get("handle") or None


def _pts(g):
    if not g:
        return []
    pts = list(g.get("points") or [])
    for p in g.get("paths") or []:
        pts.extend(p)
    for k in ("insert", "center"):
        if g.get(k):
            pts.append(g[k])
    return pts


def compare(a: dict, b: dict, tol: float, curve_tol: float | None = None) -> dict:
    # Curves (splines/ellipses/bulges) are flattened at 0.2% of entity size; allow that much shape deviation.
    bs = a.get("bounds_source") or {}
    size = math.dist(bs.get("min", (0, 0)), bs.get("max", (0, 0))) if bs else 0.0
    curve_tol = curve_tol if curve_tol is not None else max(tol, 0.002 * size)
    max_curve_dev = [0.0]
    ea ={k: e for e in a["entities"] if e.get("parent_id") is None and (k := _key(e))}
    eb = {k: e for e in b["entities"] if e.get("parent_id") is None and (k := _key(e))}
    only_a, only_b = sorted(set(ea) - set(eb)), sorted(set(eb) - set(ea))
    type_diff, layer_diff, geom_diff, max_dev = [], [], [], 0.0
    for k in sorted(set(ea) & set(eb)):
        x, y = ea[k], eb[k]
        if x["type"] != y["type"]:
            type_diff.append((k, x["type"], y["type"]))
            continue
        if x["layer"] != y["layer"]:
            layer_diff.append((k, x["layer"], y["layer"]))
        px, py = _pts(x.get("source")), _pts(y.get("source"))
        if len(px) != len(py):
            # Same curve flattened differently (e.g. spline fit vs control points): compare shapes.
            if len(px) > 1 and len(py) > 1:
                from shapely.geometry import LineString
                dev = LineString(px).hausdorff_distance(LineString(py))
                max_curve_dev[0] = max(max_curve_dev[0], dev)
                if dev > curve_tol:
                    geom_diff.append((k, x["type"], f"point count {len(px)} vs {len(py)}, hausdorff {dev:.3g}"))
            else:
                geom_diff.append((k, x["type"], f"point count {len(px)} vs {len(py)}"))
            continue
        dev = max((math.dist(p, q) for p, q in zip(px, py, strict=True)), default=0.0)
        max_dev = max(max_dev, dev)
        if dev > tol:
            geom_diff.append((k, x["type"], f"max deviation {dev:.3g}"))
    ca = Counter(e["category"] for e in a.get("elements", []))
    cb = Counter(e["category"] for e in b.get("elements", []))
    res = {
        "units": (a["units"].get("resolved_units"), b["units"].get("resolved_units")),
        "top_level_matched": len(set(ea) & set(eb)), "only_in_dwg": len(only_a), "only_in_dxf": len(only_b),
        "only_in_dwg_types": dict(Counter(ea[k]["type"] for k in only_a)),
        "only_in_dxf_types": dict(Counter(eb[k]["type"] for k in only_b)),
        "type_mismatches": len(type_diff), "layer_mismatches": len(layer_diff),
        "geometry_mismatches": len(geom_diff), "max_deviation_src_units": max_dev, "tolerance": tol,
        "max_curve_shape_deviation_src_units": max_curve_dev[0], "curve_tolerance": curve_tol,
        "element_counts_dwg": dict(ca), "element_counts_dxf": dict(cb),
        "examples": {"type": type_diff[:5], "layer": layer_diff[:5], "geometry": geom_diff[:8]},
    }
    res["equivalent"] = (not only_a and not only_b and not type_diff and not layer_diff and not geom_diff
                         and res["units"][0] == res["units"][1] and ca == cb)
    return res


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("dwg_model"); ap.add_argument("dxf_model")
    ap.add_argument("--tol", type=float, default=1e-6)
    a = ap.parse_args()
    from fireai.schema import read_model_dict   # restores defaults omitted from persisted entities
    r = compare(read_model_dict(a.dwg_model), read_model_dict(a.dxf_model), a.tol)
    print(json.dumps(r, indent=2, default=str))
    sys.exit(0 if r["equivalent"] else 1)


if __name__ == "__main__":
    main()
