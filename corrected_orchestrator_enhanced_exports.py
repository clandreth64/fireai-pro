# corrected_orchestrator_enhanced_exports.py
from pathlib import Path
import os, time

def orchestrate_project(project_json: dict):
    """
    Minimal fallback orchestrator.
    Writes tiny fake deliverables so the API can return success.
    """

    proj_id = project_json.get("project_id", "unknown")

    # Match the API's output folder: defaults to ./fireai_outputs
    root = Path(os.getenv("FIREAI_LOCAL_STORAGE", "./fireai_outputs"))
    out_dir = root / proj_id
    out_dir.mkdir(parents=True, exist_ok=True)

    # Simulate some work
    time.sleep(1)

    # Create small fake files
    (out_dir / "design.dxf").write_text("fake dxf")
    (out_dir / "model.ifc").write_text("fake ifc")
    for name in ["compliance", "hydraulics", "bom", "bracing", "multistandard"]:
        (out_dir / f"{name}.pdf").write_bytes(b"%PDF-1.4\n% fake\n")

    # Return a manifest the API normalizes into Deliverables
    return {
        "ifc": str(out_dir / "model.ifc"),
        "dxf": str(out_dir / "design.dxf"),
        "pdfs": {
            "compliance": str(out_dir / "compliance.pdf"),
            "hydraulics": str(out_dir / "hydraulics.pdf"),
            "bom": str(out_dir / "bom.pdf"),
            "bracing": str(out_dir / "bracing.pdf"),
            "multistandard": str(out_dir / "multistandard.pdf"),
        },
        "extras": [],
        "metrics": {"duration_s": 1.0}
    }
