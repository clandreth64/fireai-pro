# Human Validation Guide — reviewing FireAI (Milestone 1.7)

**Who this is for:** the owner / a fire-protection professional. You judge the drawing and FireAI's
reading of it the way you would check a colleague's work. **You never need to count CAD objects,
type coordinates, or understand FireAI's internals.**

**Why it matters:** the 11 records in `tests/real_drawings/ground_truth/` are Claude **drafts**
(`PENDING_HUMAN_VERIFICATION`). Only your answers become ground truth. FireAI is measured against
your answers, never against Claude's drafts or its own output.

## 1. Start the review tool (one command)

Prerequisites: Docker Desktop running and this repository on your computer (the drawings stay in
`tests/real_drawings_local/`, never in GitHub).

| Computer | Command (run in the repository folder) |
|---|---|
| Windows (PowerShell) | `powershell -ExecutionPolicy Bypass -File scripts\review.ps1` |
| macOS / Linux / Git Bash | `sh scripts/review.sh` |

Your browser opens <http://127.0.0.1:8765> (this computer only). **Ctrl+C** in the terminal stops it.
If the tool was already running from an earlier session, stop it and start it again to load the
current version.

## 2. What the page shows

1. **SOURCE DRAWING** (from the file) next to **FIREAI INTERPRETATION** (FireAI's overlay).
2. **Point at problems on the drawing** — an interactive view of the drawing (grey) with FireAI's
   items on top: green = rooms, blue = walls, red = doors, teal = windows. Dashed items are ones
   FireAI itself flagged as uncertain. Hover any item to see what FireAI thinks it is. Scroll to
   zoom, drag to pan, **Fit** to reset.
3. **Facts about the drawing** and **Is FireAI's interpretation right?** — the questions.

Tick **"Hide FireAI's interpretation"** if you want to answer the facts without seeing FireAI first.

## 3. The questions

**Facts** (they become ground truth; choose *how you know*: visual review, CAD file, project
documents, site knowledge, other):

* What units is the drawing drawn in? (or *cannot tell*)
* What kind of drawing is this?
* How many distinct plan views / drawings are visible?
* What type is each view? (floor plan, reflected ceiling plan, section, elevation, detail, site plan,
  riser diagram, legend, schedule …)

For each fact you see the value FireAI shows (or Claude's draft, if FireAI has none). Answer
**Correct**, **Wrong — correct value: …**, or **Skip**.

**Statements about FireAI's interpretation** — answer **Yes**, **No**, or **Skip / cannot judge**:

* The major rooms/spaces are recognized.
* Room labels are associated with the correct spaces.
* The displayed room boundaries visually match the source.
* Major walls are represented in the correct locations.
* Door and opening locations are represented correctly.
* Windows are represented correctly (where relevant).
* Stairs, columns and major structural elements are represented correctly.
* Fire-protection content is classified correctly (sprinkler vs fire alarm vs other).
* Unrelated content (annotation, furniture, electrical, schedules) is kept out of the building model.
* Nothing important is missing.
* FireAI has not confidently interpreted anything that is visibly wrong.
* FireAI's warnings / review flags are appropriate.

If you answer **No**, write a few words about what is wrong; how serious it is (minor / major /
critical) is optional. You can add an open question to anything you are unsure about.

## 4. Pointing at problems (optional but very useful)

* **Flag a FireAI item** (default): click a FireAI item and choose what is wrong — *wrong room,
  not a room/space, several rooms merged into one, wrong room label, boundary does not match the
  drawing, wrong kind of object, should not be part of the building model*.
* **Mark something missing**: click where something is missing — *a room/space, a wall, a door /
  opening, something else*.

Each flag appears in a numbered list (with **remove**) and is saved with your review. You never
draw geometry: pointing is enough. (Drawing corrected room outlines is future work.)

## 5. Saving, stale answers, and what is stored

* Save part-way whenever you like; unanswered questions stay open.
* Your review is a separate file — public drawings: `tests/real_drawings/human_reviews/`; the
  private drawing (REAL_001): `tests/real_drawings_local/human_reviews/` (never leaves this computer).
  Claude's draft is never modified.
* **Facts** stay valid until the drawing file (or Claude's draft) changes.
* **Statements and flags** judge one specific FireAI output. When FireAI is improved and its
  output for that drawing changes, those answers are kept on file but shown as **needing a fresh
  look**; the index page says so.
* The reviewer name is **not an authenticated identity** (`reviewer_identity: "unauthenticated_name"`).

## 6. Summary and metrics

**Summary and metrics** in the tool (or `python scripts/gt_review_summary.py`, local only) shows
review progress, open questions, facts vs FireAI, and — only for drawings where you judged the
*current* FireAI output — engineering-meaning metrics, each as a fraction per drawing:

| Metric | Meaning |
|---|---|
| view classification | FireAI's view types vs yours |
| false-room rate | FireAI rooms you flagged *not a room / wrong room* ÷ FireAI rooms |
| missed-room rate | rooms you marked missing ÷ rooms you consider present |
| room-label association | FireAI rooms flagged *wrong label* ÷ labelled FireAI rooms |
| room-boundary correctness | FireAI rooms flagged *wrong boundary / merged* ÷ FireAI rooms |
| opening recognition | your answer on doors/openings + missing-opening marks |
| major wall geometry | your answer on walls + missing-wall marks |
| false confident interpretation rate | flagged items FireAI presented as confident ÷ flagged items |
| critical unflagged error rate | flagged items FireAI did not itself flag for review ÷ flagged items |

There is deliberately **no single accuracy score**.

## 7. Suggested order

REAL_002 → REAL_004 → REAL_001 → REAL_003 (metric twin of REAL_002) → REAL_005, REAL_006 →
REAL_007–011 (software test drawings; units and drawing type are enough).

REAL_002 already contains your first observations (recorded from your message of 2026-09-22 and
marked as transcribed): two floor plans, and the merged second-floor room was wrong. Because
FireAI's output for REAL_002 changed in Milestone 1.7, the tool will ask you to take a fresh look at
the room statements.

## 8. Local housekeeping

Historical worktrees `../fireai-pro-m1-baseline` (`f45b03d`) and `../fireai-pro-m15` (`b6a1fb3`) are
kept for reproducing old results; remove later with `git worktree remove <path>`.
