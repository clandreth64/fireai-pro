# Milestone 1.5 — Real-Drawing Validation

**Engine versions:** baseline = Milestone 1 commit `f45b03d` (run `baseline_m1`, write-once, captured
*before* any tuning); final = Milestone 1.5 working tree (run `tuned_m15_r2`). An intermediate run
(`tuned_m15`) is kept as history. DWG conversion: GNU LibreDWG 0.14.8597 (commit `34f02f54`), built from a
checksum-verified tarball. ODA was not used (owner decision; non-commercial-only terms for non-members).

**Ground truth:** `tests/real_drawings/ground_truth/REAL_###.json` — produced by **AI-assisted visual review**
of each source rendering and the DXF tables, independent of FireAI's output, with EXACT / APPROXIMATE /
NOT_EVALUATED assertions. **Status: PENDING HUMAN CONFIRMATION.** Where my first visual reading was wrong
(REAL_002 "window tags"), the record says so.

**Data handling:** drawings, renders and full models stay in the git-ignored `tests/real_drawings_local/`
and `tests/real_drawings_outputs_local/`. Only anonymized counts/codes/timings (`tests/real_drawings/runs/`),
the corpus index (public sources only; the private source is withheld) and ground-truth records are committed.

## 1. Corpus (11 drawings, 15 files)

| ID | Kind | Files | DWG version | Why included | Source class |
|---|---|---|---|---|---|
| REAL_001 | Fire sprinkler / fire alarm set: tenant-suite key plan + piping/riser diagrams, **2 XREFs**, unitless | DWG | AC1027 (2013) | real sprinkler-contractor file, missing units, XREFs, 11 layouts | private (owner-authorized, local only) |
| REAL_002 | Two-storey residential plans, imperial | DWG | AC1021 (2007) | architectural plan, dynamic blocks, 2 plans in model space | Autodesk sample |
| REAL_003 | Same plan, **metric** | DWG | AC1021 | metric, exploded walls | Autodesk sample |
| REAL_004 | Building/stair **sections** and details | DWG | AC1021 | non-plan views, multileaders | Autodesk sample |
| REAL_005 | Civil **site plan** | DWG | AC1024 (2010) | not a building; suspicious units | Autodesk sample |
| REAL_006 | **3D** visualization model | DWG | AC1024 | no 2D plan geometry | Autodesk sample |
| REAL_007/8/9 | LibreDWG "example" drawing in 2000 / **R14** / 2018 | DWG + DXF pairs | AC1015 / AC1014 / AC1032 | DWG↔DXF equivalence, older CAD, unsupported entities | LibreDWG test data |
| REAL_010 | **Dynamic-block** library sheet | DWG + DXF pair | AC1032 | 76,674 entities, dynamic blocks, stress | LibreDWG test data |
| REAL_011 | **Nested/rotated/mirrored** block matrix (BricsCAD) | DXF | — | transform correctness | ezdxf examples (MIT) |

Not available this milestone (owner will add): tenant-improvement sets with XREF files present, sprinkler
backgrounds from other offices, true exploded legacy plans, multi-building sites.

## 2. Results by category (baseline → final)

Legend: ✅ correct / honest · ⚠ partial · ❌ wrong · — not applicable.

