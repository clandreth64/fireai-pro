"""
CLI entry for the FireAI Pro eval harness.

    python -m evals.run_evals                              run suite, print table
    python -m evals.run_evals --save-baseline evals/baseline.json
    python -m evals.run_evals --baseline evals/baseline.json     gate on regressions
    python -m evals.run_evals --json                      machine-readable output

Exit code: 0 = all invariants pass AND no regressions vs baseline; 1 otherwise.
The meta-loop's "Eval gate" runs this, reads the exit code, and parses --json.
"""
from __future__ import annotations
import argparse
import json
import sys

from . import baseline as bl
from .harness import run_suite


def main() -> None:
    ap = argparse.ArgumentParser(description="FireAI Pro eval harness")
    ap.add_argument("--baseline", help="compare current run against this baseline JSON")
    ap.add_argument("--save-baseline", help="write current metrics as a new baseline JSON")
    ap.add_argument("--json", action="store_true", help="emit the full report as JSON")
    args = ap.parse_args()

    report = run_suite()

    if args.json:
        print(json.dumps(report, indent=2))
    else:
        print(f"\nFireAI eval suite — {report['passed']}/{report['total']} passed\n")
        for r in report["results"]:
            mark = "PASS" if r["passed"] else "FAIL"
            m = r.get("metrics", {})
            print(f"  [{mark}] {r['id']:<26} "
                  f"sprk={str(m.get('total_sprinklers','?')):>5}  "
                  f"req={str(m.get('required_pressure','?')):>6}psi  "
                  f"crit={m.get('critical_flags','?')}")
            for f in r["failures"]:
                print(f"           - {f}")
        print()

    regressed = False
    if args.baseline:
        d = bl.diff(bl.load(args.baseline), bl.metrics_map(report))
        if d["regressions"]:
            regressed = True
            print("REGRESSIONS vs baseline:")
            for pid, why in d["regressions"]:
                print(f"  x {pid}: {why}")
        else:
            print("No regressions vs baseline.")
        if d["changes"]:
            print("\nmetric changes (review, not blocking):")
            for pid, why in d["changes"][:40]:
                print(f"  . {pid}: {why}")
        print()

    if args.save_baseline:
        bl.save(args.save_baseline, report)
        print(f"Baseline saved -> {args.save_baseline}\n")

    sys.exit(0 if (report["failed"] == 0 and not regressed) else 1)


if __name__ == "__main__":
    main()
