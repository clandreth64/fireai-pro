# Real-Drawing Validation (Milestones 1.5 and 1.6)

Sections 1–8 record Milestone 1.5 unchanged. Milestone 1.6 results are in **§M1.6** at the end.

## Milestone 1.5

**Engine versions:** baseline = Milestone 1 commit `f45b03d` (run `baseline_m1`, write-once, captured
*before* any tuning); final = Milestone 1.5 working tree (run `tuned_m15_r2`). An intermediate run
(`tuned_m15`) is kept as history. DWG conversion: GNU LibreDWG 0.14.8597 (commit `34f02f54`), built from a
checksum-verified tarball. ODA was not used (owner decision; non-commercial-only terms for non-members).

**Ground truth:** `tests/real_drawings/ground_truth/REAL_###.json` — produced by **AI-assisted visual review**
of each source rendering and the DXF tables, independent of FireAI's output, with EXACT / APPROXIMATE /
NOT_EVALUATED assertions. **Status (Milestone 1.6): these are Claude DRAFTS, `PENDING_HUMAN_VERIFICATION`** — see §M1.6 and docs/HUMAN_REVIEW.md; they are not ground truth until a person reviews them. Where my first visual reading was wrong
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

---

## §M1.6 — Milestone 1.6 results

**Runs:** `tuned_m15_r2` (M1.5 final, before) vs `m16_final` (M1.6, like-for-like: same harness
settings, work dir on the Windows bind mount, no unit hypothesis). `m16_profile_local` repeats REAL_001
(units = in, labelled hypothesis), REAL_004 and REAL_010 with a container-local work dir, which matches
production. `m16_final.json` was assembled from the per-file `metrics.json` because the harness could
not write the run file (container permission); the keys and anonymization are the same.

**Ground truth:** unchanged content, restructured as Claude **drafts** (`PENDING_HUMAN_VERIFICATION`).
No human review exists yet, so nothing below is scored against human truth.

### View classification (significant regions)

| File | FireAI view types | Draft says | Room logic |
|---|---|---|---|
| REAL_002 / 003 | FLOOR_PLAN ×2 (0.85, viewport titles "FIRST/SECOND FLOOR PLAN") | two floor plans | applied; `MULTIPLE_FLOOR_PLANS` — no plan auto-selected |
| REAL_004 | SECTION ×4 (0.85 / 0.70 via viewport titles + section layer names; 0.60 ×2 layer names only → review) | sections and details | **skipped** (M1.5 produced walls/doors as if plan; still 0 rooms) |
| REAL_001 (units = in) | UNKNOWN ×4 | key plan + riser diagrams | its riser diagrams/legends are drawn in **paper space**, which is not region-classified (limitation) |
| REAL_005/007/009/010/011 | UNKNOWN (no titles, no plan evidence) | site / test / library drawings | applied (nothing found) |

The "STAIR DETAIL" viewports in REAL_004 frame part of a section region; they are recorded as
`viewport_shows_part_of_region`, not used as the region's type.

### Wall analysis (plan regions only)

REAL_002: 229 wall segments → 95 paired pieces (5.5 in ×63, 3.5 in ×32); 120 junctions; 12 door
openings, 8 doorless openings. REAL_003 (metric): 92 pieces, 13 door / 6 doorless openings. REAL_004's 61
wall-layer entities are all in SECTION regions and were not analysed. Exterior evidence exists for one
floor of each plan pair; on the other, open exterior walls leave the outline unclosed, so it is `unknown`.
REAL_002/003 produced no `ROOM_SPLIT_CANDIDATE`: closing their doorless openings does not separate the
labels cleanly, so the merged regions stay flagged and unsplit.

### DWG conversion loss (handle-level, class-resolved)

| File | Significance | Detail |
|---|---|---|
| REAL_001 | minor | LINE ×7, only in unreferenced block definitions (M1.5 count census: "LINE ×7 lost", trigger) |
| REAL_002 / 003 | review | ACAD_TABLE ×2 (M1.5: "UNKNOWN ×2") |
| REAL_005 | **material** | DIMENSION ×13 in model space → `DWG_CONVERSION_LOST_ENTITIES` (error), blocks engineering |
| REAL_007 / 009 | review | ACAD_TABLE ×1 |
| REAL_008 (R14) | **material** | MULTILEADER ×1; review: ACAD_TABLE, ARC_DIMENSION, LIGHT; minor: WIPEOUT ×2 |
| REAL_004 / 010 | none | — |

