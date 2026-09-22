# Corpus comparison: `baseline_m1` (f45b03d43b9d) vs `tuned_m15_r2` (working tree on f45b03d (Milestone 1.5, )

## REAL_001.dwg

| | baseline_m1 | tuned_m15_r2 |
|---|---|---|
| Status | needs_human_input UNIT_DETECTION_FAILED | needs_human_input UNIT_DETECTION_FAILED |
| Elements (excl. text) | none | none |
| Rooms | 0 (0 labeled) | 0 (0 labeled) |
| Unclassified % | 0.0 | 0.0 |
| View regions | n/a | n/a |
| Review triggers | UNIT_DETECTION_FAILED | UNIT_DETECTION_FAILED |
| Time / peak RSS | 33.226 s / 331.9 MB | 138.937 s / 1322.3 MB |
| DWG conversion loss (audit) | n/a | {'LINE': 7} |
| Unit evidence (suggestion only) | n/a | in |

## REAL_002.dwg

| | baseline_m1 | tuned_m15_r2 |
|---|---|---|
| Status | completed | completed |
| Elements (excl. text) | column:5, door:16, room:4, stair:2, wall:4, window:42 | column:5, door:16, room:1, stair:2, wall:4, window:42 |
| Rooms | 4 (1 labeled) | 1 (0 labeled) |
| Unclassified % | 60.1 | 60.1 |
| View regions | n/a | 2 sig / 2 |
| Review triggers | ELEMENTS_REQUIRE_VERIFICATION, HIGH_UNCLASSIFIED_FRACTION | DWG_CONVERSION_LOST_ENTITIES, ELEMENTS_REQUIRE_VERIFICATION, HIGH_UNCLASSIFIED_FRACTION, MULTIPLE_DRAWING_REGIONS, ROOM_BOUNDARY_SPANS_MULTIPLE_LABELS |
| Time / peak RSS | 8.935 s / 184.6 MB | 9.567 s / 182.6 MB |
| DWG conversion loss (audit) | n/a | {'UNKNOWN_ENT': 2} |

## REAL_003.dwg

| | baseline_m1 | tuned_m15_r2 |
|---|---|---|
| Status | completed | completed |
| Elements (excl. text) | door:16, room:3, stair:2, wall:238, window:42 | door:16, room:2, stair:2, wall:238, window:42 |
| Rooms | 3 (1 labeled) | 2 (1 labeled) |
| Unclassified % | 38.0 | 38.0 |
| View regions | n/a | 2 sig / 2 |
| Review triggers | ELEMENTS_REQUIRE_VERIFICATION, HIGH_UNCLASSIFIED_FRACTION | DWG_CONVERSION_LOST_ENTITIES, ELEMENTS_REQUIRE_VERIFICATION, HIGH_UNCLASSIFIED_FRACTION, MULTIPLE_DRAWING_REGIONS, ROOM_BOUNDARY_SPANS_MULTIPLE_LABELS |
| Time / peak RSS | 13.076 s / 194.8 MB | 13.977 s / 201.5 MB |
| DWG conversion loss (audit) | n/a | {'UNKNOWN_ENT': 2} |

## REAL_004.dwg

| | baseline_m1 | tuned_m15_r2 |
|---|---|---|
| Status | completed | completed |
| Elements (excl. text) | ceiling:17, dimension:28, door:4, stair:5, title_block:1, wall:61 | ceiling:17, dimension:28, door:4, stair:5, title_block:1, wall:61 |
| Rooms | 0 (0 labeled) | 0 (0 labeled) |
| Unclassified % | 84.4 | 84.4 |
| View regions | n/a | 4 sig / 7 |
| Review triggers | ELEMENTS_REQUIRE_VERIFICATION, HIGH_UNCLASSIFIED_FRACTION, NO_ROOMS_DETECTED | ELEMENTS_REQUIRE_VERIFICATION, HIGH_UNCLASSIFIED_FRACTION, MULTIPLE_DRAWING_REGIONS, NO_ROOMS_DETECTED |
| Time / peak RSS | 170.288 s / 554.2 MB | 176.282 s / 547.6 MB |
| DWG conversion loss (audit) | n/a | none |

## REAL_005.dwg

| | baseline_m1 | tuned_m15_r2 |
|---|---|---|
| Status | completed | completed |
| Elements (excl. text) | none | none |
| Rooms | 0 (0 labeled) | 0 (0 labeled) |
| Unclassified % | 100.0 | 100.0 |
| View regions | n/a | 1 sig / 1 |
| Review triggers | HIGH_UNCLASSIFIED_FRACTION, NO_ROOMS_DETECTED, NO_WALLS_DETECTED | DWG_CONVERSION_LOST_ENTITIES, HIGH_UNCLASSIFIED_FRACTION, NO_ROOMS_DETECTED, NO_WALLS_DETECTED |
| Time / peak RSS | 3.248 s / 129.2 MB | 3.486 s / 129.2 MB |
| DWG conversion loss (audit) | n/a | {'DIMENSION': 13} |

