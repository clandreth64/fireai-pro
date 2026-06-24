"""Stage 3 — Extraction self-check.

Grade the assembled project context before design. Fields that came back missing
or implausible get flagged for the user to confirm; safe defaults (e.g. seismic
D1) are filled without clobbering real values. We do NOT block design — the
verify-repair loop escalates on a true §22 supply absence — but the user is told
exactly what to verify. api/app.py imports:

    from agentic.extraction_check import grade_extraction
"""

from __future__ import annotations
from dataclasses import dataclass, field


@dataclass
class FieldReport:
    name:        str
    human_label: str
    reason:      str


@dataclass
class ExtractionReport:
    confident:   bool
    needs_human: list = field(default_factory=list)   # [FieldReport, ...]
    ctx:         dict = field(default_factory=dict)    # context with safe defaults applied


def _num(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def grade_extraction(ctx: dict, prior_ctx: dict | None = None) -> ExtractionReport:
    ctx = dict(ctx or {})
    prior_ctx = prior_ctx or {}
    needs: list[FieldReport] = []

    occ = str(ctx.get("occupancy", "") or "").lower()
    is_storage = any(t in occ for t in ("storage", "warehouse", "rack"))

    # ── Presence + plausibility of design-critical fields ────────────────────
    area = _num(ctx.get("total_area"))
    if not area or area <= 0:
        needs.append(FieldReport("total_area", "Total floor area (sq ft)",
                                 "Missing or zero — required to size the system."))
    elif area > 2_000_000:
        needs.append(FieldReport("total_area", "Total floor area (sq ft)",
                                 f"Implausibly large ({area:,.0f} sf) — please confirm."))

    ch = _num(ctx.get("ceiling_height"))
    if not ch or ch <= 0:
        needs.append(FieldReport("ceiling_height", "Ceiling height (ft)",
                                 "Missing — drives sprinkler selection and pressure."))
    elif ch > 60:
        needs.append(FieldReport("ceiling_height", "Ceiling height (ft)",
                                 f"Unusually high ({ch:.0f} ft) — please confirm."))

    if not occ:
        needs.append(FieldReport("occupancy", "Occupancy / use",
                                 "Missing — required to set the hazard / design basis."))

    # Supply trio — all three matter for hydraulics + pump sizing
    sp = _num(ctx.get("static_pressure"))
    rp = _num(ctx.get("residual_pressure"))
    fl = _num(ctx.get("water_supply_flow"))
    if not sp or sp <= 0:
        needs.append(FieldReport("static_pressure", "Static pressure (psi)",
                                 "Missing — needed for the supply curve."))
    if not fl or fl <= 0:
        needs.append(FieldReport("water_supply_flow", "Water supply flow at residual (gpm)",
                                 "Missing — needed for the supply curve / pump sizing."))
    if sp and rp and rp > sp:
        needs.append(FieldReport("residual_pressure", "Residual pressure (psi)",
                                 f"Residual ({rp:.0f}) exceeds static ({sp:.0f}) — check the flow test."))

    # Storage occupancies need a commodity basis or the design basis is an assumption
    if is_storage and not (ctx.get("commodity_class") or ctx.get("storage_commodity")):
        needs.append(FieldReport("commodity_class", "Commodity class / storage arrangement",
                                 "Storage occupancy without a confirmed commodity class, "
                                 "storage method, or max height — the ESFR design basis is "
                                 "an assumption until an EOR confirms it."))

    # Single-point supply (only one data pair) is unreliable for permit
    if sp and fl and not ctx.get("flow_test_two_hydrant"):
        needs.append(FieldReport("flow_test_two_hydrant", "Two-hydrant flow test",
                                 "Supply appears to be a single data point. A two-hydrant "
                                 "pitot test (NFPA 13 Annex B) is needed for a reliable curve."))

    # ── Safe defaults (never clobber a real value) ───────────────────────────
    if not ctx.get("seismic_zone"):
        ctx["seismic_zone"] = "D1"   # conservative default for CA/NV/AZ territory
    if not ctx.get("pipe_material"):
        ctx["pipe_material"] = "Schedule 40 Steel"

    return ExtractionReport(confident=(len(needs) == 0), needs_human=needs, ctx=ctx)


async def self_check_extraction(ctx: dict, prior_ctx: dict | None = None) -> ExtractionReport:
    """Async wrapper around grade_extraction for call sites inside async jobs.

    grade_extraction is pure/CPU-light, so this simply awaits it on the event
    loop's default executor-free path; it exists so async callers don't block on
    a sync import boundary and so the public surface matches the rest of the
    agentic API.
    """
    return grade_extraction(ctx, prior_ctx=prior_ctx)
