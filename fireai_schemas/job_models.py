"""
FireAI Pro — Job schemas  (fireai_schemas/job_models.py)
=========================================================
Pydantic models for the /api/generate endpoint.
Adds selected_sheets and selected_formats to GenerateRequest.
"""

from __future__ import annotations

from typing import Any, Optional
from pydantic import BaseModel, Field


# ── Valid sheet keys (must match format_selector.html data-key values) ────────

VALID_SHEETS = {
    "sheet_fp00",   # Cover sheet
    "sheet_fp10",   # Floor plan(s)
    "sheet_fp20",   # Riser diagram
    "sheet_fp30",   # Hydraulic calculations
    "sheet_fp40",   # Sprinkler & pipe schedules
    "sheet_fp50",   # Installation details
    "sheet_fp51",   # Section cuts & elevations
    "sheet_fp60",   # Bill of materials
}

# ── Valid format keys ─────────────────────────────────────────────────────────

VALID_FORMATS = {
    # Drawing outputs
    "dwg_pdf",          # PDF print set (stamped)
    "dwg_3d",           # 3D DXF overlay for Autosprink
    # BIM / 3D
    "ifc",              # IFC for Revit
    "step",             # STEP solid geometry
    # Hydraulics & compliance
    "hydraulics_json",  # Autosprink hydraulic import
    "nfpa_cert",        # NFPA 13 compliance cert PDF
    "ahj_package",      # AHJ permit package
    "bom_xlsx",         # Bill of materials Excel
}

DEFAULT_SHEETS = [
    "sheet_fp00", "sheet_fp10", "sheet_fp20", "sheet_fp30",
    "sheet_fp40", "sheet_fp50", "sheet_fp60",
]

DEFAULT_FORMATS = [
    "dwg_pdf", "ifc", "hydraulics_json", "nfpa_cert", "ahj_package", "bom_xlsx",
]


# ── Project context schema ────────────────────────────────────────────────────

class DesignerInfo(BaseModel):
    name:  str = ""
    cert:  str = ""       # e.g. NICET Level IV
    email: str = ""
    phone: str = ""

class RevisionEntry(BaseModel):
    number:      str
    date:        str
    description: str
    by:          str = ""

class ProjectContext(BaseModel):
    # Identity
    project_name:             str
    project_number_internal:  str = ""
    project_number_customer:  str = ""
    location:                 str = ""
    occupancy:                str = ""
    ahj_jurisdiction:         str = ""
    ibc_year:                 str = "2021"

    # Building
    floors:                   int   = 1
    total_area:               float = 0.0    # sq ft
    ceiling_height:           float = 10.0   # ft
    construction_type:        str   = ""
    north_rotation:           float = 0.0    # degrees from true north

    # System
    system_type:              str   = "wet"
    pipe_material:            str   = "Steel"
    seismic_zone:             str   = ""
    static_pressure:          float = 0.0    # psi
    water_supply_flow:        float = 0.0    # gpm
    density_required:         float = 0.10   # gpm/sqft
    design_area:              float = 1500.0 # sqft
    ahj_amendments:           list[str] = Field(default_factory=list)

    # People
    designer:                 DesignerInfo = Field(default_factory=DesignerInfo)
    checker_name:             str = ""
    company_name:             str = "FireAI Pro"
    company_address:          str = ""
    company_phone:            str = ""
    company_email:            str = ""

    # Sheet metadata
    issue_date:               str = ""
    revisions:                list[RevisionEntry] = Field(default_factory=list)

    # Floor plan data (populated by CAD agent, can also be pre-supplied)
    floor_plan_path:          Optional[str] = None


# ── Main request model ────────────────────────────────────────────────────────

class GenerateRequest(BaseModel):
    """
    POST /api/generate

    Mirrors the format selector output exactly:
      selected_sheets:  list of sheet keys from the drawing set section
      selected_formats: list of format keys from BIM/3D and compliance sections
      project_context:  full project definition
    """
    project_context:  dict = Field(
        ...,
        description="Full project definition. See ProjectContext for all fields.",
        example={
            "project_name":    "Riverside Office Complex — Building A",
            "location":        "4200 Riverside Dr, Austin TX 78741",
            "occupancy":       "Business (Group B)",
            "system_type":     "wet",
            "ceiling_height":  14,
            "seismic_zone":    "D1",
            "static_pressure": 72,
            "water_supply_flow": 1800,
            "ahj_jurisdiction": "Austin Fire Department",
            "designer": {"name": "Jane Smith PE", "cert": "NICET Level IV"},
        }
    )
    selected_sheets:  list[str] = Field(
        default_factory=lambda: list(DEFAULT_SHEETS),
        description="Sheet keys to generate. Defaults to all standard sheets.",
    )
    selected_formats: list[str] = Field(
        default_factory=lambda: list(DEFAULT_FORMATS),
        description="Format keys to generate. Defaults to PDF + IFC + compliance.",
    )

    def validated_sheets(self) -> set[str]:
        return {s for s in self.selected_sheets if s in VALID_SHEETS}

    def validated_formats(self) -> set[str]:
        return {f for f in self.selected_formats if f in VALID_FORMATS}


# ── Response / status models ──────────────────────────────────────────────────

class JobStatus(BaseModel):
    job_id:      str
    status:      str    # queued | running | complete | partial | failed
    stage:       str    # queued | agents | drawings | done | error
    message:     str
    created_at:  str
    completed_at: Optional[str] = None

class JobResult(BaseModel):
    job_id:               str
    status:               str
    compliant:            bool = False
    iterations_used:      int  = 0
    published_files:      list[str] = Field(default_factory=list)
    requires_human_review: bool = False
    frozen_violations:    list[dict] = Field(default_factory=list)
    output_dir:           Optional[str] = None
    completed_at:         Optional[str] = None
