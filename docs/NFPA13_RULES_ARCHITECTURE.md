# NFPA 13 Rules Architecture and Deterministic Design Foundation (Milestone 2.0)

**NFPA 13 is the foundation of FireAI Pro's commercial rules architecture.** This milestone builds
HOW FireAI represents, selects, layers, traces and evaluates NFPA 13-based requirements, plus a
deterministic single-space sprinkler placement engine. It contains **no NFPA 13 requirement**: rule
CONTENT will come only from lawfully accessed authoritative sources, structured and reviewed by
qualified people. LLM output, model memory, internet summaries and FireAI output are never sources
of rule content. All numbers used in tests are `TEST_ONLY_SYNTHETIC` and are not NFPA 13 values.

```
Verified building model ──► engineering_input/3 (contract) ─┐
                                                           ▼
 Rule stack (layers) ──► RULES ENGINE (fireai/rules) ──► EngineeringConstraints (what must hold)
                                                           ▼
 Explicit inputs ──────► DETERMINISTIC ENGINES (fireai/engineering) ──► evaluations (whether it holds)
 (ceiling, class., listing,                                ▼
  tolerances, search space)        EngineeringDesignResult: valid layout SET + explanations
                                                           ▼
                         future agents / optimisers rank VALID designs; humans approve
```

## 1. Layer 1 — Rules engine (`fireai/rules/`)

**Rule hierarchy** (`RuleLayer`, applied in this order):
`base_standard` (NFPA 13) → `edition` → `jurisdiction_amendment` → `referenced_standard` →
`listing` (manufacturer) → `project` → `engineering_decision`.

| Object | Holds |
|---|---|
| `RuleSet` | `rule_set_id`, `version`, `layer`, `governing_standard`, `edition` (required for real NFPA 13 work; never defaulted), `jurisdiction`, `content_basis` (`authoritative` \| `synthetic_test_only`), review status/reviewer/date, `digest()` (sha256 of content — pins exactly which rules produced a result) |
| `Rule` | `rule_id`, `category` (families from hazard applicability, spacing, protection area, distance to boundaries, ceiling, obstruction … through hydraulics, seismic, hanging/bracing, valves, documentation), `title` (a label, never copied standard text), structured `parameters` with units, `applicability`, `exceptions`, `depends_on`, `constraint` (what engines must check), `replaces` / `replaceable_by` (layering), `source`, `author`/`authored_at`, `reviewer`/`reviewed_at`, `review_status`, `effective_status`, `change_reason` |
| `RuleSource` | kind (authoritative standard, licensed structured content, AHJ amendment, referenced standard, manufacturer listing document, project specification, engineering decision record, synthetic), document, edition, section/clause **locator** (no text), revision, date. Synthetic sources must be marked and may not carry an edition or reference |
| `Condition` / `RuleApplicability` / `RuleException` | three-valued tests over explicit design facts (`hazard.classification`, `ceiling.surface`, `ceiling.slope_deg`, `sprinkler.type`, `sprinkler.orientation`, `sprinkler.response`, `space.area_sf`, …); a missing fact is UNKNOWN, never false |
| `ConstraintTemplate` | the constraint key, the geometric `measurement`, bound (max/min), which parameter is the limit, and which boundary semantics (`wall`, `window`, `door_opening`, `open_opening`) participate — **the rule decides what is measured; the engine only measures** |

**Resolution** (`resolve.py`) is deterministic and explained:

* applicability or an exception that cannot be decided (unknown fact) → **refusal**, never an assumption;
* unmet dependency → refusal; an applicable rule no deterministic engine can evaluate → refusal (a
  design is never called valid while an applicable requirement went unchecked);
* **precedence:** contributions to the same constraint key combine **most-restrictively** (later layers
  tighten, they do not override). A rule may **replace** another only if the replaced rule lists the
  replacing rule's (later) layer in `replaceable_by` — e.g. an AHJ amendment explicitly permitted to
  modify a base rule, or a listing where the base rule defers to the listing. Anything else is refused
  (`REPLACEMENT_NOT_PERMITTED`);
