#!/usr/bin/env python3
"""
FireAI Pro — IFC (Industry Foundation Classes) extractor
========================================================

The highest-fidelity intake path. When the architect can provide an .ifc
export of the BIM model (the Bismarck set references a Revit central model on
BIM 360, so one exists), read the model DIRECTLY instead of parsing a PDF of
the same model. No regex, no OCR — areas, storeys, occupancy and construction
come from typed properties.

NOTE on naming: the Bismarck PDF was an "IFC set" = *Issued For Construction*,
NOT an Industry Foundation Classes file. This module handles a real .ifc model;
it is validated here against a representative generated model, not Bismarck.

Reads:
  project_name        <- IfcBuilding / IfcProject .Name
  project_address     <- IfcBuilding.BuildingAddress (IfcPostalAddress)
  floors              <- count of IfcBuildingStorey (or Pset NumberOfStoreys)
  total_area_sqft     <- sum of IfcSpace GrossFloorArea (unit-converted)
  ceiling_height_ft   <- IfcSpace Height quantity (unit-converted)
  occupancy_use       <- Pset_*.OccupancyType
  construction_type   <- Pset_*.ConstructionType
  sprinklered         <- Pset_*.Sprinklered

Output shape matches fireai_document_extractor so the orchestrator merges it
the same way, with provenance + confidence + needs_review on every field.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

import ifcopenshell
import ifcopenshell.util.element as ue
import ifcopenshell.util.unit as uu

SQM_TO_SQFT = 10.76391
M_TO_FT = 3.280839895


def _psets_all(model) -> dict:
    """Merge all property/quantity sets across building-level elements."""
    merged: dict = {}
    for cls in ("IfcBuilding", "IfcSpace", "IfcBuildingStorey", "IfcProject"):
        for el in model.by_type(cls):
            for pset_name, props in ue.get_psets(el).items():
                for k, v in props.items():
                    merged.setdefault(k, v)
    return merged


def _first(model, classes):
    for c in classes:
        items = model.by_type(c)
        if items:
            return items[0]
    return None


def _address(bldg) -> Optional[str]:
    a = getattr(bldg, "BuildingAddress", None)
    if not a:
        return None
    parts = list(a.AddressLines or [])
    tail = ", ".join(x for x in [getattr(a, "Town", None),
                                 getattr(a, "Region", None)] if x)
    line = ", ".join(p for p in [", ".join(parts), tail] if p)
    if getattr(a, "PostalCode", None):
        line += f" {a.PostalCode}"
    return line or None


def extract_ifc(ifc_path: str) -> dict:
    model = ifcopenshell.open(ifc_path)
    src = f"IFC model {Path(ifc_path).name}"

    bldg = _first(model, ["IfcBuilding"])
    proj = _first(model, ["IfcProject"])
    psets = _psets_all(model)

    # unit-aware area/height: detect project units, convert to imperial
    area_unit = uu.get_project_unit(model, "AREAUNIT")
    is_sqm = not area_unit or "METRE" in (area_unit.Name or "").upper()

    spaces = model.by_type("IfcSpace")
    gross = 0.0
    height_ft = None
    for sp in spaces:
        for qset in ue.get_psets(sp, qtos_only=True).values():
            if "GrossFloorArea" in qset:
                gross += float(qset["GrossFloorArea"])
            if "Height" in qset and height_ft is None:
                h = float(qset["Height"])
                height_ft = round(h * M_TO_FT, 1) if is_sqm else round(h, 1)
    total_sqft = round(gross * SQM_TO_SQFT) if (gross and is_sqm) else (round(gross) or None)

    storeys = model.by_type("IfcBuildingStorey")

    def F(value, source, conf, review=False, note=""):
        d = {"value": value, "source": source, "confidence": conf,
             "needs_review": review}
        if note:
            d["note"] = note
        return d

    name = (bldg.Name if bldg else None) or (proj.Name if proj else None)
    fields = {
        "project_name": F(name, f"{src} — IfcBuilding.Name",
                          "high" if name else "none", not name),
        "project_address": F(_address(bldg) if bldg else None,
                             f"{src} — IfcPostalAddress",
                             "high" if bldg and _address(bldg) else "none"),
        "floors": F(int(psets.get("NumberOfStoreys", len(storeys))) or None,
                    f"{src} — IfcBuildingStorey count / Pset", "high"),
        "total_area_sqft": F(total_sqft, f"{src} — sum IfcSpace GrossFloorArea"
                             + (" (m²→ft²)" if is_sqm else ""),
                             "high" if total_sqft else "none"),
        "ceiling_height_ft": F(height_ft, f"{src} — IfcSpace Height"
                               + (" (m→ft)" if is_sqm else ""),
                               "high" if height_ft else "none", review=True,
                               note="Model space height; confirm deck/clearance "
                                    "for head layout per NFPA 13."),
        "occupancy_use": F(psets.get("OccupancyType"),
                           f"{src} — Pset OccupancyType",
                           "high" if psets.get("OccupancyType") else "none"),
        "construction_type": F(psets.get("ConstructionType"),
                               f"{src} — Pset ConstructionType",
                               "high" if psets.get("ConstructionType") else "none"),
        "sprinklered": F(psets.get("Sprinklered"),
                         f"{src} — Pset Sprinklered",
                         "high" if "Sprinklered" in psets else "none"),
        # Same honest gaps as the PDF path — not modeled geometry:
        "static_pressure_psi": F(None, "REQUIRES HYDRANT FLOW TEST REPORT",
                                 "none", True),
        "residual_pressure_psi": F(None, "REQUIRES HYDRANT FLOW TEST REPORT",
                                   "none", True),
        "seismic_zone": F(None, "from structural model / ASCE 7 by site",
                          "none", True),
    }
    return {"document": {"file": Path(ifc_path).name, "file_type": "ifc",
                         "schema": model.schema, "spaces": len(spaces),
                         "storeys": len(storeys)},
            "fields": fields}


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("ifc")
    a = ap.parse_args()
    print(json.dumps(extract_ifc(a.ifc), indent=2))
