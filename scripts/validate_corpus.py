"""Run the real-drawing corpus through the drawing-understanding pipeline.

Usage (inside the Docker image, repo mounted at /app):
    python scripts/validate_corpus.py --label baseline_m1
    python scripts/validate_corpus.py --label tuned_m15 --only REAL_002 REAL_003

* Drawings:  tests/real_drawings_local/drawings/REAL_###.(dwg|dxf)   (git-ignored)
* Outputs:   tests/real_drawings_outputs_local/<label>/<file>/        (git-ignored)
  - source.png, overlay.png/.svg/.dxf, building_model.json, understanding_report.json,
    understanding_summary.md, metrics.json, converter_log.txt (DWG only)
* Anonymized metrics (counts/codes/timings only — no text, layer names or coordinates):
  tests/real_drawings/runs/<label>.json   (committed)

A label can be written only once: existing runs are never overwritten, so the
pre-tuning baseline stays intact.
"""

from __future__ import annotations

import argparse
import json
import os
import resource
import subprocess
import sys
import time
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
DRAWINGS = ROOT / "tests" / "real_drawings_local" / "drawings"
OUT_ROOT = ROOT / "tests" / "real_drawings_outputs_local"
RUNS = ROOT / "tests" / "real_drawings" / "runs"


def _one(path: Path, out_dir: Path) -> dict:
    """Process one drawing in THIS process and return metrics (called in a child process)."""
    from fireai.config import get_settings
    from fireai.pipeline import understand_drawing

    settings = get_settings()
    # M1.6: no separate probe conversion (it doubled the cost and inflated peak RSS in M1.5 runs);
    # the converter log is taken from the model / failure details of the one real run.

    # Explicit, labelled HYPOTHESIS unit overrides (e.g. FIREAI_UNITS_OVERRIDES="REAL_001=in").
    overrides = dict(kv.split("=", 1) for kv in os.getenv("FIREAI_UNITS_OVERRIDES", "").split(",") if "=" in kv)
    units_override = overrides.get(path.stem)
    t0 = time.perf_counter()
    xdir = DRAWINGS.parent / "xrefs" / path.stem          # optional locally supplied XREF files
    xref_files = sorted(x for x in xdir.iterdir() if x.is_file()) if xdir.is_dir() else None
    # FIREAI_LOCAL_WORK=1: converter scratch (converted DXF, dwgread JSON) on container-local disk instead of
    # the bind-mounted output folder -- the production layout (job dirs on local disk). Recorded in metrics.
    import tempfile
    work = Path(tempfile.mkdtemp(prefix="fireai_work_")) if os.getenv("FIREAI_LOCAL_WORK") else out_dir / "_work"
    r = understand_drawing(path, path.name, work, out_dir, units_override, settings,
                           xref_files=xref_files, review_store=None)
    total = time.perf_counter() - t0
    conv_log = None
    if path.suffix.lower() == ".dwg":
        conv_log = (r.model.source.converter_log_tail if r.model is not None
                    else str((r.failure or {}).get("details", {}).get("log_tail", "")))
        (out_dir / "converter_log.txt").write_text(conv_log or "", encoding="utf-8")
    rep = r.report
    m = r.model
    stage_ms = {s["stage"]: s["duration_ms"] for s in rep.get("stages", [])}
    metrics = {
        "id": path.stem, "file": path.name, "format": path.suffix.lower()[1:],
        "size_bytes": path.stat().st_size,
        "processing_status": r.processing_status,
        "failure_code": (r.failure or {}).get("code"),
        "failure_message": (r.failure or {}).get("message"),
        "requires_human_review": rep.get("requires_human_review"),
        "total_seconds": round(total, 3),
        "stage_ms": stage_ms,
        "peak_rss_mb": round(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024, 1),
        "peak_rss_children_mb": round(resource.getrusage(resource.RUSAGE_CHILDREN).ru_maxrss / 1024, 1),
        "model_json_bytes": (out_dir / "building_model.json").stat().st_size
        if (out_dir / "building_model.json").exists() else None,
        "converter": (rep.get("source") or {}).get("converter"),
        "converter_log_lines": len((conv_log or "").splitlines()) if conv_log is not None else None,
        "units_override_hypothesis": units_override,
        "work_dir_location": "container_local" if os.getenv("FIREAI_LOCAL_WORK") else "bind_mount",
    }
    if m is not None:
        top = [e for e in m.entities if e.parent_id is None and e.space == "model"]
        visible_top = [e for e in top if e.visible]
        uncl_top = [i for i in m.unclassified_entity_ids if m.entity(i).parent_id is None]
        cats = Counter(e.category for e in m.elements)
        metrics.update({
            "dxf_version": m.source.dxf_version,
            "units": {"insunits": m.units.insunits_code, "resolved": m.units.resolved_units,
                      "method": m.units.resolution_method},
            "bounds_ft": ({"width": round(m.bounds_normalized.width, 3), "height": round(m.bounds_normalized.height, 3)}
                          if m.bounds_normalized else None),
            "layer_count": len(m.layers),
            "layers_with_role": sum(1 for l in m.layers if l.inferred_role),
            "layer_roles": dict(Counter(l.inferred_role or "UNRECOGNIZED" for l in m.layers if l.entity_count)),
            "block_count": len(m.blocks),
            "blocks_with_role": sum(1 for b in m.blocks if b.inferred_role),
            "xref_blocks": sum(1 for b in m.blocks if b.is_xref),
            "entities_total": len(m.entities),
            "entities_top_level_model": len(top),
            "entities_paper": sum(1 for e in m.entities if e.space == "paper"),
            "entity_types_top_level": dict(Counter(e.type for e in top).most_common()),
            "hidden_top_level": len(top) - len(visible_top),
            "unsupported": dict(Counter(e.type for e in m.entities if not e.supported).most_common()),
            "element_counts": dict(cats),
            "text_entities": sum(1 for e in m.entities if e.source is not None and e.source.kind == "text"),
            "unclassified_top_level": len(uncl_top),
            "unclassified_pct_of_visible_top": round(100 * len(uncl_top) / len(visible_top), 1) if visible_top else None,
            "rooms": [{"area_sf": e.properties.get("area_sf"), "labeled": bool(e.label),
                       "method": e.properties.get("detection_method"), "verify": e.requires_verification}
                      for e in m.elements_of("room")],
            "dimension_checks": {"total": len(m.scale.dimension_checks),
                                 "agree": sum(1 for c in m.scale.dimension_checks if c.agrees)},
            "review_triggers": [t.code for t in m.diagnostics.review_triggers],
            "view_regions": [{k: r[k] for k in ("id", "entity_count", "significant", "element_counts")}
                             for r in getattr(m, "view_regions", [])],
            "conversion_audit_lost": ((getattr(m.source, "conversion_audit", None) or {}).get("lost")
                                      if m.source.converted_from_dwg else None),
            "conversion_audit": ({k: (m.source.conversion_audit or {}).get(k) for k in (
                "status", "level", "significance", "lost_by_significance", "added_in_dxf", "type_changed_count",
                "geometry_mismatch_count", "text_mismatch_count")}
                if m.source.converted_from_dwg else None),
            "view_types": [{"id": r["id"], "view_type": r.get("view_type"), "confidence": r.get("view_type_confidence"),
                            "review_state": r.get("review_state"), "room_logic": r.get("room_logic"),
                            "rules": r.get("view_type_rules")} for r in m.view_regions if r.get("significant")],
            "wall_model": (m.wall_model or {}).get("stats"),
            "xrefs": [{"status": x.status, "depth": x.depth, "entity_count": x.entity_count} for x in m.xrefs],
            "verification_status": m.verification.status if m.verification else None,
            "unit_evidence_suggestion": ((getattr(m.units, "evidence", None) or {}).get("suggested_units")
                                         if getattr(m.units, "evidence", None) else None),
            "warnings": [w.code for w in m.diagnostics.warnings],
            "errors": [w.code for w in m.diagnostics.errors],
        })
    (out_dir / "metrics.json").write_text(json.dumps(metrics, indent=2, default=str), encoding="utf-8")
    return metrics


