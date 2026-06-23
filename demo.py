"""
Stage 2 — the runtime verify-repair loop.

    design -> audit -> (violations?) -> repair -> re-design ... capped

This is the closed loop from the architecture: nothing leaves it until the
design either passes NFPA 13 or is escalated to a human PE with a clear reason.
Every iteration and every repair is captured in a trace so the reviewer can see
exactly what the loop changed and why.
"""
from __future__ import annotations
import copy
import logging
import os
import sys
from dataclasses import dataclass, field

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from fireai_nfpa13_design_engine import NFPA13DesignEngine  # noqa: E402

from .auditor import audit  # noqa: E402
from .repair import repair_for  # noqa: E402

log = logging.getLogger("fireai.verify_repair")

MAX_ITERATIONS = 4


@dataclass
class LoopResult:
    status: str            # "compliant" | "escalated" | "max_iterations"
    iterations: int
    design: dict
    ctx: dict
    decisions: list        # engineering decisions made by repairs (e.g. pump)
    trace: list            # per-iteration record
    escalation: str = ""

    @property
    def ok(self) -> bool:
        return self.status == "compliant"


def run_design_with_repair(ctx: dict, geo: dict | None = None,
                           max_iterations: int = MAX_ITERATIONS) -> LoopResult:
    ctx = copy.deepcopy(ctx)
    decisions: list = []
    trace: list = []
    design: dict = {}

    for i in range(1, max_iterations + 1):
        design = NFPA13DesignEngine(geo or {}, ctx).design()
        result = audit(design)

        entry = {
            "iteration": i,
            "compliant": result.is_compliant,
            "violations": [f"{v.section}: {v.description[:72]}"
                           for v in result.violations],
            "repairs": [],
        }

        if result.is_compliant:
            trace.append(entry)
            log.info("[loop] converged on iteration %d", i)
            return LoopResult("compliant", i, design, ctx, decisions, trace)

        # Attempt a repair for each critical violation.
        unrepairable = []
        for v in result.violations:
            action = repair_for(v, design, ctx)
            if action is None:
                unrepairable.append(v)
                continue
            ctx.update(action.ctx_delta)
            decisions.append(action.record)
            entry["repairs"].append(action.rationale)
        trace.append(entry)

        # If anything has no safe automated fix, stop and hand it to a human.
        if unrepairable:
            secs = ", ".join(v.section for v in unrepairable)
            return LoopResult(
                "escalated", i, design, ctx, decisions, trace,
                escalation=(f"No safe automated repair for: {secs}. "
                            f"Human PE required."))

    # Ran out of iterations and still not compliant.
    return LoopResult(
        "max_iterations", max_iterations, design, ctx, decisions, trace,
        escalation="Did not converge within the iteration cap. Human PE required.")