Limitation: the audit compares LibreDWG's independent JSON read with its DXF writer. Objects LibreDWG
cannot **decode** are invisible to both sides. REAL_008's two INSERTs, found in M1.5 only by comparing
with the author's own DXF export, are still not detectable without such a reference.

### Performance (before → after, like-for-like)

| File | Convert+audit (s) | Source render (s) | Overlay PNG+SVG (s) | Overlay DXF (s) | Total (s) | Pipeline peak RSS (MB) | Converter peak RSS (MB) | Model JSON (MB) |
|---|---|---|---|---|---|---|---|---|
| REAL_001.dwg (units unresolved) | 113.6 → 108.1 | 13.5 → 5.7 | — | — | 138.9 → 126.6 | 1322 → 266 | 4575 | 8.2 → 2.7 |
| REAL_002.dwg | 1.3 → 1.2 | 2.0 → 1.4 | 3.0 → 2.5 | 1.3 → 0.7 | 9.6 → 8.0 | 183 → 165 | 72 | 6.8 → 2.6 |
| REAL_004.dwg | 1.0 → 0.9 | 22.1 → 20.9 | **149.3 → 3.3** | 0.8 → 0.4 | **176.3 → 28.1** | 548 → 525 | 72 | 3.9 → 1.3 |
| REAL_010.dwg | 15.2 → 14.4 | 23.1 → 18.3 | 38.3 → 31.0 | 18.3 → 12.2 | 121.8 → 101.8 | **1833 → 1082** | 72 | **155.3 → 42.8** |
| REAL_010.dxf | — | 23.6 → 16.0 | 39.8 → 41.9 | 18.5 → 16.6 | 107.3 → 101.3 | **1838 → 1144** | — | **155.3 → 42.8** |

All other files are unchanged or ±1 s. Changes that produced this:
* The overlay underlay draws hatch outlines only (`source.png` keeps full hatches).
* Renderers reuse the loaded DXF: 1 parse instead of 4.
* The DWG census is streamed and handle-level, compared against the already-loaded document (the M1.5
  version loaded 176 MB of JSON, about 660 MB of Python memory, and re-parsed the DXF).
* The harness's duplicate probe conversion was removed. It had inflated the M1.5 peak RSS of 1322 MB.
* The model JSON is compact and omits per-entity default values. This is lossless and tested; the safety
  flags are always written.

**What did NOT improve:** REAL_001 conversion time. LibreDWG itself peaks at **4.6 GB RSS** for this
5 MB DWG, in both `dwg2dxf` and `dwgread`. The Docker VM has 7.7 GB. Measured standalone on
container-local disk, each step takes 6–16 s. In the pipeline it took 108 s on the bind mount and 221 s in
the local-work profiling run, so memory pressure and I/O dominate and vary run to run. REAL_010's DWG
conversion dropped 14.4 → 4.3 s with a local work dir.

**Targets** (not yet met for REAL_001):
* Conversion and audit in an isolated worker with ≥ 8 GB and a hard cap (`FIREAI_DWG_MAX_MEMORY_MB`,
  default 8192; overruns fail closed).
* Conversion + audit ≤ 30 s for a ≤ 10 MB DWG.
* Pipeline RSS ≤ 1.2 GB for 80 k entities (met: 1.08–1.14 GB).
* Model JSON ≤ 50 MB for 80 k entities (met: 42.8 MB).
* Overlay ≤ 60 s (met across the corpus).
Next step: store the source-entity layer separately from the semantic model, with no provenance dropped.

---

## §M1.7 — Human-discovered room-topology failure (REAL_002) and general fixes

**Human observation (owner, 2026-09-22):** REAL_002 contains two floor plans (FireAI: correct). Each
has many rooms, but FireAI showed the second floor as one large merged, unlabelled region. FireAI's
`ROOM_BOUNDARY_SPANS_MULTIPLE_LABELS` trigger correctly kept that result from being trusted. The
observation is recorded in `tests/real_drawings/human_reviews/REAL_002.json`, marked as transcribed
by Claude. No counts, boundaries or other facts were added.

### Root cause (measured, not guessed)

* Each floor's interior wall faces are drawn as **one closed polyline** (91 vertices on floor 2), with
  partitions as peninsulas. The enclosed space is therefore a single polygon, and rooms can only be
  separated by the door-closure analysis lines (rule G-DOOR-OPENING-CLOSURE).
* The door closures were built from wall vertices **rounded to 6 decimals**. Coordinates in inches
  scaled to feet have long decimals, so every closure endpoint missed the true vertex by about
  3–5×10⁻⁷ ft. Shapely does not node a line that misses by that much. All 50 closures became
  polygonize "cut edges" and separated nothing.
