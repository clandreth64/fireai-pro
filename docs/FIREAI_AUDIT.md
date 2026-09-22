# FireAI Pro — Forensic Technical Audit

| | |
|---|---|
| **Repository** | `github.com/clandreth64/fireai-pro` — branch `main`, 334 commits, 2025‑08‑12 → 2026‑06‑25 |
| **Audited commit** | `6aaba4a` (2026‑06‑25, "Update fireai_nfpa13_design_engine.py") |
| **Audit date** | 2026‑09‑22 |
| **Scope** | Read-only. Nothing pushed, no Railway access, no production contact. Local clone has its push URL disabled. |
| **Method** | Static import-graph tracing, source reading, and **execution** of the live server in Docker (`python:3.12-slim` + `requirements.txt` + poppler). Synthetic test drawings only; no customer drawings were used. |

Status vocabulary used throughout: **VERIFIED · PARTIAL · BROKEN · MOCK · NOT IMPLEMENTED · UNKNOWN**. "VERIFIED" means I executed it and inspected the result — not that a file or endpoint exists.

---

## 1. Executive summary

**What actually works today**

- The FastAPI server starts, accepts jobs, persists them in SQLite, runs them in the background, and serves downloads. (VERIFIED)
- Given a *project form* (occupancy, area, ceiling height, supply), a deterministic Python engine produces a sprinkler grid, a tree-shaped pipe layout, a hanger list, a BOM and a hydraulic "worksheet" in ~14 seconds, and the drawing engine writes 7 valid, openable DXF sheets + PDFs + XLSX + JSON + IFC. (VERIFIED as *artifacts*; not as engineering — see below.)
- The DXF sheet generator (`fireai_drawing_engine.py`) is real, reasonably layered (FP‑ layer standard, block symbols, title block), and produces files that `ezdxf.recover` opens with 0 errors.

**What does not work**