## REAL_006.dwg

| | baseline_m1 | tuned_m15_r2 |
|---|---|---|
| Status | failed GEOMETRY_EXTRACTION_FAILED | failed GEOMETRY_EXTRACTION_FAILED |
| Elements (excl. text) | none | none |
| Rooms | 0 (0 labeled) | 0 (0 labeled) |
| Unclassified % | — | — |
| View regions | n/a | n/a |
| Review triggers | none | none |
| Time / peak RSS | 0.476 s / 71.9 MB | 1.073 s / 82.9 MB |

## REAL_007.dwg

| | baseline_m1 | tuned_m15_r2 |
|---|---|---|
| Status | completed | completed |
| Elements (excl. text) | dimension:9 | dimension:9 |
| Rooms | 0 (0 labeled) | 0 (0 labeled) |
| Unclassified % | 81.8 | 81.8 |
| View regions | n/a | 1 sig / 1 |
| Review triggers | EXTENTS_IMPLAUSIBLE, HIGH_UNCLASSIFIED_FRACTION, NO_ROOMS_DETECTED, NO_WALLS_DETECTED, UNSUPPORTED_ENTITIES | DWG_CONVERSION_LOST_ENTITIES, EXTENTS_IMPLAUSIBLE, HIGH_UNCLASSIFIED_FRACTION, NO_ROOMS_DETECTED, NO_WALLS_DETECTED, UNSUPPORTED_ENTITIES |
| Time / peak RSS | 6.442 s / 161.4 MB | 7.43 s / 158.8 MB |
| DWG conversion loss (audit) | n/a | {'UNKNOWN_ENT': 1} |

## REAL_007.dxf

| | baseline_m1 | tuned_m15_r2 |
|---|---|---|
| Status | completed | completed |
| Elements (excl. text) | dimension:9 | dimension:9 |
| Rooms | 0 (0 labeled) | 0 (0 labeled) |
| Unclassified % | 82.1 | 82.1 |
| View regions | n/a | 1 sig / 1 |
| Review triggers | EXTENTS_IMPLAUSIBLE, HIGH_UNCLASSIFIED_FRACTION, NO_ROOMS_DETECTED, NO_WALLS_DETECTED, UNSUPPORTED_ENTITIES | EXTENTS_IMPLAUSIBLE, HIGH_UNCLASSIFIED_FRACTION, NO_ROOMS_DETECTED, NO_WALLS_DETECTED, UNSUPPORTED_ENTITIES |
| Time / peak RSS | 6.36 s / 162.0 MB | 7.119 s / 162.6 MB |

## REAL_008.dwg

| | baseline_m1 | tuned_m15_r2 |
|---|---|---|
| Status | needs_human_input UNIT_DETECTION_FAILED | needs_human_input UNIT_DETECTION_FAILED |
| Elements (excl. text) | none | none |
| Rooms | 0 (0 labeled) | 0 (0 labeled) |
| Unclassified % | 0.0 | 0.0 |
| View regions | n/a | n/a |
| Review triggers | UNIT_DETECTION_FAILED | UNIT_DETECTION_FAILED |
| Time / peak RSS | 2.498 s / 146.5 MB | 3.245 s / 147.2 MB |
| DWG conversion loss (audit) | n/a | {'ARC_DIMENSION': 1, 'LIGHT': 1, 'MULTILEADER': 1, 'UNKNOWN_ENT': 1, 'WIPEOUT': 2} |

## REAL_008.dxf

| | baseline_m1 | tuned_m15_r2 |
|---|---|---|
| Status | needs_human_input UNIT_DETECTION_FAILED | needs_human_input UNIT_DETECTION_FAILED |
| Elements (excl. text) | none | none |
| Rooms | 0 (0 labeled) | 0 (0 labeled) |
| Unclassified % | 0.0 | 0.0 |
| View regions | n/a | n/a |
| Review triggers | UNIT_DETECTION_FAILED | UNIT_DETECTION_FAILED |
| Time / peak RSS | 2.272 s / 148.3 MB | 2.42 s / 149.3 MB |

## REAL_009.dwg

