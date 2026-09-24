# Engineering Input Contract (version 3 — `engineering_input/3`)

**Purpose:** define exactly what Drawing Understanding must deliver before any engineering engine may
run, and freeze the boundary:

```
Drawing Understanding  (adapters: DWG, DXF, future IFC / Revit / manual input)
        ▼
Verified Normalized Model  (BuildingModel, schema 0.5.0, HUMAN_VERIFIED)
        ▼  build_engineering_input()  — the only door
EngineeringInput  (this contract)
        ▼
Engineering  (does not exist yet)
```

The contract is **format-independent**. Engineering does not know or care whether a wall came from a
DWG, a DXF, an IFC file or a human correction. It receives normalized geometry, explicit states and
uids for traceability. It **never** reaches back into CAD parsing.

Implemented as executable code: `fireai/contract/engineering_input.py`
(`build_engineering_input`, `engineering_input_blockers`, `parse_engineering_input`,
`read_legacy_engineering_input`, `space_engineering_blockers`, `EngineeringInput`) and `fireai/contract/debug.py`
(`perimeter_rows`, `cyclic_kinds`). Tests: `tests/test_engineering_boundary.py`,
`tests/test_m19_boundaries_contract.py`, `tests/test_contract_space_gate.py`. It performs no engineering and
produces no design results.

## 1. Contents

| Concept | Field(s) | Notes |
|---|---|---|
| Contract version | `contract_version` | `engineering_input/3` (see §3 for the version history and compatibility) |
| Model identity | `model_id`, `schema_version`, `source_sha256`, `document_guid`, `xref_sha256`, `engine_version` | identifies exactly which inputs and which interpretation |
| Interpretation content | `content_fingerprint` | deterministic digest of the interpretation content (`fireai/review/content.py`); identical for identical interpretations regardless of run metadata |
| Verification | `verification_fingerprint`, `verified_by`, `verified_at` | `verified_by` is an **unauthenticated name** today |
| Selected plan/view | `regions[]` (uid, view_type, view_type_source, bbox) | only regions a person selected at verification; view type FLOOR_PLAN or REFLECTED_CEILING_PLAN |
| Coordinate frame | `frame = "LOCAL"`, `units = "ft"`, `source_to_local` (4×4) | all geometry is LOCAL feet; the SRC transform allows exact traceback |
| Units | `source_units` | resolved and verified; never assumed |
| Building / level context | `spatial_context` | `status: "unassigned"` when unknown — engineering must treat it as unknown |
| Physical regions (room enclosures) | `spaces[]` (uid, label, polygon, area, origin human/machine, review status, `derived_from` uids, **`boundary`**) | rejected machine rooms are excluded; human boundaries are marked `origin: human` |
| Classified boundary (v3) | `spaces[].boundary`: `rings[]` → ordered `segments[]` | see §1a |
| Semantic (named) spaces | `semantic_spaces[]` (uid, label, `boundary_state: known`, `region_uid`) | only spaces with a KNOWN boundary pass; an unresolved one is a blocker. A known space's boundary is its region's boundary |
| Openings (v3) | `openings[]` | see §1b |
| Wall / obstruction geometry | `walls_analysis[]` (derived pieces: thickness, centerline/arc; `derived: true`), `walls_linework[]`, `columns[]` | derived geometry is labelled as derived |
| Source provenance | uids in `derived_from` / `face_element_uids` / `fill_element_uid` | resolvable in the model; no handles, layers, block names or CAD entities |
| XREF state | `xrefs[]` (name, status, sha256) | only reachable when every XREF is resolved (blocker below) |
| Conversion-loss state | `conversion_significance` | only reachable when it is not `material` |
| Human corrections | `human_corrections_applied[]` | correction ids from the review store |
| Z | `z_status = "unknown"`; `boundary.plane_z_status`, `vertical_extent` on segments and openings = `unknown` | the contract carries no elevations |
| Not provided | `not_provided[]` | ceiling height/geometry, elevations/levels, hazard/occupancy classification, sprinkler type/listing, applicable ruleset/edition, obstructions above/below the plan, construction type, water supply |

### 1a. Classified boundary (`spaces[].boundary`)

A **plan projection** of the region boundary on a plane of unknown elevation
(`representation: "plan_projection"`, `frame: LOCAL`, `units: ft`, `plane_z_status: unknown`).

* `rings[]`: `role` `outer` (inner rings = holes, reserved) and `orientation` `ccw` (the region lies
  to the LEFT of every segment). The ring starts at the lowest (then left-most) vertex, so the order
  is deterministic.
* `segments[]`, in order, closed and gap-free, reproducing the region polygon exactly:

