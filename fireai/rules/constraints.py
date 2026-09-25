"""Engineering constraints: the ONLY thing the rules engine hands to deterministic engines.

A constraint says what must be measured, against which boundary semantics, and the effective limit
(canonical units), plus every rule contribution that produced it. Engines never see rule prose.

The measurement vocabulary is purely geometric. Which measurement an NFPA 13 requirement maps to is
RULE data, decided when a person structures the rule — not by the geometry engine.
"""

from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, Field

# name -> (dimension, meaning). Adding a measurement is a reviewed engine change.
MEASUREMENTS: dict[str, tuple[str, str]] = {
    "pairwise_min_distance": ("length", "smallest centre-to-centre distance between any two sprinklers"),
    "array_axis_spacing": ("length", "centre-to-centre distance between sprinklers adjacent along either axis "
                                     "of the layout's array (room frame)"),
    "point_to_boundary_min": ("length", "for each sprinkler, the smallest distance to the participating "
                                        "boundary segments"),
    "boundary_point_to_nearest_sprinkler_max": ("length", "over every point of the participating boundary "
                                                          "segments, the distance to the nearest sprinkler "
                                                          "(the worst point)"),
    "space_point_to_nearest_sprinkler_max": ("length", "over every point of the space, the distance to the "
                                                       "nearest sprinkler (the worst point)"),
    "nearest_sprinkler_cell_area": ("area", "for each sprinkler, the area of the part of the space closer to "
                                            "it than to any other sprinkler (Voronoi cell clipped to the space). "
                                            "NOT an S x L protection area: see array_sxl_protection_area"),
    # ── M2.2A ────────────────────────────────────────────────────────────────────────────────────
    "array_sxl_protection_area": ("area", "for each sprinkler of a rectangular (room-frame) array: S x L, where "
                                          "S is taken along the BRANCH-LINE direction of the explicit layout "
                                          "orientation and L perpendicular to it (between branch lines), and each "
                                          "dimension is the larger of the two sides' values; a side's value is the "
                                          "distance to the adjacent sprinkler on that side, or, with no adjacent "
                                          "sprinkler, TWICE the perpendicular distance to the participating wall "
                                          "reference reached on that side (SXL-ARRAY/2). Requires a LayoutOrientation"),
    "array_sxl_s_dimension": ("length", "per sprinkler, the S dimension of SXL-ARRAY/2 (along the branch lines); "
                                        "worst over the sprinklers. Requires a LayoutOrientation"),
    "array_sxl_l_dimension": ("length", "per sprinkler, the L dimension of SXL-ARRAY/2 (perpendicular to the "
                                        "branch lines); worst over the sprinklers. Requires a LayoutOrientation"),
    "perpendicular_wall_distance": ("length", "END-CONDITION wall distance: for each sprinkler of a rectangular "
                                              "(room-frame) array and each array direction with no adjacent "
                                              "sprinkler (where the array terminates at a boundary), the "
                                              "perpendicular plan distance from the sprinkler centre to the first "
                                              "boundary reached in that direction, which must be a participating kind "
                                              "and perpendicular to the direction (PERP-WALL/1). MAX bound only: it "
                                              "is NOT a minimum wall clearance"),
    "min_perpendicular_wall_distance": ("length", "MINIMUM wall CLEARANCE: for each sprinkler, the perpendicular plan "
                                                  "distance from its centre to EVERY participating wall segment whose "
                                                  "perpendicular foot lies on the segment, taking the minimum; "
                                                  "independent of neighbouring sprinklers. A nearer wall end (re-entrant "
                                                  "corner / jamb) makes it NOT EVALUABLE (MIN-WALL-CLEARANCE/1). MIN "
                                                  "bound only"),
    "ceiling_to_deflector_vertical_distance": ("length", "ceiling elevation minus sprinkler deflector elevation, on "
                                                         "one explicit datum (signed: positive = deflector below the "
                                                         "ceiling); single flat ceiling region only (VERT-DEFLECTOR/1)"),
}
SXL_MEASUREMENTS = {"array_sxl_protection_area": "area", "array_sxl_s_dimension": "S",
                    "array_sxl_l_dimension": "L"}
# measurements that need the layout's explicit branch-line orientation (M2.2A.1)
ORIENTATION_MEASUREMENTS = set(SXL_MEASUREMENTS)
BOUNDARY_MEASUREMENTS = {"point_to_boundary_min", "boundary_point_to_nearest_sprinkler_max",
                         "perpendicular_wall_distance", "min_perpendicular_wall_distance"} | set(SXL_MEASUREMENTS)
# M2.2B.1: a measurement whose meaning only makes sense with one bound says so; any other bound is a
# MAPPING ERROR that the resolver and the approval check refuse (e.g. a minimum-wall rule mapped to the
# end-condition measurement)
MEASUREMENT_BOUNDS: dict[str, set[str]] = {"perpendicular_wall_distance": {"max"},
                                           "min_perpendicular_wall_distance": {"min"}}
