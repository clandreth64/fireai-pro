"""Human review summary and engineering-meaning metrics (LOCAL: contains drawing content).

    python scripts/gt_review_summary.py      # writes tests/real_drawings_outputs_local/gt_review_summary.md

Reports review progress, open questions, and — only where a person has judged the CURRENT FireAI
output — metrics defined by engineering meaning (not CAD counts). Each metric is reported per
drawing as numerator / denominator. There is deliberately no combined accuracy score.
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

METRICS = {
    "view_classification": "view types FireAI assigned vs the human's view types (per drawing: agree / disagree)",
    "false_room_rate": "FireAI rooms the human flagged as not a room / wrong room, over FireAI rooms",
    "missed_room_rate": "rooms the human marked missing, over rooms the human considers present",
    "room_label_association": "FireAI rooms with a wrong label flag, over labelled FireAI rooms",
    "room_boundary_correctness": "FireAI rooms flagged wrong boundary or merged spaces, over FireAI rooms",
    "opening_recognition": "human evaluation of doors/openings + missing door/opening markers",
    "major_wall_geometry": "human evaluation of major walls + missing wall markers",
    "false_confident_interpretation_rate": "flagged FireAI items that FireAI presented as confident, over flagged items",
    "critical_unflagged_error_rate": "flagged FireAI items FireAI itself did not flag for review, over flagged items",
}


def _ratio(n, d):
    return {"n": n, "d": d, "text": f"{n}/{d}" if d else "n/a"}


def drawing_metrics(eff: dict, mv: dict, canvas: dict) -> dict:
    """Metrics for one drawing from CURRENT human evaluations + visual flags. Empty if none."""
    if not eff.get("evaluations") and not eff.get("visual_flags"):
        return {}
    flags = eff.get("visual_flags", [])
    items = {i["uid"]: i for i in canvas["items"]}
    rooms = [i for i in canvas["items"] if i["category"] == "room"]
    by_kind = defaultdict(set)
    for f in flags:
        by_kind[f["flag"]].add(f.get("target_uid") or tuple(f.get("point_local") or []))
    false_rooms = {u for k in ("not_a_room", "wrong_room") for u in by_kind[k] if u in items}
    missing_rooms = len(by_kind["missing_room"])
    present = len(rooms) - len(false_rooms) + missing_rooms
    targeted = [items[f["target_uid"]] for f in flags if f.get("target_uid") in items]
    ev = eff.get("evaluations", {})
    out = {
        "false_room_rate": _ratio(len(false_rooms), len(rooms)),
        "missed_room_rate": _ratio(missing_rooms, present),
        "room_label_association": _ratio(len({u for u in by_kind["wrong_label"] if u in items}),
                                         len([r for r in rooms if r.get("label")])),
        "room_boundary_correctness": _ratio(len({u for k in ("wrong_boundary", "merged_spaces")
                                                 for u in by_kind[k] if u in items}), len(rooms)),
        "opening_recognition": {"evaluation": (ev.get("doors_openings") or {}).get("decision"),
                                "missing_markers": len(by_kind["missing_door_or_opening"])},
        "major_wall_geometry": {"evaluation": (ev.get("walls") or {}).get("decision"),
                                "missing_markers": len(by_kind["missing_wall"])},
        "false_confident_interpretation_rate": _ratio(sum(1 for i in targeted if i["confident"]), len(targeted)),
        "critical_unflagged_error_rate": _ratio(sum(1 for i in targeted if not i["flagged_by_fireai"]), len(targeted)),
        "critical_corrections": sum(1 for e in ev.values()
                                    if e["decision"] == "CORRECTED" and (e.get("correction") or {}).get("severity") == "critical"),
    }
    vt = eff["truth"].get("view_types")
    out["view_classification"] = FV.compare_fact("view_types", mv.get("view_types"), vt)
    return out


def build_summary(gt_dir: Path = G.GT_DIR, public_dir: Path = G.PUBLIC_REVIEWS, private_dir: Path = G.PRIVATE_REVIEWS,
                  outputs: Path = FV.OUTPUTS, corpus_lookup=G.corpus_entry) -> dict:
    drawings, questions = [], []
    decisions: dict[str, Counter] = defaultdict(Counter)
    facts_agreement: dict[str, Counter] = defaultdict(Counter)
    for gid in G.list_ids(gt_dir):
        rec, entry = G.load_record(gid, gt_dir), corpus_lookup(gid)
        path = G.review_path(gid, entry["private"], public_dir, private_dir)
        review = json.loads(path.read_text(encoding="utf-8")) if path.is_file() else None
        ed, mp = FV.preferred_model(gid, outputs)
        eff = G.effective(rec, review, entry["sha256"], **FV.currency(mp, review, outputs))
        items = (review or {}).get("items", {}) if eff["status"] != "INVALIDATED" else {}
        for q in G.QUESTIONS:
            decisions[q][items[q]["human_decision"] if q in items else "NOT_REVIEWED"] += 1
        for q, text in eff.get("open_questions", {}).items():
            questions.append({"id": gid, "question": q, "text": text})
        mv, metrics, fact_cmp = {}, {}, {}
        if mp:
            m = FV.load_model_dict(mp)
            mv = FV.machine_facts(m)
            for q in G.FACTS:
                r = FV.compare_fact(q, mv.get(q), eff["truth"].get(q))
                fact_cmp[q] = r
                facts_agreement[q][r] += 1
            metrics = drawing_metrics(eff, mv, FV.canvas(m))
        drawings.append({"id": gid, "status": eff["status"], "reason": eff.get("reason"),
                         "reviewer": eff.get("reviewer"), "not_reviewed": eff["not_reviewed"],
                         "stale_evaluations": sorted(eff.get("stale_evaluations", {})),
                         "fact_comparison": fact_cmp, "metrics": metrics, "edition": ed})
    return {"drawings": drawings, "by_status": dict(Counter(d["status"] for d in drawings)),
            "decisions": {q: dict(v) for q, v in decisions.items()},
            "facts_agreement": {q: dict(v) for q, v in facts_agreement.items()},
            "open_questions": questions}


def to_markdown(s: dict) -> str:
    L = ["# Ground-truth review summary (local — contains drawing content)", "",
         "Facts are compared with **human truth only**. Evaluations and visual flags judge one FireAI output; they "
         "are marked stale when FireAI's output changes. There is **no overall accuracy score**.", "",
         "## Drawings", "", "| Drawing | Status | Reviewer (unauthenticated) | Questions not yet answered | Stale evaluations |",
         "|---|---|---|---:|---|"]
    for d in s["drawings"]:
        L.append(f"| {d['id']} | {d['status']}{' — ' + d['reason'] if d.get('reason') else ''} | {d.get('reviewer') or '—'} | "
                 f"{len(d['not_reviewed'])} | {', '.join(d['stale_evaluations']) or '—'} |")
    L += ["", "Status counts: " + ", ".join(f"{k}: {v}" for k, v in sorted(s["by_status"].items())), "",
          "## Answers by question", "", "| Question | CONFIRMED | CORRECTED | NOT_EVALUATED | not answered |",
          "|---|---:|---:|---:|---:|"]
    for q in G.QUESTIONS:
        c = s["decisions"].get(q, {})
        L.append(f"| {q} | {c.get('CONFIRMED', 0)} | {c.get('CORRECTED', 0)} | {c.get('NOT_EVALUATED', 0)} | "
                 f"{c.get('NOT_REVIEWED', 0)} |")
    L += ["", "## Drawing facts: FireAI vs human", "", "| Fact | agree | disagree | not comparable | no human truth yet |",
          "|---|---:|---:|---:|---:|"]
    for q in G.FACTS:
        a = s["facts_agreement"].get(q, {})
        L.append(f"| {q} | {a.get('agree', 0)} | {a.get('disagree', 0)} | {a.get('not_comparable', 0)} | "
                 f"{a.get('no_human_truth', 0)} |")
    L += ["", "## Engineering-meaning metrics (current FireAI output, per drawing)", ""]
    L += [f"- **{k}**: {v}" for k, v in METRICS.items()]
    rows = [d for d in s["drawings"] if d["metrics"]]
    if rows:
        L += ["", "| Drawing | view classification | false rooms | missed rooms | wrong labels | wrong boundaries | "
                  "openings | walls | false-confident | unflagged errors | critical corrections |",
              "|---|---|---|---|---|---|---|---|---|---|---:|"]
        for d in rows:
            m = d["metrics"]
            L.append(f"| {d['id']} | {m['view_classification']} | {m['false_room_rate']['text']} | "
                     f"{m['missed_room_rate']['text']} | {m['room_label_association']['text']} | "
                     f"{m['room_boundary_correctness']['text']} | {m['opening_recognition']['evaluation'] or '—'} "
                     f"(+{m['opening_recognition']['missing_markers']} missing) | {m['major_wall_geometry']['evaluation'] or '—'} "
                     f"(+{m['major_wall_geometry']['missing_markers']} missing) | "
                     f"{m['false_confident_interpretation_rate']['text']} | {m['critical_unflagged_error_rate']['text']} | "
                     f"{m['critical_corrections']} |")
    else:
        L += ["", "No current human evaluations of FireAI output yet."]
    L += ["", "## Open questions", ""]
    L += [f"- {q['id']} {q['question']}: {q['text']}" for q in s["open_questions"]] or ["None recorded."]
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
