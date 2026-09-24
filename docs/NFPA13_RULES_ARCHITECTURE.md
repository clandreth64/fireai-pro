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
