"""Cross-checks that decide whether a completed model needs human review.

Each check produces Issues. Issues placed in ``review_triggers`` set
``requires_human_review = true``. Nothing here changes geometry.
"""

from __future__ import annotations

from collections import Counter

from fireai.interpret.text import parse_length_text_ft
from fireai.model import BuildingModel, DimensionCheck, Issue

MIN_PLAUSIBLE_EXTENT_FT = 8.0
MAX_PLAUSIBLE_EXTENT_FT = 5000.0
DIM_REL_TOL = 0.01
DIM_ABS_TOL_FT = 1.0 / 96.0   # 1/8 inch
UNCLASSIFIED_REVIEW_FRACTION = 0.30
UNSUPPORTED_REVIEW_FRACTION = 0.05


def dimension_checks(model: BuildingModel) -> tuple[list[DimensionCheck], list[Issue], list[Issue]]:
    """Compare dimension override text against measured geometry."""
    checks, warnings, triggers = [], [], []
    feet_per_unit = model.units.scale_to_normalized
    for el in model.elements_of("dimension"):
        ent = model.entity(el.source_entity_ids[0])
        g = ent.normalized
        dimlfac = ent.attributes.get("dimlfac") or 1.0
        if abs(dimlfac - 1.0) > 1e-9:
            triggers.append(Issue(code="DIMENSION_SCALE_FACTOR", element_ids=[el.id], entity_ids=[ent.id],
                                  message=f"Dimension {el.id} uses DIMLFAC={dimlfac:g}: geometry near it may not be full scale."))
        text = ent.attributes.get("text_override") or ""
        if g is None or g.measurement is None:
            continue
        stated = parse_length_text_ft(text, feet_per_unit)
        if stated is None:
            continue
        measured = g.measurement * dimlfac
        err = abs(stated - measured) / measured if measured > 0 else float("inf")
        agrees = err <= DIM_REL_TOL or abs(stated - measured) <= DIM_ABS_TOL_FT
        checks.append(DimensionCheck(entity_id=ent.id, stated_text=text, stated_length_ft=round(stated, 5),
                                     measured_length_ft=round(measured, 5), relative_error=round(err, 5), agrees=agrees))
        if not agrees:
            triggers.append(Issue(code="DIMENSION_TEXT_DISAGREES", element_ids=[el.id], entity_ids=[ent.id],
                                  message=f"Dimension text '{text}' = {stated:.3f} ft but geometry measures {measured:.3f} ft "
                                          f"({err:.1%}). Units or scale may be wrong, or the dimension was overridden."))
    return checks, warnings, triggers


_XREF_ABSENT = {"missing", "load_failed", "conversion_unavailable", "too_deep", "unresolved", "not_attempted"}


def xref_checks(xrefs) -> tuple[list[Issue], list[Issue]]:
    """Review triggers for XREFs. Anything not loaded means geometry is missing."""
    warnings: list[Issue] = []
    triggers: list[Issue] = []
    absent = [x for x in xrefs if x.status in _XREF_ABSENT]
    if absent:
        triggers.append(Issue(code="XREF_NOT_RESOLVED", severity="error",
                              entity_ids=[i for x in absent for i in x.insert_entity_ids],
                              message=f"{len(absent)} external reference(s) not loaded — their geometry is MISSING from "
                                      "this model: " + "; ".join(
                                          f"{x.name} (path in drawing: '{x.path_in_drawing or '?'}', {x.status}"
                                          + (f": {x.note}" if x.note else "") + ")" for x in absent)
                                      + ". Upload the referenced files with the drawing."))
    circ = [x for x in xrefs if x.status == "circular"]
    if circ:
        triggers.append(Issue(code="XREF_CIRCULAR", severity="error",
                              entity_ids=[i for x in circ for i in x.insert_entity_ids],
                              message="Circular external reference(s) not loaded a second time: "
                                      + ", ".join(f"{x.name} -> {x.resolved_file}" for x in circ)
                                      + ". Confirm the intended reference structure."))
    uu = [x for x in xrefs if x.status == "units_unresolved"]
    if uu:
        triggers.append(Issue(code="XREF_UNITS_UNRESOLVED", severity="error",
                              entity_ids=[i for x in uu for i in x.insert_entity_ids],
                              message="External reference(s) not loaded because units could not be established: "
                                      + "; ".join(f"{x.name}: {x.note}" for x in uu)))
    stem = [x for x in xrefs if x.status == "resolved" and x.matched_by == "same_stem_other_extension"]
    if stem:
        triggers.append(Issue(code="XREF_SUBSTITUTED_FILE",
                              message="XREF(s) resolved to a supplied file with the same name but a different "
                                      "extension (e.g. a DXF export standing in for the referenced DWG): "
                                      + ", ".join(f"{x.path_in_drawing or x.name} -> {x.resolved_file}" for x in stem)
                                      + ". Confirm it is the same revision."))
    lost = [x for x in xrefs if x.status == "resolved" and x.conversion_significance in ("material", "review")]
    if lost:
        triggers.append(Issue(code="XREF_CONVERSION_LOSS",
                              severity="error" if any(x.conversion_significance == "material" for x in lost) else "warning",
                              message="Converting XREF DWG(s) lost or changed content: "
                                      + "; ".join(f"{x.resolved_file} ({x.conversion_significance}): {x.conversion_lost}"
                                                  for x in lost)))
    res = [x for x in xrefs if x.status == "resolved"]
    if res:
        warnings.append(Issue(code="XREFS_LOADED", severity="info",
                              message="External references loaded from supplied files: "
                                      + ", ".join(f"{x.name} <- {x.resolved_file} (sha256 {x.sha256[:12]}…, "
                                                  f"unit scale {x.unit_scale:g}, {x.entity_count} entities)" for x in res)))
    ov = [x for x in xrefs if x.status == "overlay_not_loaded"]
    if ov:
        warnings.append(Issue(code="XREF_NESTED_OVERLAY_SKIPPED", severity="info",
                              message="Nested overlay XREF(s) not loaded (CAD convention: overlays do not carry "
                                      "into referencing drawings): " + ", ".join(x.name for x in ov)))
    return warnings, triggers


