# Milestone 2.0 — Owner Decision Sheet

**Status: decisions open. Nothing here is decided by FireAI, and nothing here is an engineering or
code requirement.** This sheet lists what you (the owner, with qualified fire-protection engineering
authority where needed) must decide before any M2.0 code is written. The M2.0 specification is
`MILESTONE_2_0_SPEC.md`; its §6 points here.

How to read each item: **what it means** · **why FireAI needs it** · **options** (where options exist)
· **consequences**. Where a choice needs engineering judgement or an authoritative standard, this
sheet names the question and does not answer it.

A decision is only usable by FireAI once it is **written down with who decided, when, and on what
basis**. FireAI will record each one as a versioned input, never as a default.

---

## Part A — Standards and rules

### 1. NFPA 13 edition

* **Means:** which published edition of the standard the first ruleset is transcribed from.
* **Why:** every numeric check M2.0 performs (spacing, distances, coverage) comes from a specific
  edition. Results are only reproducible and defensible if tied to one.
* **Options:** the edition adopted where your first projects are; the most recent edition; several
  editions (more work, each a separate ruleset version).
* **Consequences:** one edition keeps M2.0 small. Supporting another edition later is a new ruleset
  version, not a code change. Picking an edition your jurisdictions have not adopted gives results no
  one can use.
* **Also decide:** whether the first building type is in NFPA 13's scope at all. The first real test
  room (Part E) is in a two-storey residence; whether NFPA 13, 13R or 13D governs a building is a
  qualified determination. FireAI will not make it.

### 2. Legitimate source of and access to the standard

* **Means:** how FireAI's rule data may lawfully be derived from the standard's text.
* **Why:** the standard is copyrighted. Rules must not come from model memory (explicitly forbidden)
  or from an unlicensed copy.
* **Options:** a licensed copy read by a qualified person who transcribes parameters; a
  commercial/data licence from the publisher; manual entry by a licensed engineer per project.
* **Consequences:** this decides who can author and audit rules, and whether rule text or only
  parameters and section references may be stored in the repository.

### 3. Rule provenance and citation format

* **Means:** what each encoded rule must record.
* **Why:** every pass/fail must be traceable to its source (a rule without provenance is an
  unverifiable claim).
* **Options (minimum fields):** ruleset id and version · standard + edition · section reference ·
  parameter(s) and units · adopted text vs paraphrase policy · applicability conditions · author ·
  reviewer · date · change reason. Optional: interpretation notes, formal interpretations.
* **Consequences:** more fields mean slower authoring and much easier audit. Changing the format
  later forces re-review of every rule.

### 4. Jurisdiction and amendment handling

* **Means:** whether M2.0 uses the base edition only or also local amendments.
* **Why:** local amendments can change the same parameters the base edition sets.
* **Options:** base edition only (M2.0 results then state "no local amendments applied"); per
  jurisdiction amendment layers, versioned on top of the base ruleset.
* **Consequences:** base-only is simplest, but every result must say amendments were not evaluated.
  Amendment layers need their own provenance and authority.

## Part B — Design inputs for the first envelope

### 5. Initial sprinkler type(s)

* **Means:** which sprinkler type or types M2.0 may place (e.g. by orientation and coverage type).
* **Why:** spacing and distance limits depend on the sprinkler type and its listing.
* **Options:** exactly one type (recommended for M2.0: smallest test matrix); a small set.
* **Consequences:** each additional type multiplies the known-answer tests (decision 13).

### 6. Initial hazard / occupancy classification(s)

* **Means:** which classification(s) M2.0 accepts as an explicit input for a space.
* **Why:** limits differ by classification. FireAI must never classify a space itself; a person
  enters it with a reason.
* **Options:** one classification only for M2.0; a small set.
* **Consequences:** a space with any other classification is REFUSED. "Unknown" is always REFUSED.

### 7. Definition of the M2.0 "simple flat ceiling"

