# FireAI Pro — Spatial / BIM Architecture

**Status:** Milestone 1.5 architecture review. Documents the target architecture and records which
foundations were implemented now (schema **0.2.0**) and which are deliberately deferred.
Nothing here implements sprinkler design, hydraulics, BIM coordination, clash detection, or fabrication.

Governing rule: **never guess, never silently default, never claim success when input understanding failed.**
Unknown values stay unknown — including Z, elevation, building, and level.

---

## 1. Current normalized model (schema 0.2.0)

One `BuildingModel` per ingested source file (a *drawing-understanding record*):

```
BuildingModel (schema_version 0.2.0)
├─ source            SourceInfo: filename, format, sha256, source_uid, document_guid, version_guid,
│                    DXF version, converter {name, version, command}, converted_dxf_sha256,
│                    converter_log_tail, converter_warnings
├─ units             $INSUNITS / user override → scale_to_normalized (ft); never guessed
├─ coordinate_frames SRC, SRC_FT, LOCAL (defined) · PROJECT (unresolved)
├─ transform         SRC → LOCAL (0.1.0 compatibility)
├─ spatial_structure project/buildings/levels — "unassigned" (never invented)
├─ entities[]        SourceEntity  ← WHAT THE FILE CONTAINS
│     id, uid, handle, handle_path, uid_basis, source_object_key, type, layer, space,
│     parent_id, block_path, visible, supported, source (SRC), normalized (LOCAL), z_range,
│     attributes, provenance{origin:"source"}
├─ elements[]        BuildingElement ← WHAT FIREAI BELIEVES
│     id, uid, category, subtype, label, confidence, evidence, rules, source_entity_ids,
│     requires_verification, geometry (LOCAL), properties,
│     placement{building_id, level_id, elevation_ft, height_ft, thickness_ft, rotation_deg, z_status},
│     provenance{origin:"deterministic_inference", engine, engine_version, rule_ids, derived_from, review}
├─ unclassified_entity_ids, title_block, scale (dimension checks), layers, blocks
├─ diagnostics       warnings · errors · review_triggers
└─ fixed facts       geometry_is_synthetic=false · ready_for_design=false ·
                     engineering_review_status="not_performed" · ai_inference_used=false
```

## 2. Current 2D limitations

| Limitation | Consequence | Status |
|---|---|---|
| Plan geometry is XY only | No 3D solids, no clearances in Z | Source Z recorded in `z_range`, never promoted |
| One model = one source file | Multi-sheet / multi-file projects not unified | Future `ProjectModel` (§4) |
| No levels | Everything is "unassigned" | Correct — levels are not established by a plan DXF |
| Walls are linework | No thickness/centerline/height | Wall pairing is a later interpretation task |
| Rooms are 2D polygons | No ceiling height/volume | Ceiling data (RCP) would come from other sources |
| PROJECT frame unresolved | Files cannot yet be aligned | Requires a human/adapter decision (§5) |

## 3. Future 3D spatial architecture

Two layers, as today, but with full spatial capability:

```
SOURCE LAYER (per file, immutable once ingested)          SEMANTIC LAYER (authoritative engineering model)
┌──────────────────────────────────────┐                 ┌───────────────────────────────────────────┐
│ SourceDocument (sha256, uid, format,  │                 │ Project                                   │
│   document_guid, adapter, frames)     │                 │  └ Building                               │
│  └ SourceObject (uid, native id:      │  provenance     │     └ Level (elevation datum, height,     │
│     handle / IFC GlobalId / Revit     │◄────────────────│        status: from_source|user|unknown)  │
│     UniqueId, geometry in SRC,        │  derived_from   │        └ SpatialElement                   │
│     native properties)                │                 │           uid, category, geometry (3D),   │
└──────────────────────────────────────┘                 │           placement, relationships,        │
                                                         │           provenance, review state         │
                                                         └───────────────────────────────────────────┘
```

**Spatial element geometry (future):** a `Geometry3D` with `frame` (always explicit), representation
type (`curve2d_on_plane` | `extrusion` | `brep` | `mesh` | `swept_solid`), plus derived `aabb`
(axis-aligned bounds) for spatial indexing. Current 2D geometry maps to `curve2d_on_plane` with
**unknown** plane elevation — it is never extruded into an invented solid.

**Relationships (future):** typed edges (`hosts`, `bounds`, `contains`, `connects`, `supports`,
`clearance_to`) between spatial elements by uid. Example: door `hosted_by` wall; room `bounded_by` walls.

## 4. Project → Building → Level hierarchy

