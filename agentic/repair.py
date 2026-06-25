"""Stage 2 — Repair: size a fire pump.

Physics: the engine models available pressure at the demand flow as a fit on the
supply curve. A fire pump adds net pressure on top of that curve, so sizing a
pump to deliver (deficit + margin) makes `pressure_delta` non-negative. This is a
deterministic post-process on the engine's own numbers — it does not re-derive
geometry. Final pump selection still requires a real two-hydrant flow test and a
manufacturer pump curve (the design-grounded validator continues to say so);
this just makes the design hydraulically adequate and documents the duty point.
"""

from __future__ import annotations
import math

# NFPA 20 standard rated capacities (gpm)
_STD_GPM = [25, 50, 100, 150, 200, 250, 300, 400, 450, 500, 750, 1000,
            1250, 1500, 2000, 2500, 3000, 3500, 4000, 4500, 5000]

_MARGIN_PSI = 10.0   # engineering safety cushion above the bare deficit


def _next_std_gpm(q: float) -> float:
    for r in _STD_GPM:
        if r >= q:
            return float(r)
    return float(_STD_GPM[-1])


def size_fire_pump(design: dict, ctx: dict) -> dict:
    """Mutate `design` in place to add a sized fire pump; return a decision dict.

    The decision dict is shaped {"size_fire_pump": {...details...}} so the caller
    can read the repair name via next(iter(decision)).
    """
    deficit = max(0.0, -float(design.get("pressure_delta", 0) or 0))
    pump_net = math.ceil((deficit + _MARGIN_PSI) / 5.0) * 5.0      # nearest 5 psi
    rated_flow = _next_std_gpm(float(design.get("flow_demand", 0) or 0))

    sp = float(ctx.get("static_pressure", 0) or 0)
    rp = float(ctx.get("residual_pressure", sp * 0.85) or 0)

    # Churn (shutoff) ≈ ~140% of rated net pressure added to suction static.
    churn = round(sp + pump_net * 1.40, 1)

    pump = {
        "rated_flow_gpm":        rated_flow,
        "net_pressure_psi":      pump_net,
        "suction_static_psi":    round(sp, 1),
        "suction_residual_psi":  round(rp, 1),
        "churn_pressure_psi_est": churn,
        "deficit_cleared_psi":   round(deficit, 1),
        "drive":                 "electric (verify with EOR / power availability)",
        "listing_note":          ("Preliminary duty point. Final selection requires a "
                                  "two-hydrant flow test (NFPA 13 Annex B) and a UL/FM "
                                  "pump curve verified at 150% rated flow per NFPA 20 §4.7.1."),
    }

    # ── Make the design hydraulically adequate ───────────────────────────────
    design["pressure_delta"]  = round(float(design.get("pressure_delta", 0) or 0) + pump_net, 1)
    design["compliant"]       = design["pressure_delta"] >= 0
    design["fire_pump_added"] = True
    design["fire_pump"]       = pump

    # ── Update the §22 compliance flag from critical → pass ──────────────────
    meta  = design.setdefault("design_metadata", {})
    flags = meta.setdefault("compliance_flags", [])
    patched = False
    for f in flags:
        if f.get("section") == "§22" and f.get("severity") != "pass":
            f["severity"]    = "pass"
            f["description"] = (f"Pressure OK with fire pump — {rated_flow:.0f} gpm @ "
                                f"{pump_net:.0f} psi net sized to clear a "
                                f"{deficit:.1f} psi supply deficit (+{_MARGIN_PSI:.0f} psi margin).")
            patched = True
    if not patched:
        flags.append({
            "section": "§22",
            "description": (f"Fire pump sized — {rated_flow:.0f} gpm @ {pump_net:.0f} psi net."),
            "severity": "pass",
        })

    # ── Refresh the human-readable warnings list ─────────────────────────────
    design["warnings"] = [w for w in design.get("warnings", [])
                          if "INSUFFICIENT PRESSURE" not in str(w)]

    # ── Add the pump (and required ancillaries) to the BOM ───────────────────
    bom = design.setdefault("bom", [])
    pump_lines = [
        {"item": f"FIRE PUMP — {rated_flow:.0f} GPM @ {pump_net:.0f} PSI NET (NFPA 20-listed)",
         "part_number": "TBD", "qty": 1, "unit": "EA", "unit_cost": 38000.0, "nfpa_ref": "§22 / NFPA 20"},
        {"item": "FIRE PUMP CONTROLLER (listed)",
         "part_number": "TBD", "qty": 1, "unit": "EA", "unit_cost": 9500.0, "nfpa_ref": "NFPA 20 §10"},
        {"item": "JOCKEY PUMP + CONTROLLER",
         "part_number": "TBD", "qty": 1, "unit": "EA", "unit_cost": 3200.0, "nfpa_ref": "NFPA 20 §4.25"},
        {"item": "PUMP TEST HEADER + RELIEF VALVE",
         "part_number": "TBD", "qty": 1, "unit": "EA", "unit_cost": 2400.0, "nfpa_ref": "NFPA 20 §4.14"},
    ]
    bom.extend(pump_lines)
    try:
        design["total_material_cost"] = round(
            sum(b["qty"] * b["unit_cost"] for b in bom), 2)
    except Exception:
        pass

    return {"size_fire_pump": pump}
