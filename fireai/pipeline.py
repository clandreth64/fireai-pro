"""Drawing-understanding pipeline: upload -> building model -> overlay -> report.

Contract:
* Returns processing_status in {"completed", "needs_human_input", "failed"}.
* A failed stage never yields "completed". Any unexpected exception becomes
  INTERNAL_ERROR / failed — never a success.
* No stage fabricates geometry. If real geometry cannot be read, the job fails.
"""

from __future__ import annotations

import hashlib
import json
import shutil
import time
import uuid
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fireai import __version__
from fireai.config import Settings, get_settings
from fireai.errors import NEEDS_HUMAN_INPUT_CODES, FailureCode, PipelineFailure
from fireai.ingest.dwg import DwgConverter, select_converter
from fireai.ingest.extract import (compute_source_bounds, extract, load_dxf, normalize_entities,
                                   source_uid_for)
from fireai.ingest.filetype import sanitize_filename, validate_upload
from fireai.ingest.units import resolve_units, unit_evidence, unit_requirement
from fireai.ingest.xref import XrefResolver
from fireai.interpret import rules as R
from fireai.interpret.boundaries import classify_region_boundaries
from fireai.interpret.checks import dimension_checks, review_checks
from fireai.interpret.elements import Interpreter
from fireai.interpret.rooms import annotate_rooms
from fireai.interpret.walls import build_wall_model
from fireai.interpret.regions import (classify_regions, cluster_regions, region_index, region_warnings,
                                      summarize_regions)
from fireai.model import (BlockInfo, Bounds, BuildingModel, Issue, LayerInfo, ScaleInfo, SourceInfo, Transform,
                          UnitsInfo, VerificationBinding, XrefRecord)
from fireai.report import build_report, build_summary_md
from fireai.review.apply import (apply_element_corrections, apply_view_type_corrections, correction_issues,
                                 corrections_digest)
from fireai.review.content import content_fingerprint
from fireai.review.store import ReviewStore, verification_state
from fireai.schema import dump_model
from fireai.spatial import build_frames

_USE_CONFIGURED = object()

# THE interpretation engine version (single source). Bump whenever interpretation output can change
# for the same input: a human verification recorded under another engine version is invalidated.
# The pipeline stamps it on the verification binding, on every element FireAI derives
# (provenance.engine_version), on region boundaries and on the wall analysis layer.
ENGINE_VERSION = f"{__version__}+interp.m19.1"

ASSUMPTIONS = [
    "Model-space geometry is drawn at full scale (1 drawing unit = 1 unit of the declared units); "
    "this is verified only where dimension text can be compared with measured geometry.",
    "Only model space is interpreted as building geometry; paper space is inventoried for title-block text and viewport scales.",
    "The drawing is treated as a single 2D plan level; Z coordinates are ignored and multiple levels in one model space are not separated.",
    "Entities on off/frozen layers, or flagged invisible, are excluded from interpretation.",
    "Block contents drawn on layer '0' take the layer of their INSERT (standard CAD convention).",
    "Curves (arcs, bulges, splines, ellipses) are flattened with a chord tolerance of 0.2% of each entity's size.",
    "Semantic roles come from layer/block naming conventions (NCS/AIA and common keywords); private layer standards are left unclassified.",
]

DELIVERABLES = {
    "model_json": ("building_model.json", "application/json"),
    "report_json": ("understanding_report.json", "application/json"),
    "summary_md": ("understanding_summary.md", "text/markdown; charset=utf-8"),
    "source_png": ("source.png", "image/png"),
    "overlay_png": ("overlay.png", "image/png"),
    "overlay_svg": ("overlay.svg", "image/svg+xml"),
    "overlay_dxf": ("overlay.dxf", "application/dxf"),
}


@dataclass
class PipelineResult:
    processing_status: str
    model: BuildingModel | None
    report: dict[str, Any]
    deliverables: dict[str, str] = field(default_factory=dict)  # deliverable id -> filename in out_dir
    failure: dict | None = None
    out_dir: Path | None = None

    def path(self, deliverable_id: str) -> Path:
        return self.out_dir / self.deliverables[deliverable_id]

    @property
    def requires_human_review(self) -> bool:
        return self.report.get("requires_human_review", True)


