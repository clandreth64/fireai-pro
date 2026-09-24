# Human review, corrections and the verification gate (Milestones 1.6–1.9)

FireAI keeps three things strictly apart:

| What | Where | Written by |
|---|---|---|
| **Machine interpretation** | `building_model.json` (elements, view regions, wall analysis …) | the pipeline |
| **Human corrections / verification of a drawing** | review store `<FIREAI_DATA_DIR>/reviews/<sha[:2]>/<sha>/` (`corrections.json`, `verification.json`) | a person, via the API |
| **Ground truth for the validation corpus** | `tests/real_drawings/ground_truth/` (Claude **drafts**) + separate human-review files | Claude (draft) / a person (truth) |

FireAI output is never ground truth, and the pipeline never writes to the review store.

## 1. Corpus ground truth (validation of FireAI itself)

* The 11 records in `tests/real_drawings/ground_truth/` are **Claude-generated drafts**
  (`schema: ground_truth/2`, `review_status: PENDING_HUMAN_VERIFICATION`, content under `claude_draft`).
  They are NOT ground truth.
* A person reviews each drawing with the local tool (`docs/HUMAN_VALIDATION_GUIDE.md`, one-command
  launch). Milestone 1.7 (`human_review/3`) asks only professional questions: FACTS about the
  drawing (units, drawing type, number and type of views — human ground truth) and STATEMENTS about
  FireAI's interpretation (rooms recognized, labels, boundaries, walls, openings, windows,
  structure, fire protection, excluded content, missing content, confident errors, review flags),
  each CONFIRMED / CORRECTED / NOT_EVALUATED, plus optional VISUAL FLAGS clicked on the drawing.
