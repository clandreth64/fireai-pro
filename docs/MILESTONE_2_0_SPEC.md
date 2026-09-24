# Milestone 2.0 — Deterministic Single-Space Sprinkler Placement (SPECIFICATION ONLY)

**Status (2026-09-24): foundation IMPLEMENTED; no real NFPA 13 design possible yet.** The owner approved
the M2.0 architectural decisions (recorded in `M2_OWNER_DECISIONS.md`). The NFPA 13-centred rules
architecture and the deterministic single-space placement engine exist
(`docs/NFPA13_RULES_ARCHITECTURE.md`, `fireai/rules/`, `fireai/engineering/`) and are proven with TEST
ONLY synthetic rule values. Real engineering REFUSES until an approved, authoritative NFPA 13 rule set
(explicit edition), real listing data and the explicit design inputs exist. This document contains **no
NFPA requirements**; no value here may be copied into code as a requirement.

Readiness checkpoint (2026-09-24): see `REAL_DRAWING_VALIDATION.md` §M2.0-readiness for the real
candidate room, the real gate result and the input gap. Open owner decisions: `M2_OWNER_DECISIONS.md`.

## 1. Purpose

Prove that FireAI can make its **first independently verifiable fire-protection engineering
decision**: valid sprinkler placement in **one** defined space, under **explicitly selected** design
constraints, with every placed sprinkler explainable.

## 2. Design envelope (intentionally tiny)

| In scope | Out of scope |
|---|---|
| one HUMAN_VERIFIED floor-plan region (via the engineering input contract) | piping, hydraulics |
| one **known** semantic space (`boundary_state: known`) in that region, simple geometry | BIM coordination, clash detection |
| flat ceiling, **explicit** ceiling height | fabrication |
| **explicit** hazard/design classification | automatic hazard classification |
| **explicit** sprinkler type (and its listing data, entered explicitly) | automatic code-edition selection |
| **explicit** applicable ruleset and version | obstructions (the space must have none unresolved) |
| | multiple spaces, sloped or obstructed ceilings, soffits, beams |
| | any space with an **unresolved** boundary (open-plan named spaces); FireAI never invents one |

## 3. Inputs (all explicit, all recorded)

1. `EngineeringInput` from `build_engineering_input` (contract `engineering_input/3`, see §3a) with
   exactly one selected region and one selected space. A person chooses the space uid; it must be a
   `semantic_spaces[]` entry, and its `region_uid` names the physical region (`spaces[]`) that
   supplies the geometry.
2. A **DesignCriteria** record, entered and attributed by a person:
   * the ceiling as an explicit **ceiling record**: type (flat, per owner decision 7), height, and the
     height's **datum** (e.g. above the finished floor of the selected space; the level stays
     `unassigned`). Z source: human input. Recorded as a ceiling object, not a scalar, so sloped or
     multiple ceiling planes can later be added without changing the result format;
   * `hazard_classification` (human decision, with reason);
   * `sprinkler_type` and the listed parameters needed by the ruleset (human input, with source);
   * `ruleset_id` + `ruleset_version` (see §6);
   * `obstructions`: an explicit statement "none" for the space (otherwise out of scope → refuse).
3. Anything missing → refuse with the list of missing inputs. No defaults.

## 3a. Contract prerequisite — DONE in M1.9 (`engineering_input/3`)

Each space now carries an ordered, classified boundary (`wall | window | door_opening | open_opening
| unknown`, with `encloses`, geometry, provenance, confidence) and the package lists the openings
crossing it (`ENGINEERING_INPUT_CONTRACT.md` §1a–§1b). M2.0 must additionally REFUSE a selected space
whose boundary is not `complete` (any `unknown` segment): distances to walls are undefined there.
This refusal is implemented as `space_engineering_blockers(package, space_uid)`; M2.0 calls it
before any placement and returns REFUSED with its reasons.
How engineering treats each segment kind (e.g. whether a door opening counts as part of the
enclosure for a given check) is owner decision 8. It is not decided here.

## 4. The only engineering task

Determine sprinkler positions within the space such that every constraint in the selected ruleset
version passes. If that is impossible, report **why** (which constraint, where). Never return a
"best effort" layout that fails a constraint.

The algorithm must be deterministic (same inputs + versions → same layout), and every constraint
check is performed by deterministic geometry code (distances, spacing, coverage area computed from
the polygon). An AI component may **propose** candidate layouts, but only the deterministic checker
decides validity.

## 5. Output (per placed sprinkler, and overall)

| Field | Meaning |
|---|---|
| `sprinkler_id` / uid | stable id, provenance → space uid, criteria record, ruleset version |
| `location` | a 3D point: `frame` = LOCAL (ft) and the contract's `source_to_local` + `verification_fingerprint`, so it can be re-expressed in SRC / PROJECT later; X, Y; Z = the ceiling record's height with its **datum** and source. Never a bare 2D point |
| `spacing_to_adjacent` | distance to each neighbour and the governing constraint id |
| `distance_to_walls` | per boundary segment, with constraint id |
| `coverage_area` | the area assigned to the sprinkler and how it was computed |
| `constraints[]` | rule id, ruleset version, required vs actual, pass/fail |
| `evidence` | which inputs and geometry produced each value |
| `engine_version`, `ruleset_version`, `contract_version`, `verification_fingerprint` | reproducibility |
| overall status | VALID / IMPOSSIBLE (with reasons) / REFUSED (missing inputs, blockers) |

