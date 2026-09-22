"""Drawing-understanding report: machine-readable JSON + human-readable Markdown."""

from __future__ import annotations

from collections import Counter
from statistics import mean
from typing import Any

from fireai.model import BuildingModel

MAX_LIST = 200


def build_report(model: BuildingModel | None, processing_status: str, failure: dict | None,
                 stages: list[dict], unit_requirement: dict | None = None) -> dict[str, Any]:
    rep: dict[str, Any] = {
        "report_version": "0.1.0",
        "processing_status": processing_status,
        "engineering_review_status": "not_performed",
        "ready_for_design": False,
        "geometry_is_synthetic": False,
        "ai_inference_used": False,
        "failure": failure,
        "unit_resolution_required": unit_requirement,
        "stages": stages,
    }
    if model is None:
        rep["requires_human_review"] = True
        return rep
    types = Counter(e.type for e in model.entities if e.space == "model")
    top = [e for e in model.entities if e.parent_id is None]
    cats = Counter(e.category for e in model.elements)
    conf: dict[str, Any] = {}
    for cat in sorted(cats):
        els = model.elements_of(cat)
        conf[cat] = {"count": len(els), "mean_confidence": round(mean(e.confidence for e in els), 3),
                     "min_confidence": min(e.confidence for e in els),
                     "requiring_verification": sum(1 for e in els if e.requires_verification)}
    texts = [e for e in model.entities if e.source is not None and e.source.kind == "text" and e.visible]
    rep.update({
        "source": model.source.model_dump(),
        "units": model.units.model_dump(),
        "transform": model.transform.model_dump(),
        "bounds_source": model.bounds_source.as_dict() if model.bounds_source else None,
        "bounds_normalized_ft": model.bounds_normalized.as_dict() if model.bounds_normalized else None,
        "scale": model.scale.model_dump(),
        "entity_counts": {"total_including_block_contents": len(model.entities),
                          "top_level": len(top), "model_space_by_type": dict(types.most_common()),
                          "paper_space_items": sum(1 for e in model.entities if e.space == "paper")},
        "layers": [l.model_dump() for l in model.layers],
        "blocks": [b.model_dump() for b in model.blocks],
        "text_elements": {"count": len(texts),
                          "items": [{"entity_id": e.id, "layer": e.layer, "space": e.space, "text": e.source.text}
                                    for e in texts[:MAX_LIST]]},
        "element_counts": dict(cats),
        "walls": {"count": cats.get("wall", 0),
                  "total_length_ft": round(sum(e.properties.get("length_ft", 0) for e in model.elements_of("wall")), 2)},
        "rooms": [{"id": e.id, "label": e.label, "name": e.properties.get("name"), "number": e.properties.get("number"),
                   "area_sf": e.properties.get("area_sf"), "stated_area_sf": e.properties.get("stated_area_sf"),
                   "confidence": e.confidence, "requires_verification": e.requires_verification,
                   "detection_method": e.properties.get("detection_method"),
                   "source_entity_ids": e.source_entity_ids} for e in model.elements_of("room")],
        "areas": [{"id": e.id, "area_sf": e.properties.get("area_sf"), "subtype": e.subtype} for e in model.elements_of("area")],
        "doors": [{"id": e.id, "label": e.label, "confidence": e.confidence, "subtype": e.subtype,
                   "requires_verification": e.requires_verification} for e in model.elements_of("door")],
        "windows": {"count": cats.get("window", 0)},
        "columns": [{"id": e.id, "label": e.label, "confidence": e.confidence, "subtype": e.subtype}
                    for e in model.elements_of("column")],
        "title_block": model.title_block,
        "unclassified": {"count": len(model.unclassified_entity_ids),
                         "by_layer": dict(Counter(model.entity(i).layer for i in model.unclassified_entity_ids).most_common(50)),
                         "by_type": dict(Counter(model.entity(i).type for i in model.unclassified_entity_ids).most_common())},
        "confidence_summary": conf,
        "warnings": [w.model_dump() for w in model.diagnostics.warnings],
        "errors": [w.model_dump() for w in model.diagnostics.errors],
        "review_triggers": [w.model_dump() for w in model.diagnostics.review_triggers],
        "requires_human_review": model.requires_human_review,
        "assumptions": model.assumptions,
        "xrefs": [x.model_dump() for x in model.xrefs],
        "view_regions": [{k: r.get(k) for k in ("id", "uid", "bbox_ft", "entity_count", "significant", "view_type",
                                               "view_type_confidence", "view_type_source", "review_state",
                                               "room_logic", "view_type_evidence", "element_counts")}
                         for r in model.view_regions],
        "wall_analysis": ({"stats": model.wall_model.get("stats"), "note": model.wall_model.get("note")}
                          if model.wall_model else None),
        "human_corrections_applied": model.human_corrections_applied,
        "verification": model.verification.model_dump() if model.verification else None,
    })
    return rep