class _Stages:
    def __init__(self):
        self.items: list[dict] = []

    def run(self, name: str, fn, *a, **kw):
        t0 = time.perf_counter()
        try:
            out = fn(*a, **kw)
        except PipelineFailure as f:
            self.items.append({"stage": name, "status": "failed", "duration_ms": _ms(t0), "detail": f.code.value})
            raise
        except Exception as exc:
            self.items.append({"stage": name, "status": "failed", "duration_ms": _ms(t0),
                               "detail": f"{type(exc).__name__}"})
            raise
        self.items.append({"stage": name, "status": "ok", "duration_ms": _ms(t0)})
        return out


def _ms(t0: float) -> int:
    return int((time.perf_counter() - t0) * 1000)


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _converter_messages(log: str | None, limit: int = 50) -> list[str]:
    """Converter log lines that report problems (kept verbatim, never interpreted away)."""
    out = []
    for ln in (log or "").splitlines():
        low = ln.lower()
        if "warning" in low or "error" in low or "unhandled" in low or "unsupported" in low:
            out.append(ln.strip()[:300])
            if len(out) >= limit:
                break
    return out


def _write_json(path: Path, data) -> None:
    path.write_text(json.dumps(data, indent=2, default=str), encoding="utf-8")


def resolved_xref_shas(model: BuildingModel) -> list[str]:
    return sorted({x.sha256 for x in model.xrefs if x.status == "resolved" and x.sha256})


def verification_binding(model: BuildingModel) -> VerificationBinding:
    """Fingerprint of everything a human verification depends on: source bytes,
    loaded XREF bytes, unit resolution, engine version, applied corrections and (0.5.0) the
    interpretation CONTENT itself. Any byte change of the source is treated as material
    (conservative). Must run after the model's content (incl. diagnostics) is final."""
    xshas = resolved_xref_shas(model)
    digest = corrections_digest(model.human_corrections_applied)
    content = content_fingerprint(model)
    basis = {"source": model.source.sha256, "xrefs": xshas, "units": model.units.resolved_units,
             "engine": ENGINE_VERSION, "corrections": digest, "content": content}
    fp = hashlib.sha256(json.dumps(basis, sort_keys=True).encode()).hexdigest()
    return VerificationBinding(fingerprint=fp, source_sha256=model.source.sha256, xref_sha256=xshas,
                               resolved_units=model.units.resolved_units, engine_version=ENGINE_VERSION,
                               corrections_digest=digest, content_fingerprint=content)