def _loss_text(by_type: dict) -> str:
    return ", ".join(f"{t} x{v['count']}" for t, v in by_type.items()) or "none"


def conversion_loss_issues(audit: dict, what: str) -> list[Issue]:
    """Handle-level audit (fireai/ingest/dwg_audit.py) -> review triggers. Material loss is an error."""
    out = []
    by = audit.get("lost_by_significance", {})
    if audit.get("significance") == "material":
        parts = []
        if by.get("material"):
            parts.append("entities lost where they are drawn: " + _loss_text(by["material"]))
        if audit.get("geometry_mismatch_count"):
            parts.append(f"{audit['geometry_mismatch_count']} entities with changed geometry (e.g. handles "
                         + ", ".join(g["handle"] for g in audit["geometry_mismatch"][:5]) + ")")
        out.append(Issue(code="DWG_CONVERSION_LOST_ENTITIES", severity="error",
                         message=f"{what} does not faithfully contain the DWG: " + "; ".join(parts)
                                 + ". This content is absent or wrong in the model; obtain a DXF exported by the "
                                   "authoring CAD software before relying on it."))
    if audit.get("significance") in ("material", "review"):
        parts = []
        if by.get("review"):
            parts.append("content FireAI does not interpret was lost: " + _loss_text(by["review"]))
        if audit.get("layers", {}).get("lost"):
            parts.append(f"layers lost: {audit['layers']['lost'][:10]}")
        if audit.get("blocks", {}).get("lost"):
            parts.append(f"block definitions lost: {audit['blocks']['lost'][:10]}")
        if audit.get("type_changed_count"):
            parts.append(f"{audit['type_changed_count']} entities changed type")
        if audit.get("text_mismatch_count"):
            parts.append(f"{audit['text_mismatch_count']} texts changed")
        if parts:
            out.append(Issue(code="DWG_CONVERSION_LOSS_REVIEW", message=f"{what}: " + "; ".join(parts) + "."))
    return out