* every `EngineeringConstraint` lists each contribution (rule, layer, rule set + version, source,
  limit, action: governs / less_restrictive / replaced / replaces) and the governing rule;
* units convert exactly (1 in = 25.4 mm); a limit's dimension must match its measurement.

**Real-engineering policy** (`mode="engineering"`), all refusals:
exactly one **approved, authoritative NFPA 13 base** rule set; an explicit edition; every authoritative
rule set and rule approved; edition layers matching the base edition; no synthetic content; and an
explicit jurisdiction statement — amendment rule sets, or `amendments_not_evaluated`, which is then
carried as a **limitation** on the result. `mode="synthetic_test"` accepts only synthetic content.

## 2. Layer 2 — Deterministic engines (`fireai/engineering/`)

Consumes only `fireai.contract` and `fireai.rules` (enforced by `tests/test_engineering_boundary.py`).

**Explicit inputs** (`inputs.py`) — durable schemas, M2.0 supports a subset and refuses the rest:

| Input | Schema (durable) | M2.0 supports |
|---|---|---|
| `CeilingCondition` | regions (whole space or polygon), surface (flat, sloped, curved, stepped, open structure, unknown), slope (value, direction), elevation (value + **datum** + source), features (beam, soffit, cloud, structural / MEP obstruction, with footprints and bottom elevations), obstruction statement | one whole-space flat region, known 0° slope, known elevation, no features, explicit `none_present` |
| `DesignClassification` | scheme, value, reason, source (human decision / project document) | explicit only; unknown → refusal |
| `SprinklerListing` | manufacturer, model, SIN, type, orientation, K-factor, temperature, response, parameters, document id / revision / date, reviewer, version, **listing rules** (a `listing`-layer rule set) | synthetic only (no real data) |
| `EngineeringTolerances` | explicit length / area tolerances with source | required |
| `PlacementSearchSpace` | family, lattice step, max sprinklers, max per axis, excluded regions, source | `room_axis_array/1` |

**Design objects** (`design.py`): `SprinklerPlacement` (stable uid, layout uid, index, `DesignPoint`
with frame `LOCAL`, x/y in ft, **`ZState` unknown** with a reference to the ceiling region — never an
invented elevation — room-frame coordinates, listing identity, full `DesignProvenance`: request
fingerprint, package content and verification fingerprints, space, rule-set identities with digests,
listing, ceiling). `ConstraintEvaluation` (measured, limit, margin, tolerance, worst subject, governing
and contributing rule ids, references). `LayoutEvaluation`. `EngineeringDesignResult` (status
`VALID_LAYOUTS_FOUND` / `NO_VALID_LAYOUT_IN_SEARCH_SPACE` / `REFUSED`, basis, `engineering_use`,
disclaimers, refusals, limitations, context, inputs, rule resolution, search statistics, the full valid
layout set, rejected examples, ordering strategy, engine version, request and result fingerprints).

**Algorithm** (`placement.py`):

1. *Validation* collects every refusal (space gate, missing / unknown / unsupported inputs, rule
   resolution) before anything is generated.
2. *Candidate generation* (separate function): the room frame (u = long axis of the minimum rotated
   rectangle; square → closest to +x), a lattice of `k × step` strictly inside the space, all
   uniformly spaced index sets per axis, all u-set × v-set arrays up to the sprinkler bound, ordered by
   count then bottom-to-top, left-to-right (`ORDER-LONGAXIS-LR-BT/1`).
3. *Constraint evaluation* (separate function): structural containment and excluded regions, then
   constraints in a documented order (cheap first). Measurements are exact: worst boundary point
   (segment ends + bisector crossings), worst space point (vertices, bisector/edge crossings, Voronoi
   vertices), nearest-sprinkler cells (half-plane clipping), point-to-boundary, pairwise and array
   spacing. Pass/fail uses unrounded values and the explicit tolerance.
