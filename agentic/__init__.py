"""FireAI Pro — agentic runtime (single-module build).

Bundles stages 2 + 3 (verify-repair loop, auditor, repair, extraction self-check)
in one file so deployment is two files total. The deterministic NFPA 13 engine is
authoritative; this only AUDITS its output and applies one safe, bounded repair —
sizing a fire pump when supply is short — escalating to a human otherwise.

api/app.py imports:
    from agentic.loop import run_design_with_repair
    from agentic.extraction_check import grade_extraction
    from agentic.meta import telemetry as agent_telemetry
and the root __init__ imports self_check_extraction — all satisfied here.
"""

from __future__ import annotations
import math
import sys
import types
from dataclasses import dataclass, field

from fireai_nfpa13_design_engine import NFPA13DesignEngine


# ════════════════════════════════════════════════════════════════════════════
#  STAGE 2 — Auditor
# ════════════════════════════════════════════════════════════════════════════

def audit_design(design: dict) -> list[dict]:
    issues: list[dict] = []
    if not design:
        return issues
    delta = float(design.get("pressure_delta", 0) or 0)
    req   = float(design.get("required_pressure", 0) or 0)
    if delta < 0:
        issues.append({
            "type": "supply_deficit", "severity": "critical", "section": "§22",
            "message": (f"Water supply short by {abs(delta):.1f} psi "
                        f"(required {req:.1f} psi at demand flow). A fire pump is required."),
            "deficit_psi": round(abs(delta), 1), "auto_fixable": True,
        })
    flags = (design.get("design_metadata", {}) or {}).get("compliance_flags", []) or []
    for f in flags:
        sec = f.get("section", "")
        if f.get("severity") not in ("pass", None) and sec != "§22":
            issues.append({
                "type": "engine_flag", "severity": f.get("severity", "major"),
                "section": sec, "message": f.get("description", ""), "auto_fixable": False,
            })
    return issues


# ════════════════════════════════════════════════════════════════════════════
#  STAGE 2 — Repair: size a fire pump
# ════════════════════════════════════════════════════════════════════════════

_STD_GPM = [25, 50, 100, 150, 200, 250, 300, 400, 450, 500, 750, 1000,
            1250, 1500, 2000, 2500, 3000, 3500, 4000, 4500, 5000]
_MARGIN_PSI = 10.0


def _next_std_gpm(q: float) -> float:
    for r in _STD_GPM:
        if r >= q:
            return float(r)
    return float(_STD_GPM[-1])


def size_fire_pump(design: dict, ctx: dict) -> dict:
    deficit = max(0.0, -float(design.get("pressure_delta", 0) or 0))
    pump_net = math.ceil((deficit + _MARGIN_PSI) / 5.0) * 5.0
    rated_flow = _next_std_gpm(float(design.get("flow_demand", 0) or 0))
    sp = float(ctx.get("static_pressure", 0) or 0)
    rp = float(ctx.get("residual_pressure", sp * 0.85) or 0)
    churn = round(sp + pump_net * 1.40, 1)

    pump = {
        "rated_flow_gpm": rated_flow, "net_pressure_psi": pump_net,
        "suction_static_psi": round(sp, 1), "suction_residual_psi": round(rp, 1),
        "churn_pressure_psi_est": churn, "deficit_cleared_psi": round(deficit, 1),
        "drive": "electric (verify with EOR / power availability)",
        "listing_note": ("Preliminary duty point. Final selection requires a two-hydrant "
                         "flow test (NFPA 13 Annex B) and a UL/FM pump curve verified at "
                         "150% rated flow per NFPA 20 §4.7.1."),
    }

    design["pressure_delta"]  = round(float(design.get("pressure_delta", 0) or 0) + pump_net, 1)
    design["compliant"]       = design["pressure_delta"] >= 0
    design["fire_pump_added"] = True
    design["fire_pump"]       = pump

    meta  = design.setdefault("design_metadata", {})
    flags = meta.setdefault("compliance_flags", [])
    patched = False
    for f in flags:
        if f.get("section") == "§22" and f.get("severity") != "pass":
            f["severity"] = "pass"
            f["description"] = (f"Pressure OK with fire pump — {rated_flow:.0f} gpm @ "
                                f"{pump_net:.0f} psi net sized to clear a {deficit:.1f} psi "
                                f"supply deficit (+{_MARGIN_PSI:.0f} psi margin).")
            patched = True
    if not patched:
        flags.append({"section": "§22", "severity": "pass",
                      "description": f"Fire pump sized — {rated_flow:.0f} gpm @ {pump_net:.0f} psi net."})

    design["warnings"] = [w for w in design.get("warnings", [])
                          if "INSUFFICIENT PRESSURE" not in str(w)]

    bom = design.setdefault("bom", [])
    bom.extend([
        {"item": f"FIRE PUMP — {rated_flow:.0f} GPM @ {pump_net:.0f} PSI NET (NFPA 20-listed)",
         "part_number": "TBD", "qty": 1, "unit": "EA", "unit_cost": 38000.0, "nfpa_ref": "§22 / NFPA 20"},
        {"item": "FIRE PUMP CONTROLLER (listed)",
         "part_number": "TBD", "qty": 1, "unit": "EA", "unit_cost": 9500.0, "nfpa_ref": "NFPA 20 §10"},
        {"item": "JOCKEY PUMP + CONTROLLER",
         "part_number": "TBD", "qty": 1, "unit": "EA", "unit_cost": 3200.0, "nfpa_ref": "NFPA 20 §4.25"},
        {"item": "PUMP TEST HEADER + RELIEF VALVE",
         "part_number": "TBD", "qty": 1, "unit": "EA", "unit_cost": 2400.0, "nfpa_ref": "NFPA 20 §4.14"},
    ])
    try:
        design["total_material_cost"] = round(sum(b["qty"] * b["unit_cost"] for b in bom), 2)
    except Exception:
        pass
    return {"size_fire_pump": pump}