ANON_KEYS = {"id", "format", "size_bytes", "processing_status", "failure_code", "requires_human_review",
             "total_seconds", "stage_ms", "peak_rss_mb", "converter", "converter_log_lines", "dxf_version", "units",
             "bounds_ft", "layer_count", "layers_with_role", "layer_roles", "block_count", "blocks_with_role",
             "xref_blocks", "entities_total", "entities_top_level_model", "entities_paper", "entity_types_top_level",
             "hidden_top_level", "unsupported", "element_counts", "text_entities", "unclassified_top_level",
             "unclassified_pct_of_visible_top", "rooms", "dimension_checks", "review_triggers", "warnings", "errors",
             "units_override_hypothesis", "view_regions", "conversion_audit_lost", "unit_evidence_suggestion",
             "peak_rss_children_mb", "model_json_bytes", "conversion_audit", "view_types", "wall_model", "xrefs",
             "verification_status", "work_dir_location"}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--label", required=True)
    ap.add_argument("--only", nargs="*")
    ap.add_argument("--_child", nargs=2, help=argparse.SUPPRESS)
    ap.add_argument("--engine-commit", default=os.getenv("FIREAI_ENGINE_COMMIT", "unknown"))
    a = ap.parse_args()
    if a._child:
        m = _one(Path(a._child[0]), Path(a._child[1]))
        print(json.dumps(m, default=str))
        return

    out_label = OUT_ROOT / a.label
    run_file = RUNS / f"{a.label}.json"
    if out_label.exists() or run_file.exists():
        sys.exit(f"Run '{a.label}' already exists — runs are write-once. Choose a new label.")
    files = sorted(p for p in DRAWINGS.iterdir() if p.suffix.lower() in (".dwg", ".dxf"))
    if a.only:
        files = [p for p in files if p.stem in a.only or p.name in a.only]
    results = []
    for p in files:
        od = out_label / p.name.replace(".", "_")
        od.mkdir(parents=True)
        proc = subprocess.run([sys.executable, __file__, "--label", a.label, "--_child", str(p), str(od)],
                              capture_output=True, text=True, timeout=1800)
        if proc.returncode != 0:
            m = {"id": p.stem, "file": p.name, "format": p.suffix[1:], "processing_status": "HARNESS_CRASH",
                 "failure_code": "HARNESS_CRASH", "stderr_tail": proc.stderr[-1500:]}
        else:
            m = json.loads(proc.stdout.strip().splitlines()[-1])
        results.append(m)
        print(f"{p.name:14} {m.get('processing_status'):18} {m.get('failure_code') or '':28} "
              f"{m.get('total_seconds', '')}s rss={m.get('peak_rss_mb', '')}MB "
              f"children={m.get('peak_rss_children_mb', '')}MB elements={m.get('element_counts')}")
    RUNS.mkdir(parents=True, exist_ok=True)
    anon = [{k: v for k, v in r.items() if k in ANON_KEYS} for r in results]
    run_file.write_text(json.dumps({"label": a.label, "engine_commit": a.engine_commit,
                                    "created": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                                    "results": anon}, indent=2, default=str), encoding="utf-8")
    (out_label / "_run.json").write_text(json.dumps(results, indent=2, default=str), encoding="utf-8")
    print(f"\nwrote {run_file.relative_to(ROOT)} and {out_label.relative_to(ROOT)}/")


if __name__ == "__main__":
    main()
