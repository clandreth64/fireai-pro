# FireAI Pro 2.0 — Milestone 1: Drawing Understanding

**Scope:** DXF/DWG ingestion → normalized building model → visual verification overlay → understanding report.
**Out of scope (not implemented, not claimed):** sprinkler layout, pipe routing, hydraulics, code compliance, permit documents.

Governing rule: **never guess, never silently default, never report success when input understanding failed.**

---

## 1. Before vs after

| | v1 (audited) | v2 milestone 1 |
|---|---|---|
| Entry point | `api/app.py` → design engine → LLM reviewer → drawing engine | `api/app.py` → `fireai.api.app.create_app()` → `/api/v2/drawings` |
| DWG | raw bytes base64'd to Claude as `image/png` | converter interface (ODA / LibreDWG); none installed → `DWG_CONVERSION_UNAVAILABLE` |
| DXF | LINE/LWPOLYLINE/TEXT/MTEXT; wrong mm/m factors; coords never re-origined; rooms clamped into an invented rectangle | all common entity types, blocks exploded with lineage, WCS/OCS correct, exact unit factors, reversible transform |
| Units | guessed from extent magnitude; mm ×3.281 | `$INSUNITS` or explicit user override only; unknown → `needs_human_input` |
| Building | `_synthetic()` templates from `total_area`; gap-fill "Unclassified Area" | only drawing-backed elements; `geometry_is_synthetic` is a `Literal[False]` |
| Interpretation | LLM Vision coordinate guesses | deterministic rules with rule ids, evidence, confidence, verification flag |
| Output | "compliant" DXF/PDF/IFC set | source render, overlay (PNG/SVG/DXF), JSON model, JSON report, Markdown summary |
| Status | `complete` whenever AI flag true; errors swallowed | `processing_status` ∈ completed / needs_human_input / failed; `engineering_review_status` always `not_performed` |
| Downloads | `Path(output_dir) / filename` (DB disclosure) | registered deliverable ids only, resolved inside the job's deliverables dir |
| CORS / auth | `*` / none | same-origin by default / optional bearer token hook |
| Tests | none | 156 tests (153 pass + 3 environment-dependent skips with LibreDWG installed) + golden-file framework + real-drawing validation harness |

```
                     POST /api/v2/drawings  (multipart: file, units?)
                                   │  size limit (streaming), ext + content sniff, filename sanitised
                                   ▼
 ┌────────────────────────── fireai.pipeline.understand_drawing ──────────────────────────┐
 │ validate_upload ─► [DWG] DwgConverter.convert ─► load_dxf (ezdxf.recover)               │
 │        ─► extract (source entities, WCS, block lineage, paper-space inventory)          │
 │        ─► EMPTY_DRAWING / GEOMETRY_EXTRACTION_FAILED guards                             │
 │        ─► resolve_units ──unresolved──► source.png + inventory ─► needs_human_input     │
 │        ─► normalize (transform: origin + exact scale → feet)                            │
 │        ─► interpret (rules.py + elements.py) ─► checks.py (dimension/area/extent/...)   │
 │        ─► render: source.png, overlay.png/.svg, overlay.dxf                             │
 │        ─► report: building_model.json, understanding_report.json, understanding_summary.md │
 └─────────────────────────────────────────────────────────────────────────────────────────┘
        any PipelineFailure → failed / needs_human_input ;  any other exception → INTERNAL_ERROR (failed)
```

## 2. Package layout