```
Project (uid, name, PROJECT frame definition, vertical datum)
 ├─ Building (uid, name, status)                      0..n per project
 │   └─ Level (uid, name, elevation_ft?, height_ft?, kind: basement|floor|mezzanine|roof|penthouse|
 │             riser|split, status: from_source|user|unknown)
 │        └─ SpatialElement.placement.level_id
 └─ SourceDocuments (each with its own SRC frame and SRC_FT→PROJECT transform, possibly unresolved)
```

Rules: a level exists only if a source establishes it (IFC `IfcBuildingStorey`, Revit level, a
human) — a floor-plan DXF titled "LEVEL 2" is *evidence*, recorded as such, not an assignment.
Implemented now: `spatial_structure` container and `placement.{building_id, level_id, assignment_status}`
defaulting to `unassigned`. Not implemented: Project/Building/Level objects themselves.

## 5. Coordinate / transform strategy

**Review of the Milestone 1 lower-left normalization.** It did **not** destroy information: source
coordinates are kept on every entity and the transform was stored, so it is reversible. But its origin
is **content-dependent** — adding one stray entity shifts every normalized coordinate — and two files
that share CAD coordinates (e.g. floor 1 and floor 2 drawn on a shared base point) get different
origins. It is therefore unsuitable as a basis for project coordinates. Schema 0.2.0 makes frames explicit:

```
 SRC (drawing WCS, drawing units)                                        [every entity.source]
   │  scale s (units → ft), no translation
   ▼
 SRC_FT (drawing WCS in feet)  ── stable across edits; shared between files with shared WCS
   │ translate(−origin·s)                        │ UNRESOLVED until established
   ▼                                             ▼
 LOCAL (lower-left origin, display frame)      PROJECT (coordinated frame: rotation + translation
   [entity.normalized, element.geometry]         + elevation datum; from survey point / shared
                                                  coordinates / IFC map conversion / human)
```

* Every `Geometry` carries `frame`. Frames carry 4×4 affine matrices (parent → child).
* `fireai.spatial.transform_point` converts between frames and **raises `FrameUnresolved`** for PROJECT —
  callers must handle "we do not know where this is in the project".
* Future linked models (Revit links, IFC federations, XREFs) are additional SourceDocuments with their
  own SRC and a `SRC_FT → PROJECT` transform (rotation about Z, translation XYZ). Rotation angles
  are stored as matrices, not re-derived.
* Elevation: Z in SRC is a *drawing value*. A vertical datum belongs to the PROJECT frame and levels.
  FireAI never converts a drawing's Z=0 into "elevation 0".

FireAI can answer both questions required for coordination:
*"Where was this in the source file?"* → `entity.source` (SRC) + `handle_path`;
*"Where is it in the coordinated model?"* → SRC → SRC_FT → PROJECT (once PROJECT is established).

## 6. Source geometry vs semantic objects

Unchanged principle, now with identity: `SourceEntity` (what the file contained) and
`BuildingElement` (what FireAI believes) are separate lists joined by `source_entity_ids` and
`provenance.derived_from` (uids). A line is not a wall; a rectangle is not a room; a block is not a
sprinkler — until a rule with evidence says so, with confidence and a verification flag. Future
semantic categories (floors, ceilings, roofs, beams, joists, ducts, equipment, cable tray…) extend the
`category` vocabulary; the join model does not change.

## 7. Input adapter strategy (CAD / BIM)

```
      ┌────────────┐ ┌────────────┐ ┌────────────┐ ┌───────────────────────┐
      │ DXF adapter│ │ DWG adapter│ │ IFC adapter│ │ Revit adapter          │
      │  (ezdxf)   │ │ →DXF conv. │ │(IfcOpenShell)│(export/plugin/API)     │
      └─────┬──────┘ └─────┬──────┘ └─────┬──────┘ └──────────┬────────────┘
            └──────────────┴───────┬──────┴───────────────────┘
                                   ▼
          SourceDocument + SourceObjects (native ids, SRC geometry, native properties)
                                   ▼
          Interpretation (rules; for BIM mostly *mapping* native classes, still with provenance)
                                   ▼
                     FireAI normalized engineering model
```

* Adapters own format knowledge; the model owns meaning. No model field is named after a vendor.
* Native identity is kept in `handle_path` / future `native_id` (IFC GlobalId, Revit UniqueId).
* BIM sources usually *carry* semantics (IfcWall, IfcBuildingStorey). Adapters map them with
  `provenance.origin="source"` and rule ids like `IFC-CLASS-IfcWall`, and still record what was not mapped.
* Revit: via IFC export first (vendor-neutral); a plugin/API adapter only if IFC loses required data.

### 7a. DWG conversion strategy