* **Means:** exactly which ceilings M2.0 accepts.
* **Why:** 2D plans carry no ceiling data (the model's `z_status` is `unknown`). The ceiling must be
  an explicit input, and anything outside the definition must be refused, not approximated.
* **Decide:** the allowed ceiling type(s) (e.g. flat, smooth, horizontal); the allowed height range,
  if any; and the **datum** for the height (height above the finished floor of the selected space is
  the natural choice for M2.0, because no level elevations exist yet). Also decide how a person
  states "no beams, soffits or other ceiling features in this space".
* **Consequences:** a precise definition makes refusals predictable. A vague one invites hidden
  assumptions.

### 8. Room-boundary convention for spacing and wall-distance calculations

* **Means:** which line counts as "the wall" when measuring distances.
* **Why:** FireAI's physical region polygon follows the **inner face of the wall linework as drawn**,
  and where a door interrupts the wall, the polygon is closed by an **analysis line across the door
  opening** (not a wall). Distance-to-wall checks must know which boundary segments are real walls.
* **Options:** the inner wall face as drawn (current polygon); a human-drawn boundary; a
  finish-face offset (not derivable from these drawings). Also decide how door-opening segments are
  treated (as open, or as part of the enclosure).
* **Consequences:** this must match the ruleset's own definition (decision 3). Since M1.9 the
  engineering contract (`engineering_input/3`) gives every boundary segment a kind — `wall`, `window`
  (glazing in a wall), `door_opening`, `open_opening` or `unknown` — so the convention you choose can
  be applied per segment. Spaces with `unknown` segments are refused.

### 9. Placement objective

* **Means:** among all layouts that satisfy every rule, which one M2.0 should prefer.
* **Why:** many valid layouts usually exist. The objective must be declared, not implicit.
* **Options:** fewest sprinklers; a regular grid aligned to the room; symmetric about the room's
  axes; maximum distance margin to limits. Or report all valid candidates and let a person choose.
* **Consequences:** the objective never overrides a rule (validity is a hard filter). It changes
  which valid layout is shown first.

### 10. Deterministic tie-breaking

* **Means:** the rule that picks one layout when two are equally good under decision 9.
* **Why:** the same inputs must always give the same answer (reproducibility).
* **Options:** lexicographic order of positions in the space's LOCAL frame; alignment to a stated
  room axis; smallest maximum distance to walls, then lexicographic.
* **Consequences:** purely technical. Choose once and record it with the ruleset version.

### 11. Numerical tolerances and rounding

* **Means:** how close counts as "at the limit" and how values are rounded for display vs checking.
* **Why:** drawings carry floating-point noise (FireAI sees differences around 10⁻⁷ ft). A result
  must not flip between pass and fail because of noise.
* **Options:** a geometric tolerance for pass/fail (a fixed length); rounding rules for reported
  values (display only; checks use unrounded values).
* **Consequences:** a tolerance that is too large can pass something that is actually over a limit.
  This needs engineering sign-off, not just a programming choice.

## Part C — Authority, approval and evidence

### 12. Human approval requirements

* **Means:** who may approve an M2.0 result and what that approval means.
* **Why:** results are not design deliverables. Approval must be attributable. Today reviewer names
  are **unauthenticated**.
* **Decide:** who may verify models and approve results; whether authentication is required before
  any real-project use; whether the chosen space's boundary needs its own explicit confirmation (the
  review store supports `element_confirm`) on top of the whole-model verification.
* **Consequences:** without authentication, approvals can only be used for internal validation.

### 13. Known-answer engineering tests: authorship and sign-off

* **Means:** who writes the expected results for the §7 test cases of the M2.0 spec, and who signs
  them off.
* **Why:** "AI output must never become its own ground truth". Expected answers must come from a
  qualified person using the selected ruleset, independently of FireAI.
* **Decide:** the author(s), the reviewer, the format (inputs, expected VALID / IMPOSSIBLE / REFUSED
  result, and the reason for each constraint), and where the signed cases live.
* **Consequences:** M2.0 cannot be accepted until these cases exist and pass.

### 14. Manufacturer / listing-data provenance

* **Means:** where a sprinkler's listed parameters (those the ruleset needs) come from.
* **Why:** listed parameters are product-specific. They are inputs, not inventions.
* **Options:** entered per project from the manufacturer's data sheet (with document id, revision and
  date); a curated, versioned product-data table with provenance per entry.
* **Consequences:** a curated table saves entry time but needs its own review and update process.
  Per-project entry is slower but always traceable.

---

## Part D — Scope confirmations (inherited from the M2.0 spec)

* **Water supply, piping and hydraulics:** out of M2.0. Confirm that nothing about pipe or pressure is
  to be evaluated.
* **Obstructions:** M2.0 requires an explicit statement of "no unresolved obstructions" for the
  space, otherwise it refuses. Confirm who may make that statement.
* **Open-plan spaces:** spaces with **unresolved** boundaries (e.g. REAL_002's first floor
  Living Room / Forum / Kitchen / Hall) are refused. FireAI will not invent their boundaries.

## Part E — The first real trial room (evidence: `REAL_DRAWING_VALIDATION.md` §M2.0-readiness)

FireAI proposes these candidates. You choose, or reject both.

| Candidate | Where | Physical region | Why simple | Caveats |
|---|---|---|---|---|
| **B/R (bathroom) — REAL_002 SP00098** (metric twin: REAL_003 SP00314) | floor 2 (V2) | 69.7 sf, rectangular (7.9 × 8.8 ft), 6 vertices, known boundary | simplest valid geometry; the same result in the metric twin | a bathroom. Whether bathroom-specific provisions apply depends on the standard (decision 1), and FireAI will not decide that. One boundary segment is a door-opening analysis line (decision 8) |
| **BEDROOM — REAL_002 SP00095** | floor 2 (V2) | 117.8 sf, not rectangular (rectangularity 0.89, 11 vertices), known boundary | a habitable room with no special-case use | an irregular outline (recess) and a closet door on its boundary; since M1.9 its boundary is **incomplete** (a 1.8 ft diagonal analysis line at the closet corner is `unknown`), so M2.0 would refuse it |

Before either can be used:

1. **Product verification (not the corpus review).** Your corpus reviews of REAL_002/003/004 are
   complete and current, but they evaluate FireAI's accuracy. The engineering gate reads a
   **separate** record: a verification made in the product review workflow, with the in-scope
   region(s) selected, all review triggers acknowledged and the required checklist confirmed. None
   exists, so the real gate currently refuses REAL_002 with `model is REVIEW_REQUIRED`. Floor 2 (V2)
   has no other blocker. Floor 1 (V1) is blocked by its open-plan region until you reject it or draw
   human boundaries.
2. Decisions 1–14 above.

M1.9 contract check of the bathroom (throw-away simulated verification, not persisted): its boundary is
complete — 1 door opening (2.83 ft, shared with the adjacent space), 2 windows (2.33 ft each) and
wall segments; the metric twin has the same order of kinds. Step-by-step product verification:
`HUMAN_REVIEW.md` §4 (and the M1.9 report).
