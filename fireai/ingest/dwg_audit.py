"""Handle-level DWG -> DXF conversion-loss audit (Milestone 1.6).

The DWG side is read by LibreDWG's ``dwgread -O JSON`` (an independent parse of
the source) and STREAMED line by line: the JSON can be hundreds of MB, and only
a compact record per object is kept. The DXF side is the document the pipeline
already loaded (no second parse). Objects are matched by DWG handle, which
LibreDWG preserves in its DXF output, so a loss of one type cannot be masked by
a gain of the same type elsewhere.

Checked: entity handles and types; per-type counts; block definitions and the
blocks actually referenced; layers; text content; basic geometry fingerprints
(LINE, CIRCLE, ARC, POINT, TEXT/MTEXT insert, INSERT insert, LWPOLYLINE vertices);
drawing extents. Every difference is classified:

* material  plan geometry, text, dimensions or references lost/changed where it
            is drawn (model space, a layout, or a block that is inserted)
* review    content FireAI does not interpret but a person may need (tables,
            proxies, images, 3D, unknown types), or lost layers/blocks
* minor     masking/cosmetic (WIPEOUT) or content only in UNREFERENCED block
            definitions
Material loss is never accepted silently (see pipeline review triggers and the
engineering gate).
"""

from __future__ import annotations

import json
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path

STRUCTURAL = {"BLOCK", "ENDBLK", "SEQEND", "VERTEX_2D", "VERTEX_3D", "VERTEX_MESH", "VERTEX_PFACE",
              "VERTEX_PFACE_FACE"}
DWG_TO_DXF = {"POLYLINE_2D": "POLYLINE", "POLYLINE_3D": "POLYLINE", "POLYLINE_PFACE": "POLYLINE",
              "POLYLINE_MESH": "POLYLINE", "_3DFACE": "3DFACE", "_3DSOLID": "3DSOLID",
              "PROXY_ENTITY": "ACAD_PROXY_ENTITY", "TABLE": "ACAD_TABLE", "MINSERT": "INSERT"}
MATERIAL_TYPES = {"LINE", "LWPOLYLINE", "POLYLINE", "ARC", "CIRCLE", "ELLIPSE", "SPLINE", "INSERT", "HATCH", "SOLID",
                  "TRACE", "TEXT", "MTEXT", "ATTRIB", "DIMENSION", "MULTILEADER", "LEADER", "POINT", "MLINE"}
MINOR_TYPES = {"WIPEOUT"}
FP_KEYS = {"start", "end", "center", "radius", "ins_pt", "text_value", "points", "text"}
GEOM_TOL = 1e-6


def normalize_type(name: str | None) -> str | None:
    if not name or name in STRUCTURAL:
        return None
    if name.startswith("DIMENSION_"):
        return "DIMENSION"
    return DWG_TO_DXF.get(name, name)


@dataclass
class DwgCensus:
    entities: dict[int, dict] = field(default_factory=dict)        # handle -> {type, owner, space, fp}
    block_names: dict[int, str] = field(default_factory=dict)       # BLOCK_HEADER handle -> name
    layers: set[str] = field(default_factory=set)
    header: dict = field(default_factory=dict)
    counts: Counter = field(default_factory=Counter)
    classes: dict[int, str] = field(default_factory=dict)          # class number -> DXF name
    bytes_read: int = 0


def _val(raw: str):
    raw = raw.strip()
    if raw.endswith(","):
        raw = raw[:-1].rstrip()
    try:
        return json.loads(raw)
    except ValueError:
        return None


def stream_dwgread_json(path: Path) -> DwgCensus:
    """Line-streaming reader for LibreDWG's pretty-printed JSON (one key per line).
    Keeps memory proportional to the number of objects, not the file size."""
    c = DwgCensus()
    section = None
    obj: dict | None = None
    pending_key, pending = None, []
    cls: dict = {}
    with open(path, encoding="utf-8", errors="replace") as fh:
        for line in fh:
            c.bytes_read += len(line)
            if line.startswith('  "') and line.rstrip().endswith(("{", "[")):
                section = line.strip().split('"')[1]
                continue
            if section == "HEADER" and line.startswith('    "'):
                k, _, v = line.strip().partition(":")
                k = k.strip('"')
                if k in ("EXTMIN", "EXTMAX", "INSUNITS", "FINGERPRINTGUID"):
                    c.header[k] = _val(v)
                continue
            if section == "CLASSES":
                if line.startswith("    {"):
                    cls = {}
                elif line.startswith('      "number"') or line.startswith('      "dxfname"'):
                    k, _, v = line.strip().partition(":")
                    cls[k.strip('"')] = _val(v)
                elif line.startswith("    }") and isinstance(cls.get("number"), int) and cls.get("dxfname"):
                    c.classes[cls["number"]] = cls["dxfname"]
                continue
            if section != "OBJECTS":
                continue
            if line.startswith("    {"):
                obj = {}
                continue
            if line.startswith("    }"):
                if obj is not None:
                    _finish(c, obj)
                obj = None
                continue
            if obj is None:
                continue
            if pending_key is not None:
                pending.append(line)
                if line.startswith("      ]"):
                    obj[pending_key] = _val("".join(pending))
                    pending_key, pending = None, []
                continue
            if not line.startswith('      "'):
                continue                                  # nested content of a key we do not keep
            k, _, v = line.strip().partition(":")
            k = k.strip('"')
            v = v.strip()
            if k in ("entity", "object", "handle", "ownerhandle", "entmode", "name", "layer", "type") or k in FP_KEYS:
                if v == "[":
                    if k == "points":
                        pending_key, pending = k, ["["]
                    continue
                obj[k] = _val(v)
    return c


