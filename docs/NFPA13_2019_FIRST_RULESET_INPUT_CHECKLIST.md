# NFPA 13-2019 — First Rule Set: Structured Input Checklist (Milestone 2.2A → 2.2B)

**M2.2B:** the first package's inputs are now collected through the machine-checked templates in `docs/m2_2b/` (see its README). This checklist remains the reference for what each field means.

**This document contains no NFPA 13 requirement, value, section number or paraphrase.** It says
exactly which STRUCTURED fields a qualified person must supply, from a lawfully accessed copy of
NFPA 13 (2019 edition), for each rule entered in M2.2B, and which FireAI measurement each rule can
map to. Whether the 2019 edition contains a requirement, where, and what it says are for the author to
establish from the standard itself. Nothing is copied between the 2019 and 2025 rule sets.

Identity: `NFPA13-2019-BASE` v1 · layer `base_standard` · governing standard `NFPA 13` · edition
`2019` · EMPTY + DRAFT (`fireai/rules/catalog.py`). Every rule's `source.edition` must be `2019`; a
rule citing another edition cannot be approved, and a request mixing editions refuses.

**Envelope (M2.2A.1):** `NFPA13-2019-DEV-ENVELOPE-1` is registered: Light Hazard, wet pipe,
hydraulically calculated, non-storage, standard spray pendent, `noncombustible_unobstructed`
construction classification, smooth flat horizontal single-plane ceiling at a known elevation with no
features or obstructions, orthogonal walls, explicit branch-line orientation, small-room `not_eligible`.
It does NOT approve the rule set.

**Licensing (M2.2A.1):** NFPA 13-2019 source status is `INTERNAL_R_AND_D_ONLY`. Rules entered from it
are for INTERNAL R&D; they are NOT release-eligible for beta / production / commercial / external use
until the owner records a commercial authorization covering that use
(`docs/SOURCE_AUTHORIZATION_AND_RELEASE.md`). Do not record license numbers, customer ids or personal
data in `source_id`, notes or anywhere else — use an internal document-control reference.

---

## 1. Per-rule record (all required for approval — `fireai/rules/store.py: approval_problems`)

| Field | Entry |
|---|---|
| `rule_id`, `version` | stable id of your choosing; `1` |
| `category` | `RuleCategory` family (`spacing`, `protection_area`, `distance_to_boundary`, `ceiling_configuration`, …) |
| `title` | your own neutral label (never copied text) |
| `source` | `kind` = `authoritative_standard` / `licensed_structured_content`; `document` = `NFPA 13`; `edition` = `2019`; `reference` = section / table / figure locator; `source_id`, `access_method`, `accessed_at` |
| `constraint.measurement` | one of the measurements in §2, or `UNSUPPORTED_MEASUREMENT` + reason |
| `constraint.bound` | `max` / `min` |
| limit — EITHER | `limit_parameter` → a `Quantity` with unit (`ft`, `in`, `m`, `mm`, `sf`, `m2`) of the measurement's dimension |
| limit — OR (M2.2A) | `derived` = {`op`: `scale`, `from_key`: the constraint key it depends on, `factor_parameter`: name of a dimensionless number parameter}. The referenced constraint must be in the same rule set; missing references, cycles and dimension mismatches refuse |
| `constraint.reference_kinds` | for wall-reference measurements: which of `wall`, `window`, `door_opening`, `open_opening` count as the reference (nothing is treated as a wall by default) |
| `applicability.all_of` | conditions on the facts in §3 only |
| `exceptions` | `RuleException(when=[conditions], reason, source)` |
| `depends_on` | rule ids in the same set |
| author / reviewer / approver | three roles; reviewer ≠ author; approver authored no rule in the set |

## 2. Measurement map (what exists after M2.2A)

| Requirement kind (neutral) | Measurement | Dim. | Status |
|---|---|---|---|
| Protection area per sprinkler (S x L) | `array_sxl_protection_area` (SXL-ARRAY/2) | area | implemented — S along the explicit BRANCH-LINE direction (`LayoutOrientation`), L perpendicular; rectangular arrays, orthogonal walls. **The author must confirm SXL-ARRAY/2 matches the 2019 definition** (larger side per axis; side = adjacent sprinkler, or twice the perpendicular distance to the participating wall) |
| S or L alone (branch-line / between-line dimension) | `array_sxl_s_dimension`, `array_sxl_l_dimension` | length | implemented (M2.2A.1) — requires the explicit orientation |
| Nearest-sprinkler (Voronoi) cell area | `nearest_sprinkler_cell_area` | area | implemented — a DIFFERENT measurement; not S x L |
| Maximum spacing (along array axes) | `array_axis_spacing` | length | implemented |
| Minimum spacing | `pairwise_min_distance` | length | implemented |
| Maximum distance from walls | `perpendicular_wall_distance` (PERP-WALL/1), fixed or `derived` | length | implemented — frame-aligned walls; angled walls refuse |
| Minimum distance from walls | `point_to_boundary_min` | length | implemented |
| Worst point of the space to nearest sprinkler | `space_point_to_nearest_sprinkler_max` | length | implemented (any polygon) |
| Distance from angled / irregular walls | `angled_wall_perpendicular_distance`, `angled_wall_protected_floor_worst_distance` | length | **declared, not implemented** — an applicable rule refuses |
| Deflector distance below ceiling | `ceiling_to_deflector_vertical_distance` | length | implemented — single flat region, known ceiling elevation, explicit deflector elevation, one datum |
| Obstructions (beams, soffits, clouds, ducts, lights) | none | — | not implemented; such ceilings are refused |
| Hydraulics / density / pipe schedule tables | none | — | out of scope (the design-method fact exists for applicability only) |

## 3. Facts (applicability) and who supplies them

| Fact | Supplied by |
|---|---|
| `hazard.scheme`, `hazard.classification` | `DesignClassification` (person) |
| `system.type` | `SystemCondition.system_type` — e.g. wet / dry / preaction (stated) |
| `system.design_method` | `SystemCondition.design_method` — `hydraulically_calculated` / `pipe_schedule` / `other` (stated; separate from type) |
| `system.storage` | `SystemCondition.storage` |
| `sprinkler.type`, `.orientation`, `.response` | approved listing |
| `ceiling.surface`, `.slope_deg`, `.elevation_ft`, `.construction` (geometric), `.obstructions` | `CeilingCondition` (person) |
| `ceiling.construction_classification` (+ `.scheme`) | `CeilingRegion.construction_classification` — the standard's classification, stated by a person; never inferred from geometry |
| `space.area_sf` | verified engineering input |
| `space.eligibility.small_room` | `DesignRequest.eligibility["small_room"]` — `eligible` / `not_eligible` / `unknown`, by a named person, with the criteria considered; only `eligible` activates a provision |

## 4. For each first-slice requirement, send (per rule)

1. the locator (section / table / figure) and your copy's `source_id`, access method, date;
2. the value(s) with units, or — for a value defined relative to another requirement — the factor
   and WHICH requirement it scales;
3. the conditions under which it applies (in the §3 facts) and its exceptions;
4. the measurement from §2 it maps to, and the participating boundary kinds;
5. for S x L: confirmation (or correction) of SXL-ARRAY/1, including how openings are treated at
   perimeter sprinklers;
6. for small-room provisions: the criteria a person must record in the eligibility decision;
7. two independent hand-worked known-answer cases per rule (FireAI output is never the reference);
8. the reviewer and approver names.
