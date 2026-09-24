"""NFPA 13-centred rules architecture (Milestone 2.0).

``model``        rule sets / rules / applicability / exceptions / dependencies / provenance (no values)
``constraints``  the measurement vocabulary and the EngineeringConstraints handed to engines
``resolve``      layered, three-valued, explained resolution of a rule stack into constraints
``units``        exact unit conversion

This package contains NO NFPA 13 requirement. Rule content is data supplied from lawfully accessed,
human-reviewed sources (docs/NFPA13_RULES_ARCHITECTURE.md). It must not import CAD/interpretation
code (enforced by tests/test_engineering_boundary.py).
"""

from fireai.rules.constraints import MEASUREMENTS, EngineeringConstraint
from fireai.rules.model import (LAYER_ORDER, Condition, ConstraintTemplate, DerivedLimit, Quantity, Rule, RuleApplicability,
                                RuleException, RuleParameter, RuleSet, RuleSource)
from fireai.rules.resolve import RuleResolution, resolve

__all__ = ["LAYER_ORDER", "MEASUREMENTS", "Condition", "ConstraintTemplate", "DerivedLimit", "EngineeringConstraint", "Quantity",
           "Rule", "RuleApplicability", "RuleException", "RuleParameter", "RuleResolution", "RuleSet", "RuleSource",
           "resolve"]
