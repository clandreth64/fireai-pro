"""
Repair controller — turns an auditor Violation into a concrete, *real*
engineering action that modifies the design inputs so the next pass can clear
the violation.

A repair returns a RepairAction with:
  * ctx_delta  — changes merged into the project context before re-design
  * rationale  — plain-language description of the engineering decision
  * record     — structured note attached to the design for the PE to review

or None when there is no safe automated fix — in which case the loop escalates
to a human instead of guessing.

Design rule: repairs are legitimate engineering actions (sizing a fire pump to
a computed deficit), never cosmetic tricks to silence the checker. Every repair
is recorded in the trace and surfaced to the human reviewer. The loop fixes the
*cause*; the PE owns the *decision*.
"""
from __future__ import annotations
from dataclasses import dataclass

PUMP_SAFETY_MARGIN_PSI = 10.0


@dataclass
class RepairAction:
    kind: str
    ctx_delta: dict
    rationale: str
    record: dict


def repair_for(violation, design: dict, ctx: dict):
    """Return a RepairAction for this violation, or None to escalate."""
    if violation.kind == "insufficient_supply":
        return _repair_supply(design, ctx)
    # head_spacing and unknown kinds have no safe ctx-level fix in this engine
    # (spacing comes from hazard criteria, not a tunable input). Escalate.
    return None


def _repair_supply(design: dict, ctx: dict):
    """§22 deficit -> size a fire pump to cover it (the real-world fix when
    municipal supply can't meet the remote-area demand)."""
    delta = float(design.get("pressure_delta", 0.0))  # negative => deficit
    if delta >= 0:
        return None
    deficit = abs(delta)
    boost = round(deficit + PUMP_SAFETY_MARGIN_PSI, 1)
    cur_static = float(ctx.get("static_pressure", 0))
    cur_resid = float(ctx.get("residual_pressure", cur_static))
    return RepairAction(
        kind="insufficient_supply",
        ctx_delta={
            "static_pressure":   round(cur_static + boost, 1),
            "residual_pressure": round(cur_resid + boost, 1),
            "fire_pump_added":   True,
        },
        rationale=(
            f"Supply deficit of {deficit:.1f} psi at the remote area. "
            f"Sized a fire pump for +{boost:.1f} psi boost "
            f"(deficit + {PUMP_SAFETY_MARGIN_PSI:.0f} psi safety margin). "
            f"PE to confirm pump selection, suction conditions, and that a "
            f"pump is acceptable to the AHJ."
        ),
        record={"fire_pump": {
            "boost_psi": boost,
            "deficit_psi": round(deficit, 1),
            "reason": "NFPA 13 §22 supply deficit at remote area",
        }},
    )