| Option | Status | License / deployment | Coverage | Notes |
|---|---|---|---|---|
| **LibreDWG `dwg2dxf`** (current) | Built from pinned release 0.14.8597, commit `34f02f54`, sha256-verified tarball | GPL-3.0+; FireAI invokes it as a separate program (no linking), so FireAI code is not GPL-bound. Distributing a container that *includes* the binary is distribution of GPL software: ship source/offer and license text | Good for R13–2018 in our corpus; weaker for newest versions, proxy/custom objects, some dynamic-block data | Deterministic, reproducible, no license fee |
| ODA File Converter | **Not used** (owner decision) | Free, but non-members may use it for non-commercial applications only (ODA FAQ) | Very high fidelity | Requires ODA membership for commercial use |
| ODA Drawings SDK | Future option | Commercial membership | Highest | Linkable C++ SDK |
| Autodesk Platform Services (Model Derivative) | Future option | Commercial cloud API, per-use cost; sends drawings to a third party | Native fidelity | Data-residency review needed |
| Require customers to upload DXF | Always available | None | Depends on customer's CAD | Current fallback message |

**Measured on the Milestone 1.5 corpus (LibreDWG 0.14.8597):** geometry of converted entities matched the
native DXF to ~3×10⁻¹⁰ drawing units (numerical noise); spline flattenings differ in point count only. But the
converter **silently omits some entities** — ACAD_TABLE (2000/2018 files), WIPEOUT ×2, INSERT ×2 and a proxy
entity (R14 file) — and its log mentions only some of them. FireAI therefore runs an **independent entity census**
(`dwgread` JSON) and compares it with the converted DXF: losses raise `DWG_CONVERSION_LOST_ENTITIES`; an audit
that cannot run raises `DWG_CONVERSION_AUDIT_UNAVAILABLE`. Cost: the census JSON is large (176 MB for a 5.4 MB
DWG; conversion 8.5 s → ~98 s, peak ~4.6 GB in `dwgread`), so a lighter census is required before production.

The `DwgConverter` interface is unchanged by this choice; results record converter name, version and
command; converter log lines reporting warnings/errors are kept in `source.converter_warnings`.

## 8. Future sprinkler system graph (not implemented)

A sprinkler system is an **engineering network**, not graphical primitives:

```
WaterSupply ─► Valve(s) ─► Riser ─► FeedMain ─► CrossMain ─► [Tee] ─► BranchLine ─► [Tee/Outlet]
                                                                   └► Armover/Drop ─► Sprinkler
```

* **Nodes:** sprinklers, fittings (elbow, tee, cross, reducer, coupling, outlet), valves, drains,
  inspector's test, pumps, supply interfaces. **Edges:** pipe segments.
* **PipeSegment (future):** `uid, system_uid, start_node, end_node, start_xyz, end_xyz (PROJECT frame),
  nominal_diameter, internal_diameter, material, schedule, c_factor, role (feed/cross/branch/drop/armover),
  geometric_length, fabrication_cut_length, slope, end_preps, outlets[], hangers[], provenance,
  coordination_status, fabrication{piece_uid, release_state}`.
* One graph serves every consumer: drawings render it, hydraulics solve it, BIM exports it, coordination
  moves it, fabrication cuts it. **No subsystem keeps its own pipe representation**; subsystems add
  *views* (e.g. hydraulic node numbering) keyed by uid.
* Design objects get `provenance.origin="design"` with the rule/solver version that produced them and
  `derived_from` pointing at the building elements that constrained them (rooms, ceilings, obstructions).

## 9. Future clash-detection architecture (not implemented)

Questions such as *does this pipe intersect steel/duct/other MEP, is clearance maintained, what is the
routing envelope, can this branch move up/down/sideways* require:

1. **Real 3D geometry** for every participating object in the PROJECT frame (solids or swept profiles,
   with insulation/hanger allowances), with known units and tolerances.
2. **Resolved frames** for every source (a clash between objects in unrelated frames is meaningless).
3. A **spatial index** (BVH/R-tree on AABBs) and exact tests (swept-cylinder vs solid, min distance).
4. **Clearance rules** as versioned data (per system/material/code edition).
5. **Unknown handling:** objects with unknown Z or unresolved frames are reported as
   *not evaluable* — never as "no clash".

Deterministic geometry decides whether a clash exists. An LLM must never decide intersection; AI may
later rank valid alternatives produced by a deterministic router.

## 10. Design-to-fabrication digital thread (not implemented)

```
Project ─► SprinklerSystem ─► DesignedPipe ─► CoordinatedPipe ─► FabricationPiece ─► Label/Production
   (uid)        (uid)            (uid)        (uid, derived_from)   (uid, derived_from, cut list,
                                                                     stock optimisation, release)
```