* Snapping the same closures onto the true vertices splits floor 2 into its rooms. The synthetic
  fixtures use round coordinates (19.75 …), which is why this was never caught.
* Two secondary problems:
  * Text embedded in symbol blocks (a 3-way switch's "3", a receptacle's "GFI") was accepted as
    room-label text, producing "HALL 3".
  * Wall gaps at offset (bay) windows were reported as doorless openings.

### General fixes (no file-, name- or coordinate-specific logic)

1. Door-closure lines use the **exact** wall vertices, and closure lines are **snapped** onto the
   wall linework before polygonizing. The split-candidate closing lines get the same treatment.
2. **R-SYMBOL-TEXT:** text inside a symbol block is never a room label, unless its layer or its
   block's name says it is a room tag.
3. **W-OPENING windows:** a wall gap is classified as a window when a window element spans it. It is
   also classified as a window, at confidence 0.5 and flagged for review, when the gap has no jambs
   and a window lies within 1.5 ft (offset/bay windows). Windows are never treated as passages
   between spaces.
4. Unchanged principle: doorless openings are **not** closed automatically. Spaces joined through
   them stay one region, are flagged, and only get *suggested* split candidates.

Regression tests: `tests/test_room_topology_m17.py`. They use generated drawings with non-round
inch coordinates, a single-ring wall outline, symbol text and a bay window. **Four of them fail on
the M1.6 code** and pass now; four more guard behaviour that must not change.

### Results (runs `m16_final` → `m17`, same harness settings)

| File | Rooms before → after | Doorless openings before → after | Other changes |
|---|---|---|---|
| REAL_002 | 1 merged → 14 (11 labelled; 2 small unlabelled closets; 1 flagged open-plan region on floor 1: LIVING / FORUM / KITCHEN / HALL) | 8 → 1 (7 are windows) | floor 2: MASTER BEDROOM, WALK-IN CLOSET, HALL, 2 × BEDROOM, 2 × B/R separated; floor 1: DINING ROOM, LAUNDRY, STUDY, B/R now found (none before) |
| REAL_003 (metric twin) | 2 → 12 (11 labelled; same flagged open-plan region) | 6 → 1 (5 windows) | same rooms as REAL_002; areas within a few % (different wall thicknesses) |
| REAL_004 (sections) | 0 → 0 | 0 → 0 | unchanged; room logic still skipped |
| REAL_001 (units = in, read-only from its current location) | 0 → 0 | — | identical element counts and triggers |
| REAL_005–011 | unchanged | unchanged | no triggers added or removed |

**Regressions:** none found in the corpus or the test suite. The floor-1 open-plan region remains
merged and flagged (`ROOM_BOUNDARY_SPANS_MULTIPLE_LABELS`). This is intended: FireAI cannot tell
whether LIVING / FORUM / KITCHEN / HALL are separate rooms or one open space, and it does not invent
boundaries. REAL_002's earlier human evaluations now show as **stale** (FireAI's output changed); the
facts remain.

`ENGINE_VERSION` → `interp.m17.1`, so verifications made against the M1.6 interpretation are INVALIDATED.

## §M1.8 — Physical regions vs semantic spaces; view-aware interpretation

### Human evidence (authoritative for this iteration; records unchanged)

| Record | Reviewer / evaluated output | Findings used | Class |
|---|---|---|---|
| REAL_002 | owner, evaluated `m17` (human_review/3, HUMAN_VERIFIED at the time) | Facts confirmed: two floor plans. CORRECTED: named first-floor spaces (Kitchen / Living Room / Forum / Hall) are not individually represented; "no invented walls" | B (the model had one concept, `room`, for both enclosure and named space) |
| REAL_004 | owner, evaluated `m17` (HUMAN_VERIFIED at the time) | Facts confirmed: 4 SECTION views. Walls, structure and excluded content CONFIRMED. CORRECTED: "doors" in the central section are not plan doors; section construction/material semantics missing | A (plan interpretation applied in a section) + B (nested parts of one door split into several objects); materials: C (unsupported) |
| REAL_003 | **no human review exists** | machine comparison only (metric twin of REAL_002) | — |

CAD check of the REAL_004 claim: the flagged content is door **elevations** (leaf outlines, a glass lite and
a handle on a door-detail layer) in a SECTION view. The doors are real, so "there are no doors" would be an
E-class claim. The defect is that elevation content was **promoted to plan doors**, split into 4 objects,
and flagged only generically.

### Hypotheses

* **A: physical region ≠ semantic space.** Supported by REAL_002 and REAL_003. Implemented generally
  (`space` elements, `boundary_state` known/unresolved; see `ARCHITECTURE_V2.md` §5c).
