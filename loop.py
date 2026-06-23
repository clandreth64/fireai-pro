"""
Demo: the extraction self-check sub-agent on two extractions —

  1. a clean vector set (everything found, plausible)        -> confident
  2. a Costco-like scanned set (sparse, supply missing)      -> needs human

For the confident case we chain straight into the stage-2 verify-repair loop,
showing the full runtime plane: extract-check -> design-with-repair.

Uses grade_extraction (pure, no API) so it runs anywhere.

    python -m agentic.demo_extraction
"""
from __future__ import annotations
from .extraction_check import grade_extraction
from .loop import run_design_with_repair


def _show(title, extracted):
    print(f"\n=== {title} ===")
    report = grade_extraction(extracted)
    print(f"verdict: {report.summary()}\n")
    for fr in report.fields.values():
        flag = {"ok": " ", "repaired": "~", "needs_human": "!"}[fr.status]
        print(f"  [{flag}] {fr.name:<18} {str(fr.value):<14} "
              f"{fr.confidence:<7} {fr.reason}")
    if report.needs_human:
        print("\n  A person must confirm before designing:")
        for fr in report.needs_human:
            print(f"    - {fr.human_label}: {fr.reason}")
    return report


# 1. Clean vector set — full, plausible
clean = {
    "occupancy": "warehouse storage",
    "total_area": 60000,
    "ceiling_height": 30,
    "static_pressure": 95,
    "residual_pressure": 82,
    "water_supply_flow": 2500,
    "seismic_zone": "D1",
}

# 2. Costco-like scanned set — occupancy + a guess at area survived OCR,
#    but ceiling and the whole flow-test trio never came through.
costco_like = {
    "occupancy": "warehouse",
    "total_area": 0,
    "ceiling_height": None,
    "static_pressure": None,
    "residual_pressure": None,
    "water_supply_flow": None,
    "seismic_zone": None,
}


if __name__ == "__main__":
    r1 = _show("clean vector set", clean)
    if r1.confident:
        print("\n  -> confident: handing ctx to the verify-repair loop...")
        loop = run_design_with_repair(r1.ctx)
        d = loop.design
        print(f"     design loop: {loop.status} | "
              f"sprinklers={d.get('design_metadata',{}).get('total_sprinklers')} | "
              f"compliant={d.get('compliant')}")

    r2 = _show("Costco-like scanned set", costco_like)
    if not r2.confident:
        print("\n  -> NOT confident: design loop is blocked until the fields "
              "above are confirmed. No guessed design ships.")
