"""Exact unit conversion for rule quantities (definitions, not approximations: 1 in = 0.0254 m exactly)."""

from __future__ import annotations

from fireai.rules.model import Quantity

_LENGTH_TO_FT = {"ft": 1.0, "in": 1.0 / 12.0, "m": 1.0 / 0.3048, "mm": 1.0 / 304.8, "cm": 1.0 / 30.48}
_AREA_TO_SF = {"sf": 1.0, "ft2": 1.0, "in2": 1.0 / 144.0, "m2": 1.0 / 0.09290304, "mm2": 1.0 / 92903.04}


class UnitError(ValueError):
    pass


def dimension(unit: str) -> str:
    if unit in _LENGTH_TO_FT:
        return "length"
    if unit in _AREA_TO_SF:
        return "area"
    raise UnitError(f"unsupported unit {unit!r}")


def to_canonical(q: Quantity) -> tuple[float, str]:
    """(value, canonical unit): lengths in ft, areas in sf."""
    d = dimension(q.unit)
    return (q.value * _LENGTH_TO_FT[q.unit], "ft") if d == "length" else (q.value * _AREA_TO_SF[q.unit], "sf")
