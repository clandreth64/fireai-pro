"""Ground-truth records for the real-drawing corpus: Claude's DRAFT vs HUMAN truth.

Record (committed, tests/real_drawings/ground_truth/REAL_###.json, schema ground_truth/2):
    claude_draft   AI-assisted visual review (Claude). NOT ground truth. Never modified by a review.
    review_status  PENDING_HUMAN_VERIFICATION until a person reviews it.
Human review (separate file, schema human_review/2, written only by the review tool / a person):
    public sources  -> tests/real_drawings/human_reviews/REAL_###.json   (committable)
    private sources -> tests/real_drawings_local/human_reviews/REAL_###.json (git-ignored)

Each reviewed category keeps, side by side:
    claude_draft           snapshot of the draft value the person saw (for audit / learning events)
    human_decision         CONFIRMED | CORRECTED | NOT_EVALUATED
    human_corrected_value  typed value (see CATEGORY_SPECS) when CORRECTED
    basis, reason, open_question, decided_at
and the review records reviewer (an UNAUTHENTICATED name) and review_timestamp.

Rules enforced here:
* FireAI output is never ground truth: a decision whose basis is FireAI output is rejected, and
  this module never reads FireAI runs/outputs (machine values live in fireai_values.py).
* A review is bound to the drawing file hashes and to the exact draft it reviewed; if either
  changes, the review is INVALIDATED.
* CONFIRMED means "the draft value is right" and requires a draft value; CORRECTED requires a
  valid typed value; NOT_EVALUATED records no truth.
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
REVIEW_SCHEMA = "human_review/2"

VIEW_TYPES = ("FLOOR_PLAN", "REFLECTED_CEILING_PLAN", "SECTION", "ELEVATION", "DETAIL", "SITE_PLAN",
              "RISER_DIAGRAM", "LEGEND", "SCHEDULE", "TITLE_BLOCK", "UNKNOWN")
UNIT_CHOICES = ("in", "ft", "mm", "cm", "m", "undeclared_or_unknown")

# kind -> how a corrected value is entered and validated
CATEGORY_SPECS: dict[str, dict] = {
    "units":                {"kind": "choice", "choices": UNIT_CHOICES,
                             "help": "Units the drawing is actually drawn in."},
    "drawing_type":         {"kind": "text", "help": "What the drawing is (e.g. two-storey residential floor plans)."},
    "view_count":           {"kind": "int", "help": "Number of separate plans/views in model space."},
    "view_types":           {"kind": "counts", "keys": VIEW_TYPES, "help": "How many views of each type."},
    "extents":              {"kind": "extents", "help": "Overall drawn extents in feet (width x height)."},
    "room_count":           {"kind": "int", "help": "Number of rooms/spaces on the plan(s)."},
    "room_names":           {"kind": "list", "help": "One room name per line (repeat names that occur twice)."},
    "room_areas":           {"kind": "name_numbers", "help": "One per line: NAME = area in sq ft."},
    "room_boundaries":      {"kind": "polygons",
                             "help": 'JSON list: [{"name": "OFFICE", "polygon_src": [[x, y], ...]}] '
                                     "in the drawing's own coordinates and units."},
    "walls":                {"kind": "text_count", "help": "Count if meaningful, and how walls are drawn."},
    "doors":                {"kind": "text_count", "help": "Door count and notes (plan doors only)."},
    "windows":              {"kind": "text_count", "help": "Window count and notes."},
    "columns":              {"kind": "text_count", "help": "Column count and notes."},
    "stairs":               {"kind": "text_count", "help": "Stair count and notes."},
    "grids":                {"kind": "text_count", "help": "Grid line count and notes."},
    "sprinkler_components": {"kind": "text_count", "help": "Existing sprinkler heads/pipe/risers: count and notes."},
    "fire_alarm_components": {"kind": "text_count", "help": "Fire alarm devices (kept separate from sprinkler)."},
    "title_block":          {"kind": "key_values", "help": "One per line: field: value (e.g. scale: 1/8\" = 1'-0\")."},
    "other":                {"kind": "text", "help": "Anything else a reviewer should know."},
}
CATEGORIES = tuple(CATEGORY_SPECS)
STATUSES = ("CONFIRMED", "CORRECTED", "NOT_EVALUATED")
BASES = ("visual_review_of_source_rendering", "cad_file_inspection", "project_documents", "site_knowledge", "other")
FORBIDDEN_BASES = ("fireai_output", "fireai", "machine_interpretation")
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
    """category -> {field: value-record} from the Claude draft."""
    fields = record["claude_draft"]["fields"]
    return {cat: {k: fields[k] for k in keys if k in fields}
            for cat, keys in record["claude_draft"]["by_category"].items()}


def review_path(gid: str, private: bool, public_dir: Path = PUBLIC_REVIEWS, private_dir: Path = PRIVATE_REVIEWS) -> Path:
    if not ID_RE.match(gid or ""):
        raise KeyError(gid)
    return (private_dir if private else public_dir) / f"{gid}.json"


# ── typed values ─────────────────────────────────────────────────────────────

def parse_value(cat: str, raw) -> object:
    """Form input -> typed corrected value. Raises ValueError with a readable message."""
    spec = CATEGORY_SPECS[cat]
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
            raise ValueError("enter the correct description")
        return v
    if k == "counts":
        out = {t: int(n) for t, n in dict(raw).items() if str(n).strip() not in ("", "0")}
        if any(t not in spec["keys"] or n < 0 for t, n in out.items()):
            raise ValueError("counts must be non-negative whole numbers for known view types")
        if not out:
            raise ValueError("enter at least one count")
        return out
    if k == "extents":
        w, h = float(raw["width_ft"]), float(raw["height_ft"])
        if w <= 0 or h <= 0:
            raise ValueError("width and height must be positive")
        return {"width_ft": w, "height_ft": h}
    if k == "list":
        out = [ln.strip() for ln in str(raw or "").splitlines() if ln.strip()]
        if not out:
            raise ValueError("enter one item per line")
        return out
    if k == "name_numbers":
        out = []
        for ln in str(raw or "").splitlines():
            if not ln.strip():
                continue
            name, sep, num = ln.rpartition("=")
            if not sep or not name.strip():
                raise ValueError(f"line {ln!r}: use NAME = number")
            out.append({"name": name.strip(), "value": float(num)})
        if not out:
            raise ValueError("enter one NAME = number per line")
        return out
    if k == "polygons":
        v = json.loads(raw) if isinstance(raw, str) else raw
        if not isinstance(v, list) or not v:
            raise ValueError("enter a JSON list of rooms")
        for r in v:
            pts = r.get("polygon_src") if isinstance(r, dict) else None
            if not (isinstance(pts, list) and len(pts) >= 3 and all(
                    isinstance(p, list) and len(p) == 2 and all(isinstance(c, (int, float)) for c in p) for p in pts)):
                raise ValueError('each room needs "polygon_src": [[x, y], ...] with at least 3 points')
        return v
    if k == "text_count":
        count = raw.get("count")
        count = None if count in (None, "") else int(count)
        desc = str(raw.get("description") or "").strip()
        if count is None and not desc:
            raise ValueError("enter a count and/or a description")
        if count is not None and count < 0:
            raise ValueError("count must be 0 or more")
        return {"count": count, "description": desc or None}
    if k == "key_values":
        out = {}
        for ln in str(raw or "").splitlines():
            if not ln.strip():
                continue
            key, sep, val = ln.partition(":")
            if not sep or not key.strip():
                raise ValueError(f"line {ln!r}: use field: value")
            out[key.strip()] = val.strip()
        if not out:
            raise ValueError("enter one field: value per line")
        return out
    raise ValueError(f"unknown kind {k}")


def draft_normalized(cat: str, draft: dict | None):
    """A comparable value from a CONFIRMED draft, when the draft is a single typed fact; else None."""
    if not draft or len(draft) != 1:
        return None
    v = next(iter(draft.values())).get("value")
    kind = CATEGORY_SPECS[cat]["kind"]
    if kind == "choice" and v in CATEGORY_SPECS[cat]["choices"]:
        return v
    if kind == "int" and isinstance(v, int) and not isinstance(v, bool):
        return v
    if kind == "list" and isinstance(v, list) and all(isinstance(x, str) for x in v):
        return v
    if kind == "text_count" and isinstance(v, int) and not isinstance(v, bool):
        return {"count": v, "description": None}
    return None


# ── reviews ──────────────────────────────────────────────────────────────────

def validate_review(review: dict, record: dict, source_sha: list[str]) -> list[str]:
    errs = []
    if review.get("schema") != REVIEW_SCHEMA:
        errs.append(f"schema must be {REVIEW_SCHEMA}")
    if not str(review.get("reviewer", "")).strip():
        errs.append("reviewer is required")
    if review.get("source_sha256s") != source_sha:
        errs.append("source_sha256s do not match the corpus drawing file(s)")
    if review.get("claude_draft_sha256") != canonical_sha(record["claude_draft"]):
        errs.append("claude_draft_sha256 does not match the current draft")
    drafts = draft_values(record)
    for cat, it in (review.get("items") or {}).items():
        if cat not in CATEGORY_SPECS:
            errs.append(f"{cat}: unknown category")
            continue
        st = it.get("human_decision")
        if st not in STATUSES:
            errs.append(f"{cat}: decision must be one of {STATUSES}")
            continue
        basis = str(it.get("basis") or "").lower()
        if basis in FORBIDDEN_BASES:
            errs.append(f"{cat}: FireAI output cannot be the basis of ground truth")
        elif st != "NOT_EVALUATED" and basis not in BASES:
            errs.append(f"{cat}: basis must be one of {BASES}")
        if st == "CONFIRMED" and not drafts.get(cat):
            errs.append(f"{cat}: nothing in the draft to confirm; use CORRECTED with a value, or NOT_EVALUATED")
        if st == "CORRECTED":
            try:
                parse_value(cat, it.get("human_corrected_value"))
            except (ValueError, TypeError, KeyError, AttributeError) as exc:
                errs.append(f"{cat}: corrected value invalid — {exc}")
        if st != "CORRECTED" and it.get("human_corrected_value") is not None:
            errs.append(f"{cat}: a corrected value is only allowed with CORRECTED")
        if it.get("claude_draft") != drafts.get(cat):
            errs.append(f"{cat}: claude_draft snapshot does not match the draft")
    return errs


def make_review(record: dict, source_sha: list[str], reviewer: str, decisions: dict, note: str | None = None,
                previous: dict | None = None) -> dict:
    """decisions: {cat: {human_decision, human_corrected_value, basis, reason, open_question}}.
    decided_at is kept from the previous review for decisions that did not change."""
    drafts = draft_values(record)
    prev_items = (previous or {}).get("items") or {}
    items = {}
    for cat, d in decisions.items():
        it = {"claude_draft": drafts.get(cat), "human_decision": d.get("human_decision"),
              "human_corrected_value": d.get("human_corrected_value"), "basis": d.get("basis"),
              "reason": d.get("reason"), "open_question": d.get("open_question")}
        p = prev_items.get(cat)
        same = p is not None and all(p.get(k) == it[k] for k in it)
        it["decided_at"] = p["decided_at"] if same and p.get("decided_at") else now()
        items[cat] = it
    return {"schema": REVIEW_SCHEMA, "id": record["id"], "reviewer": reviewer.strip(),
            "reviewer_identity": "unauthenticated_name",
            "review_timestamp": now(), "source_sha256s": source_sha,
            "claude_draft_sha256": canonical_sha(record["claude_draft"]), "items": items, "note": note}


def effective(record: dict, review: dict | None, source_sha: list[str]) -> dict:
    """Human truth per category + overall status. A draft value becomes truth ONLY when a
    person CONFIRMED it."""
    if review is None:
        return {"status": PENDING, "truth": {}, "not_reviewed": list(CATEGORIES)}
    if review.get("source_sha256s") != source_sha:
        return {"status": "INVALIDATED", "reason": "drawing changed since review", "truth": {},
                "not_reviewed": list(CATEGORIES)}
    if review.get("claude_draft_sha256") != canonical_sha(record["claude_draft"]):
        return {"status": "INVALIDATED", "reason": "draft changed since review", "truth": {},
                "not_reviewed": list(CATEGORIES)}
    truth = {}
    items = review.get("items") or {}
    for cat, it in items.items():
        if it["human_decision"] == "CONFIRMED":
            truth[cat] = {"status": "CONFIRMED", "value": it["claude_draft"],
                          "comparable": draft_normalized(cat, it["claude_draft"]),
                          "basis": it.get("basis"), "by": review["reviewer"]}
        elif it["human_decision"] == "CORRECTED":
            v = parse_value(cat, it["human_corrected_value"])
            truth[cat] = {"status": "CORRECTED", "value": v, "comparable": v, "basis": it.get("basis"),
                          "by": review["reviewer"], "draft": it["claude_draft"]}
    reviewed = set(items)
    status = "HUMAN_VERIFIED" if reviewed >= set(CATEGORIES) else "PARTIALLY_HUMAN_REVIEWED"
    return {"status": status, "truth": truth, "reviewer": review["reviewer"],
            "reviewer_identity": review.get("reviewer_identity"), "reviewed_at": review["review_timestamp"],
            "not_evaluated": sorted(c for c, it in items.items() if it["human_decision"] == "NOT_EVALUATED"),
            "not_reviewed": [c for c in CATEGORIES if c not in reviewed],
            "open_questions": {c: it["open_question"] for c, it in items.items() if it.get("open_question")}}