| | baseline_m1 | tuned_m15_r2 |
|---|---|---|
| Status | completed | completed |
| Elements (excl. text) | dimension:9 | dimension:9 |
| Rooms | 0 (0 labeled) | 0 (0 labeled) |
| Unclassified % | 82.1 | 82.1 |
| View regions | n/a | 1 sig / 1 |
| Review triggers | EXTENTS_IMPLAUSIBLE, HIGH_UNCLASSIFIED_FRACTION, NO_ROOMS_DETECTED, NO_WALLS_DETECTED, UNSUPPORTED_ENTITIES | DWG_CONVERSION_LOST_ENTITIES, EXTENTS_IMPLAUSIBLE, HIGH_UNCLASSIFIED_FRACTION, NO_ROOMS_DETECTED, NO_WALLS_DETECTED, UNSUPPORTED_ENTITIES |
| Time / peak RSS | 6.147 s / 161.5 MB | 6.5 s / 162.8 MB |
| DWG conversion loss (audit) | n/a | {'UNKNOWN_ENT': 1} |

## REAL_009.dxf

| | baseline_m1 | tuned_m15_r2 |
|---|---|---|
| Status | completed | completed |
| Elements (excl. text) | dimension:9 | dimension:9 |
| Rooms | 0 (0 labeled) | 0 (0 labeled) |
| Unclassified % | 82.4 | 82.4 |
| View regions | n/a | 1 sig / 1 |
| Review triggers | EXTENTS_IMPLAUSIBLE, HIGH_UNCLASSIFIED_FRACTION, NO_ROOMS_DETECTED, NO_WALLS_DETECTED, UNSUPPORTED_ENTITIES | EXTENTS_IMPLAUSIBLE, HIGH_UNCLASSIFIED_FRACTION, NO_ROOMS_DETECTED, NO_WALLS_DETECTED, UNSUPPORTED_ENTITIES |
| Time / peak RSS | 6.421 s / 161.9 MB | 6.437 s / 162.7 MB |

## REAL_010.dwg

| | baseline_m1 | tuned_m15_r2 |
|---|---|---|
| Status | completed | completed |
| Elements (excl. text) | dimension:12, door:2, grid_line:1, title_block:1, wall:12 | dimension:12, door:2, grid_line:1, title_block:1, wall:12, window:1 |
| Rooms | 0 (0 labeled) | 0 (0 labeled) |
| Unclassified % | 75.2 | 74.4 |
| View regions | n/a | 5 sig / 7 |
| Review triggers | DIMENSION_SCALE_FACTOR, ELEMENTS_REQUIRE_VERIFICATION, GEOMETRY_OUTSIDE_BUILDING, HIGH_UNCLASSIFIED_FRACTION, NO_ROOMS_DETECTED | DIMENSION_SCALE_FACTOR, ELEMENTS_REQUIRE_VERIFICATION, GEOMETRY_OUTSIDE_BUILDING, HIGH_UNCLASSIFIED_FRACTION, MULTIPLE_DRAWING_REGIONS, NO_ROOMS_DETECTED |
| Time / peak RSS | 157.936 s / 1896.3 MB | 121.79 s / 1833.1 MB |
| DWG conversion loss (audit) | n/a | none |

## REAL_010.dxf

| | baseline_m1 | tuned_m15_r2 |
|---|---|---|
| Status | completed | completed |
| Elements (excl. text) | dimension:12, door:2, grid_line:1, title_block:1, wall:12 | dimension:12, door:2, grid_line:1, title_block:1, wall:12, window:1 |
| Rooms | 0 (0 labeled) | 0 (0 labeled) |
| Unclassified % | 75.2 | 74.4 |
| View regions | n/a | 5 sig / 7 |
| Review triggers | DIMENSION_SCALE_FACTOR, ELEMENTS_REQUIRE_VERIFICATION, GEOMETRY_OUTSIDE_BUILDING, HIGH_UNCLASSIFIED_FRACTION, NO_ROOMS_DETECTED | DIMENSION_SCALE_FACTOR, ELEMENTS_REQUIRE_VERIFICATION, GEOMETRY_OUTSIDE_BUILDING, HIGH_UNCLASSIFIED_FRACTION, MULTIPLE_DRAWING_REGIONS, NO_ROOMS_DETECTED |
| Time / peak RSS | 148.684 s / 2624.7 MB | 107.3 s / 1837.7 MB |

## REAL_011.dxf

| | baseline_m1 | tuned_m15_r2 |
|---|---|---|
| Status | completed | completed |
| Elements (excl. text) | none | none |
| Rooms | 0 (0 labeled) | 0 (0 labeled) |
| Unclassified % | 50.0 | 50.0 |
| View regions | n/a | 1 sig / 1 |
| Review triggers | HIGH_UNCLASSIFIED_FRACTION, NO_ROOMS_DETECTED, NO_WALLS_DETECTED | HIGH_UNCLASSIFIED_FRACTION, NO_ROOMS_DETECTED, NO_WALLS_DETECTED |
| Time / peak RSS | 5.34 s / 138.4 MB | 4.402 s / 137.9 MB |