Human approval is required before a result is used for anything else. The result is not a design
deliverable.

Each placed sprinkler is a **design object** in the authoritative model (`provenance.origin =
"design"`, engine and ruleset versions, `derived_from` = space uid, ceiling record and criteria). It
is the seed of a future sprinkler-graph node (`SPATIAL_BIM_ARCHITECTURE.md` §8), not a separate
representation. Later systems (routing, hydraulics, coordination, BIM, fabrication) extend these
objects; they never re-derive them from a drawing.

## 6. Decisions required BEFORE implementation (owner / engineering authority)

Plain-English decision sheet with options and consequences: `M2_OWNER_DECISIONS.md` (14 decisions
plus scope confirmations and the first real trial room).

1. **NFPA 13 edition** to encode first (and whether others are needed).
2. **Legal access** to the standard text for encoding rules (licence terms for commercial software;
   who transcribes; how the source is cited per rule).
3. **Rule provenance format:** for every rule — source document, edition, section reference, adopted
   text or paraphrase policy, author, reviewer, date.
4. **Jurisdiction:** base edition only, or local amendments (and how amendments are versioned).
5. **Which sprinkler types** are in the first envelope, and where their listed parameters come
   from (manufacturer data sheets as explicit inputs?).
6. **Which hazard classifications** are in the first envelope.
7. **Ceiling definition:** which ceiling heights and types are "simple/flat" for M2.0.
8. **Wall reference geometry:** which face defines the space boundary for distance-to-wall (human
   room boundary, wall inner face, or wall analysis centreline minus thickness). This must match the
   ruleset's definition.
9. **Placement objective** among valid layouts (fewest sprinklers? regular grid? symmetric?) and
   the tie-break rule that keeps it deterministic.
10. **Tolerances:** geometric tolerance for pass/fail at boundaries and rounding rules.
11. **Who may approve** results, and how approval identity is authenticated.
12. **Known-answer authority:** who produces and signs off the expected results in §7.
13. **Listing-data provenance:** where a sprinkler's listed parameters come from, and how they are
    versioned.
14. **Product verification of the trial drawing:** a person verifies the model in the product review
    workflow, selecting the region. The corpus human review measures FireAI's accuracy and does
    **not** satisfy the engineering gate.

## 7. Known-answer test cases (to be authored with expected results by a qualified person)

Expected values will come from the selected ruleset **after** §6 is decided. None are written here.

| Case | Purpose |
|---|---|
| Simple rectangle | baseline grid placement |
| Dimension just **below** a spacing threshold | boundary behaviour (fewer sprinklers) |
| Dimension just **above** a spacing threshold | boundary behaviour (one more row/column) |
| Narrow room | single-line layouts, wall-distance governing |
| L-shaped room | non-convex coverage |
| Irregular (non-rectilinear) room | polygon coverage, angled walls |
| Boundary conditions (exact threshold values) | tolerance handling |
| Intentionally impossible geometry (e.g. a space too small/large for the selected constraints) | must return IMPOSSIBLE with the reason, never a layout |
| Missing input (each criterion) | must return REFUSED listing the missing input |
| Unverified / blocked model | must be refused by the contract |

## 8. Architecture requirements

* Code lives in `fireai/engineering/`. It consumes **only** `fireai.contract.EngineeringInput`
  (import boundary enforced by tests).
* The ruleset is **data**: versioned, with per-rule provenance; engine code does not hard-code
  requirements.
* Results are persisted with all versions and are reproducible.
* Every rule evaluation records a classification: deterministic rule / interpretation / project
  assumption / human decision.
* An AI component may only **propose** candidate positions. Validity comes from the deterministic
  checker alone (`AGENTIC_LEARNING_ARCHITECTURE.md` §5).
* Every human approval, rejection or correction of an M2.0 result is recorded as a structured
  learning event (FireAI's result, the human decision, the reason, and all versions), scoped to the
  project. It never changes rules globally without the versioned release process (§8–§9 of that
  document).

## 9. Limitations carried forward from M1.x (visible, not fixed by M2.0)

* Paper-space risers and legends are not classified.
* UNKNOWN regions need human view-type decisions.
* Merged rooms and open-plan semantic spaces (unresolved boundaries) need human boundaries.
  Selection is per view region, so one open-plan area blocks every room on that floor until it is
  resolved.
* Door-closure pairing can cut diagonal analysis lines across wall corners; such boundary portions
  are `unknown` (the space is then incomplete and refused by M2.0), never doorways.
* Wall-gap analysis can report several overlapping openings for one door, and misses some openings;
  the contract's openings come from region boundaries and door/window elements instead.
* Wrong-but-declared units may go undetected.
* Fire alarm and fire protection content is not separated.
* Reviewer identity is not authenticated.
* LibreDWG cannot decode some objects and uses pathological resources on some files.
* 2D plans carry no Z (ceiling height must be entered explicitly).