| Module | Responsibility |
|---|---|
| `fireai/errors.py` | `FailureCode` enum, `PipelineFailure` |
| `fireai/config.py` | env settings with restrictive defaults |
| `fireai/model.py` | canonical building model (Pydantic, schema `0.2.0`; JSON schema in `docs/building_model.schema.json`; migrations in `fireai/schema.py`) |
| `fireai/ingest/filetype.py` | extension + content sniffing, filename sanitising |
| `fireai/ingest/dwg.py` | `DwgConverter` interface, `OdaFileConverter`, `LibreDwgConverter`, `select_converter` |
| `fireai/ingest/units.py` | `$INSUNITS` resolution, exact factors, unit-resolution requirement |
| `fireai/ingest/extract.py` | deterministic source extraction, bounds, normalization |
| `fireai/interpret/rules.py` | layer/block naming rules (data, with ids) |
| `fireai/interpret/elements.py` | source entities → building elements |
| `fireai/interpret/text.py` | room labels, stated areas, dimension strings, title-block regexes |
| `fireai/interpret/checks.py` | review triggers |
| `fireai/render/overlay.py` | verification images and overlay DXF |
| `fireai/report.py` | JSON report + Markdown summary |
| `fireai/pipeline.py` | stage orchestration (thin; no business logic) |
| `fireai/jobs/` | SQLite job store, isolated per-job storage, runner |
| `fireai/api/` | FastAPI app factory, routes, security middleware, UI |

## 3. Building model (schema 0.2.0 — see docs/SPATIAL_BIM_ARCHITECTURE.md for identity, frames, placement, provenance)

Two strictly separate layers:

* **`entities`** — *what the drawing contains*. Each `SourceEntity`: `id`, DXF `handle`, `type`, `layer`, `space`, `parent_id` + `block_path` (for block contents), `visible`, `supported`, `source` geometry (WCS, drawing units), `normalized` geometry (feet), `attributes`.
* **`elements`** — *what FireAI believes they are*. Each `BuildingElement`: `id`, `category`, `subtype`, `label`, `confidence`, `evidence[]`, `rules[]`, `source_entity_ids[]`, `requires_verification`, normalized `geometry`, `properties`.

Plus: `source` (filename, format, sha256, DXF version, converter provenance), `units`, `transform` (`normalized = (source − origin) × scale`), `bounds_source`, `bounds_normalized`, `scale` (dimension checks, viewport scales, title-block scale text), `layers`, `blocks`, `unclassified_entity_ids`, `title_block`, `diagnostics` (`warnings`, `errors`, `review_triggers`), `requires_human_review`, `assumptions`, and fixed fields `geometry_is_synthetic=false`, `ready_for_design=false`, `engineering_review_status="not_performed"`, `ai_inference_used=false`.

Element categories: wall, door, window, room, area, column, stair, shaft, structural, grid_line, text_annotation, dimension, title_block, ceiling, existing_fire_protection, existing_mep. Anything not classified is listed in `unclassified_entity_ids` and drawn in orange on the overlay.

## 4. Failure states

| Code | When | processing_status |
|---|---|---|
| `UNSUPPORTED_FORMAT` | extension not .dxf/.dwg | failed (HTTP 415 at upload) |
| `INVALID_DRAWING` | empty file, content ≠ extension, not a DXF/DWG, unparseable DXF | failed (HTTP 422 at upload when detectable) |
| `FILE_TOO_LARGE` | over `FIREAI_MAX_UPLOAD_MB` | HTTP 413 |
| `DWG_CONVERSION_UNAVAILABLE` | DWG uploaded, no converter configured/installed | failed |
| `DWG_CONVERSION_FAILED` | converter error/timeout/no output/output not DXF/output unreadable | failed |
| `EMPTY_DRAWING` | no model-space entities | failed |
| `GEOMETRY_EXTRACTION_FAILED` | no visible supported geometry; entity limit exceeded | failed |
| `UNIT_DETECTION_FAILED` | `$INSUNITS` missing/0/unsupported/invalid | **needs_human_input** (re-run via `POST /{id}/resolve-units`) |
| `OVERLAY_GENERATION_FAILED` | source or overlay rendering failed | failed |
| `INTERNAL_ERROR` | any unexpected exception | failed |

A completed model that still needs a person carries `requires_human_review = true` and the `DRAWING_REQUIRES_HUMAN_REVIEW` semantics via its `review_triggers`.

## 5. Conditions that trigger human review

