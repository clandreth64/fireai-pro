"""Ground-truth records for the real-drawing corpus: Claude's DRAFT vs HUMAN truth.

Record (committed, tests/real_drawings/ground_truth/REAL_###.json, schema ground_truth/2):
    claude_draft   AI-assisted visual review (Claude). NOT ground truth.
    review_status  PENDING_HUMAN_VERIFICATION until a person reviews it.
Human review (separate file, written only by the review tool / a person):
    public sources  -> tests/real_drawings/human_reviews/REAL_###.json   (committable)
    private sources -> tests/real_drawings_local/human_reviews/REAL_###.json (git-ignored)

Rules enforced here:
* FireAI output is never ground truth: a human item whose basis is FireAI output is
  rejected, and nothing in this module reads FireAI runs/outputs.
* A review is bound to the drawing's sha256 and to the exact draft it reviewed;
  if either changes the review is INVALIDATED.
* CONFIRMED means "the draft value is right" and requires a draft value;
  CORRECTED requires the human value; NOT_EVALUATED records no truth.
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

CATEGORIES = ("units", "drawing_type", "view_count", "extents", "room_count", "room_names", "room_areas", "walls",
              "doors", "windows", "columns", "stairs", "grids", "sprinkler_components", "title_block", "other")
STATUSES = ("CONFIRMED", "CORRECTED", "NOT_EVALUATED")
BASES = ("visual_review_of_source_rendering", "cad_file_inspection", "project_documents", "site_knowledge", "other")
FORBIDDEN_BASES = ("fireai_output", "fireai", "machine_interpretation")
PENDING = "PENDING_HUMAN_VERIFICATION"


def canonical_sha(obj) -> str:
    return hashlib.sha256(json.dumps(obj, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def corpus_entry(gid: str) -> dict:
    """All corpus files for one drawing id (a drawing may exist as DWG and DXF).
    Returns {"id", "description", "private", "sha256": sorted list of file sha256s}."""
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
    out: dict[str, dict] = {}
    for cat, keys in record["claude_draft"]["by_category"].items():
        out[cat] = {k: fields[k] for k in keys if k in fields}
    return out


def review_path(gid: str, private: bool, public_dir: Path = PUBLIC_REVIEWS, private_dir: Path = PRIVATE_REVIEWS) -> Path:
    if not ID_RE.match(gid or ""):
        raise KeyError(gid)
    return (private_dir if private else public_dir) / f"{gid}.json"


def validate_review(review: dict, record: dict, source_sha: list[str]) -> list[str]:
    errs = []
    if not str(review.get("reviewer", "")).strip():
        errs.append("reviewer is required")
    if review.get("source_sha256s") != source_sha:
        errs.append("source_sha256s do not match the corpus drawing file(s)")
    if review.get("claude_draft_sha256") != canonical_sha(record["claude_draft"]):
        errs.append("claude_draft_sha256 does not match the current draft")
    drafts = draft_values(record)
    items = review.get("items") or {}
    for cat, it in items.items():
        if cat not in CATEGORIES:
            errs.append(f"{cat}: unknown category")
            continue
        st = it.get("status")
        if st not in STATUSES:
            errs.append(f"{cat}: status must be one of {STATUSES}")
            continue
        basis = str(it.get("basis") or "").lower()
        if basis in FORBIDDEN_BASES:
            errs.append(f"{cat}: FireAI output cannot be the basis of ground truth")
        elif st != "NOT_EVALUATED" and basis not in BASES:
            errs.append(f"{cat}: basis must be one of {BASES}")
        if st == "CONFIRMED" and not drafts.get(cat):
            errs.append(f"{cat}: nothing in the draft to confirm; use CORRECTED with a value, or NOT_EVALUATED")
        if st == "CORRECTED" and it.get("value") in (None, ""):
            errs.append(f"{cat}: CORRECTED needs the correct value")
    return errs


def make_review(record: dict, source_sha: list[str], reviewer: str, items: dict, note: str | None = None) -> dict:
    return {"schema": "human_review/1", "id": record["id"], "reviewer": reviewer.strip(),
            "reviewed_at": datetime.now(timezone.utc).isoformat(), "source_sha256s": source_sha,
            "claude_draft_sha256": canonical_sha(record["claude_draft"]), "items": items, "note": note}


def effective(record: dict, review: dict | None, source_sha: list[str]) -> dict:
    """Human truth per category + overall status. Draft values become truth ONLY when a
    person CONFIRMED them."""
    if review is None:
        return {"status": PENDING, "truth": {}}
    if review.get("source_sha256s") != source_sha:
        return {"status": "INVALIDATED", "reason": "drawing changed since review", "truth": {}}
    if review.get("claude_draft_sha256") != canonical_sha(record["claude_draft"]):
        return {"status": "INVALIDATED", "reason": "draft changed since review", "truth": {}}
    drafts = draft_values(record)
    truth = {}
    for cat, it in (review.get("items") or {}).items():
        if it["status"] == "CONFIRMED":
            truth[cat] = {"value": drafts.get(cat), "status": "CONFIRMED", "basis": it.get("basis"),
                          "by": review["reviewer"]}
        elif it["status"] == "CORRECTED":
            truth[cat] = {"value": it["value"], "status": "CORRECTED", "basis": it.get("basis"),
                          "by": review["reviewer"], "draft": drafts.get(cat)}
    reviewed = set(review.get("items") or {})
    status = "HUMAN_VERIFIED" if reviewed >= set(CATEGORIES) else "PARTIALLY_HUMAN_REVIEWED"
    return {"status": status, "truth": truth, "reviewer": review["reviewer"], "reviewed_at": review["reviewed_at"],
            "not_evaluated": sorted(c for c, it in (review.get("items") or {}).items()
                                    if it["status"] == "NOT_EVALUATED"),
            "not_reviewed": sorted(set(CATEGORIES) - reviewed)}
