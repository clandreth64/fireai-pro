# NFPA 13-2025 — First Rule Set: Input Checklist (Milestone 2.1)

**This document contains no NFPA 13 requirement, value, section number or paraphrase.** It lists the
*kinds* of information a qualified person must extract from a lawfully accessed copy of NFPA 13
(2025 edition) and enter through the rule-authoring workflow, and it says, for each kind, whether
FireAI can already consume it. Whether the 2025 edition contains a given requirement, where it is,
and what it says are for the qualified author to establish from the standard itself — never from
memory, from an LLM, or from this checklist.

Target envelope (owner decision 2026-09-24, `fireai/rules/catalog.py` `NFPA13-2025-DEV-ENVELOPE-1`):
NFPA 13-2025 · commercial · wet pipe · Light Hazard · standard spray pendent · smooth, flat,
unobstructed ceiling · non-storage · one simple known space. The envelope says what FireAI will
*attempt*; it classifies nothing and is not a requirement.

Rule set identity: `NFPA13-2025-BASE` version `1`, layer `base_standard`, governing standard
`NFPA 13`, edition `2025` — created EMPTY and DRAFT (`ensure_rule_set_identity`). It cannot be
approved empty. Until it is approved, real engineering refuses with `RULESET_NOT_APPROVED`.

---

## 1. Fields every rule needs (the approval check refuses a rule that lacks any)

| Field | What to enter | Enforced by |
|---|---|---|
| `rule_id` | stable identifier of your choosing (e.g. `N13-2025-SPACING-MAX-LH-SSP`); never reused for different content | store (unique per set) |
| `version` | `1`; a changed approved rule gets a new rule version **and** a `change_reason` | `author_rule` |
| `category` | the model's category family (`spacing`, `protection_area`, `distance_to_boundary`, `ceiling_configuration`, `obstruction`, … — see `RuleCategory`) | model |
| `title` | short neutral description in your own words (no copied text) | — |
| `source.kind` | `authoritative_standard` (your licensed copy) or `licensed_structured_content` | `approval_problems` |
| `source.document` | `NFPA 13` | `approval_problems` |
| `source.edition` | `2025` — must equal the rule set's edition; mixing editions refuses (`EDITION_MISMATCH`) | `approval_problems`, `resolve` |
| `source.reference` (locator) | section / table / figure identifier as printed in your copy | `approval_problems` |
| `source.source_id` | identifier of *your* copy (licence / order / subscription id) | `approval_problems` |
| `source.access_method` | e.g. licensed print copy, licensed digital subscription | `approval_problems` |
| `source.accessed_at` | date you consulted the copy | `approval_problems` |
| `parameters` | each limit as a `Quantity` **with unit** (`ft`, `in`, `m`, `mm`, `sf`, `m2`); the dimension must match the measurement | `approval_problems` |
| `constraint.measurement` | one of the supported measurements (§3) — or `UNSUPPORTED_MEASUREMENT` with a reason | `approval_problems`, `resolve` |
| `constraint.bound` | `max` or `min` | model |
| `constraint.reference_kinds` | for boundary measurements: which boundary kinds participate (`wall`, `window`, `door_opening`, `unknown`…) — a decision you make and record | `approval_problems` |
| `applicability` | `all_of` conditions (`eq`, `in`, `lt`, `known`…) on the facts in §2 only | `approval_problems` |
| `exceptions` | `RuleException(when=[conditions], reason, source)`: when it holds the rule does not apply (recorded); or a separate more-specific rule; `replaceable_by` names the LAYERS (e.g. `listing`) allowed to replace this rule | `resolve` |
| `depends_on` | other rule ids **in the same set** | `approval_problems` |
| `author` / `authored_at` | the qualified person entering the rule | store event log |
| `reviewer` / decision | a **different** qualified person | `review_rule` |
| approver | a person who authored none of the set's rules | `approve_rule_set` |

Identity assurance is `unauthenticated_name (NOT PRODUCTION SAFE)` and is recorded in every event.

## 2. Facts a rule may be conditioned on (and who supplies them)

| Fact | Supplied by | Envelope value to match |
|---|---|---|
| `hazard.scheme` / `hazard.classification` | `DesignClassification` — a human decision, never FireAI | `NFPA 13 occupancy hazard classification` / `Light Hazard` |
| `system.type` / `system.storage` | `SystemCondition` | `wet_pipe` / `non_storage` |
| `sprinkler.type` / `.orientation` / `.response` | approved `SprinklerListing` | `standard_spray` / `pendent` |
| `ceiling.surface` / `.construction` / `.slope_deg` / `.elevation_ft` / `.obstructions` | `CeilingCondition` | `flat` / `smooth_unobstructed` / known / known / `none_present` |
| `space.area_sf` | verified `engineering_input/3` region | — |