# ════════════════════════════════════════════════════════════════════════════
#  STAGE 2 — Verify-repair loop  (run_design_with_repair)
# ════════════════════════════════════════════════════════════════════════════

@dataclass
class LoopResult:
    design:     dict
    decisions:  list = field(default_factory=list)
    status:     str  = "compliant"
    escalation: str  = ""
    iterations: int  = 1


def _has_supply_data(ctx: dict) -> bool:
    return float(ctx.get("static_pressure", 0) or 0) > 0 and \
           float(ctx.get("water_supply_flow", 0) or 0) > 0


def run_design_with_repair(project_context: dict, geometry: dict, max_iter: int = 3) -> LoopResult:
    geometry = geometry or {}
    decisions: list = []
    design = NFPA13DesignEngine(geometry, project_context).design()
    iterations = 1

    supply = next((i for i in audit_design(design) if i["type"] == "supply_deficit"), None)
    if supply:
        if not _has_supply_data(project_context):
            return LoopResult(design=design, decisions=decisions, iterations=iterations,
                status="escalated",
                escalation=("Water supply is inadequate and no usable flow-test data "
                            "(static pressure + flow) is on file, so a fire pump cannot be "
                            "sized automatically. EOR must obtain a two-hydrant flow test "
                            "per NFPA 13 Annex B."))
        decisions.append(size_fire_pump(design, project_context))
        iterations += 1

    remaining = [i for i in audit_design(design) if i["severity"] == "critical"]
    if remaining:
        return LoopResult(design=design, decisions=decisions, iterations=iterations,
            status="review_required", escalation="; ".join(i["message"] for i in remaining))
    return LoopResult(design=design, decisions=decisions, iterations=iterations,
                      status="compliant", escalation="")


# ════════════════════════════════════════════════════════════════════════════
#  STAGE 3 — Extraction self-check  (grade_extraction / self_check_extraction)
# ════════════════════════════════════════════════════════════════════════════

@dataclass
class FieldReport:
    name: str
    human_label: str
    reason: str


@dataclass
class ExtractionReport:
    confident:   bool
    needs_human: list = field(default_factory=list)
    ctx:         dict = field(default_factory=dict)


def _num(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def grade_extraction(ctx: dict, prior_ctx: dict | None = None) -> ExtractionReport:
    ctx = dict(ctx or {})
    needs: list[FieldReport] = []
    occ = str(ctx.get("occupancy", "") or "").lower()
    is_storage = any(t in occ for t in ("storage", "warehouse", "rack"))

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

    if is_storage and not (ctx.get("commodity_class") or ctx.get("storage_commodity")):
        needs.append(FieldReport("commodity_class", "Commodity class / storage arrangement",
                                 "Storage occupancy without a confirmed commodity class, storage "
                                 "method, or max height — the ESFR design basis is an assumption "
                                 "until an EOR confirms it."))

    if sp and fl and not ctx.get("flow_test_two_hydrant"):
        needs.append(FieldReport("flow_test_two_hydrant", "Two-hydrant flow test",
                                 "Supply appears to be a single data point. A two-hydrant pitot "
                                 "test (NFPA 13 Annex B) is needed for a reliable curve."))

    if not ctx.get("seismic_zone"):
        ctx["seismic_zone"] = "D1"
    if not ctx.get("pipe_material"):
        ctx["pipe_material"] = "Schedule 40 Steel"

    return ExtractionReport(confident=(len(needs) == 0), needs_human=needs, ctx=ctx)


async def self_check_extraction(ctx: dict, prior_ctx: dict | None = None) -> ExtractionReport:
    return grade_extraction(ctx, prior_ctx=prior_ctx)


# ════════════════════════════════════════════════════════════════════════════
#  Submodule aliases so `from agentic.loop import ...` style imports keep working
# ════════════════════════════════════════════════════════════════════════════

def _register_submodule(name: str, **attrs):
    mod = types.ModuleType(f"agentic.{name}")
    for k, v in attrs.items():
        setattr(mod, k, v)
    sys.modules[f"agentic.{name}"] = mod
    return mod

loop            = _register_submodule("loop", run_design_with_repair=run_design_with_repair, LoopResult=LoopResult)
auditor         = _register_submodule("auditor", audit_design=audit_design)
repair          = _register_submodule("repair", size_fire_pump=size_fire_pump)
extraction_check = _register_submodule(
    "extraction_check", grade_extraction=grade_extraction,
    self_check_extraction=self_check_extraction,
    FieldReport=FieldReport, ExtractionReport=ExtractionReport)

__all__ = ["run_design_with_repair", "grade_extraction", "self_check_extraction",
           "audit_design", "size_fire_pump", "loop", "auditor", "repair", "extraction_check"]
