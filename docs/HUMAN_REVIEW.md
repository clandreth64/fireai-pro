# Human review, corrections and the verification gate (Milestone 1.6)

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
* A person reviews each drawing with the local tool — **step-by-step instructions, one-command
  launch and category list: `docs/HUMAN_VALIDATION_GUIDE.md`**. Decisions per category are
  CONFIRMED / CORRECTED (typed, structured value) / NOT_EVALUATED, with a basis, optional reason and
  optional open question; a basis of "FireAI output" is rejected.
* Reviews (`schema: human_review/2`) are separate files (public → `tests/real_drawings/human_reviews/`,
  private → `tests/real_drawings_local/human_reviews/`). Per category they keep `claude_draft`
  (snapshot), `human_decision`, `human_corrected_value`, `basis`, `reason`, `open_question`,
  `decided_at`; per review `reviewer` (**unauthenticated name**), `review_timestamp`. A review is bound
  to the drawing hashes and the exact draft; if either changes it is INVALIDATED.
* `tests/real_drawings/gt.py` computes effective truth (a draft value becomes truth only when a
  person CONFIRMED it); `scripts/gt_review_summary.py` reports review progress and FireAI-vs-human
  agreement **per category** (no overall score), using human truth only.

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
version (`ENGINE_VERSION` in `fireai/pipeline.py`) and the applied corrections. Status:

| Status | Meaning |
|---|---|
| `UNREVIEWED` | no verification recorded; no automatic review triggers |
| `REVIEW_REQUIRED` | no verification recorded and review triggers exist, or a person rejected it |
| `HUMAN_VERIFIED` | a person verified exactly this fingerprint |
| `INVALIDATED` | a verification exists but the source, XREFs, units, engine or corrections changed (or it was for another revision) |

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
