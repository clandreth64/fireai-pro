"""Deterministic engineering engines (Milestone 2.0: single-space sprinkler placement).

Consumes ONLY the engineering input contract (``fireai.contract``) and resolved rule constraints
(``fireai.rules``); never CAD, the raw building model, or interpretation code (enforced by
tests/test_engineering_boundary.py). It evaluates supplied constraints; it never decides what a
standard requires.
"""

from fireai.engineering.design import (PLACEMENT_ENGINE_VERSION, SYNTHETIC_DISCLAIMERS, EngineeringDesignResult,
                                       SprinklerPlacement)
from fireai.engineering.inputs import (CeilingCondition, CeilingRegion, DesignClassification, DesignRequest, Elevation,
                                       EngineeringTolerances, InputSource, PlacementSearchSpace, Slope,
                                       SprinklerListing)
from fireai.engineering.placement import explain_layout, run_design

__all__ = ["PLACEMENT_ENGINE_VERSION", "SYNTHETIC_DISCLAIMERS", "CeilingCondition", "CeilingRegion",
           "DesignClassification", "DesignRequest", "Elevation", "EngineeringDesignResult", "EngineeringTolerances",
           "InputSource", "PlacementSearchSpace", "Slope", "SprinklerListing", "SprinklerPlacement", "explain_layout",
           "run_design"]