- **Drawing understanding does not exist.** On every input path, the building the design is based on is either (a) guessed by an LLM from a 100‑dpi image, or (b) *invented* from `total_area` by a template (`_synthetic()` → "Open Office / Corridor / Lobby", "Main Warehouse / Tire Center / Food Service", …).
- **DWG is not supported.** A `.dwg` is base64-encoded and sent to Claude labelled `image/png`. A garbage file named `fake.dwg` produced a job marked **complete + compliant with 9 deliverables** — identical to the result from a real DXF.
- **DXF geometry is discarded.** A correctly drawn test DXF (3 rooms, labels, door block, dimension) lost every room; the design used the invented template while reporting `geometry_synthetic: False`. Metric DXFs are mis-scaled by 1,000×.
- **Hydraulics are not a network calculation.** Pipe lengths in the "critical path" are fractions of the building diagonal; the worksheet synthesizes an idealized square remote area instead of walking the routed pipes; the water-supply curve uses the **wrong exponent (0.54 instead of 1.85)**, which *overstates available pressure above the test flow by up to ~24 psi* in my test; design density is not enforced (light-hazard test case delivers 145.7 gpm against a 150 gpm requirement and is marked compliant).
- **Failures produce "successful" jobs.** With no Anthropic key, the AI compliance reviewer throws an auth error — the job is still recorded `status: complete, compliant: true, requires_human_review: false`. Missing water-supply data is silently replaced with 72 psi / 61.2 psi / 1,500 gpm defaults.
- **Security:** no authentication on any endpoint, CORS `*`, and a verified file-disclosure bug that lets anyone download the SQLite job database (all customers' project data) from a failed job.
- The **"giant orchestrator"** (`master_fireai_orchestrator.py`, 5,499 lines) has had a **syntax error since at least 2025‑09‑29** and is not used. ~53,000 of the repo's ~66,500 Python lines are unreachable from the deployed server.

**Biggest technical risk**

Not a bug — a *category* problem: **the system produces confident, professional-looking, "compliant" deliverables that are disconnected from the input drawing**, and the UI markets them as "AHJ-ready" / "Stamped PDF". The pipeline has no concept of *uncertainty* or *provenance*: an invented building, an invented water supply and a failed AI review all flow through to a green "compliant" result. For a life-safety product, this is the risk to eliminate first.

**Bottom line recommendation:** Salvage the *infrastructure ideas* and the DXF *sheet-rendering* code; **rebuild** ingestion, building model, rules, layout, and hydraulics from scratch with test-first discipline. Remove the ~53k lines of dead code from the working tree (it remains in Git history).

---

## 2. Current architecture map (what actually runs)

```
Railway (Nixpacks/Railpack auto-detect — no Dockerfile, no railway.toml ever committed)
  Procfile: web: uvicorn api.app:app --host 0.0.0.0 --port ${PORT:-8000}
  single process, single container, local disk only

api/app.py  (FastAPI, 692 lines)
 ├─ lifespan: init SQLite (job_store, agent_config_store)
 │            start dispatcher loop (polls SQLite every 5 s)
 │            start "improvement loop" (nightly 02:00 UTC LLM prompt rewriting — output never used)
 │
 ├─ POST /api/generate            (JSON form only → geometry = {})
 │     └─► _run_job()
 │
 ├─ POST /api/generate/upload     (multipart files + form JSON)
 │     └─► run_with_documents()   (FastAPI BackgroundTask, in-process)
 │           1. for each PDF: fireai_project_extractor.extract_project_context()
 │                 pdftotext regexes  +  Claude Vision on cover page & "floor plan" page
 │           2. agentic.grade_extraction()  (field presence checks; inserts seismic "D1", Sch 40 defaults)
 │           3. if total upload < 50 MB:
 │                 1 file  → fireai_document_processor.handle_upload()   (Claude Vision 2-pass; DXF/IFC stubs)
 │                 n files → fireai_document_intelligence.handle_document_set() (Claude Vision per page)
 │              else: skip geometry entirely (OOM workaround)
 │           4. _run_job(ctx, geometry)
 │
 └─ _run_job()
       A. agentic.loop.run_design_with_repair()
            NFPA13DesignEngine(geometry, ctx).design()      ← deterministic, but see §6–8
              normalize_geometry → (falls back to _synthetic(ctx) template)
              _build_zones → _place_sprinklers (bbox grid) → _route_pipes_tree
              _hydraulic_calc_hw → hydraulic_worksheet reconciliation
              _hanger_schedule → _valve_schedule → _bill_of_materials → _compliance_check
            if pressure deficit: size_fire_pump() and flip §22 flag to "pass"
       B. FireAIOrchestrator.run(design_output)  (fireai_orchestrator_v2.py)
            ONE Claude call: "NFPA 13 reviewer" over a summary dict
            then Claude "AHJ agent" → permit-package JSON (discarded)
            on failure: circuit breaker → SMTP email (fireai_email_escalator)
       C. FireAIDrawingEngine.generate_selected()  → FP0.0–FP6.0 DXF (+ matplotlib PDFs, + 3D DXF)
       D. fireai_export.write_requested()          → hydraulics.json, bill_of_materials.xlsx, model.ifc
       E. files written to ./outputs/<job_id>/ on container disk; job row updated in ./fireai_jobs.db
```

Everything else in the repo (see §3) is **not reachable** from this entry point — confirmed by a static AST import graph including lazy imports, and by searching for `importlib`/`__import__` (only `dispatcher.py` → `api.app`).

**Evolution (from git history):** the Procfile has always pointed at `api/app.py`. `api/app.py` called `master_fireai_orchestrator.orchestrate` (guarded import) until 2026‑01‑21 — during which time that module did not compile — then `orchestrate.py` (v17 "unified orchestrator") until 2026‑03‑19, then the current `fireai_orchestrator_v2` + `fireai_nfpa13_design_engine` architecture. All 334 commits were made via the GitHub web editor ("Update app.py" ×59, "Add files via upload" ×26): no local dev loop, no CI, no tests in the loop.

---

## 3. Repository inventory

**Shape:** 85 tracked files, 66,481 lines of Python, 2 HTML files, 5 JSON files, flat root directory, empty `README.md`. No `docs/`, no `tests/`, no Dockerfile, no `railway.toml`/`nixpacks.toml`, no `pyproject.toml`, no lockfile, no Python version pin, no CI config.

### 3.1 Live modules (reachable from `api.app` — 13,121 lines)

| Module | Lines | Role | Notes |
|---|---:|---|---|
| `api/app.py` | 692 | FastAPI app, job runner, routes | Entry point |
| `fireai_nfpa13_design_engine.py` | 1,390 | Zones, placement, routing, hydraulics, hangers, BOM, compliance flags | Deterministic; core of "design" |
| `hydraulic_worksheet.py` | 369 | "Node-by-node" worksheet; overrides engine pressure for non-ESFR | Synthesized network |
| `agentic/` (6 files) | 713 | Verify/repair loop, fire-pump auto-sizing, extraction grading, telemetry | Logic duplicated between `agentic/__init__.py` and submodules |
| `fireai_orchestrator_v2.py` | 882 | LLM "agents", NFPA reviewer, AHJ agent, circuit breaker | In live path only the reviewer + AHJ agent run |
| `context_bus.py` | 162 | Async pub/sub between LLM agents | Only used by legacy LLM-agent mode (not reached when a design exists) |
| `fireai_email_escalator.py` | 273 | SMTP escalation emails | Sends project data to `FIREAI_ESCALATION_EMAIL` |
| `fireai_project_extractor.py` | 948 | pdftotext regex + Claude Vision for project data and rooms | Main extraction path for large sets |
| `fireai_document_processor.py` | 616 | Single-file geometry: PDF/DXF/IFC/image | See §5 |
| `fireai_document_intelligence.py` | 1,145 | Multi-file: classify & extract every page via Claude Vision | |
| `document_analyzer.py` | 269 | `/api/analyze` form auto-fill via Claude Vision | |
| `pdf_building_extractor.py` / `pdf_page_isolator.py` | 446 / 100 | Floor-plan page finder, single-page isolation (OOM fix) | |
| `fireai_drawing_engine.py` | 2,857 | DXF sheet set FP0.0–FP7.0, PDF via matplotlib | Most mature module |
| `isometric_builder.py` / `detail_drawings.py` | 347 / 417 | FP7.0 isometric, FP5.0 details | |
| `fireai_export.py` | 239 | JSON / XLSX / IFC writers | |
| `job_store.py` / `dispatcher.py` | 205 / 99 | SQLite job table, polling dispatcher | |
| `agent_config_store.py` / `improvement_loop.py` | 281 / 538 | Versioned prompts, nightly LLM self-improvement | **Output is never read by any agent** |
| `fireai_schemas/job_models.py` | 172 | Pydantic request models | `ProjectContext` model defined but not used for validation |

### 3.2 Unreachable / legacy modules (~53,000 lines)

| Module | Lines | State |
|---|---:|---|
| `master_fireai_orchestrator.py` | 5,499 | **Syntax error** (line 5028) in every revision since 2025‑09‑29. Contains `MockRoutingResult`, a hand-written "FireAI Compliance Placeholder" PDF, placeholder IFC writer. |
| `fireai_pro_master_Standards.py` | 4,437 | Needs `requests` (not in requirements) → import fails |
| `merged_symbols_ai_enhanced.py` / `fireai_licensed.py` | 4,165 / 2,493 | Symbol "platforms"; need `pandas` / `asyncpg`; `np.random.uniform` sprinkler placement, genetic algorithm |
| `enhanced_hydraulics_engine.py` | 3,947 | Hardy-Cross solver (structurally plausible, untested); `DummyNumPy.linalg.solve` returns zeros if numpy is missing |
| `fireai_routing_advanced.py` | 3,926 | "RL" routing with `random.choice` exploration |
| `nfpa13_routing_apis.py` | 3,172 | Needs `requests`; mock supplier APIs |
| `enhanced_cad_engine.py` | 2,577 | Needs `psutil` |
| `nfpa13_calc_sheets.py`, `node_by_node_tables.py`, `enhanced_bracing_engine.py` ("mock USGS"), `fitting_takeoff_bom.py`, `professional_*.py` (4), `bim_3d_engine.py`, `pipe_sizing_optimizer.py`, `pipe_routing.py`, `sprinkler_placement.py`, `network_hydraulics.py`, `geometry_extraction_engine.py` (shapely polygonize), `room_extractor.py`, `floor_plan_analyzer.py`, `floor_plan_intelligence.py`, `color_geometry_extractor.py`, `dxf_generator_v2.py`, `master_fireai_products_enhanced.py` | ~20,000 | Only reachable via `orchestrate.py`, which nothing imports |
| `orchestrate.py` | 1,462 | Former v17 orchestrator; imports all of the above |
| `fireai_document_extractor.py.py`, `fireai_ifc_source.py`, `fireai_vision_heights.py` | 440 / 154 / 246 | Double `.py.py` extension → not importable by name |
| `s3_uploader.py` | 91 | **Not wired in**; `boto3` not in requirements |
| `fireai_schemas/job_store.py` | 49 | Redis job store — unused |
| `_disabled_dummy.py`, `demo.py`, `demo_extraction.py` (syntax error), `extraction_check.py` (actually a copy of `agentic/loop.py`), `quality_gate.py`, `adapter.py`, `baseline.py`, `harness.py`, `scorer.py`, `run_evals.py` (syntax error) | — | Eval harness uploaded **with filenames scrambled**: `scorer.py` contains JSON, `baseline.json` contains Python, `ordinary_retail.json` contains the light-office case, `harness.py` contains the scorer. |
| `test_fireai.py` | 254 | CLI script (argparse), **no assertions**; not a test suite |

### 3.3 Other inventory items

| Category | Finding |
|---|---|
| Docker files | None ever committed |
| Railway config | Only `Procfile` (4 revisions, 2025‑08) |
| Dependency files | `requirements.txt` (unpinned `>=`, duplicate `ifcopenshell`/`pillow`, `pytesseract` without system tesseract). Missing for legacy code: `requests`, `pandas`, `asyncpg`, `psutil`, `boto3`, `scipy`, `sklearn`, `torch`, `shapely`, `networkx`… |
| System dependencies | `poppler-utils` (`pdftotext`, `pdfinfo`, `pdftoppm`) required by live extractors but **not declared anywhere** |
| Database | SQLite file `fireai_jobs.db` in the app directory (or `JOB_DB_PATH`), tables `jobs`, agent configs, performance logs |
| Storage | Container-local `./outputs/<job_id>/` and `./uploads/` |
| CAD libraries | `ezdxf` 1.4 (read + write), `ifcopenshell` 0.8 (read in stub, IFC written by hand-rolled STEP text in `fireai_export`) |
| PDF libraries | `pdfplumber`, `pypdf`, `reportlab`, `matplotlib` (DXF→PDF), poppler CLI |
| Geometry libraries | None in live path (all geometry is hand-written dict math). `shapely`/`networkx` only in dead code. |
| AI integrations | Anthropic SDK: Vision (4 modules), NFPA reviewer, AHJ agent, prompt rewriter. Model IDs hard-coded/env: `claude-sonnet-4-6`, `claude-sonnet-4-20250514`, `claude-opus-4-5` |
| Fixtures / sample drawings | **None.** Three JSON "reference projects" (form inputs only) |
| Generated artifacts committed | None |
| UI | `fireai_upload_ui.html` (served at `/`), `format_selector.html` (`/design`) — advertise "AHJ-ready", "Stamped PDF", "AHJ permit package" |

### 3.4 Environment variables referenced by the live code (names only)

`PORT`, `ANTHROPIC_API_KEY`, `FIREAI_MODEL`, `FIREAI_MAX_ITERATIONS`, `FIREAI_STRICT_ITER`, `FIREAI_MIN_IMPROVEMENT`, `FIREAI_ESCALATION_EMAIL`, `JOB_DB_PATH`, `DISPATCHER_POLL_INTERVAL`, `IMPROVEMENT_HOUR_UTC`, `MIN_JOBS_FOR_IMPROVEMENT`, SMTP settings in `fireai_email_escalator.py` (`SMTP_*` user/password/host names).
Legacy/unused: `AWS_S3_BUCKET`/`S3_BUCKET`, `AWS_REGION`/`AWS_DEFAULT_REGION`, `AWS_S3_ENDPOINT_URL`/`S3_ENDPOINT_URL`, `AWS_S3_PREFIX`/`S3_PREFIX`, `AWS_URL_EXPIRY`/`S3_URL_EXPIRES`, `AWS_SECRET_ACCESS_KEY` (+ key id), `REDIS_URL`, SMTP_USERNAME/SMTP_PASSWORD, supplier API keys.

---

## 4. Capability matrix

| Capability | Status | Evidence | Risk | Recommendation |
|---|---|---|---|---|
| DWG ingestion | **MOCK** | `.dwg` in `SUPPORTED_EXT`, but routed to `_vision_full_analysis(file,"image")` which base64s raw bytes as `image/png`. No ODA/LibreDWG/converter anywhere. `fake.dwg` → complete + compliant job. | Critical — users believe DWG is read | Rebuild: explicit DWG→DXF conversion service (ODA File Converter or LibreDWG), or reject DWG until then |
| DXF ingestion | **BROKEN** | Reads only `LINE`, `LWPOLYLINE`, `TEXT`, `MTEXT` in modelspace. `$INSUNITS` map wrong for mm (×3.281) and m (×39.37). No re-origin → real-world coordinates clamped into an invented rectangle → rooms deleted. Verified with synthetic DXF. | Critical | Rebuild |
| PDF ingestion | **PARTIAL** | Text: `pdftotext` regexes for project metadata (works on text PDFs). Geometry: LLM estimates rooms from 100‑dpi render; pdfplumber lines become "walls" unfiltered. >50 MB sets skip geometry entirely. Not executed against a real set (no key, no authorized drawings). | High | Keep text/page-classification ideas; rebuild geometry path |
| Image ingestion | **PARTIAL** | Sent to Claude Vision; same LLM-coordinates approach | High | Defer |
| IFC ingestion | **MOCK** | Each `IfcWall` → fake 10‑ft segment at its placement origin; `IfcSpace` boundaries left empty → dropped | Medium | Rebuild later with `ifcopenshell.geom` |
| Geometry extraction | **BROKEN** | See DXF/PDF rows; no polyline/arc/block/hatch handling, no geometry library | Critical | Rebuild (milestone 1) |
| Layer interpretation | **NOT IMPLEMENTED** | Only `"EXT" in layer` → exterior flag. Every `LINE` on every layer is a "wall". | High | Rebuild: layer-mapping rules + AI-assisted classification |
| Block interpretation | **NOT IMPLEMENTED** | `INSERT` entities ignored (door block in test DXF dropped) | High | Rebuild |
| Room detection | **MOCK** | Rooms come from LLM coordinate guesses or `_synthetic()` templates; DXF labels never associated with polygons | Critical | Rebuild |
| Wall detection | **BROKEN** | "All lines are walls"; PDF lines include dimension/hatch/text strokes | High | Rebuild |
| Building model | **NOT IMPLEMENTED** | Only an ad-hoc dict `{walls, rooms, columns, obstructions, building_dimensions}`; no schema, units, levels, provenance, or confidence | Critical | Build (milestone 1) |
| Sprinkler placement | **PARTIAL** | Deterministic grid on each room's *bounding box*; S/2 wall offset; small-room rule; ESFR 10×10 verified. Non-rectangular rooms over-covered / overlapping; columns & obstructions unused | High | Rebuild on real polygons after building model exists |
| Spacing validation | **PARTIAL** | Checks spacing along the first 15 rows only, one axis only; other checks are hard-coded `"pass"` (§8.5.4.1, §8.7.2 while generating 0 arm-overs, §8.16, §8.17, Table 12.1) | High | Rebuild as rules engine with real checks |
| Obstruction logic | **NOT IMPLEMENTED** | `self.obs` loaded, never used | High | Later milestone |
| Pipe routing | **PARTIAL** | Deterministic comb/tree (spine + cross stubs + branch spans). Branch sizing assumes end-feed while tee is center-fed; no wall/structure awareness | High | Rebuild |
| Pipe sizing | **PARTIAL** | Pipe-schedule tables; light-hazard table wrong for 2½" (20 vs NFPA 30) and 3" (40 vs 60); engine never selects 6"/8"; velocity-sizing inside hydraulics disagrees with drawn sizes | High | Rebuild; hydraulic sizing |
| Hydraulics | **BROKEN** | Critical-path lengths = % of building diagonal; worksheet synthesizes √n × √n remote area; supply curve exponent 0.54 (should be 1.85); density not enforced; placeholder `p = min + i*0.5`. No known-answer tests. | Critical | Rebuild with graph solver + test suite |
| Rules engine | **NOT IMPLEMENTED** | Hard-coded dict constants + string keyword maps; no versioning, no edition, no citations beyond labels, no tests | Critical | Build |
| BOM | **PARTIAL** | Counts derived from engine output; `part_number: "TBD"` everywhere; costs hard-coded; fittings counts from simplified model | Medium | Refactor after network model is real |
| DXF export | **PARTIAL** | Files VERIFIED valid: 7 sheets, `ezdxf.recover` 0 errors, sensible FP- layers & blocks. `$INSUNITS=6` (meters) is wrong for a 96‑units/ft drawing; overlapping text in info blocks; scale label mismatch | Low (file) / High (content) | Salvage renderer, refactor units & layout |
| PDF export | **PARTIAL** | Renders via matplotlib, but pages come out ~7×5 in (not 36×27), text is outlined (0 extractable chars); fallback writes a title-only PDF that still counts as a deliverable | Medium | Refactor (proper plot to sheet size) |
| IFC export | **PARTIAL** | Opens in ifcopenshell; contains only 56 `IfcBuildingElementProxy` for heads — no pipes, fittings, spaces | Medium | Rebuild later |
| Artifact storage | **PARTIAL** | Container local disk only; S3 code exists but unwired; persistence on Railway depends on an unknown volume | High | Rebuild on object storage |
| Railway deployment | **UNKNOWN** | Procfile only; could not inspect Railway (no CLI/credentials). Whether a volume, poppler, or env vars exist is unknown | Medium | Read-only Railway inspection when authorized |
| Job processing | **PARTIAL** | Works in-process; SQLite dispatcher; no retries, no cancellation, no concurrency limit; jobs lost on restart mid-run; upload path deliberately bypasses the queue | Medium | Refactor to a real queue/worker |
| Authentication | **NOT IMPLEMENTED** | No auth on any endpoint; CORS `*` | Critical | Build |
| Testing | **NOT IMPLEMENTED** | 0 automated tests with assertions; eval harness files scrambled & 2 have syntax errors | Critical | Build first |

---

## 5. CAD / drawing pipeline audit

### 5.1 Trace: upload → processing

1. `POST /api/generate/upload` streams each file to a temp file (good — fixes an earlier OOM).
2. **PDFs only:** `extract_project_context()` → `pdftotext` regex extraction (project name, occupancy, area, construction type, seismic, AHJ, codes, etc.) → page classifier (keyword scoring) → Claude Vision on the cover page and on one "floor plan" page. DXF/IFC/DWG files skip this step.
3. `grade_extraction()` flags missing fields (area, ceiling height, occupancy, supply, commodity class, two-hydrant test) and **injects defaults**: `seismic_zone="D1"`, `pipe_material="Schedule 40 Steel"`.
4. If total upload ≤ 50 MB → `DocumentProcessor.process()` (1 file) or `handle_document_set()` (n files), 240 s timeout. If > 50 MB, geometry is skipped and only the Vision room list from step 2 is used.
5. `_run_job()` with whatever geometry came back (often `{}`).

### 5.2 Question-by-question

| Question | Answer (real implementation) |
|---|---|
| Is DWG truly supported? | **No.** Raw DWG bytes are sent to the LLM as a PNG. |
| Does DWG require conversion? | Yes — and **no conversion exists** (no ODA File Converter, LibreDWG, Teigha, or cloud API). |
| Is DXF geometry parsed? | Minimally: `LINE`, `LWPOLYLINE`, `TEXT`, `MTEXT` in modelspace only. |
| Entity types supported | Above four. Not: `POLYLINE`, `ARC`, `CIRCLE`, `SPLINE`, `ELLIPSE`, `HATCH`, `DIMENSION`, `INSERT`, `ATTRIB`, `SOLID`, `REGION`, paperspace. |
| Blocks parsed? | No. |
| Nested blocks? | No. |
| Layers interpreted? | No, beyond substring `"EXT"`. Every `LINE` is a wall. |
| Units detected? | Reads `$INSUNITS` but mapping is wrong: 4 (mm) → ×3.281, 6 (m) → ×39.37. Inches and feet correct. |
| Scale detected? | PDF only: scale-bar number sequences, `1/8" = 1'` regex, or back-calculated from `total_area`; default 9 pt/ft. Vision "drawing_scale" is recorded but unused. |
| Dimensions interpreted? | No (DXF `DIMENSION` ignored; Vision prompt *asks* the model to read grid dimension strings). |
| TEXT/MTEXT parsed? | Collected as `annotations` with insert points — then **never used**. |
| Room labels recognized? | Not from CAD. From PDFs only via the LLM. |
| Polylines understood? | Closed `LWPOLYLINE` with area > 25 → "room"; open → "wall". Exterior wall outline therefore becomes a "room". |
| Walls identified? | No real identification. |
| Doors/openings? | No. |
| Structural geometry? | LLM is asked for a structural grid (used for grid bubbles on drawings). No columns extracted from CAD. |
| XREFs handled? | No. |
| Coordinates normalized? | **No** in DXF path. The design engine later re-origins by bbox, but by then the processor has already clamped rooms into an invented `sqrt(area/0.75)` rectangle, destroying them. |

### 5.3 Verified run — synthetic DXF

Input (generated for this audit): 100 × 60 ft building, inches, drawn at (10,000 ft, 4,000 ft) as real plans often are; exterior polyline, 2 partition lines, 3 closed room polylines (3,000 / 2,000 / 1,000 sf), 3 room labels, a door block, a linear dimension.

| Stage | Result |
|---|---|
| DXF read | OK. 2 "walls" (partitions), 3 annotations — at un-normalized coordinates |
| Rooms | All 3 real rooms (and the exterior "room") clamped into invented 89.4 × 67.1 ft box → degenerate → discarded; replaced by 1 "Unclassified Area 1" gap-fill of 5,999 sf |
| Design engine | Building computed as **397.6 × 159.4 ft** (walls at 10,000 ft + rooms at 0), coverage < 15 % → **synthetic template** Open Office/Corridor/Lobby |
| Job result | `status: complete`, `compliant: true`, 25 heads, 471 ft pipe, 9 files, `geometry_synthetic: False` (wrong) |
| mm variant | Coordinates scaled ×3.281 → a 60 ft wall reported at 60,002 "ft"; same outcome |
| `fake.dwg` (garbage bytes) | **Identical** result: complete, compliant, 25 heads, 471 ft, 9 files |

Conclusion: in the current system, **the uploaded drawing has no influence on the geometry of the design** unless the LLM returns room coordinates that happen to cover ≥ 20 % of its own estimated footprint.

---

## 6. Building understanding audit

**A normalized building model does not exist.** What passes between stages is an untyped dict:

```
{ walls: [{points:[{x,y}], exterior?, layer?, thickness?}],
  rooms: [{name, boundary:[{x,y}], area_sf, hazard_override?, hazard_classification?, ceiling_height_ft?}],
  columns: [], obstructions: [], annotations: [],
  building_dimensions: {width_ft, depth_ft}, floor_area_sf, ceiling_height_ft,
  _use_synthetic?, _synthetic?, _scale? }
```

| Element | Present? |
|---|---|
| Rooms | LLM coordinates or template rectangles; hazard by keyword |
| Walls | Unclassified line segments |
| Boundaries | Axis-aligned bounding boxes are what the engine actually uses |
| Openings / doors | No |
| Ceilings | One `ceiling_height` for the building (per-room value only from the LLM); **extractor maps cover-sheet `building_height_ft` into `ceiling_height`** — building height ≠ ceiling height, and this value drives ESFR selection and temperature ratings |
| Structural members | No (LLM grid lines for drawing only) |
| Floors / levels | `floors` divides total area; no per-level geometry |
| Dimensions | No |
| Occupancy / hazard | Keyword maps (`ZONE_MAP`, `_infer_hazard`) and LLM labels; `"unclassified"` → ESFR K14 |
| Existing MEP | No |

No units field, no coordinate reference, no provenance (which file/entity/page produced an element), no confidence.

---

## 7. Fire sprinkler design audit

All design happens in `fireai_nfpa13_design_engine.py` (+ `hydraulic_worksheet.py`, `agentic/repair.py`). The LLM does **not** place sprinklers or compute hydraulics in the live path (the older LLM-agent mode that did is bypassed whenever a design exists — i.e., always). This is the right instinct; the implementation is the problem.

| Capability | Classification | Notes |
|---|---|---|
| Sprinkler placement | **HEURISTIC** (deterministic) | Square grid `min(S, √coverage)` over room bounding boxes; not polygon-aware; overlapping bboxes double-place |
| Spacing | **PARTIAL** | Max spacing from table; min spacing (6 ft std / 8 ft ESFR) not checked; distance-to-wall only as grid offset |
| Coverage | **PARTIAL** | Coverage area stored per head as `grid_x × grid_y`; not validated against the actual polygon |
| Hazard classification | **HEURISTIC** | Room-name keyword maps; LLM labels; occupancy fallback. `"unclassified"` defaults to ESFR |
| Obstruction handling | **NOT IMPLEMENTED** | |
| Branch-line generation | **HEURISTIC** | Rows grouped by 3 ft tolerance |
| Main routing | **HEURISTIC** | Riser at `min(x)-4 ft`, spine along long axis, one cross stub per row |
| Riser placement | **HEURISTIC** | Fixed offset from first head; no room/wall awareness |
| Pipe sizing | **PARTIAL** | Schedule tables (light-hazard table wrong for 2½"/3"); hydraulic calc sizes its own "critical path" pipes by 20 fps velocity independently of the drawn sizes |
| Fittings | **HEURISTIC** | One tee per span; equivalent-length lookup by `int(d)` in engine |
| Valves | **HEURISTIC** | Fixed list (OS&Y, alarm check, flow switch, IT, drain) + one butterfly per zone at invented coordinates |
| Hangers | **HEURISTIC** | `ceil(L / max_spacing)` per section; type from `structural_framing` string |
| Seismic bracing | **HEURISTIC** | 4-way brace every 40 ft on mains/cross-mains if zone ∈ {C, D, D1, D2, E}; lateral bracing, load calcs, Zone-of-influence not done. "D1" is not an ASCE 7 Seismic Design Category |
| Hydraulic calculations | **HEURISTIC / BROKEN** | See §8 |
| Remote-area selection | **HEURISTIC** | n farthest heads by Euclidean distance from assumed riser; area shape rule (1.2√A) not applied |
| Water supply | **BROKEN** | Wrong curve exponent; silent defaults (72 psi / 61.2 psi / 1,500 gpm) when absent |
| Design density | **NOT IMPLEMENTED** (as an enforced constraint) | Density appears only as a label; flows are `K√Pmin` |
| Hose allowance | **REAL / DETERMINISTIC** | 100 / 250 / 500 gpm by hazard, added at source — correct concept |
| System type | **NOT IMPLEMENTED** | Always wet; dry/preaction/antifreeze not modeled (dry-system area increase, etc.) |
| Material selection | **PARTIAL** | Material string → C-factor; Sch 10/40 IDs in worksheet only |
| BOM generation | **PARTIAL** | Real counts from engine output; all part numbers "TBD"; hard-coded prices |
| Fire-pump sizing | **HEURISTIC** | Auto-added on any deficit; `(deficit + 10 psi)` rounded; then §22 flag flipped to "pass" |

---

## 8. Hydraulics audit

**Equations present**
- Hazen-Williams, psi/ft: `p = 4.52·Q^1.85 / (C^1.85·d^4.87)` — correct NFPA form.
- Sprinkler discharge `Q = K√P` — correct.
- Elevation `0.433 psi/ft` — correct.
- Supply curve: `P = Ps − (Ps−Pr)·(Q/Qr)^0.54` — **wrong**. NFPA uses the N^1.85 relationship: `P = Ps − (Ps−Pr)·(Q/Qr)^1.85`.

**Independent numerical checks (executed against the live functions)**

| Check | Result |
|---|---|
| Supply curve, Ps=75, Pr=64 @ 1000 gpm | Engine vs correct: 250 gpm 69.8 vs 74.2 psi (too pessimistic); 1500 gpm **61.3 vs 51.7** (+9.6 psi); 2000 gpm **59.0 vs 35.3** (+23.7 psi) — **non-conservative above the test flow**, i.e., can pass a design that needs a pump |
| Density, light hazard office (0.10 × 1500 = 150 gpm) | Engine 114.1 gpm; worksheet 145.7 gpm → **fails density, marked compliant** |
| Density, OH2 kitchen (0.20 × 1500 = 300 gpm) | 448 gpm (passes, by coincidence of K8 @ min pressure and head count) |
| Missing supply data | Uses static 72 / residual 61.2 / 1500 gpm defaults → `compliant: true` |
| Light-hazard pipe schedule | 2½" allows 20 (NFPA: 30), 3" allows 40 (NFPA: 60) |
| ESFR spacing | 10 × 10 ft = 100 ft²/head — OK |

**Structural problems**
- **No graph/network representation.** `pipe_sections` are drawn but never traversed hydraulically. The engine's critical path uses `L_main = 0.75 × building diagonal`, `L_cross = 0.30 × short side`, `L_branch = √n × S`. The worksheet then invents `√n` heads per branch with spacing derived from `L_cross`.
- Engine calc uses **nominal** diameter; worksheet uses internal diameter — two different answers; worksheet wins for non-ESFR, engine for ESFR.
- Head pressures in the engine are `Pmin + 0.5·i` (placeholder; comment in header claims it was replaced).
- No branch-line balancing, no loop/grid solving, no pressure-balancing at junctions (worksheet assumes other branches see the junction pressure).
- Units: US customary only, implicit; floats rounded to 1–2 decimals mid-calculation.
- Edge cases: `n_remote` from `area / max_coverage` rather than actual head coverage; ESFR path never uses worksheet.
- **Tests with known expected results: none.** The eval "reference projects" assert only `total_sprinklers ≥ 1` and `compliant == true`.

**Verdict: the hydraulic engine is not correct and must not be relied upon.** The legacy `enhanced_hydraulics_engine.py` contains a Hardy-Cross implementation that looks structurally real but is unreachable and untested; treat it as reference only.

---

## 9. Rules / code engine audit

There is **no rules engine**. NFPA content exists as:
- Python dict constants (`HAZARD_CRITERIA`, `PIPE_SCHEDULE`, `MAX_HANG`, `FEQ`, `HW_C`, costs) — hard-coded, unversioned, no edition, no source citation per value.
- Keyword dictionaries mapping room names → hazard (`ZONE_MAP`, `_infer_hazard`, `OCCUPANCY_DEFAULT`) duplicated in 3 modules with differences.
- A compliance "check" that emits mostly hard-coded `"pass"` flags.
- LLM prompts asking Claude to "review against the full NFPA 13 standard" (**prompt-based**, non-deterministic, and silently treated as a pass when it errors).
- Section references like `§8.5.4.1`, `§22.4` that do not consistently correspond to any single NFPA 13 edition (the 2019+ editions renumbered chapters). `nfpa_edition` is hard-coded to "Current Edition".

Rule classification: **hard-coded** (constants), **heuristic** (keyword hazard), **prompt-based** (reviewer). Not configuration-driven, not database-driven, not versioned, not tested.

---

## 10. Artifact pipeline audit (from verified job `06788934`, light office)

| Output | Generated? | Generator | Meaningful engineering content? | Valid / openable? | Stored | Retrievable |
|---|---|---|---|---|---|---|
| 7 DXF sheets (FP0.0–FP6.0) | Yes | `fireai_drawing_engine` | Renders the engine output faithfully; that output is built on an invented building | Yes — R2018, 0 recover errors; `$INSUNITS=6` (m) wrong | Local disk | `/api/jobs/{id}/download/{f}` |
| FP5.1 Sections, FP7.0 Isometric | Only if requested | same | — | Not tested | | |
| 7 PDFs | Yes (only if `dwg_pdf` format) | matplotlib | Same as DXF | Open, but ~7×5 in pages, 0 extractable text; fallback PDF is a title-only placeholder counted as a deliverable | Local | Yes |
| `layout_3d.dxf` | If `dwg_3d` | inline | Lines at ceiling height only | Not tested | | |
| `hydraulics.json` | Yes | `fireai_export` | Mirrors flawed calc | Valid JSON | Local | Yes |
| `bill_of_materials.xlsx` | Yes | `fireai_export` | 33 rows, all part numbers TBD | Opens | Local | Yes |
| `model.ifc` | Yes | hand-written STEP | 56 proxy objects, no pipes | Opens (IFC2X3) | Local | Yes |
| `nfpa13_compliance_cert.pdf`, `ahj_permit_package.pdf`, `model.step` | **Never written** | — | — | — | — | Advertised by UI/orchestrator, filtered out because file doesn't exist |
| "Stamped" PDF | **No stamp logic exists** | — | — | — | — | UI claims it |

Observed issues on sheets: overlapping text in the hydraulic info block; supply-vs-demand graph plots the supply curve near 20–30 psi while stating 75 psi static; scale label "3/16" = 1'" on a sheet whose title block says 1/8" = 1'-0".

---

## 11. Storage / S3 audit

- **Provider in use:** none. Artifacts live in `./outputs/<job_id>/` on the container filesystem; jobs in SQLite `./fireai_jobs.db` (override `JOB_DB_PATH`).
- **S3 code (`s3_uploader.py`):** boto3, virtual-hosted addressing, optional custom endpoint (R2/MinIO-style), key `PREFIX/project_id/timestamp/filename`, pre-signed GET URLs (default 7 days). **Not imported by anything; boto3 not installed.** Consumes a `Deliverables` schema that doesn't exist in `fireai_schemas`.
- Upload/download logic: FastAPI `FileResponse` from local disk; no signed URLs; no content-type (always `application/octet-stream`).
- Lifecycle: none — outputs and DB grow forever (or vanish on redeploy if no volume).
- Failure handling: none (errors swallowed during export; missing files silently dropped from list).
- Bucket configuration / public access: not determinable from repo (no infra-as-code).

---

## 12. Railway / deployment audit

| Item | Finding |
|---|---|
| Services | One `web` process (Procfile). No worker service, no separate DB service referenced in code. |
| Build | No Dockerfile / nixpacks.toml / railway.toml ever committed → Railway auto-detects Python and installs `requirements.txt`. Python version unpinned. |
| System packages | poppler-utils needed by live code but not declared. Whether Railway images had it: **UNKNOWN** (comments in `api/app.py` describe successful ~2 min extractions, suggesting either poppler was present or fallbacks were hit). |
| Start command | `uvicorn api.app:app --host 0.0.0.0 --port ${PORT:-8000}`, single worker |
| Workers | In-process `BackgroundTasks` + asyncio dispatcher + nightly loop inside the web process |
| Database | SQLite on container disk. Persistence depends on a Railway volume and `JOB_DB_PATH` — **UNKNOWN** |
| Storage | Container disk — **UNKNOWN** whether a volume is mounted |
| Env var names | See §3.4 |
| Health check | `/health` returns hard-coded `true` for dispatcher/improvement loop; checks nothing |
| Operational history | Code comments document repeated OOM SIGKILLs on 150+ MB PDF sets and the 50 MB "skip geometry" workaround |
| Railway CLI access | Not installed / not authenticated — **no Railway inspection performed** |

---

## 13. Test coverage audit

| Category | Tests |
|---|---|
| Unit | 0 |
| Integration | 0 |
| Geometry | 0 |
| CAD | 0 |
| Hydraulic | 0 (no known-answer tests) |
| Rules | 0 |
| API | 0 |
| Storage | 0 |
| End-to-end | `test_fireai.py` is a manual CLI runner without assertions |
| Evals | 3 reference-project JSONs with trivial expectations (`sprinklers ≥ 1`, `compliant`); harness files scrambled, `run_evals.py` & `demo_extraction.py` have syntax errors |

**Critical untested engineering functionality:** units/scale conversion, polygon areas, spacing and coverage, remote-area selection, Hazen-Williams, K-factor, supply curve, pipe-schedule tables, density/area, pump sizing, DXF parsing.

**Local run results (this audit):**
- `pip install -r requirements.txt`: OK (py3.12).
- `compileall`: 3 files fail (`master_fireai_orchestrator.py`, `run_evals.py`, `demo_extraction.py`).
- `ruff` restricted to syntax errors + undefined names (`E9,F63,F7,F82`): 594 findings, all in dead files (`run_evals.py`, `master_fireai_orchestrator.py`, `demo_extraction.py`; `F821 true` in `scorer.py`). A full default-rules run reports thousands more (2,349 auto-fixable alone). No project lint config.
- Type checking: no config and no type annotations discipline; not meaningful to run.
- Imports: all live modules import; 15 non-live modules fail (syntax, relative imports, missing `requests`/`pandas`/`asyncpg`/`psutil`/`boto3`).
- Startup: OK; `/health` 200.
- JSON job: complete in ~14 s, 17 files.
- Upload jobs: DXF and fake DWG both "complete + compliant".

---

## 14. Security findings

No secret values are printed in this report.

| # | Severity | Finding | Evidence | Recommended action |
|---|---|---|---|---|
| S1 | **Critical** | **File disclosure of the app directory** via `/api/jobs/{id}/download/{filename}`: jobs without `output_dir` (queued, running, failed) resolve `Path("") / filename` against the working directory | Verified locally: created a failing job, downloaded `fireai_jobs.db` (516 KB, contains every job's project context), `requirements.txt`, `Procfile`. Subdirectory traversal (`%2F`) blocked. | Resolve paths under a per-job root and `is_relative_to` check; require auth; move DB out of app dir. Treat production DB contents as potentially exposed. |
| S2 | **Critical** | **No authentication** on any endpoint, including `/api/jobs` (lists all jobs + full contexts), `/api/improvement/run` (triggers paid LLM calls), `/api/improvement/rollback/*` | Verified 200s without credentials | Add auth before any redeploy |
| S3 | High | CORS `allow_origins=["*"]` with all methods/headers | Verified preflight | Restrict origins |
| S4 | High | Unbounded uploads; `/api/analyze` reads whole files into memory (known OOM vector); no MIME/content validation; extension-only routing | Code | Size limits, content sniffing, streaming |
| S5 | High | Customer drawings and project data are sent to Anthropic (Vision) and to an escalation email address; no documented data-handling policy | Code | Decide policy; disclose; allow opt-out |
| S6 | Medium | `json.loads(project_context)` on user input without validation → 500s; Pydantic `ProjectContext` unused | Code | Validate with schema |
| S7 | Medium | LLM prompt injection surface: drawing text is placed in prompts whose output can flip compliance/review results | Design | Never let LLM output gate engineering pass/fail |
| S8 | Low | `subprocess.run` calls use argument lists (no shell) with temp-file paths — **no command injection found** | Code | OK |
| S9 | Info | **No committed secrets found.** Pattern scan of working tree and all 334 commits (Anthropic/AWS/GitHub/Slack keys, private keys, DSNs with passwords, hard-coded password/token literals, `.env` files): all hits were `os.getenv(...)` reads or header-name literals | Scan | No rotation required by this finding. Railway-side variables were not inspected. |
| S10 | Info | Public repo? Not determined. If public, the architecture and endpoints above are discoverable | — | Confirm visibility |

---

## 15. Salvage / refactor / rebuild matrix

| Module / area | Verdict | Why |
|---|---|---|
| FastAPI app shell (`api/app.py` routes, streaming upload, lifespan) | **REFACTOR** | Streaming-to-disk upload and background-job pattern are sound ideas; the 450-line job function, silent fallbacks, auth absence and path bug must go |
| `job_store.py` + `dispatcher.py` | **REFACTOR** | Simple and working, but SQLite-in-container + in-process worker won't survive restarts/scale; replace with Postgres + a real queue while keeping the API shape |
| `fireai_drawing_engine.py` (+ `isometric_builder`, `detail_drawings`) | **SALVAGE → REFACTOR** | Most valuable existing code: real DXF sheet generation, layer standard, symbol blocks, title block. Needs: correct `$INSUNITS`, consume a typed design model, paper-space layouts / proper PDF plotting, split into per-sheet modules |
| `fireai_export.py` | **REFACTOR** | JSON/XLSX writers fine; IFC writer should be replaced with ifcopenshell API when BIM export becomes a goal |
| `pdf_page_isolator.py`, page classifier & regex extractors in `fireai_project_extractor.py` | **SALVAGE (ideas + some code)** | Page isolation solved a real OOM; title-block/code-data regexes are useful for metadata; fix `building_height → ceiling_height` |
| `fireai_nfpa13_design_engine.py` | **REBUILD** | Deterministic-first instinct is right, but it's a single class doing zoning, placement, routing, hydraulics, hangers, BOM and compliance on bounding boxes with a synthetic fallback. Keep the data tables only as *inputs to verify* against the standard |
| `hydraulic_worksheet.py` | **REBUILD** | Synthesized network; wrong supply curve; no density |
| `agentic/*` (verify-repair, pump sizing) | **REMOVE** (concept may return later) | Auto-"repairing" compliance by adding a pump and flipping flags is unsafe; extraction grading ideas can move into the building-model validation layer |
| `fireai_orchestrator_v2.py`, `context_bus.py` | **REMOVE / REBUILD** | LLM "agents" for CAD/hydraulics/routing/bracing are the wrong architecture; LLM reviewer silently passes on error. A future AI-assist layer should be advisory, provenance-tracked, and never gate pass/fail |
| `fireai_document_processor.py`, `fireai_document_intelligence.py`, `document_analyzer.py`, `pdf_building_extractor.py` | **REBUILD** | LLM-guessed coordinates, synthetic dims, fake DWG/IFC paths. Prompts may be reused for AI-assisted *classification* |
| `improvement_loop.py`, `agent_config_store.py` | **REMOVE** | Rewrites prompts nightly; nothing consumes them; exposed unauthenticated |
| `fireai_email_escalator.py` | **REMOVE** (for now) | Sends project data externally; reintroduce as notifications later |
| `s3_uploader.py` | **REFACTOR** (reuse pattern) | Unwired but reasonable boto3 + presign pattern |
| `master_fireai_orchestrator.py`, `orchestrate.py`, and all `enhanced_*`, `professional_*`, `merged_*`, `fireai_licensed`, `fireai_pro_master_Standards`, `nfpa13_*`, `fitting_takeoff_bom`, `pipe_*`, `sprinkler_placement`, `network_hydraulics`, `bim_3d_engine`, `floor_plan_*`, `geometry_extraction_engine`, `room_extractor`, `color_geometry_extractor`, `dxf_generator_v2` | **REMOVE** from working tree (keep in Git history / an `archive` tag) | Unreachable, partly broken, contains mocks and randomness. Two may be consulted as reference: `enhanced_hydraulics_engine.py` (Hardy-Cross structure) and `geometry_extraction_engine.py`/`room_extractor.py` (shapely polygonize approach) |
| Eval harness files | **REBUILD** | Scrambled; replace with pytest + golden fixtures |
| HTML UIs | **REBUILD** later | Remove "AHJ-ready"/"Stamped" claims immediately if anything is still live |
| Procfile-only deployment | **REFACTOR** | Add Dockerfile (pins Python + poppler + ODA/LibreDWG) and `railway.toml` |

---

## 16. Proposed FireAI Pro 2.0 architecture

### 16.1 Principles
1. **Separation of AI and determinism.** AI proposes *labels with confidence*; deterministic code computes and validates. No LLM output may directly set a pass/fail or a numeric engineering value.
2. **Provenance & uncertainty everywhere.** Every element in the building model records its source (file, layout, entity handle/page, bbox) and how it was derived (parsed / rule / AI / user). Unknowns stay unknown — no silent defaults.
3. **Typed contracts between stages** (Pydantic v2 models, JSON-schema versioned). Each stage is a pure function `input model → output model + diagnostics`, testable in isolation.
4. **Explicit units** (store SI or feet internally — pick one, convert at edges; `pint` for boundary conversions).
5. **Versioned rules** with edition, section citation, and tests.
6. **Fail loudly.** A job has `status` (succeeded/failed) *and* an engineering `verdict` (not_evaluated / needs_review / meets_modeled_rules), never conflated.

### 16.2 Components

```
                ┌──────────────────────────── API (FastAPI, auth, validation) ─────────────────────────┐
                │                                                                                       │
UI ───────────► │  projects · uploads · jobs · artifacts · review/overrides                              │
                └───────────────┬───────────────────────────────────────────────────────┬───────────────┘
                                │ enqueue                                                │ signed URLs
                        ┌───────▼────────┐                                       ┌──────▼──────┐
                        │ Job orchestration│  (thin: a DAG of stage calls,        │ Artifact    │
                        │ queue + workers  │   retries, idempotent, status/logs)  │ storage (S3/│
                        └───────┬──────────┘                                      │ R2) + DB    │
                                │                                                 └─────────────┘
 1 File ingestion ──► 2 CAD conversion ──► 3 Geometry extraction ──► 4 Drawing interpretation
   (hash, type sniff,   (DWG→DXF via ODA/     (ezdxf: all entities,    (layer mapping rules,
    size limits,         LibreDWG in its own   blocks/nested/xrefs,     AI-assisted layer/
    virus scan opt.)     container; PDF→       units, extents, text;    symbol/room-label
                         vector/raster)        shapely topology)        classification w/ conf.)
                                                                              │
                                                                              ▼
                                                   5 NORMALIZED BUILDING MODEL (typed, versioned,
                                                     provenance + confidence per element)
                                                                              │
                                    6 Design assumptions (explicit, user-confirmable:
                                      hazard per room, supply data, system type, edition)
                                                                              │
                  7 Deterministic rules engine (versioned rule packs, pure functions, citations)
                                                                              │
     8 Sprinkler layout ──► 9 Pipe-network generation ──► 10 Hydraulic engine (graph solver)
                                                                              │
                                              11 Validation (rules re-run on final design)
                                                                              │
                     12 BOM ─ 13 CAD/BIM export (DXF/IFC) ─ 14 PDF/report generation
```

| # | Component | Deterministic / AI | Key tech |
|---|---|---|---|
| 1 | File ingestion | Deterministic | content sniffing, SHA-256 dedupe, limits |
| 2 | CAD conversion | Deterministic (external tool) | ODA File Converter or LibreDWG in isolated container; version-pinned |
| 3 | Geometry extraction | Deterministic | ezdxf (incl. `INSERT` explode w/ transforms, nested blocks, XREF detection), shapely |
| 4 | Drawing interpretation | Rules first, **AI assist** for ambiguity (layer names, symbol blocks, room labels), always with confidence | layer-standard dictionaries (AIA/NCS), heuristics, LLM for classification only |
| 5 | Building model | Data contract | Pydantic models; JSON schema versioned |
| 6 | Design assumptions | User-confirmed; AI may suggest | explicit "assumption" records |
| 7 | Rules engine | Deterministic | rule packs per NFPA 13 edition; data tables with citations; property-based tests |
| 8 | Sprinkler layout | Deterministic | polygon-aware placement, obstruction rules |
| 9 | Pipe network | Deterministic | graph model (networkx), topology validation |
| 10 | Hydraulics | Deterministic | node-by-node for trees, Hardy-Cross/Newton for loops/grids; known-answer test suite |
| 11 | Validation | Deterministic | re-run rules on final model; produce findings with citations |
| 12 | BOM | Deterministic | from network model; catalog-driven |
| 13 | CAD/BIM export | Deterministic | salvaged DXF sheet renderer; later IFC via ifcopenshell API |
| 14 | PDF/report | Deterministic | proper plotting to sheet size; report templates |
| 15 | Job orchestration | Deterministic | queue (e.g., Postgres-backed or Redis/RQ/Arq), idempotent stages, per-stage artifacts |
| 16 | Artifact storage | — | S3/R2, content-addressed keys, lifecycle rules |
| 17 | API | — | auth, per-tenant isolation, schema validation |
| 18 | UI | — | review/override workflow over the debug overlay |

Repository layout (proposed): `packages/` (`ingest`, `cad_convert`, `geometry`, `interpret`, `building_model`, `rules`, `layout`, `network`, `hydraulics`, `validation`, `bom`, `export_cad`, `reports`), `services/api`, `services/worker`, `tests/` (unit, golden, e2e), `fixtures/drawings/`, `docker/`.

---

## 17. Recommended implementation sequence

0. **Contain risk now (hours, needs your authorization):** confirm whether the Railway deployment is live/public; if so, take it down or put it behind auth, and remove "AHJ-ready/Stamped" claims. Nothing else in the old code needs fixing.
1. **Repo hygiene:** new branch/repo structure; archive tag of current state; move dead code out of the tree; `pyproject.toml`, pinned deps, Dockerfile (py + poppler + DWG converter), ruff/mypy, pytest, CI.
2. **Test fixtures:** assemble 5–10 representative, non-confidential DWG/DXF plans (public sample sets or ones you create), plus hand-labeled expected room/wall/door lists.
3. **Milestone 1 — Drawing understanding** (§18).
4. **Rules engine foundation:** schema for rules + first rule pack (spacing/coverage/wall-distance for light & ordinary hazard, one edition), each with tests.
5. **Hydraulics engine** (independent of layout): graph model + node-by-node tree solver + supply curve, validated against hand-calculated and published example problems.
6. **Sprinkler layout** on the building model (polygon-aware, light/OH first).
7. **Pipe network generation** → feed hydraulics → validation loop.
8. **Exports:** port the DXF sheet renderer to the new model; PDF plotting; BOM.
9. **Platform:** auth, Postgres, object storage, queue/worker, Railway config.
10. Storage/ESFR/dry systems/seismic/obstructions — later milestones.

---

## 18. First milestone — Drawing understanding

**Goal:** Given a representative architectural DWG (via DXF), produce a **normalized building model** and a **visual debug overlay**, with every element traceable and every uncertainty explicit. No sprinkler design.

**Deliverables**
1. `building_model.json` (schema v0.1) containing, where available: units & source `$INSUNITS`, detected scale/extents, levels (one per file or layout for now), layers (name, entity counts, inferred role + confidence), walls (centerline or face pairs, thickness), rooms (polygon, area, label text & source, name), doors/openings (from blocks/arcs, host wall), dimensions (measured vs stated), text labels, blocks (name, count, inferred type), structural elements (columns/grids where identifiable), ceiling info if present (RCP layers/text), existing FP/MEP entities (layer-based), and a `diagnostics` list (unknown layers, unclosed rooms, unit conflicts).
2. `overlay.svg` / `overlay.png` / `overlay.dxf`: original drawing in grey; walls, room fills with labels, doors, columns, and unclassified geometry in distinct colors; legend; per-element IDs linking back to the JSON.
3. A CLI (`fireai inspect plan.dwg`) and an API endpoint that return both.
4. Tests: unit tests per extractor; golden-file tests on the fixture set measuring room recall/precision, area error, label-association accuracy.

**Acceptance criteria (proposal — you decide the thresholds)**
- DWG→DXF conversion succeeds on all fixtures; unit detection correct on 100 %.
- ≥ 90 % of labeled rooms detected with area within ±2 % on the fixture set; every room label associated or explicitly unassociated.
- Zero silent defaults: anything not determinable appears in `diagnostics`.
- Deterministic: same input → byte-identical model (AI suggestions cached and recorded with model/version).

---

## 19. Questions / decisions that require your input

1. **Production status:** Is the Railway deployment still running and publicly reachable? May I (later) do read-only Railway inspection if you install/authenticate the CLI? Should it be taken down or put behind auth now given S1/S2?
2. **Target standard:** Which NFPA 13 edition(s) and which jurisdictions/AHJs first? Any NFPA 13R/13D scope?
3. **Initial scope:** Light + Ordinary Hazard wet-pipe systems only for the first design milestone (excluding storage/ESFR/dry)? I recommend yes.
4. **DWG conversion:** Acceptable to depend on the ODA File Converter (free, closed-source, license terms apply) vs. LibreDWG (GPL, weaker coverage) vs. a paid API?
5. **Test drawings:** Can you provide (or authorize) 5–10 non-confidential representative DWGs? Three real project PDF sets exist on the local machine — may they be used locally only?
6. **AI data policy:** Is sending customer drawings to Anthropic acceptable? Any customer contracts restricting that?
7. **Repo strategy:** New repository (clean history) vs. a `v2` branch in this repo with an `archive/v1` tag?
8. **Stack constraints:** Stay on Python + FastAPI + Railway? Postgres OK? Any preference for Redis vs. Postgres-backed queue?
9. **Internal units:** Feet/inches (industry-native) or SI internally?
10. **Who reviews engineering outputs?** Is a licensed engineer / NICET designer available to define golden expected results for rules and hydraulics tests?
11. **Existing users/data:** Are there customers or saved jobs in the production SQLite DB that must be preserved or notified (see S1)?

---

### Appendix A — Commands and artifacts from this audit (local only)

- Clone: `C:\Users\cland\code\fireai-pro` (push URL set to `DISABLED_AUDIT_NO_PUSH`).
- Docker image: `fireai-audit:py312` (python:3.12-slim + poppler-utils + tesseract + requirements).
- Synthetic test drawings, probe scripts and job outputs are in the session scratchpad, not in the repo.
- Only file added to the clone: this report (uncommitted).