* **B: view type constrains interpretation.** Supported by REAL_004 and the CAD evidence. Implemented
  generally (`depiction` elements in non-plan views, `view_context` on every element, G-NESTED-PARTS).
  Doors are not banned from sections: they are kept as what the view depicts. UNKNOWN views are
  marked `unconfirmed` and refused by the engineering contract.

Tests: `tests/test_m18_spaces_and_views.py` (15 tests; 12 fail on the M1.7 code).

### Results (runs `m17` → `m18`, same harness settings)

| File | Before | After |
|---|---|---|
| REAL_002 (in) | 14 rooms; one flagged 830 sf region holding 4 names | the same 14 physical regions (areas identical); 15 semantic spaces: 11 **known** (each the only name in its region), 4 **unresolved** (LIVING ROOM, FORUM, KITCHEN, HALL: point at the label, no boundary, no area); 2 unlabelled closets have no space; the trigger lists the 4 space ids |
| REAL_003 (m) | 12 rooms; the same flagged region | the same 12 physical regions; the same 15 names and the same states (11 known, 4 unresolved) as REAL_002. Imperial and metric are equivalent; known areas differ by ≤ 5 % as in M1.7 (different wall drawing) |
| REAL_004 (4 × SECTION) | 4 plan `door` elements (D00100–D00103), all in one section | 0 plan doors or windows; 2 `depiction` elements (`door_in_section`, confidence 0.5, verification required, `plan_semantic: false`), one per drawn door. The lite and handle are merged into their leaf (G-NESTED-PARTS). Source union identical: 56 → 56 entities. New trigger `NON_PLAN_OPENING_DEPICTIONS`. Walls, stairs and ceilings unchanged and marked `view_context: non_plan` |
| REAL_001 (units = in, read-only) | 1019 FP, 11 title_block, 1090 text, 4 UNKNOWN views | identical counts; elements now carry `view_context: unconfirmed` |
| REAL_005–011 | — | identical categories, regions and triggers; REAL_006 still fails and REAL_008 still needs units (pre-existing) |

**Human reviews after reprocessing:** REAL_002 and REAL_004 keep their facts (the sources are unchanged).
All 12 evaluations of each are **stale** (their evaluated model differs), so the effective status is
`PARTIALLY_HUMAN_REVIEWED`. The records were not edited; invalidation happened on its own.

**Performance:** the interpret stage changed by ≤ 0.3 s per file; model JSON grew by ≤ 34 kB (REAL_003).
Wall-clock differences in this run (REAL_010: 90 s → 124 s) came from stages M1.8 does not touch
(LibreDWG conversion, DXF load, overlay DXF write) while other containers were loading the host.

**Limitations:** semantic space boundaries inside an open region stay unresolved until a person draws
them; section/elevation materials and construction semantics are not modelled; UNKNOWN views keep plan
interpretation (marked unconfirmed); REAL_003 has no human review.

## §M2.0-readiness — Final checkpoint before Milestone 2.0 (2026-09-24, at `a22dd7a`)

No Drawing Understanding code changed in this checkpoint. A verification corpus run (`readiness`,
outputs kept outside the repository so the reviewed m18 outputs stay current) is identical to `m18`
for every file (categories, triggers, physical regions).

### Human review state (corpus reviews, human_review/3)

| Drawing | Reviewer | Evaluated output | Status | Facts | Evaluations |
|---|---|---|---|---|---|
| REAL_002 (in) | owner | m18, engine `interp.m18.1` (current) | HUMAN_VERIFIED | 4/4 CONFIRMED | 11 CONFIRMED, fire_protection NOT_EVALUATED, 0 stale |
| REAL_003 (m) | clandreth | m18 (current) | HUMAN_VERIFIED | 4/4 CONFIRMED | 11 CONFIRMED, fire_protection NOT_EVALUATED, 0 stale |
| REAL_004 (sections) | clandreth | m18 (current) | HUMAN_VERIFIED | 4/4 CONFIRMED | 6 CONFIRMED, 5 NOT_EVALUATED (room ×3, windows, fire protection), 1 CORRECTED (major: section materials/construction not modelled), 0 stale |

These reviews measure FireAI's accuracy. They are **not** the product verification that the
engineering gate reads (see below).

### Imperial/metric semantic regression pair (permanent)