def _ref(v):
    return v[-1] if isinstance(v, list) and v else None


def _finish(c: DwgCensus, o: dict) -> None:
    h = _ref(o.get("handle"))
    if h is None:
        return
    if "object" in o:
        if o["object"] == "BLOCK_HEADER" and isinstance(o.get("name"), str):
            c.block_names[h] = o["name"]
        elif o["object"] == "LAYER" and isinstance(o.get("name"), str):
            c.layers.add(o["name"])
        return
    name = o.get("entity")
    if name in ("UNKNOWN_ENT", "PROXY_ENTITY") and isinstance(o.get("type"), int) and o["type"] in c.classes:
        name = c.classes[o["type"]]          # LibreDWG could not decode it; the class table names it
    t = normalize_type(name)
    if t is None:
        return
    mode = o.get("entmode")
    space = {2: "model", 1: "paper"}.get(mode, "block")
    fp = {k: o[k] for k in FP_KEYS if k in o and o[k] is not None}
    if t == "MTEXT" and "text" in fp:
        fp["text_value"] = fp.pop("text")
    c.entities[h] = {"type": t, "owner": _ref(o.get("ownerhandle")) if space == "block" else None, "space": space,
                     "fp": fp}
    c.counts[t] += 1


@dataclass
class DxfCensus:
    entities: dict[int, dict] = field(default_factory=dict)
    block_names: set[str] = field(default_factory=set)
    referenced_blocks: set[str] = field(default_factory=set)
    layers: set[str] = field(default_factory=set)
    header: dict = field(default_factory=dict)
    counts: Counter = field(default_factory=Counter)


def _dxf_fp(e) -> dict:
    t = e.dxftype()
    d = e.dxf
    try:
        if t == "LINE":
            return {"start": list(d.start), "end": list(d.end)}
        if t in ("CIRCLE", "ARC"):
            return {"center": list(d.center), "radius": d.radius}
        if t in ("TEXT", "MTEXT", "ATTRIB"):
            txt = e.text if t == "MTEXT" else d.text
            return {"ins_pt": list(d.insert), "text_value": txt}
        if t == "INSERT":
            return {"ins_pt": list(d.insert)}
        if t == "LWPOLYLINE":
            return {"points": [list(p[:2]) for p in e.get_points("xy")]}
    except Exception:
        return {}
    return {}


def dxf_census(doc) -> DxfCensus:
    c = DxfCensus()
    c.layers = {layer.dxf.name for layer in doc.layers}
    for k in ("$EXTMIN", "$EXTMAX", "$INSUNITS"):
        if k in doc.header:
            v = doc.header.get(k)
            c.header[k[1:]] = list(v) if hasattr(v, "__iter__") else v
    for block in doc.blocks:
        name = block.name
        c.block_names.add(name)
        space = ("model" if block.block_record.is_modelspace else
                 "paper" if block.block_record.is_any_paperspace else "block")
        for e in block:
            t = e.dxftype()
            if t == "INSERT":
                c.referenced_blocks.add(e.dxf.name)
                for a in e.attribs:
                    _add_dxf(c, a, space, name)
            _add_dxf(c, e, space, name)
    return c


def _add_dxf(c: DxfCensus, e, space, block_name) -> None:
    h = e.dxf.get("handle")
    t = e.dxftype()
    c.counts[t] += 1
    if not h:
        return
    try:
        hv = int(h, 16)
    except ValueError:
        return
    c.entities[hv] = {"type": t, "space": space, "block": block_name, "fp": _dxf_fp(e)}


def _close(a, b) -> bool:
    if isinstance(a, (int, float)) and isinstance(b, (int, float)):
        return abs(a - b) <= GEOM_TOL * max(1.0, abs(a), abs(b))
    if isinstance(a, list) and isinstance(b, list):
        n = min(len(a), len(b))
        if isinstance(a[0] if a else None, list) or isinstance(b[0] if b else None, list):
            return len(a) == len(b) and all(_close(x, y) for x, y in zip(a, b, strict=True))
        return all(_close(x, y) for x, y in zip(a[:n], b[:n], strict=True))
    return a == b