* Facts persist until the drawing or draft changes. Statements and flags are bound to the FireAI
  interpretation they judged (`evaluated_model`: output path, model sha256, engine version and —
  since M1.9 — `content_fingerprint`) and become STALE when FireAI's interpretation CONTENT changes
  (`fireai/review/content.py`: geometry, classifications, view types, regions, semantic spaces,
  boundaries, openings, source/XREF identity, units, transforms, corrections, triggers). Reprocessing
  that changes only run metadata (model id, timestamp, converter label, output path) keeps them
  current. A review recorded before M1.9 is judged by the content of the exact artifact it evaluated
  (located under the outputs folder and verified by its sha256); if that artifact is gone or changed,
  the byte comparison decides (conservative). Per answer: `question`, `claude_draft` (reference),
  `human_decision`, `confirmed_value` + `confirmed_value_source` or `human_corrected_value`,
  `basis`, `reason`, `open_question`, `decided_at`; per review `reviewer` (**unauthenticated
  name**), `review_timestamp`, optional `recorded_by` (e.g. a transcription of the owner's words).
* `tests/real_drawings/gt.py` computes truth and evaluations; `scripts/gt_review_summary.py`
  reports progress and engineering-meaning metrics per drawing (no overall score), using human
  answers only.

New drawings from the owner: `python scripts/intake_drawing.py <file> --source private
--description "<generic description>" [--xrefs <dir>]` (checks the destination is git-ignored,
records metadata only, refuses identifying descriptions, creates an empty pending record).

## 2. Corrections to a processed drawing (persist across reprocessing)

Each stored correction also keeps a `machine_snapshot` of FireAI's interpretation at correction time
(value, confidence, evidence, rules, engine version) so it can later be expressed as a structured
`LearningEvent` (`fireai/review/learning.py`, derived read-only; no training — see
`docs/AGENTIC_LEARNING_ARCHITECTURE.md`).

`POST /api/v2/drawings/{job}/corrections` with `{kind, data, reviewer, note}`:

| kind | data | Applied |
|---|---|---|
| `view_type` | `region_uid`, `view_type` | before interpretation — changes whether room logic runs; FireAI's proposal is kept as `machine_view_type` |
| `room_boundary` | `polygon_src` (SRC frame, drawing units), optional `label`, `replaces_element_uid` | adds a room with `provenance.origin = "human"`; the replaced machine room stays, marked `review.status = "rejected"` |
| `element_reject` / `element_confirm` | `element_uid` | sets the element's `provenance.review` |

Corrections are stored per **source file sha256** and applied on every run of that file
(`POST /reprocess`). Each records its context (XREF hashes, engine version). A correction whose
context no longer matches, or whose target no longer exists, is **not applied** and raises
`HUMAN_CORRECTIONS_NOT_APPLIED`. Corrections made on another revision of the same drawing (same DXF
document GUID, different bytes) raise `HUMAN_CORRECTIONS_FROM_OTHER_REVISION`. Nothing is dropped
silently.

## 3. Verification gate

`model.verification` = fingerprint of: source sha256, loaded XREF sha256s, resolved units, engine
version (`ENGINE_VERSION` in `fireai/pipeline.py`), the applied corrections and (schema 0.5.0) the
interpretation `content_fingerprint`. The engineering gate is therefore stricter than the corpus
review: an engine-version change alone invalidates a product verification even when the content is
identical, and a content change under the same engine version invalidates it too. Status:

| Status | Meaning |
|---|---|
| `UNREVIEWED` | no verification recorded; no automatic review triggers |
| `REVIEW_REQUIRED` | no verification recorded and review triggers exist, or a person rejected it |
| `HUMAN_VERIFIED` | a person verified exactly this fingerprint |
| `INVALIDATED` | a verification exists but the source, XREFs, units, engine, corrections or interpretation content changed (or it was for another revision) |

`POST /api/v2/drawings/{job}/verification` records a decision. `verify` requires: reviewer; the
required categories (units, drawing_type, view_regions, extents, walls, rooms) CONFIRMED or
CORRECTED (a CORRECTED item needs a value or note); **every** current review-trigger code
acknowledged; the drawing region(s) in scope selected (FireAI never selects the plan); and no stored
correction missing from the model (reprocess first).

Future engineering must enter through `fireai.contract.build_engineering_input` (docs/ENGINEERING_INPUT_CONTRACT.md), which first calls `fireai.review.gate.require_verified_model(model, store)`, which
additionally blocks on: unresolved units, XREFs not loaded, material DWG conversion loss, and a
selected region that is not a plan view. No engineering exists in this milestone.

**Production gap:** the reviewer is a free-text name. Before production it must be the authenticated
user (see PRODUCTION_REQUIREMENTS.md).

## 4. How to verify a drawing for engineering (product workflow, step by step)

The product UI shows the interpretation; the verification itself is recorded through the API
(`/docs` on the running server is FastAPI's interactive form for every call below). Only a person
does this. FireAI never records a verification.

1. **Run the current code** (repository mounted, LibreDWG image; review store and jobs persist in the
   git-ignored `.fireai_data/` of the repository):
   `docker run --rm -p 8010:8000 -e FIREAI_DWG_CONVERTER=libredwg -v "<repo>:/app" -w /app fireai:m15-base uvicorn api.app:app --host 0.0.0.0 --port 8000`
   Check `http://localhost:8010/health` shows `"dwg_conversion": "libredwg"`.
2. **Upload** the drawing at `http://localhost:8010/` (or `POST /api/v2/drawings`, form field `file`).
   Wait for `completed`. Note the `job_id`.
3. **Inspect** `overlay.png` / `overlay.svg` (deliverables of the job) against `source.png`: walls,
   rooms (physical regions), named spaces (diamonds; hollow = unresolved), doors, windows and the
   pink `opening` footprints. Open `understanding_summary.md` for the review triggers.
4. **Read the verification context:** `GET /api/v2/drawings/{job_id}/verification`. It lists the
   required checklist categories, every current review-trigger code, the regions (id, uid, view type)
   and the current `readiness` blockers.
5. **Record your decision:** `POST /api/v2/drawings/{job_id}/verification` with
   `{"reviewer": "<your name>", "decision": "verify" | "reject",
     "checklist": {"units": {"status": "CONFIRMED"}, "drawing_type": {...}, "view_regions": {...},
                   "extents": {...}, "walls": {...}, "rooms": {...}},
     "acknowledged_triggers": [<every code from step 4 that you have looked at>],
     "selected_region_uids": [<the uid of the plan region you verify for engineering>],
     "note": "<what you checked / anything excluded>"}`.
   Use `CORRECTED` with a `note` (or `value`) for a category that is wrong, or `reject`. Only select
   regions you actually checked.
6. **Confirm:** the response shows `"recorded": "HUMAN_VERIFIED"` and `readiness.ready: true` (or
   lists the remaining blockers). `GET .../verification` shows the same. Any later reprocessing with a
   different engine, source, XREFs, units, corrections or content will show `INVALIDATED`.
