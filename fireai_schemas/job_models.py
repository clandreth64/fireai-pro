from __future__ import annotations
from typing import Dict, List, Optional, Literal
from pydantic import BaseModel, Field

JobPhase = Literal[
    "queued",
    "parsing",
    "symbols",
    "routing",
    "codes",
    "hydraulics",
    "bracing",
    "bom",
    "exporting",
    "done",
    "error"
]

class Artifact(BaseModel):
    kind: Literal["ifc", "dxf", "pdf", "excel", "json", "log", "other"]
    name: str
    path: str
    meta: Dict[str, str] = Field(default_factory=dict)

class Deliverables(BaseModel):
    ifc: Optional[str] = None
    dxf: Optional[str] = None
    pdfs: Dict[str, str] = Field(default_factory=dict)  # e.g. {"compliance":"...", "hydraulics":"...", ...}
    extras: List[Artifact] = Field(default_factory=list)

class ProjectInput(BaseModel):
    project_id: str
    building_name: Optional[str] = None
    level_of_protection: Optional[str] = None  # light/ordinary/extra hazard, etc.
    design_standards: List[str] = Field(default_factory=lambda: ["NFPA13"])
    metadata: Dict[str, str] = Field(default_factory=dict)

class EngineWarning(BaseModel):
    engine: str
    message: str
    code_ref: Optional[str] = None  # e.g., "NFPA13 8.5.2"
    severity: Literal["LOW", "MEDIUM", "HIGH", "CRITICAL"] = "LOW"

class ErrorInfo(BaseModel):
    code: str
    message: str
    engine: Optional[str] = None
    hint: Optional[str] = None

class JobResult(BaseModel):
    job_id: str
    project_id: str
    status: JobPhase
    # You can store timestamps or durations per phase here:
    phases: Dict[str, float] = Field(default_factory=dict)
    deliverables: Deliverables = Field(default_factory=Deliverables)
    warnings: List[EngineWarning] = Field(default_factory=list)
    error: Optional[ErrorInfo] = None
    metrics: Dict[str, float] = Field(default_factory=dict)  # e.g., {"route_length_m": 123.4}