A requirement that depends on anything else (e.g. room volume, ceiling pocket, a use not in this list)
cannot be approved yet: add the fact first as a reviewed engine change.

## 3. Categories to structure for the first envelope

For each row: *does the 2025 edition impose something in this category for the envelope, and where?*
(author) → *which measurement does it consume?* (author + reviewer decision) → enter.

| # | Category (neutral name) | Measurement consumed today | Unit dim. | Typical facts in applicability | Supported now? | Engineering work needed first |
|---|---|---|---|---|---|---|
| 1 | Maximum protection area per sprinkler | `nearest_sprinkler_cell_area` (Voronoi cell clipped to space) | area | hazard, sprinkler type, ceiling | **Only if** the standard's definition equals this measurement | If the standard defines protection area differently (e.g. from spacing products along axes), a new reviewed measurement is required; until then mark `UNSUPPORTED_MEASUREMENT` |
| 2 | Maximum spacing between sprinklers | `array_axis_spacing` (adjacent, along array axes) | length | hazard, sprinkler type | Yes for rectangular arrays | Non-array layouts evaluate as `NOT_EVALUABLE` (UNKNOWN), never PASS |
| 3 | Minimum spacing between sprinklers | `pairwise_min_distance` | length | sprinkler type | Yes | — |
| 4 | Maximum distance from walls | `boundary_point_to_nearest_sprinkler_max` is the only related measurement; it is **not** a per-wall perpendicular distance | length | hazard, sprinkler type | **Mapping decision required** | Likely a new measurement (perpendicular wall-to-sprinkler distance, per wall) — reviewed engine change |
| 5 | Minimum distance from walls | `point_to_boundary_min` | length | sprinkler type | Yes | Record which boundary kinds participate (door openings? windows?) |
| 6 | Coverage of the whole space (worst point) | `space_point_to_nearest_sprinkler_max` | length | — | Yes, if the standard's requirement is of this form | — |
| 7 | Small-room provisions | same measurements, conditioned on `space.area_sf` and other room facts | — | `space.area_sf` + others | **Partially** | Any room fact beyond area (e.g. room definition conditions) needs new facts |
| 8 | Deflector position below ceiling | none (vertical) | length | ceiling construction | **No** — Z is `unknown` | Vertical placement engine; mark `UNSUPPORTED_MEASUREMENT` |
| 9 | Obstruction rules (beams, soffits, ducts, lights, clouds) | none | — | — | **No** — refused (`CEILING_NOT_SUPPORTED_IN_M2_0`) | Obstruction geometry + measurements |
| 10 | Ceiling slope / height applicability limits | applicability only (`ceiling.slope_deg`, `ceiling.elevation_ft`) | — | ceiling | Yes, as conditions | — |
| 11 | Exceptions / alternatives within the above | `exceptions`, specific rule, `replaceable_by` | — | — | Yes | Author decides and reviewer confirms precedence |
| 12 | Listing-governed limits (the standard defers to the listing) | listing-layer rules (see `SPRINKLER_LISTING_INPUT_CHECKLIST.md`) | — | — | Yes | — |
| 13 | Hydraulic / water supply / pipe sizing | none | — | — | **No** (out of scope) | Hydraulics engine |

Rules in rows marked "No" may still be *recorded* (with `UNSUPPORTED_MEASUREMENT` and a reason) so
the rule set is complete about what it cannot evaluate: an applicable unsupported rule makes the
design REFUSE (`UNSUPPORTED_MEASUREMENT`) instead of silently ignoring a requirement.

## 4. Workflow (API: `fireai/rules/store.py`)

1. `ensure_rule_set_identity(store, NFPA13_2025_BASE, created_by)` — EMPTY DRAFT, never overwrites.
2. `author_rule(rid, "1", rule, author)` for each rule (draft only; synthetic content refused).
3. `submit_for_review(rid, "1", by)` — refuses an empty set.
4. `review_rule(rid, "1", rule_id, reviewer, "approve" | "reject", note)` — reviewer ≠ author;
   approval runs `approval_problems` and refuses with the exact list of missing items.
5. `approve_rule_set(rid, "1", approver)` — every rule reviewed; approver authored none of them.
6. Changes: `new_version(rid, "1", "2", by, change_reason)`; older approved versions become
   `superseded`, and project designs that used them become **STALE** (`fireai/project`).

## 5. Also required before the first real design (not rules)

* Known-answer cases for the entered rules, authored and signed by a qualified person independently
  of FireAI (FireAI output is never the reference).
* Jurisdiction statement: amendment rule set(s), or `amendments_not_evaluated` as a limitation.
* An approved authoritative listing (`SPRINKLER_LISTING_INPUT_CHECKLIST.md`).
* For the trial space: design classification, system condition, ceiling condition (surface,
  construction, slope, height + datum, obstruction statement) and tolerances — each attributed to a
  named human.
