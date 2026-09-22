"""Compare two corpus runs (anonymized metrics) side by side as Markdown.

Usage: python scripts/compare_runs.py baseline_m1 tuned_m15 > comparison.md
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

RUNS = Path(__file__).resolve().parent.parent / "tests" / "real_drawings" / "runs"


def load(label):
    d = json.loads((RUNS / f"{label}.json").read_text())
    return d, {f"{r['id']}.{r['format']}": r for r in d["results"]}


def cell(r, key):
    if r is None:
        return "—"
    v = r.get(key)
    if key == "element_counts":
        v = v or {}
        return ", ".join(f"{k}:{n}" for k, n in sorted(v.items()) if k != "text_annotation") or "none"
    if key == "rooms":
        v = v or []
        lab = sum(1 for x in v if x.get("labeled"))
        return f"{len(v)} ({lab} labeled)"
    if key == "review_triggers":
        return ", ".join(sorted(set(v or []))) or "none"
    if key == "status":
        return f"{r.get('processing_status')}{' ' + r['failure_code'] if r.get('failure_code') else ''}"
    if key == "perf":
        return f"{r.get('total_seconds', '?')} s / {r.get('peak_rss_mb', '?')} MB"
    if key == "regions":
        regs = r.get("view_regions") or []
        return f"{sum(1 for x in regs if x.get('significant'))} sig / {len(regs)}" if regs else "n/a"
    return "—" if v is None else str(v)


def main():
    a_label, b_label = sys.argv[1], sys.argv[2]
    (a, ra), (b, rb) = load(a_label), load(b_label)
    print(f"# Corpus comparison: `{a_label}` ({a['engine_commit'][:12]}) vs `{b_label}` ({b['engine_commit'][:40]})\n")
    rows = [("status", "Status"), ("element_counts", "Elements (excl. text)"), ("rooms", "Rooms"),
            ("unclassified_pct_of_visible_top", "Unclassified %"), ("regions", "View regions"),
            ("review_triggers", "Review triggers"), ("perf", "Time / peak RSS")]
    for key in sorted(set(ra) | set(rb)):
        print(f"## {key}\n\n| | {a_label} | {b_label} |\n|---|---|---|")
        for k, name in rows:
            print(f"| {name} | {cell(ra.get(key), k)} | {cell(rb.get(key), k)} |")
        lost = (rb.get(key) or {}).get("conversion_audit_lost")
        if lost is not None:
            print(f"| DWG conversion loss (audit) | n/a | {lost or 'none'} |")
        sug = (rb.get(key) or {}).get("unit_evidence_suggestion")
        if sug:
            print(f"| Unit evidence (suggestion only) | n/a | {sug} |")
        print()


if __name__ == "__main__":
    main()