| Category | REAL_001 | REAL_002 | REAL_003 | REAL_004 | REAL_005 | REAL_006 | REAL_007–9 | REAL_010 | REAL_011 |
|---|---|---|---|---|---|---|---|---|---|
| **Geometry fidelity** | ✅ → ✅ (source rendered; 4,802 entities) | ✅ | ✅ | ✅ | ✅ | ✅ fail-closed (no 2D geometry) | ✅ shared entities identical to 3e-10; converter drops tables/wipeouts | ✅ DWG ≡ DXF (1e-12) | ✅ all 54 nested/mirrored inserts coincide with renderer |
| **Unit accuracy** | ✅ blocked (unitless) → ✅ blocked **+ evidence "in"** (viewport 1:96 × "1/8"=1'-0"") | ✅ in | ✅ m; extents within 0.4 ft of imperial twin | ✅ in | ❌ undetectable: header "in" likely wrong (parcel 66 ft) — no dims/scale to check | — | ✅ mm; R14 blocked (no $INSUNITS) | ✅ in; DIMLFAC flagged | ✅ m |
| **Room detection** | — (no rooms without units) | ❌ 3 false rooms (wall solids) + 1 merged region with 12 concatenated labels → ⚠ 0 false rooms, merged region **flagged & unnamed** | ❌ 2 false rooms + merged region → ⚠ **B/R (48 sf) found via door closure**, merged region flagged | ✅ none (sections) | ✅ none | — | ✅ none | ✅ none | ✅ none |
| **Wall detection** | — (walls are in XREF → reported with units supplied) | ✅ wall linework (layer "Walls") | ✅ 238 exploded wall lines | ⚠ section wall cuts labelled walls (not plan walls) | ✅ none | — | ✅ none | ⚠ 12 walls → 12 incl. WALL_ABOVE/BELOW now **qualified & flagged** | — |
| **Openings** | — | ⚠ 16 doors / 42 windows by *layer* → same counts, now also by **effective block name** (Door/Window dynamic blocks) | same as 002 | ⚠ door elevations labelled doors | — | — | — | ⚠ 2 doors, 1 window (library symbols) | — |
| **Structural** | — | ✅ 5 porch columns, 2 stairs | ✅ 2 stairs (porch posts not on a column layer — correctly unclassified) | ✅ 5 stairs | — | — | — | ✅ 1 grid line | — |
| **Text association** | — | ❌ labels + finish notes concatenated → ✅ never concatenated; finish notes separated; candidates listed | same as 002 | ⚠ multileader text unsupported → ✅ extracted; view titles no longer "title blocks" | — | — | — | — | ✅ 12 texts |
| **Unknown handling** | ✅ units blocked; XREFs listed | ⚠ two plans not reported → ✅ `MULTIPLE_DRAWING_REGIONS` | same | ⚠ 4 views not reported → ✅ 4 regions (= ground truth) | ✅ NO_WALLS/NO_ROOMS; ✅ now also 13 lost DIMENSIONs | ✅ fail-closed | ✅ EXTENTS_IMPLAUSIBLE | ✅ | ✅ |
| **Traceability** | ✅ every element → entity uid/handle path | ✅ | ✅ | ✅ | ✅ | — | ✅ handles preserved through LibreDWG | ✅ | ✅ |
| **Visual verification** | ✅ source.png | ✅ overlay reviewed | ✅ | ✅ | ✅ | — | ✅ | ✅ | ✅ |

No drawing produced synthetic geometry, a silent unit default, or an invented Z/level in any run.

## 3. Failure taxonomy (baseline) and status

| Class | Recurrence (baseline) | Severity | Status after Milestone 1.5 |
|---|---|---|---|
| MULTIPLE_DRAWING_REGIONS (plans/sections/details/diagrams in one model space) | 001, 002, 003, 004, 010, 011 | High — FireAI treated them as one building | **Fixed:** detected and reported (`G-VIEW-REGIONS`); FireAI still does not decide which region is the building |
| TEXT_ASSOCIATION_FAILED (concatenated names, finish notes as names) | 002, 003 | High — confident-looking wrong label | **Fixed:** per-entity parsing, `T-FINISH-NOTE`, `suspected_merged_region` |
| ROOM_BOUNDARY_INCOMPLETE / DOOR_OPENING_BREAKS_ROOM | 002, 003 | High | **Partial:** `G-DOOR-OPENING-CLOSURE` closes door openings (REAL_003 bathroom now isolated); **open archways/stairs still merge rooms** — now flagged, not hidden |
| FALSE_ROOM_FROM_WALL_SOLID (thin L/U wall poché passing the cavity filter) | 002, 003 (5 false rooms) | High — rooms that do not exist | **Fixed:** mean-width filter 2A/P < 1.5 ft |
| DWG_CONVERSION_ENTITY_LOSS (silent) | 001, 002, 003, 005, 007, 008, 009 | High — geometry missing without notice | **Mitigated:** independent `dwgread` census → review trigger. Census is type-count based: losses can be masked (R14 INSERT loss not detected by census) |
| UNIT_METADATA_MISSING | 001, 008 | Blocking (correct) | Unchanged blocking + **evidence offered, never applied** |
| UNIT_METADATA_SUSPECT (declared units probably wrong, no way to check) | 005 | High | **Not solved** — no dimension/scale evidence exists in the file |
| XREF_NOT_RESOLVED | 001 | High | Detected/reported (unchanged); XREF loading not implemented |
| DYNAMIC_BLOCK_UNSUPPORTED (anonymous *U/*B) | 002, 003, 004, 010 | Medium | **Fixed:** effective names drive classification |
| VIEW_TITLE_MISCLASSIFIED_AS_TITLE_BLOCK (surfaced by the fix above) | 003, 004 | Medium | **Fixed:** `B-VIEW-TITLE` |
| NON_PLAN_VIEW (sections/elevations interpreted with plan rules) | 004 | Medium | **Not solved** — mitigated by region reporting; view-type classification deferred |
| MULTILEADER_UNSUPPORTED | 004, 007 | Medium | **Fixed (text only)**; leader lines not extracted |
| WALL_QUALIFIER (above/below/demo walls counted as walls) | 010 | Medium | **Fixed:** `L-WALL-QUALIFIER` |
| TABLE_UNSUPPORTED / WIPEOUT / 3D entities | 007, 008, 006 | Low–Medium | Reported as unsupported (unchanged) |
| WALL_PAIRING_FAILED / WALL_THICKNESS_UNKNOWN | all wall drawings | Medium | Not implemented (deferred) |
| TAG_VS_OBJECT | — | — | **Not a failure:** my initial reading of REAL_002 was wrong; tags are part of the Window/Door blocks |

## 4. Generalized fixes (all with regression tests)

| Fix | Test(s) |
|---|---|
| View regions `G-VIEW-REGIONS` | `test_two_plans_in_model_space_are_reported`, `test_single_plan_with_title_block_is_one_significant_region` |
| Door-opening closure (analysis lines, never walls) | `test_door_opening_closure_separates_rooms[exploded, closed pieces]` |
| No label concatenation / merged-region flag | `test_opening_without_door_yields_flagged_merged_region_not_a_concatenated_name` |
| Finish-note vocabulary | `test_is_finish_note_vocabulary`, `test_finish_notes_are_not_room_names_but_real_names_are_kept` |
| Cavity mean-width filter | covered by the closure tests (ring face must not become a room) |
| Dynamic-block effective names | `test_dynamic_block_effective_name_drives_classification` |
| View title vs title block | `test_view_titles_are_not_title_blocks` (7 cases) |
| Multileader text | `test_multileader_text_is_extracted` |
| Wall qualifiers | `test_walls_above_or_demolished_are_qualified_not_plain_walls` |
| Unit evidence (never applied) | `test_unit_evidence_is_suggested_but_not_applied`, `test_conflicting_unit_evidence_gives_no_suggestion` |
| Conversion census + triggers | `test_entity_census_comparison`, `test_conversion_loss_triggers_review`, `test_missing_conversion_audit_triggers_review` |
| Real LibreDWG round trip DWG ≡ DXF | `test_real_libredwg_roundtrip_dwg_equals_dxf` |
| Fake DWG with a real converter | `test_fake_dwg_with_real_converter_fails_conversion` |

No rule references a specific customer layer name; all are documented vocabularies or geometry rules.

## 5. DWG conversion (LibreDWG 0.14.8597)

| Pair | Top-level matched by handle | Max deviation (source units) | Element counts DWG = DXF | Missing after conversion |
|---|---|---|---|---|
| REAL_007 (2000) | 67 / 67 | 2.7e-10 | yes | ACAD_TABLE ×1 |
| REAL_008 (R14) | 62 / 67 | 2.7e-10 | yes (units blocked) | WIPEOUT ×2, INSERT ×2, proxy ×1 |
| REAL_009 (2018) | 67 / 67 | 2.7e-10 | yes | ACAD_TABLE ×1 |
| REAL_010 (2018, dynamic blocks) | 117 / 117 | 1.4e-12 | yes | none — **fully equivalent** |

Splines: same curve, different flattening point counts (max shape deviation 60 mm on an 11 km drawing,
within the 0.2 % curve tolerance). Converter census losses on non-paired files: REAL_001 LINE ×7,
REAL_002/003 undecodable ×2, REAL_005 **DIMENSION ×13**. Converter provenance (name, version, command,
converted-DXF sha256, log tail, warning lines, census) is stored in every DWG model.

## 6. Performance baseline

See the table generated from `tests/real_drawings/runs/*.json`:

| File | Size (MB) | Entities (total) | Convert (s) | Load+extract (s) | Interpret (s) | Source render (s) | Overlay PNG+SVG (s) | Overlay DXF (s) | Total baseline → final (s) | Peak RSS baseline → final (MB) |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| REAL_001.dwg | 5.37 | 4802 | 113.6 | 9.2 | — | 13.5 | — | — | 33.2 → 138.9 | 332 → 1322 |
| REAL_002.dwg | 0.23 | 2057 | 1.3 | 0.9 | 0.1 | 2.0 | 3.0 | 1.3 | 8.9 → 9.6 | 185 → 183 |
| REAL_003.dwg | 0.26 | 2353 | 1.6 | 1.0 | 3.4 | 2.3 | 3.2 | 1.5 | 13.1 → 14.0 | 195 → 202 |
| REAL_004.dwg | 0.19 | 1681 | 1.0 | 0.5 | 1.6 | 22.1 | 149.3 | 0.8 | 170.3 → 176.3 | 554 → 548 |
| REAL_005.dwg | 0.17 | 27 | 0.4 | 0.1 | 0.0 | 0.6 | 1.7 | 0.2 | 3.2 → 3.5 | 129 → 129 |
| REAL_006.dwg | 0.73 | — | 0.8 | 0.2 | — | — | — | — | 0.5 → 1.1 | 72 → 83 |
| REAL_007.dwg | 0.58 | 181 | 0.8 | 0.4 | 0.0 | 1.6 | 2.2 | 1.5 | 6.4 → 7.4 | 161 → 159 |
| REAL_007.dxf | 1.39 | 182 | — | 0.6 | 0.0 | 1.7 | 2.2 | 1.8 | 6.4 → 7.1 | 162 → 163 |
| REAL_008.dwg | 0.44 | 176 | 0.6 | 0.4 | — | 1.5 | — | — | 2.5 → 3.2 | 146 → 147 |
| REAL_008.dxf | 0.58 | 231 | — | 0.3 | — | 1.5 | — | — | 2.3 → 2.4 | 148 → 149 |
| REAL_009.dwg | 0.15 | 181 | 0.5 | 0.4 | 0.0 | 1.4 | 1.9 | 1.3 | 6.1 → 6.5 | 162 → 163 |
| REAL_009.dxf | 0.85 | 182 | — | 0.4 | 0.0 | 1.7 | 2.0 | 1.4 | 6.4 → 6.4 | 162 → 163 |
| REAL_010.dwg | 2.18 | 76674 | 15.2 | 12.0 | 1.4 | 23.1 | 38.3 | 18.3 | 157.9 → 121.8 | 1896 → 1833 |
| REAL_010.dxf | 10.31 | 76674 | — | 11.3 | 1.3 | 23.6 | 39.8 | 18.5 | 148.7 → 107.3 | 2625 → 1838 |
| REAL_011.dxf | 0.09 | 510 | — | 0.1 | 0.0 | 1.7 | 2.0 | 0.1 | 5.3 → 4.4 | 138 → 138 |

Observations: interpretation is fast (≤ 3.4 s). Cost is dominated by (a) rendering — REAL_004's hatch
patterns make the overlay take ~150 s, mostly the SVG export (not optimized: baseline first);
(b) the DWG census on large files (REAL_001: conversion 8.5 → 114 s, ~4.6 GB peak in `dwgread`);
(c) model size on block-heavy drawings (REAL_010: 106 MB model JSON, ~1.8 GB RSS). REAL_010 overlay
improved 88 → 38 s by batching linework.

## 7. Remaining failure modes (honest)

1. Rooms merged through doorless openings (archways, stairs, open plans) — flagged, not split.
2. Declared-but-wrong units with no dimensions/scale text (REAL_005) — undetectable today.
3. Section/elevation views interpreted with plan vocabularies (walls/doors in sections).
4. XREF content absent (not loaded).
5. Converter losses the type-count census cannot see (masked by other entities of the same type).
6. No wall pairing/thickness; windows/doors measured from block footprints including tags.
7. Fire-alarm (`FA-`) layers grouped with fire protection.
8. Performance on hatch-heavy and block-heavy drawings; census memory.

## 8. Readiness assessment

Drawing understanding is **trustworthy in its failure behaviour** (nothing invented, everything traceable,
problems surfaced) but **not yet reliable at producing complete room/wall models from real architectural
files**: in this corpus, the only real architectural plans (REAL_002/003) yielded one correctly isolated room
and one flagged merged region per plan. A first sprinkler-design milestone is therefore justified **only**
if scoped to inputs where rooms are explicit (closed room/area polylines or human-confirmed boundaries),
single-region, units declared, with a human confirming the room model before design starts.