4. The **valid layout set** is returned in full; nothing is optimised. Rejected layouts record their
   first failure (in the documented order); any candidate can be re-explained in full
   (`explain_layout`).

**Synthetic marking (not console text):** `EngineeringDesignResult` refuses construction unless every
result whose basis is synthetic has `engineering_use = NOT_FOR_ENGINEERING_USE` and the disclaimers
`TEST ONLY`, `SYNTHETIC RULE VALUES`, `NOT NFPA 13 COMPLIANT`, `NOT FOR ENGINEERING USE`; synthetic
rule sets, sources and listings must carry `TEST_ONLY_SYNTHETIC` in their identifiers and cannot carry
an NFPA edition or reference; mixing synthetic and authoritative content is refused in both modes;
an authoritative non-refused result always requires qualified human approval.

## 3. Layer 3 — future orchestration (not built)

Agents may inspect context, list missing inputs, retrieve *candidate* rules for human structuring,
propose classifications (as proposals), propose layouts (as candidates to `evaluate_layout`), and
explain results. Compliance is decided only by the rules engine + deterministic evaluator.

## 4. Commercial scale

| Question | Answer |
|---|---|
| One room → 500,000 sf | by partitioning: every design request is scoped to one verified space; a building is many spaces (and later zones/systems) evaluated independently and in parallel, with cross-space constraints added as separate constraint kinds |
| many floors / thousands of sprinklers | spaces carry region (and later level) identity; placements are uid'd objects keyed to spaces — no global coordinate list |
| different conditions per area | rules resolve against each space's own facts (classification, ceiling, listing) |
| multiple system types | a system type becomes a fact and a rule-applicability condition; listings and rule sets vary per system |
| routing / hydraulics / coordination | consume `SprinklerPlacement` uids and positions with frame + unknown Z; they add their own objects referencing these uids; nobody re-reads CAD |
| clash detection | placements live in the same LOCAL frame (→ PROJECT later) as boundaries, openings, walls; Z is explicit unknown until established |
| optimisation | ranks members of the VALID set; it never redefines validity |
| edition changes / AHJ / listings / project specs | new rule sets (layers) and versions; geometry code unchanged (tests 17–19) |
| reproducibility | request fingerprint = package fingerprints + rule-set digests + listing + inputs + search + engine + ordering; identical requests give byte-identical results |

**Measured cost** (M2.0 search family): see `REAL_DRAWING_VALIDATION.md` §M2.0. Candidate count grows
roughly with the square of the lattice count per axis raised to the number of axes (`O(K_u² · K_v²)`
for arrays), and evaluation per candidate with the sprinkler count cubed for the worst-space-point
measurement. The REAL_002 bathroom (17 × 15 lattice, ≤ 4 sprinklers) evaluates 21,263 candidates in
≈ 31 s single-threaded. This family is suitable for single rooms only. Strategy for commercial scale:

* partition by space/zone (and ceiling region) — the unit of work stays small;
* prune per axis before the product (spacing and wall-distance limits bound the index sets);
* incremental / dominance pruning (a layout that fails a monotone constraint prunes its extensions);
* cache per-point measurements (already done for point-to-boundary);
* coarse-to-fine lattices and constraint-derived bounds on sprinkler count;
* return valid sets compactly (array parameters) instead of materialising every placement;
* parallel evaluation per space; incremental recomputation keyed by request fingerprints.

## 5. Before the FIRST real NFPA 13 rule set

Needed from the owner / qualified reviewer (FireAI cannot supply any of these):

1. the NFPA 13 **edition** to structure first, and lawful access to it (licensed copy or licensed
   structured content);
2. the **scope** of the first rule set: which occupancy / hazard classifications, sprinkler types and
   orientations, ceiling configurations;
3. for every requirement in that scope, a qualified person's structured record: section locator,
   parameters with units, applicability conditions, exceptions, dependencies, **which geometric
   measurement it maps to** (and which boundary kinds participate — e.g. how door openings and windows
   are treated), and a reviewer's approval;
