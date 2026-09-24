"""Supported-envelope gate for REAL engineering (Milestone 2.1).

The envelope is looked up from the base rule set's standard + edition (data in
``fireai.rules.catalog``), never from a constant. Every envelope condition must be matched by an
EXPLICIT input; a missing input is a refusal, a different value is OUTSIDE_SUPPORTED_ENVELOPE — there
is no fallback to a "nearest" supported condition, and nothing is inferred.
"""

from __future__ import annotations

from fireai.engineering.design import DesignIssue
from fireai.rules.catalog import envelope_for


def envelope_blockers(req, rule_sets) -> list[DesignIssue]:
    bases = [s for s in rule_sets if s.layer == "base_standard" and s.content_basis == "authoritative"]
    if len(bases) != 1:
        return []                                  # the rules policy already refuses (no / several bases)
    b = bases[0]
    env = envelope_for(b.governing_standard, b.edition)
    if env is None:
        return [DesignIssue(code="NO_SUPPORTED_ENVELOPE",
                            message=f"FireAI has no supported development envelope for {b.governing_standard} "
                                    f"{b.edition}; real design is not supported under it yet")]
    out: list[DesignIssue] = []

    def outside(condition, value, allowed):
        out.append(DesignIssue(code="OUTSIDE_SUPPORTED_ENVELOPE",
                               message=f"{condition} {value!r} is outside envelope {env.envelope_id} "
                                       f"(supported: {allowed}); no fallback is applied",
                               detail={"condition": condition, "value": value, "envelope": env.envelope_id}))

    if req.system is None:
        out.append(DesignIssue(code="MISSING_SYSTEM_CONDITION", message="no system type / storage condition supplied"))
    else:
        if req.system.system_type not in env.system_types:
            outside("system type", req.system.system_type, env.system_types)
        if req.system.storage not in env.storage_conditions:
            outside("storage condition", req.system.storage, env.storage_conditions)
    if req.classification is not None and (req.classification.scheme, req.classification.value) not in env.classifications:
        outside("classification", f"{req.classification.scheme}: {req.classification.value}", env.classifications)
    if req.listing is not None:
        if req.listing.sprinkler_type not in env.sprinkler_types:
            outside("sprinkler type", req.listing.sprinkler_type, env.sprinkler_types)
        if req.listing.orientation not in env.orientations:
            outside("sprinkler orientation", req.listing.orientation, env.orientations)
    if req.ceiling is not None:
        for r in req.ceiling.regions:
            if r.surface not in env.ceiling_surfaces:
                outside("ceiling surface", r.surface, env.ceiling_surfaces)
            if r.construction not in env.ceiling_constructions:
                outside("ceiling construction", r.construction, env.ceiling_constructions)
        if req.ceiling.obstructions_statement not in env.obstruction_statements:
            outside("obstruction statement", req.ceiling.obstructions_statement, env.obstruction_statements)
    return out
