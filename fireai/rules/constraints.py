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
                                            "it than to any other sprinkler (Voronoi cell clipped to the space)"),
}
BOUNDARY_MEASUREMENTS = {"point_to_boundary_min", "boundary_point_to_nearest_sprinkler_max"}

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
                + (f"; also considered: {', '.join(f'{c.rule_id} ({c.action})' for c in others)}" if others else ""))
