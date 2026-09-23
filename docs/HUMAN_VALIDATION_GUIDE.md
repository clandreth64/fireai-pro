# Human Validation Guide — reviewing FireAI's ground truth

**Who this is for:** the owner / a fire-protection professional reviewing the 11 real drawings in
the validation corpus. No programming needed.

**Why it matters:** the 11 ground-truth records were **drafted by Claude** from rendered drawings.
They are *not* independent truth and stay `PENDING_HUMAN_VERIFICATION` until you review them. Until
then, FireAI's accuracy on real drawings is **not measured** — only described. Your decisions become
the reference FireAI is evaluated against. Claude's drafts and FireAI's output never are.

---

## 1. Start the review tool (one command)

Prerequisites (one time): Docker Desktop running, and this repository on your computer. The drawings
themselves are already in `tests/real_drawings_local/` on this machine (they are never in GitHub).

From the repository folder:

| Computer | Command |
|---|---|
| Windows (PowerShell) | `powershell -ExecutionPolicy Bypass -File scripts\review.ps1` |
| macOS / Linux / Git Bash | `sh scripts/review.sh` |

The first start builds the `fireai:dev` image (several minutes). Then your browser opens
<http://127.0.0.1:8765>. The tool only listens on this computer. Press **Ctrl+C** in the terminal to
stop it.

The images come from the latest local corpus run. If a drawing shows "no local rendering", run:
`docker run --rm --user root -v "%CD%:/app" -w /app fireai:dev python scripts/validate_corpus.py --label <new-label>`
(PowerShell: use `${PWD}` instead of `%CD%`).

## 2. What you see for each drawing

| Panel | What it is | Is it truth? |
|---|---|---|
| **SOURCE DRAWING** | The drawing rendered straight from the file | It is the evidence |
| **FIREAI INTERPRETATION** | FireAI's overlay: what it found, colour-coded | No — machine output |
| **CLAUDE DRAFT** column | What Claude wrote when drafting the record | No — a draft |
| **FIREAI VALUE** column | FireAI's value for the same category | No — machine output |
| **YOUR DECISION** column | Your decision | **Yes — this is the ground truth** |

Tick **"Hide FireAI's interpretation"** to judge the drawing without seeing FireAI's answer first. We
recommend this for your first pass. Click an image to open it full size.

## 3. Decide each category

For every category choose one of:

* **CONFIRMED** — Claude's draft is right. Only possible when a draft value exists.
* **CORRECTED** — the draft is wrong or missing. Enter the right value in the structured box.
* **NOT_EVALUATED** — you cannot or will not judge it. This records *no* truth, which is fine.

Also choose the **basis** of your decision: visual review of the source rendering, CAD file
inspection, project documents, site knowledge, or other. "FireAI output" is not an accepted basis.

Structured corrections (no free-text parsing needed):

| Category | How to enter a correction |
|---|---|
| units | pick in / ft / mm / cm / m / undeclared_or_unknown |
| drawing_type | short description |
| view_count | number of separate plans/views |
| view_types | a number per view type (FLOOR_PLAN, SECTION, RISER_DIAGRAM, …) |
| extents | width and height in feet |
| room_count | number |
| room_names | one name per line (repeat duplicates) |
| room_areas | one per line: `NAME = area_sf` |
| room_boundaries | JSON: `[{"name": "OFFICE", "polygon_src": [[x, y], ...]}]` in the drawing's own coordinates |
| walls, doors, windows, columns, stairs, grids | count and/or a short description |
| sprinkler_components, fire_alarm_components | count and/or description (kept separate on purpose) |
| title_block | one per line: `field: value` |
| other | free text |

Optional per category: a **reason**, and an **open question**. Open questions appear in the summary
so nothing you are unsure about gets lost.

You can **save partial reviews** and come back later. A category you did not touch simply stays "not
reviewed". A drawing becomes `HUMAN_VERIFIED` only when every category has a decision (NOT_EVALUATED
counts as a decision).

## 4. Where your review is stored

* Claude's draft (`tests/real_drawings/ground_truth/REAL_###.json`) is **never modified**.
* Your review is a separate file:
  * public drawings → `tests/real_drawings/human_reviews/REAL_###.json` (may be committed)
  * the private drawing (REAL_001) → `tests/real_drawings_local/human_reviews/REAL_001.json`
    (never leaves this computer)
* For each category the file keeps `claude_draft` (what you saw), `human_decision`,
  `human_corrected_value`, `basis`, `reason`, `open_question` and `decided_at`. The whole review
  records `reviewer`, `reviewer_identity` and `review_timestamp`.
* If the drawing file or Claude's draft changes later, your review is automatically marked
  **INVALIDATED** instead of being silently reused.

**Limitation:** the reviewer name is typed in and is **not an authenticated identity**
(`reviewer_identity: "unauthenticated_name"`). That is acceptable for local validation, but not for
production approvals.

## 5. Summary and evaluation

Open **Review summary** in the tool, or run `python scripts/gt_review_summary.py` in the container. It
writes the local, git-ignored `tests/real_drawings_outputs_local/gt_review_summary.md`. It shows:

* drawings awaiting review / partially reviewed / fully reviewed;
* per category: how many CONFIRMED / CORRECTED / NOT_EVALUATED / not reviewed;
* open questions;
* FireAI vs **human** truth per category: agree / disagree / not comparable / no human truth yet,
  plus the list of disagreements.

There is deliberately **no single accuracy score**. FireAI is compared only with your decisions
(CONFIRMED or CORRECTED), never with Claude's unconfirmed drafts.

## 6. Suggested order

1. REAL_002 and REAL_003 (real floor plans; rooms, walls, doors, windows matter most).
2. REAL_004 (sections — check that no rooms/plan content is claimed).
3. REAL_001 (the sprinkler drawing — units, views, sprinkler vs fire alarm, XREFs).
4. REAL_005, 007–011 (test/library/site drawings — mostly units, extents, drawing type).
5. REAL_006 (3D model; FireAI correctly refuses it — confirm drawing_type, mark the rest NOT_EVALUATED).

## 7. Local housekeeping (not part of the review)

Historical worktrees `../fireai-pro-m1-baseline` (Milestone 1, `f45b03d`) and `../fireai-pro-m15`
(Milestone 1.5, `b6a1fb3`) are kept for reproducing old results. They can be removed later with
`git worktree remove <path>`.
