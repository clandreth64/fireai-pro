"""Human ground-truth review summary (LOCAL: it contains drawing content such as room names).

    python scripts/gt_review_summary.py            # writes tests/real_drawings_outputs_local/gt_review_summary.md

Shows which drawings await review, are partially or fully reviewed; per-category counts of
CONFIRMED / CORRECTED / NOT_EVALUATED; open questions; and FireAI-vs-HUMAN agreement per
category. FireAI is compared ONLY with human truth. There is no single accuracy score.
"""

from __future__ import annotations

import json
import sys
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tests" / "real_drawings"))
import fireai_values as FV  # noqa: E402
import gt as G  # noqa: E402


def build_summary(gt_dir: Path = G.GT_DIR, public_dir: Path = G.PUBLIC_REVIEWS, private_dir: Path = G.PRIVATE_REVIEWS,
                  outputs: Path = FV.OUTPUTS, corpus_lookup=G.corpus_entry) -> dict:
    drawings = []
    decisions: dict[str, Counter] = defaultdict(Counter)
    agreement: dict[str, Counter] = defaultdict(Counter)
    disagreements, questions = [], []
    for gid in G.list_ids(gt_dir):
        rec, entry = G.load_record(gid, gt_dir), corpus_lookup(gid)
        path = G.review_path(gid, entry["private"], public_dir, private_dir)
        review = json.loads(path.read_text(encoding="utf-8")) if path.is_file() else None
        eff = G.effective(rec, review, entry["sha256"])
        items = (review or {}).get("items", {}) if eff["status"] != "INVALIDATED" else {}
        for cat in G.CATEGORIES:
            decisions[cat][items[cat]["human_decision"] if cat in items else "NOT_REVIEWED"] += 1
        for cat, q in eff.get("open_questions", {}).items():
            questions.append({"id": gid, "category": cat, "question": q})
        editions = {}
        for ed, mp in FV.latest_models(gid, outputs).items():
            mv = FV.machine_values(FV.load_model_dict(mp))
            res = {}
            for cat in G.CATEGORIES:
                r = FV.compare(cat, mv.get(cat), eff["truth"].get(cat))
                res[cat] = r
                agreement[cat][r] += 1
                if r == "disagree":
                    disagreements.append({"id": gid, "edition": ed, "category": cat, "fireai": mv.get(cat),
                                          "human": eff["truth"][cat]["value"]})
            editions[ed] = {"model": str(mp.relative_to(outputs)) if mp.is_relative_to(outputs) else str(mp),
                            "comparison": res}
        drawings.append({"id": gid, "description": entry["description"], "private": entry["private"],
                         "status": eff["status"], "reason": eff.get("reason"), "reviewer": eff.get("reviewer"),
                         "reviewed_at": eff.get("reviewed_at"), "not_reviewed": eff["not_reviewed"],
                         "editions": editions})
    by_status = Counter(d["status"] for d in drawings)
    return {"drawings": drawings, "by_status": dict(by_status), "decisions": {c: dict(v) for c, v in decisions.items()},
            "agreement": {c: dict(v) for c, v in agreement.items()}, "disagreements": disagreements,
            "open_questions": questions}


def to_markdown(s: dict) -> str:
    L = ["# Ground-truth review summary (local — contains drawing content)", "",
         "FireAI is compared **only with human truth** (CONFIRMED or CORRECTED). Claude's drafts are never a "
         "reference. There is **no overall accuracy score**: read each category on its own.", "",
         "## Drawings", "", "| Drawing | Status | Reviewer (unauthenticated) | Categories not yet reviewed |",
         "|---|---|---|---|"]
    for d in s["drawings"]:
        L.append(f"| {d['id']} | {d['status']}{' — ' + d['reason'] if d.get('reason') else ''} | "
                 f"{d.get('reviewer') or '—'} | {len(d['not_reviewed'])} |")
    L += ["", "Status counts: " + ", ".join(f"{k}: {v}" for k, v in sorted(s["by_status"].items())), "",
          "## Human decisions by category", "",
          "| Category | CONFIRMED | CORRECTED | NOT_EVALUATED | not reviewed |", "|---|---:|---:|---:|---:|"]
    for cat in G.CATEGORIES:
        c = s["decisions"].get(cat, {})
        L.append(f"| {cat} | {c.get('CONFIRMED', 0)} | {c.get('CORRECTED', 0)} | {c.get('NOT_EVALUATED', 0)} | "
                 f"{c.get('NOT_REVIEWED', 0)} |")
    L += ["", "## FireAI vs human, by category (per drawing file)", "",
          "| Category | agree | disagree | not comparable | no human truth yet |", "|---|---:|---:|---:|---:|"]
    for cat in G.CATEGORIES:
        a = s["agreement"].get(cat, {})
        L.append(f"| {cat} | {a.get('agree', 0)} | {a.get('disagree', 0)} | {a.get('not_comparable', 0)} | "
                 f"{a.get('no_human_truth', 0)} |")
    L += ["", "## Disagreements", ""]
    L += [f"- {d['id']} ({d['edition']}) **{d['category']}**: FireAI `{json.dumps(d['fireai'], default=str)}` vs "
          f"human `{json.dumps(d['human'], default=str)}`" for d in s["disagreements"]] or ["None (or no human truth yet)."]
    L += ["", "## Open questions", ""]
    L += [f"- {q['id']} {q['category']}: {q['question']}" for q in s["open_questions"]] or ["None recorded."]
    return "\n".join(L) + "\n"


def main():
    s = build_summary()
    out = FV.OUTPUTS / "gt_review_summary.md"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(to_markdown(s), encoding="utf-8")
    print(to_markdown(s))
    print(f"(written to {out.relative_to(ROOT)} — local only)")


if __name__ == "__main__":
    main()