# measurements that follow array directions to a wall reference: straight, room-frame-aligned
# boundaries only; angled / irregular boundaries REFUSE (never evaluated with straight-wall logic)
WALL_RAY_MEASUREMENTS = {"perpendicular_wall_distance"} | set(SXL_MEASUREMENTS)
# measurements that need an established vertical position (ceiling elevation + deflector elevation)
VERTICAL_MEASUREMENTS = {"ceiling_to_deflector_vertical_distance"}
# measurements that treat boundary segments as WALL references (angled and unknown segments refuse)
WALL_REFERENCE_MEASUREMENTS = WALL_RAY_MEASUREMENTS | {"min_perpendicular_wall_distance"}
# measurement CONTRACTS that are declared (so a rule can name them honestly) but not implemented: an
# applicable rule using one REFUSES (MEASUREMENT_NOT_IMPLEMENTED) and it can never be approved.
DECLARED_MEASUREMENTS: dict[str, tuple[str, str]] = {
    "angled_wall_perpendicular_distance": ("length", "perpendicular plan distance from a sprinkler to the "
                                                     "applicable ANGLED / irregular wall segment; which segment "
                                                     "applies to which sprinkler needs a reviewed definition"),
    "angled_wall_protected_floor_worst_distance": ("length", "worst horizontal distance from protected floor area "
                                                             "bounded by an angled / irregular wall to the "
                                                             "sprinkler(s) assigned to it"),
}

# The design facts a rule's applicability may test, and which explicit input supplies each. A rule
# conditioned on a fact outside this vocabulary cannot be approved: FireAI could never establish it.
FACTS: dict[str, str] = {
    "hazard.scheme": "DesignClassification.scheme",
    "hazard.classification": "DesignClassification.value",
    "system.type": "SystemCondition.system_type",
    "system.storage": "SystemCondition.storage",
    "sprinkler.type": "SprinklerListing.sprinkler_type",
    "sprinkler.orientation": "SprinklerListing.orientation",
    "sprinkler.response": "SprinklerListing.response_type",
    "ceiling.surface": "CeilingRegion.surface",
    "ceiling.construction": "CeilingRegion.construction",
    "ceiling.slope_deg": "CeilingRegion.slope",
    "ceiling.elevation_ft": "CeilingRegion.elevation",
    "ceiling.obstructions": "CeilingCondition.obstructions_statement",
    "space.area_sf": "engineering_input/3 physical region area",
    # M2.2A
    "system.design_method": "SystemCondition.design_method (distinct from system.type)",
    "ceiling.construction_classification.scheme": "CeilingRegion.construction_classification.scheme",
    "ceiling.construction_classification": "CeilingRegion.construction_classification.value (the classification "
                                           "the standard uses — distinct from the geometric ceiling facts)",
    "space.eligibility.small_room": "DesignRequest.eligibility['small_room'].status (human-attributed; never "
                                    "derived from area alone)",
}


class Contribution(BaseModel):
    """One rule's part in an effective constraint, and what happened to it."""
    rule_id: str
    rule_set_id: str
    rule_set_version: str
    layer: str
    content_basis: str
    source_document: str
    source_reference: Optional[str] = None
    limit: float                                     # canonical units
    unit: str
    action: Literal["governs", "less_restrictive", "replaced", "replaces"]
    note: str = ""
    # M2.2A: set when the limit is derived from another resolved constraint (provenance of the value)
    derived: Optional[dict] = None           # {op, from_key, from_limit, from_unit, from_governing_rule_id, factor}


class EngineeringConstraint(BaseModel):
    key: str
    measurement: str
    bound: Literal["max", "min"]
    limit: float                                     # effective limit, canonical units (ft / sf)
    unit: Literal["ft", "sf"]
    reference_kinds: list[str] = Field(default_factory=list)
    contributions: list[Contribution]
    governing_rule_id: str

    def explain(self) -> str:
        g = next(c for c in self.contributions if c.rule_id == self.governing_rule_id)
        others = [c for c in self.contributions if c.rule_id != self.governing_rule_id]
        return (f"{self.key}: {self.bound} {self.limit:g} {self.unit} ({self.measurement}"
                + (f" vs {'/'.join(self.reference_kinds)}" if self.reference_kinds else "") + f") governed by "
                f"{g.rule_id} [{g.layer}, {g.rule_set_id} v{g.rule_set_version}]"
                + (f" = {g.derived['factor']:g} x {g.derived['from_key']} ({g.derived['from_limit']:g} "
                   f"{g.derived['from_unit']}, governed by {g.derived['from_governing_rule_id']})" if g.derived else "")
                + (f"; also considered: {', '.join(f'{c.rule_id} ({c.action})' for c in others)}" if others else ""))