def _compare_fp(dwg: dict, dxf: dict) -> list[str]:
    bad = []
    for k in ("start", "end", "center", "radius", "ins_pt", "points"):
        if k in dwg and k in dxf and not _close(dwg[k], dxf[k]):
            bad.append(k)
    if "text_value" in dwg and "text_value" in dxf and isinstance(dwg["text_value"], str):
        a = " ".join(dwg["text_value"].split())
        b = " ".join(str(dxf["text_value"]).split())
        if a != b:
            bad.append("text_value")
    return bad


def compare(dwg: DwgCensus, dxf: DxfCensus, method: str) -> dict:
    referenced = dxf.referenced_blocks
    lost_by: dict[str, dict[str, list]] = defaultdict(lambda: defaultdict(list))   # class -> type -> handles
    type_changed, geom_bad, text_bad = [], [], []
    for h, d in dwg.entities.items():
        x = dxf.entities.get(h)
        where = d["space"]
        owner_name = dwg.block_names.get(d["owner"]) if d["owner"] is not None else None
        in_use = where in ("model", "paper") or (owner_name is not None and owner_name in referenced)
        if x is None:
            t = d["type"]
            cls = ("minor" if t in MINOR_TYPES or not in_use else "material" if t in MATERIAL_TYPES else "review")
            lost_by[cls][t].append(format(h, "X"))
            continue
        if x["type"] != d["type"] and not (d["type"] == "POLYLINE" and x["type"] in ("LWPOLYLINE", "POLYLINE")):
            type_changed.append({"handle": format(h, "X"), "dwg": d["type"], "dxf": x["type"]})
            continue
        bad = _compare_fp(d["fp"], x["fp"])
        if bad:
            rec = {"handle": format(h, "X"), "type": d["type"], "fields": bad}
            (text_bad if bad == ["text_value"] else geom_bad).append(rec)
    added = Counter(x["type"] for h, x in dxf.entities.items() if h not in dwg.entities)
    lost_layers = sorted(dwg.layers - dxf.layers)
    dwg_blocks = set(dwg.block_names.values())
    lost_blocks = sorted(b for b in dwg_blocks - dxf.block_names if not b.startswith("*"))
    ext = {}
    for k in ("EXTMIN", "EXTMAX"):
        a, b = dwg.header.get(k), dxf.header.get(k)
        if isinstance(a, list) and isinstance(b, list) and abs(a[0]) < 1e19:
            ext[k] = {"dwg": a, "dxf": b, "agrees": _close(a[:2], b[:2])}
    significance = "none"
    if lost_by.get("minor") or ext and not all(v["agrees"] for v in ext.values()):
        significance = "minor"
    if lost_by.get("review") or lost_layers or lost_blocks or type_changed or text_bad:
        significance = "review"
    if lost_by.get("material") or geom_bad:
        significance = "material"
    lost_counts = {t: len(hs) for cls in lost_by.values() for t, hs in cls.items()}
    return {
        "status": "ok", "method": method, "level": "handle",
        "source_counts": dict(dwg.counts), "output_counts": dict(dxf.counts),
        "lost": dict(sorted(lost_counts.items())),
        "lost_by_significance": {cls: {t: {"count": len(hs), "handles_sample": hs[:20]} for t, hs in sorted(v.items())}
                                 for cls, v in lost_by.items()},
        "added_in_dxf": dict(added),
        "type_changed": type_changed[:50], "type_changed_count": len(type_changed),
        "geometry_mismatch": geom_bad[:50], "geometry_mismatch_count": len(geom_bad),
        "text_mismatch": text_bad[:50], "text_mismatch_count": len(text_bad),
        "layers": {"dwg": len(dwg.layers), "dxf": len(dxf.layers), "lost": lost_layers},
        "blocks": {"dwg": len(dwg_blocks), "dxf": len(dxf.block_names), "lost": lost_blocks},
        "extents": ext,
        "significance": significance,
        "note": {"none": "no losses or differences detected",
                 "minor": "only cosmetic/unreferenced differences",
                 "review": "content FireAI does not interpret (or tables/layers/blocks) was lost or changed",
                 "material": "plan geometry, text, dimensions or references were lost or changed in use"}[significance],
    }


def dwgread_census(dwgread: Path, dwg_path: Path, work_dir: Path, timeout_s: int, run_cmd) -> tuple[DwgCensus | None, str]:
    out = work_dir / "dwgread.json"
    proc = run_cmd([str(dwgread), "-O", "JSON", "-o", str(out), str(dwg_path)], timeout_s)
    if not out.is_file():
        return None, f"dwgread exited {proc.returncode} without output"
    try:
        census = stream_dwgread_json(out)
    finally:
        out.unlink(missing_ok=True)       # can be hundreds of MB
    return census, ""