| Trigger | Condition |
|---|---|
| `UNIT_DETECTION_FAILED` | units unresolved (blocking) |
| `UNITS_OVERRIDE_CONFLICTS_WITH_HEADER` | user units differ from `$INSUNITS` |
| `EXTENTS_IMPLAUSIBLE` | largest extent < 8 ft or > 5,000 ft |
| `DIMENSION_TEXT_DISAGREES` | dimension override text ≠ measured geometry (>1% and >1/8") |
| `DIMENSION_SCALE_FACTOR` | a dimension uses DIMLFAC ≠ 1 |
| `ROOM_AREA_LABEL_MISMATCH` | stated room area differs from computed polygon area by >5% |
| `UNASSOCIATED_ROOM_LABELS` | room-label text not inside any detected room |
| `NO_WALLS_DETECTED` / `NO_ROOMS_DETECTED` | none found |
| `GEOMETRY_OUTSIDE_BUILDING` | non-title-block geometry extent > 4× wall extent |
| `XREF_NOT_RESOLVED` | external references present (geometry missing) |
| `UNSUPPORTED_ENTITIES` | unsupported entities > 5% of top-level entities (else warning) |
| `DXF_AUDIT_ERRORS` | unrepairable structural errors |
| `ELEMENTS_REQUIRE_VERIFICATION` | any element with confidence < 0.80, ambiguous/missing room label, polygonized rooms, title-block fields, conflicting block/layer evidence |
| `HIGH_UNCLASSIFIED_FRACTION` | > 30% of top-level entities unclassified |
| `MULTIPLE_DRAWING_REGIONS` | ≥ 2 significant separate regions in model space (several plans, sections, details) — added 1.5 |
| `ROOM_BOUNDARY_SPANS_MULTIPLE_LABELS` | one derived boundary contains several different room names (merged through openings) — added 1.5 |
| `DWG_CONVERSION_LOST_ENTITIES` | independent DWG entity census shows types missing from the converted DXF — added 1.5 |
| `DWG_CONVERSION_AUDIT_UNAVAILABLE` | a DWG was converted but its entity census could not be verified — added 1.5 |

Even with no triggers the model is **not** ready for design: a person must still compare `source.png` with `overlay.png`.

## 5b. Interpretation rules added in Milestone 1.5 (from real-drawing failures)

| Rule | Behaviour |
|---|---|
| `G-VIEW-REGIONS` | Clusters visible model-space geometry by gap (max(5 ft, 3% of extent)); regions reported, elements tagged `view_region`; never decides which region is the building |
| `G-DOOR-OPENING-CLOSURE` | When rooms are derived from wall linework, wall vertices within a detected door's footprint are bridged (1.5–8 ft, shortest first, never crossing walls) with *analysis lines* stored on the room (`door_closures`) — never walls. Rooms using them are verification-required (confidence 0.55) |
| label handling | Each text entity is parsed separately; different names inside one boundary are never concatenated — the region becomes `suspected_merged_region`, unnamed, confidence ≤ 0.3 |
| `T-FINISH-NOTE` | Label lines made only of finish/annotation vocabulary (HRWD FLOOR, TILE, 9'-0" CLG, GFI…) are notes, not names |
| dynamic blocks | Anonymous `*U/*B` copies are classified by their effective name (AcDbBlockRepBTag) |
| `L-WALL-QUALIFIER` | Wall layers qualified ABOVE/BELOW/OVHD/DEMO/EXIST/FUTURE… become `qualified_*` walls, confidence ≤ 0.5, verification required |
| MULTILEADER | Text content extracted (leader lines not) |
| cavity filter | Faces with mean width 2A/P < 1.5 ft (incl. rings around the building) are wall cavities, not rooms |
| unit evidence | When units are missing: viewport scale × stated scale × plot paper units → `suggested_units`, returned with the requirement, **never applied** |

## 6. Where AI/LLM inference is used

**Nowhere in this pipeline.** `ai_inference_used` is a `Literal[False]`. All classification is rule-based. If AI assistance is added later it must: only propose classifications (never geometry), record model + prompt version, be cached for determinism, and always set `requires_verification`.

## 7. Assumptions still in effect

1. Model space is drawn at full scale; verified only where dimension override text can be compared.
2. Only model space is building geometry; paper space is inventoried for text/title blocks/viewport scales.
3. One 2D plan level per drawing; Z ignored; multiple plans in one model space are not separated (flagged by `GEOMETRY_OUTSIDE_BUILDING` when detectable).
4. Off/frozen/invisible entities are excluded.
5. Block contents on layer `0` inherit the INSERT's layer.
6. Curves flattened (chord tolerance 0.2% of entity size; arcs via Bézier ≈0.03% radial error).
7. Semantic roles come from naming conventions (NCS/AIA + keywords); unmatched names stay unclassified.
8. Normalized units are feet; origin is the lower-left of visible model-space geometry (not the building corner).
9. Wall elements are single linework entities — wall thickness, centerlines, and wall pairing are not derived.
10. Door/window "nominal width" is the larger side of the block footprint, not a verified clear opening.

## 8. Security posture

* Downloads: only deliverable ids registered in the job record; resolved path must be a direct child of that job's `deliverables/` dir. Job ids: 128-bit random hex, regex-validated.
* Storage: `FIREAI_DATA_DIR` (default `.fireai_data/`, git-ignored); uploads stored as `upload.<ext>`, never under the client's filename.
* Upload limit enforced while streaming (ASGI middleware) and while copying; content sniffing before processing.
* CORS: disabled unless `FIREAI_CORS_ORIGINS` is set. Security headers (nosniff, frame deny, CSP, no-referrer). SVG served as attachment.
* Subprocesses (DWG converters) use argument lists, no shell, with timeouts.
* **Authentication: NOT production-ready.** Only an optional shared bearer token (`FIREAI_API_TOKEN`). All routes go through `require_principal`, and job rows have an `owner` column, so real per-user auth and tenant isolation can be added in one place. **Do not expose this service publicly without authentication.**

## 9. Configuration

| Env var | Default | Meaning |
|---|---|---|
| `FIREAI_DATA_DIR` | `<repo>/.fireai_data` | job DB + files |
| `FIREAI_MAX_UPLOAD_MB` | 100 | upload limit |
| `FIREAI_CORS_ORIGINS` | *(empty)* | comma-separated allowed origins |
| `FIREAI_API_TOKEN` | *(unset)* | shared bearer token |
| `FIREAI_DWG_CONVERTER` | `auto` | `auto` / `oda` / `libredwg` / `none` |
| `FIREAI_ODA_CONVERTER`, `FIREAI_LIBREDWG_DWG2DXF` | PATH lookup | converter executables |
| `FIREAI_DWG_TIMEOUT_S` | 180 | conversion timeout |
| `FIREAI_MAX_ENTITIES` | 500000 | extraction limit (fails closed) |
| `FIREAI_MAX_CONCURRENT_JOBS` | 2 | in-process job concurrency |

## 10. Running

```bash
docker build -f docker/Dockerfile -t fireai:dev .
docker run --rm fireai:dev pytest -q
docker run --rm -p 8000:8000 fireai:dev          # UI at http://localhost:8000
python scripts/generate_golden_fixtures.py       # regenerate synthetic golden drawings
```

The Dockerfile lives in `docker/` so it does not change how Railway builds the service. The Procfile is unchanged.

## 11. Known limitations

* DWG conversion uses GNU LibreDWG 0.14.8597 (built in `docker/Dockerfile`); ODA is not used. LibreDWG drops some entity types (ACAD_TABLE, WIPEOUT, some INSERTs/proxies on R14) — detected by the conversion audit.
* Real-drawing validation: 11 drawings / 15 files (see `docs/REAL_DRAWING_VALIDATION.md`); ground truth pending human confirmation.
* Rooms come from closed polylines on room/area layers, or from regions fully enclosed by wall linework. Door gaps break enclosure, so wall-only drawings with open doorways yield few rooms (reported as `NO_ROOMS_DETECTED` rather than invented).
* No wall-thickness/centerline derivation; no door-to-wall hosting; no opening detection from wall gaps.
* No XREF resolution, no multi-level separation, no hatch-based room detection, no MLEADER/TABLE text, no proxy/ACIS entities.
* Grid bubbles are left unclassified (drawn orange).
* Jobs run in-process (FastAPI BackgroundTasks); a restart loses running jobs (they stay `running`).
* SQLite + local disk: not horizontally scalable; no object storage yet.
