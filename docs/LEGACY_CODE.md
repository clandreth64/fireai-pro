# Legacy (v1) code — status and proposed removal

Determined by a static AST import graph from `api/app.py` (including lazy/function-level
imports) after milestone 1. **Nothing has been deleted.** Deletion requires separate approval.

Labels: **ACTIVE** (served app uses it) · **REUSABLE** (not used, worth keeping or porting) ·
**OBSOLETE** (unreachable and not worth porting) · **DANGEROUS** (would produce false engineering
claims or invented geometry if ever reconnected — must never be re-wired) · **UNCERTAIN** (decide with the owner).

_Updated Milestone 1.5 (2026-09-22): import graph re-verified — the served app reaches only the `fireai/`
package plus `TextExtractor` regexes in `fireai_project_extractor.py`. No legacy module was reconnected._

## Active

| File | Used for |
|---|---|
| `api/app.py` | ASGI entry (rewritten: now only builds the v2 app; v1 routes return 410) |
| `fireai_project_extractor.py` | **Only** `TextExtractor` regexes (project name/address/owner) for title-block parsing, via `fireai/interpret/text.py`. Its "Unknown Project" fallback is treated as not-found. Recommend porting those ~40 lines into `fireai/interpret/text.py` and then retiring the file. |
| `requirements.txt`, `Procfile` | build/run |

## Reusable (not wired in — keep for later milestones)

| File | Why keep | Later milestone |
|---|---|---|
| `fireai_drawing_engine.py`, `isometric_builder.py`, `detail_drawings.py` | Most mature v1 code: FP sheet set, layer standard, symbol blocks, title block. Needs a typed design model and fixed `$INSUNITS`. | CAD export |
| `pdf_page_isolator.py` | Solved real OOM on 150 MB PDF sets | PDF ingestion |
| `fireai_export.py` (JSON/XLSX writers) | Simple, working writers | BOM/report export |
| `s3_uploader.py` | Reasonable boto3 + presigned-URL pattern (unwired, boto3 missing) | Artifact storage |
| `enhanced_hydraulics_engine.py` (Hardy-Cross class only) | Structurally plausible loop solver — reference only, untested | Hydraulics |
| `geometry_extraction_engine.py`, `room_extractor.py` | shapely polygonize approach — reference only | Drawing understanding v2 |

## Dangerous (never reconnect; remove first once deletion is approved)

| File(s) | Why dangerous |
|---|---|
| `fireai_nfpa13_design_engine.py` (`_synthetic`, `_fill_zone_gaps`) | Invents buildings from floor area; reports `geometry_synthetic: False` in some paths |
| `hydraulic_worksheet.py` | Wrong supply-curve exponent (0.54 vs 1.85) is non-conservative above test flow; density not enforced |
| `agentic/` (`size_fire_pump`, auditor) | Flips failed §22 checks to "pass" by adding a pump |
| `fireai_orchestrator_v2.py` | Treats a failed LLM review as compliant; AHJ agent writes permit text |
| `fireai_document_processor.py`, `fireai_document_intelligence.py`, `pdf_building_extractor.py`, `document_analyzer.py` | DWG bytes sent to an LLM as PNG; LLM-guessed coordinates; synthetic dimensions and gap-fill rooms |
| `master_fireai_orchestrator.py` | Placeholder "compliance" PDFs, mock routing |
| `improvement_loop.py` | Rewrites prompts autonomously (unused output, unauthenticated trigger) |
| `fireai_upload_ui.html`, `format_selector.html` | Advertise AHJ-ready / stamped deliverables |

## Obsolete (propose removal from the working tree; remains in git history)

| File(s) | Reason |
|---|---|
| `master_fireai_orchestrator.py` | Syntax error since ≥ 2025-09-29; mocks and placeholder PDF/IFC writers |
| `orchestrate.py` and everything only it imports: `enhanced_cad_engine.py`, `enhanced_bracing_engine.py`, `fireai_pro_master_Standards.py`, `fitting_takeoff_bom.py`, `floor_plan_analyzer.py`, `floor_plan_intelligence.py`, `master_fireai_products_enhanced.py`, `network_hydraulics.py`, `nfpa13_calc_sheets.py`, `node_by_node_tables.py`, `pipe_routing.py`, `pipe_sizing_optimizer.py`, `professional_bom_generator.py`, `professional_dxf_engine.py`, `professional_hydraulics.py`, `professional_shop_drawing.py`, `sprinkler_placement.py`, `bim_3d_engine.py` | Unreachable; mocks, random placement, unverified calcs |
| `merged_symbols_ai_enhanced.py`, `fireai_licensed.py`, `fireai_routing_advanced.py`, `nfpa13_routing_apis.py` | Unreachable; missing deps; randomness (`np.random`, RL exploration) |
| `color_geometry_extractor.py`, `dxf_generator_v2.py` | Unreachable, superseded |
| `fireai_nfpa13_design_engine.py`, `hydraulic_worksheet.py` | Synthetic building fallback, wrong supply-curve exponent, density not enforced (see audit). Keep tables only as material to *verify* against the standard, not as code |
| `agentic/` (all), `extraction_check.py`, `demo.py`, `demo_extraction.py` | Auto-"repair" flipped compliance flags; duplicated logic; syntax errors |
| `fireai_orchestrator_v2.py`, `context_bus.py`, `fireai_email_escalator.py` | LLM agents/reviewer that passed silently on error; emails project data |
| `improvement_loop.py`, `agent_config_store.py` | Rewrote prompts nightly; output never consumed |
| `dispatcher.py`, `job_store.py`, `fireai_schemas/` | v1 job system (replaced by `fireai/jobs/`) |
| `fireai_document_processor.py`, `fireai_document_intelligence.py`, `document_analyzer.py`, `pdf_building_extractor.py` | DWG-as-image, LLM-guessed coordinates, synthetic dimensions |
| `fireai_document_extractor.py.py`, `fireai_ifc_source.py`, `fireai_vision_heights.py` | Double extension / unreachable |
| `adapter.py`, `baseline.py`, `harness.py`, `scorer.py`, `run_evals.py`, `quality_gate.py`, `baseline.json`, `ordinary_retail.json`, `ordinary2_kitchen.json`, `esfr_warehouse.json` | Eval harness with scrambled filenames and syntax errors; replaced by `tests/` |
| `test_fireai.py` | CLI script without assertions; replaced by `tests/` |
| `_disabled_dummy.py` | Placeholder |
| `fireai_upload_ui.html`, `format_selector.html` | Advertise "AHJ-ready"/"Stamped"; no longer served |

## Uncertain

| File | Question for owner |
|---|---|
| `fireai_project_extractor.py` (non-regex parts: page classifier, Claude Vision on cover sheet) | Keep the page-classification idea for PDF ingestion? The Vision part must not return. |
| `README.md` (empty) | Replace with a v2 README when the repo strategy (new repo vs. branch) is decided |

## Suggested procedure (after approval)

1. Tag current state: `git tag archive/v1-before-cleanup`.
2. `git rm` the Obsolete list in one commit; move Reusable files to `legacy/` in another.
3. Port `TextExtractor` regexes into `fireai/interpret/text.py`; remove the last Active legacy import.
4. Re-run `pytest` and the import-graph check.
