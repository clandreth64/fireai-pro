"""
Demo: run the verify-repair loop on a deliberately starved supply and watch it
detect the §22 deficit, size a fire pump, and converge — or escalate.

    python -m agentic.demo
"""
from __future__ import annotations
from .loop import run_design_with_repair


def _print(result):
    print(f"\nstatus: {result.status.upper()}  (iterations: {result.iterations})\n")
    for e in result.trace:
        tag = "COMPLIANT" if e["compliant"] else "violations"
        print(f"  iteration {e['iteration']}: {tag}")
        for v in e["violations"]:
            print(f"     ! {v}")
        for r in e["repairs"]:
            print(f"     ~ repair: {r}")
    if result.decisions:
        print("\nengineering decisions (for PE review):")
        for d in result.decisions:
            print(f"  - {d}")
    if result.escalation:
        print(f"\nescalation: {result.escalation}")
    d = result.design
    md = d.get("design_metadata", {})
    print(f"\nfinal: compliant={d.get('compliant')} "
          f"sprinklers={md.get('total_sprinklers')} "
          f"required={round(float(d.get('required_pressure',0)),1)}psi "
          f"delta={round(float(d.get('pressure_delta',0)),1)}psi")


if __name__ == "__main__":
    starved = {
        "occupancy": "warehouse storage",
        "total_area": 80000,
        "ceiling_height": 35,
        "static_pressure": 38,
        "residual_pressure": 28,
        "water_supply_flow": 450,
        "seismic_zone": "D1",
        "pipe_material": "Schedule 40 Steel",
    }
    print("=== starved ESFR warehouse — expect deficit -> fire pump -> converge ===")
    _print(run_design_with_repair(starved))