4. the mapping of protection-area and spacing definitions onto measurements: if the standard's
   definitions are not one of the existing measurements, a new measurement is added as a reviewed
   engine change first;
5. the jurisdiction statement for the first trial (amendment rule sets, or an explicit
   "amendments not evaluated" limitation);
6. real listing data for the chosen sprinkler(s), from the manufacturer's document (id, revision,
   date) with the listing's own constraints structured as listing-layer rules;
7. the design classification, ceiling condition (height + datum, flat, no features, an explicit
   obstruction statement) and tolerances for the trial space, each attributed;
8. independently authored and signed known-answer cases for the rule set (FireAI output is never the
   reference).

## 6. Milestone 2.1 additions

**Editions are data.** `fireai/rules/catalog.py` registers rule-set identities and development
envelopes per (standard, edition). `NFPA13-2025-BASE` v1 is created EMPTY and DRAFT. NFPA 13-2022, or
any later edition, is added by registering another identity and entering its rules, with no engine code
change. `resolve()` refuses with `EDITION_MISMATCH` when a non-base layer declares a different
`base_edition` or a base rule cites a different edition. Editions are never mixed.

**Authoring and two-person review** (`fireai/rules/store.py`, `fireai/engineering/listings.py`):
- Storage is write-once versioned files plus an append-only event log.
- Lifecycle: draft → under_review → reviewed → approved → superseded / retired.
- The reviewer must not be the author, and the set approver must have authored none of its rules.
- Approval runs `approval_problems` / `listing_problems`. These check the source locator, edition,
  access metadata, units and dimension, known measurement and boundary kinds, facts in the `FACTS`
  vocabulary, and internal dependencies.
- A requirement FireAI cannot yet measure is recorded with `UNSUPPORTED_MEASUREMENT`. If that rule
  applies, the design refuses.
- Identities are unauthenticated names and are not production safe.

**Supported envelope** (`fireai/engineering/envelope.py`): in engineering mode, the explicit inputs
must match the envelope registered for the base standard and edition. The inputs checked are system
type, storage, classification, sprinkler type and orientation, ceiling surface and construction, and
the obstruction statement. Otherwise the design refuses with `OUTSIDE_SUPPORTED_ENVELOPE`,
`MISSING_SYSTEM_CONDITION` or `NO_SUPPORTED_ENVELOPE`. There is no fallback classification. Ceiling
refusals now carry `detail.condition`: surface, slope, elevation change, beam/soffit/cloud features,
obstructed construction, unknown obstruction statement, or unknown elevation or slope.

**Exact pruned search** (`fireai/engineering/search.py`, `exact_pruned/1`): this uses the same
candidate space as M2.0 (`room_axis_array/1`), evaluated as a pipeline:
1. per-axis index sets;
2. count bounds;
3. point constraints, ranked per lattice point (bitmask);
4. pairwise constraints;
5. coverage;
6. the full measure.

A stage rejects a candidate only if the full evaluation would reject it for the same constraint, and
every rejection is attributed. `reference_search` keeps the M2.0 brute force. Tests and the benchmark
show identical valid sets and candidate counts.

**Valid-set representation** (`ValidLayoutSet`, `room_axis_array_indices/1`):
- The complete valid set is stored as families (n_u, ds_u, n_v, ds_v), each with its origin offsets on
  the lattice, plus the room frame and step.
- Explicit layouts with full explanations are included up to `max_explicit_layouts` (200). Beyond
  that, `iter_valid_layouts` enumerates them on demand.
- Implemented: an exact, lossless family encoding, and a status taken from the complete set.
- Deferred: symbolic (interval) encoding of offsets, ranking or optimisation over the set, and
  multi-space sets.

**Agent candidate interface** (`evaluate_proposal`): an external proposer, such as a future agent,
submits `CandidateProposal` positions in frame LOCAL, pinned to the package fingerprints, rule-set
digests and listing digest. The deterministic engine returns one of:
- PASS;
- FAIL;
- UNKNOWN, when a constraint can't be evaluated (for example, array spacing of a non-array layout);
- REFUSED, for a stale fingerprint, wrong frame, version mismatch or any design refusal.

