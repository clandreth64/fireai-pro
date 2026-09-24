"""Human ground truth and human evaluation for the real-drawing corpus (review schema human_review/3).

Two kinds of questions, both answerable by a fire-protection professional looking at the drawing —
no CAD counting, no coordinates, no FireAI internals:

* FACTS about the drawing (units, drawing type, number of views, view types). Answers are human
  GROUND TRUTH and persist until the drawing or Claude's draft changes.
* EVALUATIONS of FireAI's interpretation ("the major rooms are recognized", "nothing important is
  missing", ...). Answers judge ONE FireAI interpretation: they are bound to that output
  (``evaluated_model``) and become STALE when FireAI's interpretation CONTENT for the drawing
  changes (content fingerprint, fireai/review/content.py). Reprocessing that only changes run
  metadata (model id, timestamp, converter label, output path) does not make them stale.

Every answer is CONFIRMED / CORRECTED / NOT_EVALUATED. Corrections are structured (choices,
numbers, severity + description) and can be backed by VISUAL FLAGS: items the reviewer clicked on
the drawing (wrong room, wrong label, merged spaces, missing room here, ...).

Records:
    tests/real_drawings/ground_truth/REAL_###.json  Claude's DRAFT (never modified by a review)
    human reviews: public -> tests/real_drawings/human_reviews/, private ->
    tests/real_drawings_local/human_reviews/ (git-ignored)

Rules: FireAI output is never ground truth by itself — a fact becomes truth only when a person
CONFIRMS or CORRECTS it after looking at the source (basis recorded; "FireAI output" is not an
accepted basis). Claude's draft is shown for reference only.
"""

from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent
REPO = ROOT.parent.parent
GT_DIR = ROOT / "ground_truth"
CORPUS = ROOT / "corpus.json"
PUBLIC_REVIEWS = ROOT / "human_reviews"
PRIVATE_REVIEWS = REPO / "tests" / "real_drawings_local" / "human_reviews"
ID_RE = re.compile(r"^REAL_\d{3}$")
REVIEW_SCHEMA = "human_review/3"

VIEW_TYPES = ("FLOOR_PLAN", "REFLECTED_CEILING_PLAN", "SECTION", "ELEVATION", "DETAIL", "SITE_PLAN",
              "RISER_DIAGRAM", "LEGEND", "SCHEDULE", "TITLE_BLOCK", "UNKNOWN")
UNIT_CHOICES = ("in", "ft", "mm", "cm", "m", "cannot_tell")
SEVERITIES = ("minor", "major", "critical")

FACTS: dict[str, dict] = {
    "units":        {"kind": "choice", "choices": UNIT_CHOICES,
                     "question": "What units is the drawing drawn in?"},
    "drawing_type": {"kind": "text", "question": "What kind of drawing is this (e.g. two-storey residential floor plans)?"},
    "view_count":   {"kind": "int", "question": "How many distinct plan views / drawings are visible?"},
    "view_types":   {"kind": "counts", "keys": VIEW_TYPES,
                     "question": "What type is each view? (number of views of each type)"},
}
EVALUATIONS: dict[str, str] = {
    "rooms_recognized":  "The major rooms/spaces are recognized.",
    "room_labels":       "Room labels are associated with the correct spaces.",
    "room_boundaries":   "The displayed room boundaries visually match the source.",
    "walls":             "Major walls are represented in the correct locations.",
    "doors_openings":    "Door and opening locations are represented correctly.",
    "windows":           "Windows are represented correctly (where relevant).",
    "structure":         "Stairs, columns and major structural elements are represented correctly.",
    "fire_protection":   "Fire-protection content is classified correctly (sprinkler vs fire alarm vs other).",
    "excluded_content":  "Unrelated content (annotation, furniture, electrical, schedules) is kept out of the building model.",
    "missing_content":   "Nothing important is missing.",
    "confident_errors":  "FireAI has not confidently interpreted anything that is visibly wrong.",
    "review_flags":      "FireAI's warnings / review flags are appropriate (it flags what it should not be trusted on).",
}
QUESTIONS = tuple(FACTS) + tuple(EVALUATIONS)
STATUSES = ("CONFIRMED", "CORRECTED", "NOT_EVALUATED")
BASES = ("visual_review_of_source_rendering", "cad_file_inspection", "project_documents", "site_knowledge", "other")
FORBIDDEN_BASES = ("fireai_output", "fireai", "machine_interpretation")
FLAG_KINDS = ("wrong_room", "not_a_room", "merged_spaces", "wrong_label", "wrong_boundary", "misclassified",
              "should_be_excluded", "missing_room", "missing_wall", "missing_door_or_opening", "missing_other")
