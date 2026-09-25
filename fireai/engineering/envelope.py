"""Supported-envelope gate for REAL engineering (Milestone 2.1; extended in M2.2A.1).

The envelope is looked up from the base rule set's standard + edition (data in
``fireai.rules.catalog``), never from a constant. Every envelope condition must be matched by an
EXPLICIT input; a missing input is a refusal, a different value is OUTSIDE_SUPPORTED_ENVELOPE — there
is no fallback to a "nearest" supported condition, and nothing is inferred. An envelope never approves
a rule set: it only bounds what FireAI will attempt.
"""

from __future__ import annotations

from fireai.engineering.design import DesignIssue
from fireai.engineering.geometry import room_frame
from fireai.engineering.measure import boundary_uv, misaligned_segments
from fireai.rules.catalog import envelope_for, envelope_record
from fireai.rules.constraints import BOUNDARY_MEASUREMENTS


def _region(req):
    pkg = req.package
    sem = {s.uid: s for s in pkg.semantic_spaces}
    regions = {s.uid: s for s in pkg.spaces}
    space = sem.get(req.space_uid)
    return regions.get(space.region_uid) if space else regions.get(req.space_uid)


def envelope_blockers(req, rule_sets) -> list[DesignIssue]:
    bases = [s for s in rule_sets if s.layer == "base_standard" and s.content_basis == "authoritative"]
    if len(bases) != 1:
        return []                                  # the rules policy already refuses (no / several bases)
    b = bases[0]
    env = envelope_for(b.governing_standard, b.edition)
    if env is None:
        rec = envelope_record(b.governing_standard, b.edition)
        why = (f"envelope {rec.envelope_id} is {rec.status}" if rec is not None
               else "no development envelope is registered")
        return [DesignIssue(code="NO_SUPPORTED_ENVELOPE",
                            message=f"FireAI does not support engineering under {b.governing_standard} {b.edition} "
                                    f"yet ({why})", detail={"envelope": rec.envelope_id if rec else None})]
    out: list[DesignIssue] = []

    def outside(condition, value, allowed):
        out.append(DesignIssue(code="OUTSIDE_SUPPORTED_ENVELOPE",
                               message=f"{condition} {value!r} is outside envelope {env.envelope_id} "
                                       f"(supported: {allowed}); no fallback is applied",
                               detail={"condition": condition, "value": value, "envelope": env.envelope_id}))

    def missing(code, message):
        out.append(DesignIssue(code=code, message=message, detail={"envelope": env.envelope_id}))

    if req.system is None:
        out.append(DesignIssue(code="MISSING_SYSTEM_CONDITION", message="no system type / storage condition supplied"))
    else:
        if req.system.system_type not in env.system_types:
            outside("system type", req.system.system_type, env.system_types)
        if req.system.storage not in env.storage_conditions:
            outside("storage condition", req.system.storage, env.storage_conditions)
        if env.design_methods is not None:
            if req.system.design_method == "unknown":
                missing("MISSING_DESIGN_METHOD", f"envelope {env.envelope_id} requires an explicit system design method")
            elif req.system.design_method not in env.design_methods:
                outside("design method", req.system.design_method, env.design_methods)
    if req.classification is not None and (req.classification.scheme, req.classification.value) not in env.classifications:
        outside("classification", f"{req.classification.scheme}: {req.classification.value}", env.classifications)
    if req.listing is not None:
        if req.listing.sprinkler_type not in env.sprinkler_types:
            outside("sprinkler type", req.listing.sprinkler_type, env.sprinkler_types)
        if req.listing.orientation not in env.orientations:
            outside("sprinkler orientation", req.listing.orientation, env.orientations)
        if env.installation_styles is not None:
            if req.listing.installation_style is None:
                missing("MISSING_INSTALLATION_STYLE", f"envelope {env.envelope_id} requires the listing's installation "
                                                      "style (exposed / recessed / flush / concealed)")
            elif req.listing.installation_style not in env.installation_styles:
                outside("installation style", req.listing.installation_style, env.installation_styles)
    if req.ceiling is not None:
        for r in req.ceiling.regions:
            if r.surface not in env.ceiling_surfaces:
                outside("ceiling surface", r.surface, env.ceiling_surfaces)
            if r.construction not in env.ceiling_constructions:
                outside("ceiling construction", r.construction, env.ceiling_constructions)
            if env.construction_classifications is not None:
                cc = r.construction_classification
                if cc is None:
                    missing("MISSING_CONSTRUCTION_CLASSIFICATION",
                            f"envelope {env.envelope_id} requires an explicit construction classification for "
                            f"ceiling region {r.uid} (geometry does not establish it)")
                elif (cc.scheme, cc.value) not in env.construction_classifications:
                    outside("construction classification", f"{cc.scheme}: {cc.value}", env.construction_classifications)
            if env.requires_horizontal_ceiling:
                if r.slope.status != "known" or (r.slope.value_deg or 0.0) != 0.0:
                    outside("ceiling slope", r.slope.value_deg if r.slope.status == "known" else "unknown", [0.0])
                if r.elevation.status != "known":
                    missing("MISSING_CEILING_ELEVATION", f"envelope {env.envelope_id} requires a known ceiling elevation")
        if env.requires_horizontal_ceiling and len(req.ceiling.regions) != 1:
            outside("ceiling planes", len(req.ceiling.regions), [1])
        if env.requires_horizontal_ceiling and req.ceiling.features:
            outside("ceiling features", sorted({f.kind for f in req.ceiling.features}), [])
        if req.ceiling.obstructions_statement not in env.obstruction_statements:
            outside("obstruction statement", req.ceiling.obstructions_statement, env.obstruction_statements)
    if env.small_room_statuses is not None:
        dec = req.eligibility.get("small_room")
        status = dec.status if dec is not None else "not_determined"
        if status not in env.small_room_statuses:
            outside("small-room eligibility", status, env.small_room_statuses)
    if env.requires_branch_line_orientation and req.orientation is None:
        missing("MISSING_LAYOUT_ORIENTATION",
                f"envelope {env.envelope_id} requires an explicit branch-line orientation (LayoutOrientation)")
    if env.search_families is not None and req.search is not None and req.search.family not in env.search_families:
        outside("placement search family", req.search.family, env.search_families)
    if env.installation_contexts is not None:
        ctx = req.installation.kind if req.installation is not None else "unknown"
        if ctx == "unknown":
            missing("MISSING_INSTALLATION_CONTEXT", f"envelope {env.envelope_id} requires an explicit installation "
                                                    "context (new system / modification / replacement)")
        elif ctx not in env.installation_contexts:
            outside("installation context", ctx, env.installation_contexts)
    if env.required_fact_rules is not None:
        facts_with_rules = {r.constraint.fact_requirement.fact for rs in list(rule_sets)
                            + ([req.listing.rules] if req.listing is not None else [])
                            for r in rs.rules if r.constraint is not None and r.constraint.fact_requirement is not None}
        for fact in env.required_fact_rules:
            if fact == "sprinkler.response" and req.listing is not None and not req.listing.response_type:
                missing("MISSING_RESPONSE_TYPE", f"envelope {env.envelope_id} requires the listing's response type")
            if fact not in facts_with_rules:
                missing("REQUIRED_FACT_RULE_MISSING",
                        f"envelope {env.envelope_id} requires an approved rule evaluating {fact!r}; the rule stack has "
                        "none, so the fact would go unchecked")
    if env.perimeter_boundary_kinds is not None:
        region = _region(req)
        if region is not None:
            other = sorted({s.kind for ring in region.boundary.rings for s in ring.segments}
                           - set(env.perimeter_boundary_kinds))
            if other:
                outside("perimeter boundary kinds", other,
                        f"{env.perimeter_boundary_kinds} only (LIMITATION: door openings, open openings, windows and "
                        "unknown segments are not wall references in this envelope; their treatment is deferred)")
    if env.wall_reference_kinds is not None:
        stack = list(rule_sets) + ([req.listing.rules] if req.listing is not None else [])
        for rs in stack:
            for r in rs.rules:
                c = r.constraint
                if c is not None and c.measurement in BOUNDARY_MEASUREMENTS \
                        and sorted(c.reference_kinds) != sorted(env.wall_reference_kinds):
                    outside("rule wall reference kinds", f"{r.rule_id}: {sorted(c.reference_kinds)}",
                            env.wall_reference_kinds)
    if env.requires_orthogonal_geometry and req.tolerances is not None:
        region = _region(req)
        if region is not None:
            segs = [s for ring in region.boundary.rings for s in ring.segments]
            frame = room_frame([tuple(p) for p in region.polygon_local_ft])
            angled = misaligned_segments(boundary_uv(frame, segs), req.tolerances.length_ft)
            if angled:
                outside("space geometry", f"{len(angled)} angled / irregular boundary segment(s)",
                        "orthogonal straight walls only")
    return out
