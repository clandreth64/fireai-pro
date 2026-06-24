"""Stage 2 — Verify-repair loop.

design → audit → repair → re-audit, bounded, with escalation to a human when
there is no safe automated fix. This is the entrypoint api/app.py imports:

    from agentic.loop import run_design_with_repair

Returns a LoopResult with the (possibly repaired) design plus a record of every
engineering decision taken, so the meta-loop and the UI can show exactly what the
system changed and why.
"""

from __future__ import annotations
from dataclasses import dataclass, field

from fireai_nfpa13_design_engine import NFPA13DesignEngine
from .auditor import audit_design
from .repair import size_fire_pump


@dataclass
class LoopResult:
    design:     dict
    decisions:  list = field(default_factory=list)   # [{"size_fire_pump": {...}}, ...]
    status:     str  = "compliant"                    # compliant | review_required | escalated
    escalation: str  = ""
    iterations: int  = 1


def _has_supply_data(ctx: dict) -> bool:
    sp = float(ctx.get("static_pressure", 0) or 0)
    fl = float(ctx.get("water_supply_flow", 0) or 0)
    return sp > 0 and fl > 0


def run_design_with_repair(project_context: dict, geometry: dict,
                           max_iter: int = 3) -> LoopResult:
    geometry = geometry or {}
    decisions: list = []

    # ── Initial deterministic design ─────────────────────────────────────────
    design = NFPA13DesignEngine(geometry, project_context).design()
    iterations = 1

    issues = audit_design(design)
    supply = next((i for i in issues if i["type"] == "supply_deficit"), None)

    # ── Repair: size a fire pump for a supply deficit (the one safe fix) ──────
    if supply:
        if not _has_supply_data(project_context):
            return LoopResult(
                design=design, decisions=decisions, iterations=iterations,
                status="escalated",
                escalation=("Water supply is inadequate and no usable flow-test data "
                            "(static pressure + flow) is on file, so a fire pump cannot "
                            "be sized automatically. EOR must obtain a two-hydrant flow "
                            "test per NFPA 13 Annex B."))
        decisions.append(size_fire_pump(design, project_context))
        iterations += 1

    # ── Re-audit after repair ────────────────────────────────────────────────
    remaining = [i for i in audit_design(design) if i["severity"] == "critical"]
    if remaining:
        return LoopResult(
            design=design, decisions=decisions, iterations=iterations,
            status="review_required",
            escalation="; ".join(i["message"] for i in remaining))

    return LoopResult(design=design, decisions=decisions,
                      iterations=iterations, status="compliant", escalation="")