`tests/test_unit_system_equivalence.py` with `tests/real_drawings/semantic_pair.py` compares
engineering meaning:
* view count and types;
* physical regions, and whether they are merged;
* known and unresolved semantic spaces;
* door / window / stair / column / depiction counts per view;
* space connectivity through openings;
* per-view engineering blockers (the real contract under a throw-away simulated verification);
* extents, areas and wall length in feet, within tolerances.

It runs on generated drawings in in / mm / cm / m (always, exact tolerance), proves it detects a unit
misreading and a semantic change, and runs on the real pair when the local corpus and LibreDWG exist.
Evidence-backed differences are declared in `tests/real_drawings/equivalence_pairs.json`. A
declaration must match exactly and fails the test when it stops being observed.

REAL_002 ↔ REAL_003 result: view types, open-plan condition (the same 4 unresolved spaces),
blockers, door/window/stair counts, extents (≤ 3 %), unique-room areas (≤ 8 %) and wall length
(≤ 10 %) agree. Declared differences:

| Difference | Class | Evidence |
|---|---|---|
| "DINING ROOM" vs "DINNING ROOM" | source | the metric file's label text is spelled that way |
| 5 columns vs 0 | source | the imperial file inserts a `Column` block; the metric file has plain lines there and no column block |
| 2 unlabelled closet regions (floor 2) only in imperial, plus the resulting connection change | **FireAI limitation** | the closets exist in both: 16.15 sf (in) vs 14.17 sf (m, thicker walls); the fixed 16.0 sf minimum for polygonized rooms keeps one pair and drops the other |

Also found: one door can produce several overlapping wall-gap openings (3 in imperial, 4 in metric
for the same closet door), and wall-gap analysis misses some doors and windows that do exist as
elements. Connectivity is therefore compared as a set.

### First real M2.0 candidate and the real engineering gate

* **Real gate (`require_verified_model` / `build_engineering_input`), unchanged:** REAL_002 and
  REAL_003 are **REFUSED** with `model is REVIEW_REQUIRED`. No product verification exists (no review
  store record with selected regions and acknowledged triggers). The corpus review does not create
  one, and FireAI does not create one on anyone's behalf.
* **Other blockers:**
  * none at model level: units resolved, no XREFs, and conversion loss is `review` (2 ACAD_TABLE
    schedule objects, no plan geometry);
  * per region: floor 1 (V1) is blocked by the open-plan merged region and its 4 unresolved spaces;
    floor 2 (V2) has **no** other blocker.
* **Candidates on V2 (known boundaries):**
  * B/R (bathroom; bathtub inside), 69.7 sf, rectangular;
  * second B/R, 69.2 sf;
  * BEDROOM 117.8 sf and 141.7 sf (non-rectangular);
  * WALK-IN CLOSET 54.4 sf;
  * MASTER BEDROOM 231.5 sf;
  * HALL 149.7 sf (contains the stair).

  The same set is found in REAL_003.
* **Simplest technically valid candidate:** REAL_002 `SP00098` "B/R" in physical region `R00097`:
  * 69.74 sf, 7.9 × 8.8 ft, rectangularity 1.00;
  * one boundary segment is a door-opening analysis line;
  * metric twin: REAL_003 `SP00314`, 66.2 sf, rectangularity 1.00.
* **Package preview:** built through the real contract code with a *simulated* verification in a
  throw-away store. It is not a pass and was not persisted.
  * It contains identity, schema 0.4.0, engine, source sha, fingerprint, the LOCAL frame, units in
    and the SRC→LOCAL matrix, `spatial_context: unassigned`, `z_status: unknown`, the 8 `not_provided`
    items, 9 physical regions, 7 known semantic spaces, 45 derived wall pieces (6 bound the
    candidate) and 0 columns.
  * The leak scan found no entity ids, handles, layers, block names or DXF types.
  * **Missing:** openings and boundary-segment kinds (the door-closure segment is indistinguishable
    from a wall). This is the M2.0 prerequisite in `MILESTONE_2_0_SPEC.md` §3a.

### Technical debt found

1. Contract draft 2 has no openings or boundary-segment kinds (blocks M2.0 wall-distance checks).
2. Fixed `MIN_POLYGONIZED_ROOM_SF` edge sensitivity (the closet asymmetry above).
3. Overlapping or missed wall-gap openings.
4. Corpus-review evaluations bind to the output file's bytes. `model_id`, `created_at` and the
   converter label change on every re-run, so a re-run with identical content makes evaluations
   stale. This is conservative, but it costs the reviewer time. The product verification
   fingerprint is not affected.
5. Element provenance records `engine_version` "0.1.0" while the model binding records
   "0.1.0+interp.m18.1".
6. Selection is per view region, so an open-plan area on one floor blocks every room on that floor.