def review_checks(model: BuildingModel, unsupported: Counter, xrefs, hidden_count: int,
                  audit_errors: list[str]) -> tuple[list[Issue], list[Issue]]:
    warnings: list[Issue] = []
    triggers: list[Issue] = []

    if model.units.resolution_method == "user_override":
        warnings.append(Issue(code="UNITS_FROM_USER", severity="info",
                              message=f"Units '{model.units.resolved_units}' were supplied by the user, not read from the drawing."))
        if model.units.note:
            triggers.append(Issue(code="UNITS_OVERRIDE_CONFLICTS_WITH_HEADER", message=model.units.note))

    b = model.bounds_normalized
    if b is not None:
        biggest = max(b.width, b.height)
        if biggest < MIN_PLAUSIBLE_EXTENT_FT or biggest > MAX_PLAUSIBLE_EXTENT_FT:
            triggers.append(Issue(code="EXTENTS_IMPLAUSIBLE",
                                  message=f"Drawing extents are {b.width:,.1f} x {b.height:,.1f} ft, outside the plausible range "
                                          f"for a building plan ({MIN_PLAUSIBLE_EXTENT_FT:g}–{MAX_PLAUSIBLE_EXTENT_FT:,.0f} ft). "
                                          f"The declared units ({model.units.resolved_units}) may be wrong."))

    walls = model.elements_of("wall")
    rooms = model.elements_of("room")
    if not walls:
        triggers.append(Issue(code="NO_WALLS_DETECTED",
                              message="No walls were identified. Wall layers may use a naming convention FireAI does not recognize."))
    if not rooms:
        triggers.append(Issue(code="NO_ROOMS_DETECTED",
                              message="No room boundaries were identified (no closed room/area polylines and no regions enclosed by walls)."))

    if walls and b is not None:
        xs = [p[0] for w in walls if w.geometry for p in w.geometry.points]
        ys = [p[1] for w in walls if w.geometry for p in w.geometry.points]
        tb_ids = {sid for t in model.elements_of("title_block") for sid in t.source_entity_ids}
        other = [p for e in model.entities if e.normalized and e.space == "model" and e.visible
                 and e.id not in tb_ids and e.type != "INSERT" and e.parent_id not in tb_ids
                 for p in (e.normalized.points or ([e.normalized.insert] if e.normalized.insert else []))]
        if xs and other:
            wx = max(xs) - min(xs); wy = max(ys) - min(ys)
            ox = max(p[0] for p in other) - min(p[0] for p in other)
            oy = max(p[1] for p in other) - min(p[1] for p in other)
            if wx * wy > 0 and (ox * oy) / (wx * wy) > 4.0:
                triggers.append(Issue(code="GEOMETRY_OUTSIDE_BUILDING",
                                      message="Model space contains substantial geometry well outside the walls (details, "
                                              "other plans, or stray entities). Confirm which region is the building."))

    xw, xt = xref_checks(xrefs)
    warnings.extend(xw)
    triggers.extend(xt)

    total_top = sum(1 for e in model.entities if e.parent_id is None and e.space == "model")
    n_unsupported = sum(unsupported.values())
    if n_unsupported:
        issue = Issue(code="UNSUPPORTED_ENTITIES",
                      message="Entities not interpreted (kept in inventory): "
                              + ", ".join(f"{t} x{n}" for t, n in unsupported.most_common()))
        (triggers if total_top and n_unsupported / total_top > UNSUPPORTED_REVIEW_FRACTION else warnings).append(issue)

    if hidden_count:
        warnings.append(Issue(code="HIDDEN_GEOMETRY_EXCLUDED",
                              message=f"{hidden_count} entities on off/frozen layers or marked invisible were excluded from interpretation."))

    if audit_errors:
        triggers.append(Issue(code="DXF_AUDIT_ERRORS",
                              message=f"The DXF has {len(audit_errors)} structural error(s) that could not be repaired: "
                                      + "; ".join(audit_errors[:5])))

    low = [e for e in model.elements if e.requires_verification]
    if low:
        cats = Counter(e.category for e in low)
        triggers.append(Issue(code="ELEMENTS_REQUIRE_VERIFICATION", element_ids=[e.id for e in low],
                              message=f"{len(low)} interpreted element(s) require human verification: "
                                      + ", ".join(f"{c} x{n}" for c, n in cats.most_common())))

    visible_top = [e for e in model.entities if e.space == "model" and e.visible and e.parent_id is None]
    if visible_top:
        frac = len([i for i in model.unclassified_entity_ids if model.entity(i).parent_id is None]) / len(visible_top)
        if frac > UNCLASSIFIED_REVIEW_FRACTION:
            triggers.append(Issue(code="HIGH_UNCLASSIFIED_FRACTION",
                                  message=f"{frac:.0%} of top-level entities could not be classified."))

    if model.source.converted_from_dwg:
        audit = model.source.conversion_audit or {}
        if audit.get("status") != "ok":
            triggers.append(Issue(code="DWG_CONVERSION_AUDIT_UNAVAILABLE",
                                  message="Could not independently verify that the DWG->DXF conversion kept every entity "
                                          f"({audit.get('note', 'no audit')}). Entities may be missing without notice."))
        elif "significance" not in audit:          # pre-1.6 count-level audit
            if audit.get("lost"):
                triggers.append(Issue(code="DWG_CONVERSION_LOST_ENTITIES",
                                      message="The converted DXF is missing entities that exist in the DWG: "
                                              + ", ".join(f"{t} x{n}" for t, n in audit["lost"].items())
                                              + ". Their geometry is absent from this model."))
        else:
            triggers.extend(conversion_loss_issues(audit, "The converted DXF"))
            if audit["significance"] == "minor":
                warnings.append(Issue(code="DWG_CONVERSION_MINOR_DIFFERENCES", severity="info",
                                      message="DWG->DXF conversion differences judged minor: "
                                              + _loss_text(audit.get("lost_by_significance", {}).get("minor", {}))
                                              + (" (drawing extents header differs)" if any(
                                                  not v["agrees"] for v in audit.get("extents", {}).values()) else "")))
        warnings.append(Issue(code="DWG_CONVERTED",
                              message=f"Geometry was read from a DXF produced by DWG converter "
                                      f"'{model.source.converter.get('name') if model.source.converter else '?'}'. "
                                      "Proxy objects and some custom entities may not survive conversion."))
    return warnings, triggers
