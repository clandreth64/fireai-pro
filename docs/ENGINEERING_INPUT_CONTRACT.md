# Engineering Input Contract (draft 1 — `engineering_input/1-draft`)

**Purpose:** define exactly what Drawing Understanding must deliver before any engineering engine may
run, and freeze the boundary:

```
Drawing Understanding  (adapters: DWG, DXF, future IFC / Revit / manual input)
        ▼
Verified Normalized Model  (BuildingModel, schema 0.3.0, HUMAN_VERIFIED)
        ▼  build_engineering_input()  — the only door
EngineeringInput  (this contract)
        ▼
Engineering  (does not exist yet)
```

The contract is **format-independent**. Engineering does not know or care whether a wall came from a
DWG, a DXF, an IFC file or a human correction. It receives normalized geometry, explicit states and
uids for traceability. It **never** reaches back into CAD parsing.

Implemented as executable code: `fireai/contract/engineering_input.py`
(`build_engineering_input`, `engineering_input_blockers`, `EngineeringInput`). Tests:
`tests/test_engineering_boundary.py`. It performs no engineering and produces no design results.

## 1. Contents

| Concept | Field(s) | Notes |
|---|---|---|
| Contract version | `contract_version` | `engineering_input/1-draft`; will be versioned like the schema |
| Model identity | `model_id`, `schema_version`, `source_sha256`, `document_guid`, `xref_sha256`, `engine_version` | identifies exactly which inputs and which interpretation |
| Verification | `verification_fingerprint`, `verified_by`, `verified_at` | `verified_by` is an **unauthenticated name** today |
| Selected plan/view | `regions[]` (uid, view_type, view_type_source, bbox) | only regions a person selected at verification; view type FLOOR_PLAN or REFLECTED_CEILING_PLAN |
| Coordinate frame | `frame = "LOCAL"`, `units = "ft"`, `source_to_local` (4×4) | all geometry is LOCAL feet; the SRC transform allows exact traceback |
| Units | `source_units` | resolved and verified; never assumed |
| Building / level context | `spatial_context` | `status: "unassigned"` when unknown — engineering must treat it as unknown |
| Room / space boundaries | `spaces[]` (uid, label, polygon, area, origin human/machine, review status, `derived_from` uids) | rejected machine rooms are excluded; human boundaries are marked `origin: human` |
| Wall / obstruction geometry | `walls_analysis[]` (derived pieces: thickness, centerline/arc; `derived: true`), `walls_linework[]`, `columns[]` | derived geometry is labelled as derived |
| Source provenance | uids in `derived_from` / `face_element_uids` | resolvable in the model; no handles, layers or CAD entities |
| XREF state | `xrefs[]` (name, status, sha256) | only reachable when every XREF is resolved (blocker below) |
| Conversion-loss state | `conversion_significance` | only reachable when it is not `material` |
| Human corrections | `human_corrections_applied[]` | correction ids from the review store |
| Z | `z_status = "unknown"` | the contract carries no elevations |
| Not provided | `not_provided[]` | ceiling height/geometry, elevations/levels, hazard/occupancy classification, sprinkler type/listing, applicable ruleset/edition, obstructions above/below the plan, construction type, water supply |

Everything in `not_provided` must reach engineering as **separate, explicit, recorded design inputs**
(see `MILESTONE_2_0_SPEC.md`). Drawing understanding never infers them.

## 2. Hard blockers (engineering cannot start)

First `require_verified_model` runs **unchanged**. Its blockers:

1. model is not `HUMAN_VERIFIED` (UNREVIEWED, REVIEW_REQUIRED or INVALIDATED — e.g. source, XREF,
   units, engine version or corrections changed since verification);
2. units unresolved;
3. any XREF not loaded (missing, circular, units unresolved, load failed, …). The gate blocks **every**
   absent XREF, not only "material" ones, because FireAI cannot know an absent XREF is immaterial;
4. material DWG conversion loss (source or XREF);
5. a selected region that is not a plan view.

Then the contract adds:

6. invalid or missing SRC → LOCAL coordinate transform;
7. no selected plan region;
8. a selected region with **no** room/space boundary (required room boundary unresolved);
9. a selected region containing an unresolved **merged-room** boundary. A person must reject it or
   replace it with human room boundaries;
10. stored human corrections that were not applied to this model (or exist only for another revision).

Any blocker raises `ContractViolation` with every reason listed. There is no override flag.

## 3. Rules for future engineering code

* Engineering code lives in `fireai/engineering/` (not yet created). It may import
  `fireai.contract` only. It may not import `ezdxf`, `fireai.ingest`, `fireai.interpret`,
  `fireai.render`, `fireai.pipeline`, `fireai.jobs`, `fireai.api` or the raw `fireai.model`. This is
  enforced by `tests/test_engineering_boundary.py`, which applies automatically once the package exists.
* Engineering results reference contract uids and the `verification_fingerprint` they were computed
  from. If the fingerprint changes, results are stale.
* Engineering must treat every `not_provided` item as missing until supplied explicitly. It must
  never default one.
* Changing the contract is a versioned, reviewed change (`engineering_input/2`, …), never an ad-hoc
  field addition.

## 4. Known upstream limitations (carried into the contract's scope)

* Paper-space-drawn views (e.g. risers, legends) are not region-classified.
* Regions without titles or evidence stay UNKNOWN. A person must set the view type before they can
  be selected.
* Rooms merged through openings need human boundaries (blocker 9).
* Declared-but-wrong units may go undetected. The human `units` confirmation at verification is the
  control.
* Fire alarm and fire sprinkler content are not yet separated semantically.
* The reviewer name is not authenticated.
* LibreDWG cannot decode some objects (these are invisible to the conversion audit), and its memory
  use is pathological on some files (about 4.6 GB for a 5 MB DWG).
* No Z / ceiling data comes from 2D plans.