def build_summary_md(rep: dict[str, Any]) -> str:
    L: list[str] = []
    src = rep.get("source") or {}
    L.append(f"# Drawing understanding summary — {src.get('filename', '(unknown file)')}\n")
    L.append("> **Not for design or permit.** This report describes what FireAI read from the drawing. "
             "No sprinkler design, hydraulic calculation, or code-compliance evaluation has been performed.\n")
    status = rep["processing_status"]
    L.append(f"- **Processing status:** `{status}`")
    L.append(f"- **Requires human review:** {'**YES**' if rep.get('requires_human_review') else 'no automatic triggers'}")
    L.append("- **Engineering review status:** not performed")
    L.append("- **Geometry is synthetic:** false  |  **AI inference used:** none")
    if rep.get("failure"):
        f = rep["failure"]
        L.append(f"\n## Failure: `{f['code']}`\n\n{f['message']}\n")
    if rep.get("unit_resolution_required"):
        u = rep["unit_resolution_required"]
        L.append("\n## Units required\n")
        L.append(f"{u['reason']}\n\n**Action:** {u['action']} Accepted: {', '.join(u['accepted_units'])}.\n")
    if "units" in rep:
        u = rep["units"]
        L.append(f"- **Source format:** {src.get('format')}"
                 + (f" (converted by {src['converter']['name']})" if src.get("converted_from_dwg") else ""))
        L.append(f"- **Units:** {u.get('resolved_units') or 'UNRESOLVED'} via {u['resolution_method']} "
                 f"($INSUNITS={u.get('insunits_code')}) -> normalized to {u['normalized_units']}")
        b = rep.get("bounds_normalized_ft")
        if b:
            L.append(f"- **Extents:** {b['width']:,.2f} x {b['height']:,.2f} ft")
        ec = rep["entity_counts"]
        L.append(f"- **Entities:** {ec['top_level']:,} top-level, {ec['total_including_block_contents']:,} including block contents")
    if rep.get("element_counts") is not None:
        L.append("\n## What FireAI identified\n")
        L.append("| Category | Count | Mean confidence | Need verification |")
        L.append("|---|---:|---:|---:|")
        for cat, c in rep["confidence_summary"].items():
            L.append(f"| {cat} | {c['count']} | {c['mean_confidence']:.2f} | {c['requiring_verification']} |")
        L.append(f"| **unclassified entities** | {rep['unclassified']['count']} | — | — |")
        if rep["rooms"]:
            L.append("\n### Rooms\n")
            L.append("| ID | Label | Area (sf) | Stated (sf) | Confidence | Verify | Method |")
            L.append("|---|---|---:|---:|---:|---|---|")
            for r in rep["rooms"]:
                L.append(f"| {r['id']} | {r['label'] or '*(unlabeled)*'} | {r['area_sf']:,.1f} | "
                         f"{'' if r['stated_area_sf'] is None else f'{r['stated_area_sf']:,.0f}'} | {r['confidence']:.2f} | "
                         f"{'YES' if r['requires_verification'] else ''} | {r['detection_method']} |")
        sc = rep.get("scale", {})
        checks = sc.get("dimension_checks", [])
        L.append("\n### Scale / unit verification\n")
        if checks:
            ok = sum(1 for c in checks if c["agrees"])
            L.append(f"{ok} of {len(checks)} dimension strings agree with measured geometry.")
        else:
            L.append("No dimension text could be compared with geometry: **full-scale model space is assumed, not verified.**")
        if rep.get("title_block"):
            L.append("\n### Title block (regex extraction — verify)\n")
            for k, v in rep["title_block"].get("fields", {}).items():
                if not k.startswith("_"):
                    L.append(f"- {k}: {v['value']}")
        if rep.get("verification"):
            v = rep["verification"]
            L.append(f"\n### Human verification: `{v['status']}`\n")
            L.append("A model must be HUMAN_VERIFIED before any engineering use; FireAI never sets that status."
                     + (" Reasons: " + "; ".join(v["status_reasons"]) if v.get("status_reasons") else ""))
        if rep.get("xrefs"):
            L.append("\n### External references (XREFs)\n")
            L.append("| XREF | Depth | Status | Resolved file | Unit scale | Entities |")
            L.append("|---|---:|---|---|---:|---:|")
            for x in rep["xrefs"]:
                L.append(f"| {x['name']} | {x['depth']} | **{x['status']}** | {x.get('resolved_file') or '—'} | "
                         f"{x.get('unit_scale') if x.get('unit_scale') is not None else '—'} | {x['entity_count']} |")
        sig = [r for r in rep.get("view_regions") or [] if r.get("significant")]
        if sig:
            L.append("\n### Drawing regions (likely view type — verify)\n")
            L.append("| Region | View type | Confidence | Source | Review | Room logic |")
            L.append("|---|---|---:|---|---|---|")
            for r in sig:
                L.append(f"| {r['id']} | {r.get('view_type')} | {r.get('view_type_confidence', 0):.2f} | "
                         f"{r.get('view_type_source')} | {r.get('review_state')} | {r.get('room_logic')} |")
        if rep.get("wall_analysis"):
            st = rep["wall_analysis"]["stats"]
            L.append("\n### Wall analysis (derived analysis geometry — not source walls)\n")
            L.append(f"{st['wall_pieces']} paired wall pieces ({st['curved_pieces']} curved) from {st['wall_segments']} "
                     f"wall-layer segments; {st['junctions']} junctions; {st['door_openings']} door openings, "
                     f"{st['doorless_openings']} doorless openings (verify).")
        L.append("\n### Layers\n")
        L.append("| Layer | Entities | Interpreted role | Confidence |")
        L.append("|---|---:|---|---:|")
        for l in sorted(rep["layers"], key=lambda x: -x["entity_count"])[:60]:
            L.append(f"| {l['name']} | {l['entity_count']} | {l['inferred_role'] or '*(unrecognized)*'} | "
                     f"{l['role_confidence']:.2f} |")
        if rep["unclassified"]["count"]:
            L.append("\n### Not interpreted (unclassified)\n")
            L.append(", ".join(f"{k}: {v}" for k, v in rep["unclassified"]["by_layer"].items()))
    for title, key in (("Review triggers", "review_triggers"), ("Warnings", "warnings"), ("Errors", "errors")):
        items = rep.get(key) or []
        if items:
            L.append(f"\n## {title}\n")
            for i in items:
                L.append(f"- `{i['code']}` — {i['message']}")
    if rep.get("assumptions"):
        L.append("\n## Assumptions in effect\n")
        for a in rep["assumptions"]:
            L.append(f"- {a}")
    L.append("\n## Stages\n")
    for s in rep["stages"]:
        L.append(f"- {s['stage']}: {s['status']} ({s['duration_ms']} ms)" + (f" — {s['detail']}" if s.get("detail") else ""))
    return "\n".join(L) + "\n"