| Field | Meaning |
|---|---|
| `uid`, `index` | stable identity, position along the ring |
| `kind` | `wall` · `window` (glazing in a wall) · `door_opening` · `open_opening` (doorless) · `unknown` |
| `encloses` | `true` = physically closes the region (wall, window) · `false` = passage (door or doorless opening) · `null` = not established (`unknown`) |
| `start_local_ft`, `end_local_ft`, `length_ft` | geometry (LOCAL feet) |
| `opening_uid`, `fill_element_uid` | the `openings[]` object this segment belongs to, and the door/window element filling it |
| `derived_from` | uids of the evidence: wall linework, wall-analysis pieces, door/window elements |
| `confidence`, `requires_verification`, `rules` | how certain, and which rule decided (§1c) |
| `vertical_extent` | `{status: unknown}` — the future home of the face's bottom/top |

* `complete` = no `unknown` segment; `length_by_kind_ft` sums lengths per kind.

**The question it answers:** *which portions of this room's perimeter are bounding walls, and where
are the openings?* Wall = `kind in (wall, window)`, `encloses: true`. Openings = `door_opening` /
`open_opening`, each linked to one `openings[]` object. Anything FireAI could not establish is
`unknown` — **never** assumed to be a wall.

### 1b. Openings (`openings[]`)

One object per **physical** opening (the void) that crosses the boundary of a selected space.

