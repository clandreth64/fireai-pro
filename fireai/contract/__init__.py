"""The Drawing Understanding -> Engineering boundary (see docs/ENGINEERING_INPUT_CONTRACT.md).

Future engineering code consumes ONLY ``EngineeringInput`` from ``build_engineering_input``.
It must not import ezdxf, fireai.ingest, fireai.interpret, fireai.render or fireai.pipeline
(enforced by tests/test_engineering_boundary.py).
"""

from fireai.contract.engineering_input import (CONTRACT_VERSION, ContractViolation, EngineeringInput,
                                               build_engineering_input, engineering_input_blockers)

__all__ = ["CONTRACT_VERSION", "ContractViolation", "EngineeringInput", "build_engineering_input",
           "engineering_input_blockers"]