A proposal never changes validity and is never ground truth.

**Project persistence** (`fireai/project/`):
- Hierarchy: project → building → level → design area (space refs), with versioned inputs.
- Each design revision stores its request and result write-once.
- `currency()` reports CURRENT; STALE when rules, listing or inputs changed; or INVALIDATED when the
  verified model or its verification changed.
- A layout selection must be a member of the valid set.
- Downstream consumers re-materialise placements from the stored request, never from CAD.

## 7. Milestone 2.2A: measurement foundation

These are software requirements derived from the owner's review of NFPA 13-2019 source material.
The repository still holds no rule values. Tests: `tests/test_m22a_measurements.py`.

**S×L protection area** (`array_sxl_protection_area`, SXL-ARRAY/1, `fireai/engineering/measure.py`):
- It is a separate, named measurement. `nearest_sprinkler_cell_area` (Voronoi) is unchanged and does
  not stand in for it: in test 7 the two differ (120 vs 140 sf) and the requested one is used.
- For each sprinkler of a rectangular room-frame array, S is taken along u and L along v.
- Each dimension is the larger of its two sides. A side's value is the distance to the adjacent
  sprinkler, or, with no adjacent sprinkler, twice the perpendicular distance to the participating
  wall reference reached on that side.
- The explanation records, per sprinkler and per side, which geometry produced each value (adjacent
  sprinkler, or wall kind, segment uid and distance).
- The S×L product doesn't depend on which axis is called S, and the room frame makes it independent
  of plan rotation (test 6).

**Perpendicular wall distance** (`perpendicular_wall_distance`, PERP-WALL/1): for each sprinkler and
each array direction with no adjacent sprinkler, the distance to the first boundary reached. That
boundary must be perpendicular to the direction, within the explicit length tolerance, and of a kind
the rule lists in `reference_kinds`. A window, door opening, open opening or unknown segment is never
silently a wall. If the rule doesn't list it, the measurement is NOT EVALUABLE, which is never a pass.

**Irregular walls:** if any boundary segment is angled relative to the array frame, a request with a
wall-reference measurement refuses up front (`IRREGULAR_BOUNDARY_UNSUPPORTED`). Straight-wall logic is
never applied to angled walls. Two measurement contracts are declared but not implemented:
`angled_wall_perpendicular_distance` and `angled_wall_protected_floor_worst_distance`. A rule that
uses one can't be approved, and if it applies the design refuses (`MEASUREMENT_NOT_IMPLEMENTED`).
The worst-horizontal-distance measurement `space_point_to_nearest_sprinkler_max` already works for
any polygon. A boundary segment of unknown kind refuses too (`BOUNDARY_KIND_UNKNOWN`).

**Derived limits** (`ConstraintTemplate.derived = DerivedLimit(op="scale", from_key, factor_parameter)`):
the limit is a factor times the effective limit of another constraint.
- The referenced limit is the one the resolver computed (most restrictive, after replacements), so
  the derived value follows it when that rule changes.
- The factor is a dimensionless parameter of the same rule.
- Keys are resolved in dependency order, and the result is deterministic and unit-safe.
- There is one operation (`scale`): no expressions and no `eval`.
- It is covered by the digest, because the template is part of the rule set.
- These refuse: a missing reference (`DERIVED_DEPENDENCY_MISSING`), a reference that didn't resolve
  (`DERIVED_DEPENDENCY_UNRESOLVED`), a cycle (`DERIVED_DEPENDENCY_CYCLE`), and a factor with a unit or
  a cross-dimension reference (`DERIVED_UNIT_MISMATCH`).
- Each contribution's `derived` field records the op, source key, source limit, its governing rule
  and the factor, and `explain()` prints them.
- Approval requires a numeric factor and a source constraint in the same rule set.