Each step creates new objects with `provenance.derived_from` pointing upstream, so a shop label traces
back to the design and the project. Drawings, BOMs, cut lists and labels are **generated from the
model**; FireAI never parses its own PDF/DXF output to recover what it designed.

## 11. Provenance strategy

Implemented now (`Provenance`): `origin` (source | deterministic_inference | design | human),
`engine`, `engine_version`, `rule_ids`, `derived_from` (uids), `review` (unreviewed | confirmed |
rejected | modified, by, at, note). This answers: which file (via source_uid), which source object
(uid/handle_path), direct vs inferred, which rule, which engine version, human review state, and —
by following `derived_from` in reverse — which downstream objects came from it.

Deferred: an append-only change log (who changed what, when, during coordination), release/approval
states for fabrication, and signatures. These extend `Provenance`/`ReviewState` without changing the
meaning of existing fields. No event-sourcing system is built now.

## 12. Schema-versioning strategy

* `schema_version` on every persisted model; `fireai.schema.load_model` is the only loader.
* Known older versions are migrated **step by step** (`0.1.0 → 0.2.0` implemented and tested with a
  real 0.1.0 model); unknown or newer versions are **refused** (`UnsupportedSchemaVersion`).
* Migrations only derive information that exists or mark new information unknown; they record
  themselves in the returned `applied` list.
* Planned line: `0.x` drawing understanding (2D → 3D building understanding) → `1.x` ProjectModel with
  buildings/levels/spatial elements → `2.x` sprinkler system graph + hydraulics results → `3.x`
  coordination state → `4.x` fabrication. Major bumps mean meaning changed and require explicit
  migrations; minor bumps only add optional fields.
* Persisted data must never be re-interpreted in place by a newer engine: re-interpretation produces a
  new model with a new `engine_version` in provenance.

## 13. Changes made NOW (Milestone 1.5, schema 0.2.0)

| Change | Why now | Tests |
|---|---|---|
| Deterministic `uid` for entities/elements (uuid5 namespaced by source sha256 + handle path) | Persisted data would otherwise need id rewrites later | uniqueness, determinism, file scoping, block handle paths |
| `document_guid`/`version_guid` + `source_object_key` | Cross-revision matching needs the DXF identity recorded at ingest | recorded & keyed |
| Explicit `frame` on every Geometry; `coordinate_frames` with matrices; PROJECT unresolved | Retro-fitting frame semantics onto stored coordinates is error-prone | round trip, reproduces LOCAL, SRC_FT stable under edits, PROJECT raises |
| `z_range` on entities (source Z recorded, never promoted) | 3D sources otherwise lose Z silently | 3D line recorded, elevation stays unknown |
| `placement` (building/level/elevation/height/thickness/rotation, z_status) defaulting to unknown | Future data needs a home; defaults must mean "unknown", not 0 | all unknown; door rotation from source |
| `spatial_structure` container (unassigned) | Hierarchy slot without inventing buildings/levels | unassigned |
| `provenance` on entities and elements | Traceability must exist from the first persisted record | derived_from = source uids |
| `schema.py` with migration 0.1.0→0.2.0 and refusal of unknown versions | Persisted models must not silently change meaning | real 0.1.0 fixture migrates; unknown refused |
| DWG→DXF linkage: `converted_dxf_sha256`, converter log/warnings | Conversion must be auditable | recorded |

## 14. Changes that explicitly WAIT

ProjectModel/Building/Level objects · 3D geometry representations and solids · relationship graph ·
IFC/Revit adapters · PROJECT frame establishment UI · spatial index and clash engine · sprinkler
system graph · hydraulics · coordination/change log · fabrication objects · event sourcing ·
production database schema. None are needed to validate drawing understanding, and building them now
would be speculative.

## 15. Risks and tradeoffs

| Risk | Mitigation |
|---|---|
| uid depends on file sha256 → a re-saved drawing gets new uids | `source_object_key` (document GUID + handle path) supports cross-revision matching; matching will be explicit, never assumed |
| `$FINGERPRINTGUID` is copied by "Save As" | Used only as a *candidate* key, never as unique identity |
| DXF files without handles | `uid_basis="sequence"` flags uids that are unstable across edits |
| LibreDWG fidelity gaps | Converter warnings recorded; DWG vs DXF equivalence tests; conversion failures fail closed |
| GPL obligations if the converter binary ships in a product image | Process isolation; legal review before commercial distribution |
| 2D elements lack Z; future 3D work may be tempted to "assume ceiling height" | `z_status`/`elevation_ft=None` semantics and tests forbid defaults |
| Model size (full source inventory per file) | Acceptable at current scale; later: store source layer separately, reference by uid |
| Frames add complexity for simple consumers | LOCAL remains available; frame is explicit on every geometry |
