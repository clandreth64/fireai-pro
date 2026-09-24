"""The Drawing Understanding -> Engineering boundary (see docs/ENGINEERING_INPUT_CONTRACT.md).

Future engineering code consumes ONLY ``EngineeringInput`` from ``build_engineering_input``
(or a persisted package loaded with ``parse_engineering_input``, which accepts only the current
contract version). It must not import ezdxf, fireai.ingest, fireai.interpret, fireai.render or
fireai.pipeline (enforced by tests/test_engineering_boundary.py).
"""

from fireai.contract.engineering_input import (CONTRACT_VERSION, ContractVersionError, ContractViolation,
                                               EngineeringInput, EngineeringInputV2Draft, build_engineering_input,
                                               engineering_input_blockers, parse_engineering_input,
                                               read_legacy_engineering_input)

__all__ = ["CONTRACT_VERSION", "ContractVersionError", "ContractViolation", "EngineeringInput",
           "EngineeringInputV2Draft", "build_engineering_input", "engineering_input_blockers",
           "parse_engineering_input", "read_legacy_engineering_input"]