**New facts, each explicit and attributed:**
- `system.design_method` (`hydraulically_calculated`, `pipe_schedule`, `other` or `unknown`) is
  separate from `system.type`. Unknown means the fact is absent.
- `ceiling.construction_classification` (+ `.scheme`) is the standard's construction classification,
  stated by a person. It is separate from the geometric `ceiling.construction`, `surface`, `slope`
  and `elevation`.
- `space.eligibility.<name>` comes from `DesignRequest.eligibility`: a named person's decision with
  the criteria considered. Only `eligible` activates a provision; an explicit `unknown` never does.
  With no decision the fact is absent, and a rule that needs it refuses. Area alone never sets it.

**Vertical deflector measurement** (`ceiling_to_deflector_vertical_distance`): ceiling elevation minus
deflector elevation, on one explicit datum, signed so that positive means below the ceiling.
- The deflector position is an explicit input (`DeflectorPosition`: frame plus `Elevation`). With it,
  placements carry `Z = from_input`; without it Z stays `unknown`.
- A vertical rule refuses with `VERTICAL_CEILING_ELEVATION_UNKNOWN`, `SPRINKLER_Z_UNKNOWN`,
  `WRONG_COORDINATE_FRAME` or `VERTICAL_DATUM_MISMATCH`.
- In the search, the measurement is evaluated once in a new GLOBAL stage.
- This starts the 3D path. Sloped and stepped ceilings are still refused.

**Fingerprints:** the request fingerprint now also covers `system`, `eligibility` and `deflector`. The
M2.1 fingerprint omitted `system`; that gap is now fixed. The engine version is `placement/0.2.0`, so
fingerprints from earlier engines differ by design. Valid sets for the old measurements are unchanged
(test 25, and the REAL_002 regression).

**Editions:** `NFPA13-2019-BASE` and `NFPA13-2025-BASE` are separate EMPTY DRAFT identities. No
engine or rules-mechanism module contains an edition literal (a structural test checks this).
Mixing editions refuses (`MULTIPLE_BASE_RULESETS`, `EDITION_MISMATCH`), and both refuse real
engineering while empty. 2019 has no development envelope yet, so it also refuses with
`NO_SUPPORTED_ENVELOPE`.

## 8. Milestone 2.2A.1: branch-line orientation, the 2019 development envelope, release gate

**S/L correction.** This supersedes the §7 wording "S along u". In SXL-ARRAY/2, S is measured along the
BRANCH-LINE direction and L perpendicular to it, between branch lines. The room's long axis never
defines S.

The branch-line direction is a first-class, fingerprinted and provenance-bearing design input,
`LayoutOrientation`, with these fields:
- `frame`;
- `branch_line_direction`, a line direction canonicalised so its sign doesn't matter;
- `strategy`;
- `version`;
- `source`.

The perpendicular direction is `cross_line_direction`. The orientation can come from four places:
- a person, as `explicit_design_input`;
- a named, versioned default strategy, `ROOM-LONG-AXIS-DEFAULT/1` (`default_orientation`), which
  returns nothing when the long axis is ambiguous;
- a future routing engine;
- an optimiser.

A proposal (`CandidateProposal.orientation`) may carry its own orientation, so different orientations
can be evaluated and compared. A missing orientation refuses (`LAYOUT_ORIENTATION_MISSING`), as do a
wrong frame and a direction that neither array axis follows (`ORIENTATION_NOT_ALIGNED_WITH_ARRAY_FRAME`).
The orientation is part of:
- the request fingerprint;
- the layout (`array.orientation`);
- the result inputs;
- project `dependencies_of`, so changing it makes stored designs STALE. The M2.2A gap is also
  closed: `eligibility` and `deflector` are now tracked there too.

There are three measurements over the same per-sprinkler S and L. They use the same geometry and each
explains which geometry produced each value:
- `array_sxl_protection_area` (S×L);
- `array_sxl_s_dimension`;
- `array_sxl_l_dimension`.

