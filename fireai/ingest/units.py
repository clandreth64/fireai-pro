"""Drawing-unit resolution and conversion to normalized units (feet).

Rules (no guessing):
* Units come ONLY from the DXF header ``$INSUNITS`` or an explicit user override.
* ``$INSUNITS`` = 0 (unitless) or missing  -> UNIT_DETECTION_FAILED (needs human input).
* ``$INSUNITS`` with a code FireAI does not support -> UNIT_DETECTION_FAILED.
* ``$MEASUREMENT`` (imperial/metric flag) is recorded as a hint but is NEVER used
  to pick units: it describes hatch/linetype defaults, not geometry units.
* Drawing extents are NEVER used to infer units (the v1 engine did this).

Conversion factors are exact (inch = 25.4 mm by definition).
"""

from __future__ import annotations

from dataclasses import dataclass

from fireai.errors import FailureCode, PipelineFailure

NORMALIZED_UNITS = "ft"

# unit name -> feet per unit (exact)
FEET_PER_UNIT: dict[str, float] = {
    "in": 1.0 / 12.0,
    "ft": 1.0,
    "mm": 1.0 / 304.8,
    "cm": 10.0 / 304.8,
    "m": 1000.0 / 304.8,
}

# $INSUNITS codes FireAI accepts -> unit name
INSUNITS_SUPPORTED: dict[int, str] = {1: "in", 2: "ft", 4: "mm", 5: "cm", 6: "m"}

# Codes defined by AutoCAD but not accepted for building plans in this milestone.
INSUNITS_KNOWN_UNSUPPORTED: dict[int, str] = {
    3: "miles", 7: "kilometers", 8: "microinches", 9: "mils", 10: "yards", 11: "angstroms",
    12: "nanometers", 13: "microns", 14: "decimeters", 15: "decameters", 16: "hectometers",
    17: "gigameters", 18: "astronomical units", 19: "light years", 20: "parsecs",
    21: "US survey feet", 22: "US survey inches", 23: "US survey yards", 24: "US survey miles",
}

USER_UNIT_ALIASES = {
    "in": "in", "inch": "in", "inches": "in",
    "ft": "ft", "foot": "ft", "feet": "ft",
    "mm": "mm", "millimeter": "mm", "millimeters": "mm", "millimetre": "mm", "millimetres": "mm",
    "cm": "cm", "centimeter": "cm", "centimeters": "cm", "centimetre": "cm", "centimetres": "cm",
    "m": "m", "meter": "m", "meters": "m", "metre": "m", "metres": "m",
}


@dataclass(frozen=True)
class UnitResolution:
    insunits_code: int | None
    detected_units: str | None
    resolved_units: str | None
    method: str                 # drawing_header | user_override | unresolved
    scale_to_ft: float | None
    measurement_hint: str | None
    note: str | None = None

    @property
    def resolved(self) -> bool:
        return self.resolved_units is not None


def parse_user_units(value: str | None) -> str | None:
    if value is None or str(value).strip() == "":
        return None
    key = str(value).strip().lower()
    if key not in USER_UNIT_ALIASES:
        raise PipelineFailure(
            FailureCode.UNIT_DETECTION_FAILED,
            f"Unit override '{value}' is not recognized.",
            {"accepted_units": sorted(set(USER_UNIT_ALIASES.values()))},
        )
    return USER_UNIT_ALIASES[key]


def resolve_units(insunits: int | None, measurement: int | None, user_override: str | None) -> UnitResolution:
    """Resolve drawing units. Never raises for unknown units — returns an
    unresolved result so the caller can still inventory the drawing, then
    report UNIT_DETECTION_FAILED with a unit-resolution requirement."""
    hint = {0: "imperial", 1: "metric"}.get(measurement) if measurement is not None else None
    detected = INSUNITS_SUPPORTED.get(insunits) if insunits is not None else None
    override = parse_user_units(user_override)

    if override is not None:
        note = None
        if detected is not None and detected != override:
            note = (f"User override '{override}' differs from drawing header '{detected}' "
                    f"($INSUNITS={insunits}). Override applied at user's instruction.")
        return UnitResolution(insunits, detected, override, "user_override", FEET_PER_UNIT[override], hint, note)

    if detected is not None:
        return UnitResolution(insunits, detected, detected, "drawing_header", FEET_PER_UNIT[detected], hint)

    if insunits in (None, 0):
        note = ("Drawing header does not declare units ($INSUNITS is "
                + ("missing" if insunits is None else "0 = unitless") + ").")
    elif insunits in INSUNITS_KNOWN_UNSUPPORTED:
        note = f"$INSUNITS={insunits} ({INSUNITS_KNOWN_UNSUPPORTED[insunits]}) is not a supported unit for building plans."
    else:
        note = f"$INSUNITS={insunits} is not a valid AutoCAD unit code."
    return UnitResolution(insunits, None, None, "unresolved", None, hint, note)


def unit_requirement(res: UnitResolution) -> dict:
    """The structured request returned to the user when units are unresolved."""
    return {
        "reason": res.note,
        "insunits_code": res.insunits_code,
        "measurement_system_hint": res.measurement_hint,
        "action": "Re-submit this drawing with an explicit 'units' value.",
        "accepted_units": sorted(FEET_PER_UNIT),
    }