def understand_drawing(upload_path: Path, original_filename: str | None, work_dir: Path, out_dir: Path,
                       units_override: str | None = None, settings: Settings | None = None,
                       converter: DwgConverter | None | object = _USE_CONFIGURED,
                       xref_files: list[Path] | None = None,
                       review_store: ReviewStore | None | object = _USE_CONFIGURED) -> PipelineResult:
    settings = settings or get_settings()
    store = ReviewStore(settings.review_dir) if review_store is _USE_CONFIGURED else review_store
    work_dir.mkdir(parents=True, exist_ok=True)
    out_dir.mkdir(parents=True, exist_ok=True)
    stages = _Stages()
    model: BuildingModel | None = None
    deliverables: dict[str, str] = {}

    def finish(status: str, failure: PipelineFailure | None, requirement: dict | None = None) -> PipelineResult:
        fail = failure.to_dict() if failure else None
        rep = build_report(model, status, fail, stages.items, requirement)
        if model is None or status != "completed":
            rep["requires_human_review"] = True
        _write_json(out_dir / DELIVERABLES["report_json"][0], rep)
        deliverables["report_json"] = DELIVERABLES["report_json"][0]
        (out_dir / DELIVERABLES["summary_md"][0]).write_text(build_summary_md(rep), encoding="utf-8")
        deliverables["summary_md"] = DELIVERABLES["summary_md"][0]
        if model is not None:
            (out_dir / DELIVERABLES["model_json"][0]).write_text(dump_model(model), encoding="utf-8")
            deliverables["model_json"] = DELIVERABLES["model_json"][0]
        return PipelineResult(status, model, rep, dict(deliverables), fail, out_dir)

    try:
        display_name = sanitize_filename(original_filename)
        fmt = stages.run("validate_upload", validate_upload, upload_path, original_filename)
        sha = _sha256(upload_path)
        conversion = None
        dxf_path = upload_path
        if fmt == "dwg":
            conv = select_converter(settings) if converter is _USE_CONFIGURED else converter
            if conv is None:
                stages.items.append({"stage": "dwg_conversion", "status": "failed", "duration_ms": 0,
                                     "detail": FailureCode.DWG_CONVERSION_UNAVAILABLE.value})
                raise PipelineFailure(
                    FailureCode.DWG_CONVERSION_UNAVAILABLE,
                    "DWG files require a DWG->DXF converter (ODA File Converter or LibreDWG), which is not "
                    "installed on this server. Export the drawing to DXF from your CAD software and upload the DXF.",
                    {"configured": settings.dwg_converter})
            conversion = stages.run("dwg_conversion", conv.convert, upload_path, work_dir / "dwg_convert",
                                    settings.dwg_timeout_s)
            dxf_path = conversion.dxf_path

        try:
            doc, audit_errors, audit_fixes = stages.run("load_dxf", load_dxf, dxf_path)
        except PipelineFailure as f:
            if fmt == "dwg":
                raise PipelineFailure(FailureCode.DWG_CONVERSION_FAILED,
                                      f"Converted DXF could not be read: {f.message}") from f
            raise

        if conversion is not None:
            stages.run("conversion_audit", conversion.finalize_audit, doc)
        header = doc.header
        source_uid = source_uid_for(sha)
        document_guid = header.get("$FINGERPRINTGUID") if "$FINGERPRINTGUID" in header else None
        version_guid = header.get("$VERSIONGUID") if "$VERSIONGUID" in header else None
        insunits = header.get("$INSUNITS") if "$INSUNITS" in header else None
        measurement = header.get("$MEASUREMENT") if "$MEASUREMENT" in header else None
        ures = stages.run("resolve_units", resolve_units, insunits, measurement, units_override)
        conv_for_xrefs = None
        if xref_files:
            try:
                conv_for_xrefs = select_converter(settings) if converter is _USE_CONFIGURED else converter
            except Exception:
                conv_for_xrefs = None
        resolver = XrefResolver(list(xref_files or []), conv_for_xrefs, work_dir, settings.dwg_timeout_s)
        ex = stages.run("extract_geometry", extract, doc, settings.max_entities, audit_errors, audit_fixes,
                        source_uid, document_guid, resolver, ures.resolved_units, sha)
        top_model = [e for e in ex.entities if e.space == "model" and e.parent_id is None]
        if not top_model:
            raise PipelineFailure(FailureCode.EMPTY_DRAWING, "The drawing's model space contains no entities.",
                                  {"paper_space_items": sum(1 for e in ex.entities if e.space == "paper")})
        bounds_src = compute_source_bounds(ex.entities)
        if bounds_src is None:
            raise PipelineFailure(
                FailureCode.GEOMETRY_EXTRACTION_FAILED,
                "No visible, supported geometry could be read from model space.",
                {"unsupported": dict(ex.unsupported), "top_level_entities": len(top_model),
                 "hidden": sum(1 for e in top_model if not e.visible)})

        # Inventory (layers / blocks) — independent of units
        layer_types: dict[str, Counter] = {}
        for e in ex.entities:
            layer_types.setdefault(e.layer, Counter())[e.type] += 1
        layer_roles = {name: R.classify_layer(name) for name in set(layer_types) | set(ex.layer_table)}
        layers = []
        for name in sorted(set(layer_types) | set(ex.layer_table)):
            info = ex.layer_table.get(name, {})
            m = layer_roles.get(name)
            layers.append(LayerInfo(name=name, color=info.get("color"), is_off=info.get("is_off", False),
                                    is_frozen=info.get("is_frozen", False),
                                    entity_count=sum(layer_types.get(name, Counter()).values()),
                                    entity_types=dict(layer_types.get(name, Counter())),
                                    inferred_role=m.role if m else None, role_confidence=m.confidence if m else 0.0,
                                    role_rule=m.rule_id if m else None))
        block_roles = {}
        blocks = []
        effective = {e.attributes["block"]: e.attributes["effective_block"] for e in ex.entities
                     if e.type == "INSERT" and e.attributes.get("effective_block")}
        for name in sorted(set(ex.block_inserts) | ex.xref_blocks):
            eff = effective.get(name)
            m = R.classify_block(eff or name)
            if m and eff:
                m = R.RoleMatch(m.role, m.confidence, m.rule_id,
                                m.evidence + f" (effective name of anonymous dynamic block '{name}')")
            block_roles[name] = m
            blocks.append(BlockInfo(name=name, effective_name=eff, insert_count=ex.block_inserts.get(name, 0),
                                    is_xref=name in ex.xref_blocks, inferred_role=m.role if m else None,
                                    role_confidence=m.confidence if m else 0.0, role_rule=m.rule_id if m else None))

        model = BuildingModel(
            model_id=uuid.uuid4().hex, created_at=datetime.now(timezone.utc).isoformat(), fireai_version=__version__,
            source=SourceInfo(filename=display_name, format=fmt, sha256=sha, size_bytes=upload_path.stat().st_size,
                              dxf_version=doc.dxfversion, converted_from_dwg=conversion is not None,
                              converter=conversion.provenance() if conversion else None,
                              source_uid=source_uid, document_guid=document_guid, version_guid=version_guid,
                              converted_dxf_sha256=_sha256(dxf_path) if conversion else None,
                              converter_log_tail=(conversion.log_tail or "")[-4000:] if conversion else None,
                              converter_warnings=_converter_messages(conversion.log_tail) if conversion else [],
                              conversion_audit=conversion.audit if conversion else None),
            units=UnitsInfo(insunits_code=ures.insunits_code, detected_units=ures.detected_units,
                            resolved_units=ures.resolved_units, resolution_method=ures.method,
                            scale_to_normalized=ures.scale_to_ft, measurement_system_hint=ures.measurement_hint,
                            resolved=ures.resolved, note=ures.note),
            transform=Transform(origin=bounds_src.min, scale=ures.scale_to_ft),
            coordinate_frames=build_frames(ures.resolved_units, ures.scale_to_ft, bounds_src.min),
            bounds_source=bounds_src,
            scale=ScaleInfo(viewport_scales=ex.viewport_scales),
            layers=layers, blocks=blocks, entities=ex.entities,
            xrefs=[XrefRecord(**r) for r in ex.xref_records],
            assumptions=list(ASSUMPTIONS),
        )
        model.diagnostics.warnings.extend(ex.warnings)
        if audit_fixes:
            model.diagnostics.warnings.append(Issue(code="DXF_AUDIT_FIXES", severity="info",
                                                    message=f"ezdxf repaired {audit_fixes} minor structural issue(s) while loading."))
        for err in audit_errors:
            model.diagnostics.errors.append(Issue(code="DXF_AUDIT_ERROR", severity="error", message=err))

        # Source rendering is useful even when units are unresolved.
        from fireai.render.overlay import render_source_png, render_overlay, write_overlay_dxf
        try:
            stages.run("render_source", render_source_png, doc, bounds_src, out_dir / "source.png", display_name)
            deliverables["source_png"] = "source.png"
        except Exception as exc:
            raise PipelineFailure(FailureCode.OVERLAY_GENERATION_FAILED, f"Source rendering failed: {exc}") from exc

        if not ures.resolved:
            texts = [e.source.text for e in ex.entities if e.source is not None and e.source.text]
            evidence = unit_evidence(ex.viewport_scales, texts)
            model.units.evidence = evidence
            req = unit_requirement(ures, evidence)
            model.diagnostics.review_triggers.append(Issue(code="UNIT_DETECTION_FAILED", message=ures.note or "units unresolved"))
            model.requires_human_review = True
            raise PipelineFailure(FailureCode.UNIT_DETECTION_FAILED,
                                  "Drawing units could not be determined from the file. FireAI will not assume units.",
                                  {"unit_resolution_required": req})

        normalize_entities(model.entities, model.transform)
        s = model.transform.scale
        model.bounds_normalized = Bounds(min=(0.0, 0.0), max=(bounds_src.width * s, bounds_src.height * s))

        regions = stages.run("view_regions", cluster_regions, model)
        classify_regions(model, regions, layer_roles, block_roles)
        corrections = store.corrections(sha) if store else []
        applied = apply_view_type_corrections(regions, corrections, resolved_xref_shas(model))
        res = stages.run("interpret", Interpreter(model.entities, layer_roles, block_roles, source_uid,
                                                  region_index(model, regions), engine_version=ENGINE_VERSION).run)
        model.elements = res.elements
        model.unclassified_entity_ids = res.unclassified_entity_ids
        model.title_block = res.title_block
        if model.title_block:
            sc = model.title_block["fields"].get("scale_text")
            model.scale.title_block_scale_text = sc["value"] if sc else None

        region_issues = summarize_regions(model, regions)
        model.view_regions = regions
        model.wall_model = stages.run("wall_analysis", build_wall_model, model, source_uid)
        if model.wall_model is not None:
            model.wall_model["engine_version"] = ENGINE_VERSION
        room_issues = stages.run("room_analysis", annotate_rooms, model)
        applied += apply_element_corrections(model, corrections, resolved_xref_shas(model))
        model.human_corrections_applied = applied
        room_issues += correction_issues(applied)
        # after corrections: human room boundaries are classified too; openings need all regions
        room_issues += stages.run("region_boundaries", classify_region_boundaries, model, source_uid, ENGINE_VERSION)
        stale = [x for x in (store.other_revisions(model) if store else []) if store.corrections(x)]
        if stale:
            room_issues.append(Issue(code="HUMAN_CORRECTIONS_FROM_OTHER_REVISION", severity="error",
                                     message=f"Human corrections exist for {len(stale)} other revision(s) of this "
                                             "drawing (same document GUID) and were NOT applied, because the source "
                                             "changed. Review them against this revision."))
        checks, dim_warn, dim_trig = dimension_checks(model)
        model.scale.dimension_checks = checks
        hidden = sum(1 for e in model.entities if e.parent_id is None and not e.visible)
        warn, trig = review_checks(model, ex.unsupported, model.xrefs, hidden, audit_errors)
        model.diagnostics.warnings.extend(dim_warn + warn + region_warnings(regions))
        model.diagnostics.review_triggers.extend(dim_trig + res.issues + trig + region_issues + room_issues)
        model.requires_human_review = bool(model.diagnostics.review_triggers)
        model.verification = verification_binding(model)
        vs = verification_state(model, store)
        model.verification.status = vs["status"]
        model.verification.status_reasons = vs["reasons"]

        try:
            stages.run("render_overlay", render_overlay, doc, model, out_dir / "overlay.png", out_dir / "overlay.svg")
            # last: adds FIREAI_* layers to the loaded document
            stages.run("write_overlay_dxf", write_overlay_dxf, doc, model, out_dir / "overlay.dxf")
        except Exception as exc:
            raise PipelineFailure(FailureCode.OVERLAY_GENERATION_FAILED,
                                  f"Verification overlay could not be generated: {type(exc).__name__}: {exc}") from exc
        deliverables.update({"overlay_png": "overlay.png", "overlay_svg": "overlay.svg", "overlay_dxf": "overlay.dxf"})
        return finish("completed", None)

    except PipelineFailure as f:
        status = "needs_human_input" if f.code in NEEDS_HUMAN_INPUT_CODES else "failed"
        return finish(status, f, f.details.get("unit_resolution_required"))
    except Exception as exc:  # never a success
        return finish("failed", PipelineFailure(FailureCode.INTERNAL_ERROR, f"{type(exc).__name__}: {exc}"))
    finally:
        shutil.rmtree(work_dir, ignore_errors=True)