Because the S×L product is symmetric, the separate S and L limits are where orientation becomes an
engineering difference (tests 1–2 and 13). The Voronoi `nearest_sprinkler_cell_area` stays a separate
measurement.

**NFPA13-2019-DEV-ENVELOPE-1** (`fireai/rules/catalog.py`) is a supported development envelope, not a
rule set, and it holds no values. It covers:
- Light Hazard;
- wet pipe;
- hydraulically calculated;
- non-storage;
- standard spray pendent;
- ceiling construction classification `noncombustible_unobstructed`;
- a smooth, flat, horizontal single-plane ceiling with a known elevation, no features and no
  obstructions;
- orthogonal straight walls;
- `room_axis_array/1`;
- an explicit orientation;
- an explicit small-room decision of `not_eligible`.

Anything else refuses (`OUTSIDE_SUPPORTED_ENVELOPE` / `MISSING_*`). The envelope doesn't approve
`NFPA13-2019-BASE`. The 2025 envelope from M2.1 is unchanged and never inherits 2019 conditions.

**Release gate:** see `SOURCE_AUTHORIZATION_AND_RELEASE.md`. Engineering status and release
authorization are separate. External use of NFPA-derived content requires owner-recorded
`COMMERCIAL_AUTHORIZED` status. Today 2019 is internal R&D only and 2025 is not available.

## 9. Milestone 2.2B, phase 1: first NFPA 13-2019 rule-package workflow (stopped at owner input)

**Envelopes:** `NFPA13-2025-DEV-ENVELOPE-1` is now `inactive_not_supported`. It is kept with its status
history but never applied, so 2025 engineering refuses with `NO_SUPPORTED_ENVELOPE`. The 2025 identity
stays EMPTY and DRAFT. `NFPA13-2019-DEV-ENVELOPE-1` is `active_internal_rnd` and adds three conditions:
- every boundary segment must be a solid `wall`;
- rules may use only `["wall"]` as their wall reference;
- the listing's installation style must be `exposed`. `SprinklerListing.installation_style` is new.

**Intake** (`fireai/rules/intake.py`) holds the fixed mapping `M22B_MAPPINGS`, from locator to
key, measurement, bound and wall kinds. The owner input template is generated from the same code and
checked by a test, so the two can't drift. `load_rule_package` reports every missing or unconfirmed
field at once. It refuses titles and notes that look like copied text, identifiers in the
document-control reference, and fewer than three distinct people (author, reviewer, approver).

**Known answers** (`fireai/rules/known_answers.py`): cases are write-once and audit-logged. They are
worked by hand, never from FireAI output: the author and provenance can't name FireAI, a model or an
agent. Each case is reviewed by someone other than its author and verified by running the
deterministic engine (`tests/fixtures/known_answer_harness.py`).
- A verification pins the rule's content digest, so editing a rule invalidates its old verifications.
- A mismatch is recorded, and the case's expected values are never rewritten.
- `RuleStore(…, known_answers=…)` refuses to approve an NFPA-derived rule until it has at least two
  reviewed, verified cases for its exact content. This applies to both rule review and set approval.

**Modes:**
- `rule_review` runs rules still in draft or under review, labelled RULES UNDER REVIEW. It is used
  only for known-answer verification.
- `rule_validation` runs approved authoritative rules with a SYNTHETIC listing, with disclaimers
  including NOT A PRODUCT-SPECIFIC DESIGN.
- Neither mode can produce an engineering result.

**Search:** an exact rectangle fast path decides perpendicular wall distance and the S and L
dimensions at the axis stage. It applies when the space is a frame-aligned rectangle whose every
segment is a participating kind. It is proven equal to the brute-force reference. The commercial
fixture (57.5 × 38 ft at 0.5 ft, up to 24 sprinklers) evaluates 89.2M candidates in about 7 s.

**Status summary** (`fireai/engineering/status.py`): shows ENGINEERING RULE STATUS / SOURCE
AUTHORIZATION / EXTERNAL RELEASE side by side. It never claims customer-, AHJ- or production-ready or
commercially licensed.
