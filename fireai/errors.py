"""Explicit, fail-closed failure states for the drawing-understanding pipeline.

A failure is never converted into a successful result. Every stage either
returns real output or raises PipelineFailure with one of these codes.
"""

from __future__ import annotations

from enum import Enum
from typing import Any


class FailureCode(str, Enum):
    INVALID_DRAWING = "INVALID_DRAWING"
    UNSUPPORTED_FORMAT = "UNSUPPORTED_FORMAT"
    FILE_TOO_LARGE = "FILE_TOO_LARGE"
    DWG_CONVERSION_FAILED = "DWG_CONVERSION_FAILED"
    DWG_CONVERSION_UNAVAILABLE = "DWG_CONVERSION_UNAVAILABLE"
    UNIT_DETECTION_FAILED = "UNIT_DETECTION_FAILED"
    GEOMETRY_EXTRACTION_FAILED = "GEOMETRY_EXTRACTION_FAILED"
    EMPTY_DRAWING = "EMPTY_DRAWING"
    OVERLAY_GENERATION_FAILED = "OVERLAY_GENERATION_FAILED"
    INTERNAL_ERROR = "INTERNAL_ERROR"


# Codes that mean "a human must supply information before processing can
# continue" rather than "this input is unusable".
NEEDS_HUMAN_INPUT_CODES = {FailureCode.UNIT_DETECTION_FAILED}

# Review code attached to completed models that still need a human decision.
DRAWING_REQUIRES_HUMAN_REVIEW = "DRAWING_REQUIRES_HUMAN_REVIEW"


class PipelineFailure(Exception):
    """Raised by any stage that cannot produce trustworthy output."""

    def __init__(self, code: FailureCode, message: str, details: dict[str, Any] | None = None):
        super().__init__(f"{code.value}: {message}")
        self.code = code
        self.message = message
        self.details = details or {}

    def to_dict(self) -> dict[str, Any]:
        return {"code": self.code.value, "message": self.message, "details": self.details}