| Field | Meaning |
|---|---|
| `uid`, `id` | stable identity |
| `kind` | `door` · `open` (doorless) · `window` · `unknown` (reserved for adapters that report an opening without its type; FireAI's 2D rules never produce it) |
| `passable` | door/open: `true`; window: `false`; unknown: `null` |
| `footprint_local_ft` | plan footprint of the void (the hull of the boundary segments that cross it: a rectangle through the wall when both faces bound regions, else the span line) |
| `width_ft`, `depth_ft` | width along the wall; wall depth when both faces are region boundaries, else `null` |
| `fill_element_uid`, `fill_category` | the door/window element, when one exists |
| `space_uids`, `boundary_segment_uids` | which selected spaces it connects, and their segments |
| `derived_from`, `confidence`, `requires_verification`, `review_status` | provenance and state |
| `vertical_extent` | `{status: unknown}` (sill/head come from sections, BIM or a person — never defaulted) |

Openings are keyed by their fill element (or, for doorless openings, by the wall pieces around the
gap). The wall analysis can report several overlapping gaps for one door (a known limitation of that
analysis layer); they map to ONE opening and are listed on the model's opening element
(`analysis_gap_ids`) for traceability. Openings that lie wholly inside one region (for example
inside an unresolved open-plan region) are not boundary openings and are not in the package.

### 1c. Boundary rules (Drawing Understanding, `fireai/interpret/boundaries.py`)

| Rule | Classification |
|---|---|
| `B-WALL-FACE` | the edge lies on wall linework (source geometry) → `wall` |
| `B-WALL-BAND` | the edge lies inside a derived wall piece (e.g. a polygon drawn on the centreline) → `wall` |
| `B-WINDOW` | a window element sits in the wall outside the edge (its centre within the window strip) → `window` |
| `B-DOOR-CLOSURE` | the edge is FireAI's analysis line across a door opening → `door_opening` |
| `B-DOOR-GAP` | an edge portion without wall, spanned by a door element → `door_opening` |
| `B-OPEN-OPENING` | an edge portion without wall at a doorless wall-analysis opening → `open_opening` |
| `B-OPENING-IN-WALL-LINE` | openings are only recognised ALONG a wall line; a diagonal analysis line (across a wall corner, between two door leaves) stays `unknown` |
| `B-UNKNOWN` | no evidence → `unknown` (+ review trigger `REGION_BOUNDARY_UNCLASSIFIED`) |
| `O-OPENING` | one `opening` element per physical opening crossed by region boundaries |

## 2. Hard blockers (engineering cannot start)

First `require_verified_model` runs **unchanged**. Its blockers:

1. model is not `HUMAN_VERIFIED` (UNREVIEWED, REVIEW_REQUIRED or INVALIDATED — e.g. source, XREF,
   units, engine version, corrections or interpretation content changed since verification);
2. units unresolved;
3. any XREF not loaded (missing, circular, units unresolved, load failed, …). The gate blocks **every**
   absent XREF, not only "material" ones, because FireAI cannot know an absent XREF is immaterial;
4. material DWG conversion loss (source or XREF);
5. a selected region that is not a plan view.

Then the contract adds:

6. invalid or missing SRC → LOCAL coordinate transform;
7. no selected plan region;
8. a selected region with **no** room/space boundary (required room boundary unresolved);
9. a selected region containing an unresolved **merged-room** boundary, or a semantic space whose
   boundary is **unresolved** (several named spaces in one open region). A person must reject the
   region or replace it with human room boundaries;
10. stored human corrections that were not applied to this model (or exist only for another revision);
11. (v3) a physical region in a selected region without a classified boundary (a model produced before
    schema 0.5.0 — reprocess the drawing);
12. (v3) a model without an interpretation content fingerprint (produced before schema 0.5.0).

Any blocker raises `ContractViolation` with every reason listed. There is no override flag.

`unknown` boundary segments are **not** a package blocker (the region is still a known enclosure);
they are flagged (`complete: false`, trigger `REGION_BOUNDARY_UNCLASSIFIED`).

**Single-space gate.** Engineering on ONE space calls `space_engineering_blockers(package, space_uid)`
(semantic-space or physical-region uid). It uses only the package (no model, no CAD) and refuses:

* a space whose boundary is not established everywhere (any `unknown` segment): distances to walls
  are undefined there;
* a physical region without exactly one known named space (unless the caller explicitly does not
  need a named space);
* an id that is not in the package;
* a boundary segment that references an opening missing from the package.

## 3. Versions, compatibility and reproducibility

| Version | Milestone | Content |
|---|---|---|
| `engineering_input/1-draft` | pre-M2 checkpoint | first executable contract (never persisted by the product) |
| `engineering_input/2-draft` | M1.8 | + `semantic_spaces[]` |
| `engineering_input/3` | M1.9 | + classified `boundary` on every space, + `openings[]`, + `content_fingerprint`; blockers 11–12 |

* **No silent reinterpretation.** `parse_engineering_input(data)` accepts only
  `engineering_input/3`. An older package raises `ContractVersionError` ("regenerate it from the
  verified model"); nothing converts v2 into v3, because v2 never contained the boundary
  classification or openings that v3 guarantees.
* **v2 packages stay readable** for audit and reproducibility with `read_legacy_engineering_input`
  (`EngineeringInputV2Draft`, read-only). They must not be given to engineering.
* **Regeneration is required.** Build v3 from a model produced by the current engine (schema 0.5.0).
  A model migrated from 0.4.0 has no boundary classification: the contract blocks it (blockers 11–12)
  until the drawing is reprocessed. The migration never fabricates boundaries or openings.
* **Reproducibility.** A package records `engine_version`, `schema_version`, `content_fingerprint`
  and `verification_fingerprint`. The same source, XREFs, units, corrections and engine produce the
  same content fingerprint; engineering results must cite it and become stale when it changes.

## 4. Rules for engineering code

* Engineering code lives in `fireai/engineering/` (M2.0: single-space placement) and the rules engine in
  `fireai/rules/` (docs/NFPA13_RULES_ARCHITECTURE.md). Engineering may import `fireai.contract` and
  `fireai.rules`; the rules engine may import neither the engineering engines nor CAD code. Neither may
  import `ezdxf`, `fireai.ingest`, `fireai.interpret`, `fireai.render`, `fireai.pipeline`, `fireai.jobs`,
  `fireai.api` or the raw `fireai.model`. Enforced by `tests/test_engineering_boundary.py`.
* Engineering results reference contract uids and the `verification_fingerprint` / `content_fingerprint`
  they were computed from. If a fingerprint changes, results are stale.
* Engineering must treat every `not_provided` item as missing until supplied explicitly. It must
  never default one. `unknown` segments are never treated as walls.
* Changing the contract is a versioned, reviewed change, never an ad-hoc field addition.

## 5. Known upstream limitations (carried into the contract's scope)

* Paper-space-drawn views (e.g. risers, legends) are not region-classified.
* Regions without titles or evidence stay UNKNOWN. A person must set the view type before they can
  be selected.
* Rooms merged through openings need human boundaries (blocker 9). Selection is per view region, so
  an unresolved open-plan area blocks every space on that floor until it is resolved.
* Human-drawn room boundaries get a classified boundary but do not yet create `semantic_spaces[]`
  entries of their own.
* Door-closure pairing (M1.7, `G-DOOR-OPENING-CLOSURE`) can cut a diagonal analysis line across a
  wall corner or between two door leaves. v3 classifies such a segment `unknown` (never a doorway);
  the room polygon itself is unchanged.
* The wall analysis layer can report overlapping duplicate gaps for one door and misses some openings;
  openings in the contract are derived from region boundaries and door/window elements instead.
* Declared-but-wrong units may go undetected. The human `units` confirmation at verification is the
  control.
* Fire alarm and fire sprinkler content are not yet separated semantically.
* The reviewer name is not authenticated.
* LibreDWG cannot decode some objects (these are invisible to the conversion audit), and its memory
  use is pathological on some files (about 4.6 GB for a 5 MB DWG).
* No Z / ceiling data comes from 2D plans.