FLAG_QUESTION = {"wrong_room": "rooms_recognized", "not_a_room": "rooms_recognized", "merged_spaces": "room_boundaries",
                 "wrong_label": "room_labels", "wrong_boundary": "room_boundaries", "misclassified": "confident_errors",
                 "should_be_excluded": "excluded_content", "missing_room": "rooms_recognized",
                 "missing_wall": "walls", "missing_door_or_opening": "doors_openings", "missing_other": "missing_content"}
PENDING = "PENDING_HUMAN_VERIFICATION"


def canonical_sha(obj) -> str:
    return hashlib.sha256(json.dumps(obj, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def corpus_entry(gid: str) -> dict:
    """All corpus files for one drawing id (a drawing may exist as DWG and DXF)."""
    files = [f for f in json.loads(CORPUS.read_text(encoding="utf-8"))["files"] if f["id"] == gid]
    if not files:
        raise KeyError(gid)
    return {"id": gid, "description": files[0].get("description", ""),
            "private": any(f.get("source") == "private" for f in files),
            "files": [f["file"] for f in files], "sha256": sorted(f["sha256"] for f in files)}


def load_record(gid: str, gt_dir: Path = GT_DIR) -> dict:
    if not ID_RE.match(gid or ""):
        raise KeyError(gid)
    return json.loads((gt_dir / f"{gid}.json").read_text(encoding="utf-8"))


def list_ids(gt_dir: Path = GT_DIR) -> list[str]:
    return sorted(p.stem for p in gt_dir.glob("REAL_*.json"))


def draft_values(record: dict) -> dict[str, dict]:
    """draft category -> {field: value-record} (reference only)."""
    fields = record["claude_draft"]["fields"]
    return {cat: {k: fields[k] for k in keys if k in fields}
            for cat, keys in record["claude_draft"]["by_category"].items()}


def draft_fact(record: dict, q: str):
    """The draft's value for a FACT question, when it is a single typed value; else None."""
    d = draft_values(record).get(q)
    if not d or len(d) != 1:
        return None
    v = next(iter(d.values())).get("value")
    try:
        return parse_value(q, v)
    except (ValueError, TypeError, KeyError, AttributeError):
        return None


def review_path(gid: str, private: bool, public_dir: Path = PUBLIC_REVIEWS, private_dir: Path = PRIVATE_REVIEWS) -> Path:
    if not ID_RE.match(gid or ""):
        raise KeyError(gid)
    return (private_dir if private else public_dir) / f"{gid}.json"


def parse_value(q: str, raw):
    """Structured value for a FACT question, or {severity, description} for an EVALUATION."""
    if q in EVALUATIONS:
        sev = (raw or {}).get("severity") or None          # optional: never invented for the reviewer
        desc = str((raw or {}).get("description") or "").strip()
        if sev is not None and sev not in SEVERITIES:
            raise ValueError(f"severity must be one of {SEVERITIES} (or left blank)")
        if not desc:
            raise ValueError("describe what is wrong (a few words is enough)")
        return {"severity": sev, "description": desc}
    spec = FACTS[q]
    k = spec["kind"]
    if k == "choice":
        if raw not in spec["choices"]:
            raise ValueError(f"choose one of {spec['choices']}")
        return raw
    if k == "int":
        v = int(str(raw).strip())
        if v < 0:
            raise ValueError("must be 0 or more")
        return v
    if k == "text":
        v = str(raw or "").strip()
        if not v:
            raise ValueError("enter a short description")
        return v
    if k == "counts":
        out = {t: int(n) for t, n in dict(raw).items() if str(n).strip() not in ("", "0")}
        if any(t not in spec["keys"] or n < 0 for t, n in out.items()):
            raise ValueError("counts must be non-negative whole numbers for known view types")
        if not out:
            raise ValueError("enter at least one count")
        return out
    raise ValueError(f"unknown kind {k}")


def validate_flag(f: dict) -> list[str]:
    errs = []
    if f.get("flag") not in FLAG_KINDS:
        errs.append(f"visual flag: kind must be one of {FLAG_KINDS}")
    missing = str(f.get("flag", "")).startswith("missing_")
    if missing and not (isinstance(f.get("point_local"), list) and len(f["point_local"]) == 2):
        errs.append("visual flag: a 'missing' flag needs the clicked location")
    if not missing and not f.get("target_uid"):
        errs.append("visual flag: select the FireAI item it refers to")
    return errs


def validate_review(review: dict, record: dict, source_sha: list[str]) -> list[str]:
    errs = []
    if review.get("schema") != REVIEW_SCHEMA:
        errs.append(f"schema must be {REVIEW_SCHEMA}")
    if not str(review.get("reviewer", "")).strip():
        errs.append("reviewer name is required")
    if review.get("source_sha256s") != source_sha:
        errs.append("source_sha256s do not match the corpus drawing file(s)")
    if review.get("claude_draft_sha256") != canonical_sha(record["claude_draft"]):
        errs.append("claude_draft_sha256 does not match the current draft")
    items = review.get("items") or {}
    if any(q in EVALUATIONS for q in items) and not (review.get("evaluated_model") or {}).get("model_sha256"):
        errs.append("evaluations need the FireAI output they judge (evaluated_model)")
    for q, it in items.items():
        if q not in QUESTIONS:
            errs.append(f"{q}: unknown question")
            continue
        st = it.get("human_decision")
        if st not in STATUSES:
            errs.append(f"{q}: decision must be one of {STATUSES}")
            continue
        if q in FACTS and st != "NOT_EVALUATED":
            basis = str(it.get("basis") or "").lower()
            if basis in FORBIDDEN_BASES:
                errs.append(f"{q}: FireAI output cannot be the basis of ground truth")
            elif basis not in BASES:
                errs.append(f"{q}: basis must be one of {BASES}")
        if st == "CONFIRMED" and q in FACTS:
            if it.get("confirmed_value") is None:
                errs.append(f"{q}: there is no value to confirm — use CORRECTED and enter it, or NOT_EVALUATED")
            elif it.get("confirmed_value_source") not in ("fireai", "claude_draft"):
                errs.append(f"{q}: confirmed_value_source must say which shown value was confirmed")
        if st == "CORRECTED":
            try:
                parse_value(q, it.get("human_corrected_value"))
            except (ValueError, TypeError, KeyError, AttributeError) as exc:
                errs.append(f"{q}: correction invalid — {exc}")
        if st != "CORRECTED" and it.get("human_corrected_value") is not None:
            errs.append(f"{q}: a corrected value is only allowed with CORRECTED")
    for f in review.get("visual_flags") or []:
        errs += validate_flag(f)
    return errs


def make_review(record: dict, source_sha: list[str], reviewer: str, decisions: dict, note: str | None = None,
                previous: dict | None = None, evaluated_model: dict | None = None, visual_flags=None,
                recorded_by: str | None = None) -> dict:
    """decisions: {question: {human_decision, human_corrected_value, confirmed_value, confirmed_value_source,
    basis, reason, open_question}}. decided_at is kept for decisions that did not change."""
    prev_items = (previous or {}).get("items") or {}
    items = {}
    for q, d in decisions.items():
        it = {"question": FACTS[q]["question"] if q in FACTS else EVALUATIONS.get(q),
              "type": "fact" if q in FACTS else "evaluation",
              "claude_draft": draft_values(record).get(q),
              "human_decision": d.get("human_decision"),
              "confirmed_value": d.get("confirmed_value"), "confirmed_value_source": d.get("confirmed_value_source"),
              "human_corrected_value": d.get("human_corrected_value"), "basis": d.get("basis"),
              "reason": d.get("reason"), "open_question": d.get("open_question")}
        p = prev_items.get(q)
        same = p is not None and all(p.get(k) == it[k] for k in it)
        it["decided_at"] = p["decided_at"] if same and p.get("decided_at") else now()
        items[q] = it
    rv = {"schema": REVIEW_SCHEMA, "id": record["id"], "reviewer": reviewer.strip(),
          "reviewer_identity": "unauthenticated_name", "review_timestamp": now(), "source_sha256s": source_sha,
          "claude_draft_sha256": canonical_sha(record["claude_draft"]), "evaluated_model": evaluated_model,
          "items": items, "visual_flags": list(visual_flags or []), "note": note}
    if recorded_by:
        rv["recorded_by"] = recorded_by
    return rv


def evaluations_stale(em: dict, current_model_sha: str | None, current_content_fp: str | None,
                      evaluated_content_fp: str | None = None) -> tuple[bool, str]:
    """(stale?, basis). Content fingerprints decide when both sides are known: the one recorded in
    the review, or ``evaluated_content_fp`` computed from the exact reviewed artifact (verified by
    its sha256) for reviews recorded before content fingerprints existed. Otherwise (conservative)
    the output file's bytes decide, as before."""
    if not current_model_sha and not current_content_fp:
        return False, "no_current_output"
    reviewed_fp = em.get("content_fingerprint") or evaluated_content_fp
    if reviewed_fp and current_content_fp:
        return reviewed_fp != current_content_fp, "content_fingerprint"
    return bool(current_model_sha) and em.get("model_sha256") != current_model_sha, "file_bytes"


def effective(record: dict, review: dict | None, source_sha: list[str], current_model_sha: str | None = None,
              current_content_fp: str | None = None, evaluated_content_fp: str | None = None) -> dict:
    """Human truth (facts), human evaluations of FireAI, and overall status.

    status: PENDING_HUMAN_VERIFICATION | PARTIALLY_HUMAN_REVIEWED | HUMAN_VERIFIED | INVALIDATED.
    Evaluations whose evaluated_model differs from ``current_model_sha`` are reported as stale."""
    empty = {"truth": {}, "evaluations": {}, "stale_evaluations": {}, "not_reviewed": list(QUESTIONS),
             "open_questions": {}, "visual_flags": []}
    if review is None:
        return {"status": PENDING, **empty}
    if review.get("source_sha256s") != source_sha:
        return {"status": "INVALIDATED", "reason": "drawing changed since review", **empty}
    if review.get("claude_draft_sha256") != canonical_sha(record["claude_draft"]):
        return {"status": "INVALIDATED", "reason": "draft changed since review", **empty}
    items = review.get("items") or {}
    truth, evals = {}, {}
    for q, it in items.items():
        st = it["human_decision"]
        if q in FACTS and st in ("CONFIRMED", "CORRECTED"):
            v = it["confirmed_value"] if st == "CONFIRMED" else parse_value(q, it["human_corrected_value"])
            truth[q] = {"status": st, "value": v, "comparable": v, "basis": it.get("basis"), "by": review["reviewer"],
                        "confirmed_value_source": it.get("confirmed_value_source")}
        elif q in EVALUATIONS:
            evals[q] = {"decision": st, "correction": it.get("human_corrected_value"), "reason": it.get("reason")}
    em = review.get("evaluated_model") or {}
    stale, basis = evaluations_stale(em, current_model_sha, current_content_fp, evaluated_content_fp)
    reviewed = set(items)
    status = "HUMAN_VERIFIED" if reviewed >= set(QUESTIONS) and not stale else "PARTIALLY_HUMAN_REVIEWED"
    return {"status": status, "truth": truth, "evaluations": {} if stale else evals,
            "stale_evaluations": evals if stale else {}, "evaluated_model": em, "currency_basis": basis,
            "reviewer": review["reviewer"], "reviewer_identity": review.get("reviewer_identity"),
            "reviewed_at": review["review_timestamp"],
            "not_evaluated": sorted(q for q, it in items.items() if it["human_decision"] == "NOT_EVALUATED"),
            "not_reviewed": [q for q in QUESTIONS if q not in reviewed],
            "open_questions": {q: it["open_question"] for q, it in items.items() if it.get("open_question")},
            "visual_flags": [] if stale else list(review.get("visual_flags") or []),
            "stale_visual_flags": list(review.get("visual_flags") or []) if stale else []}
