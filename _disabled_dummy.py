# corrected_orchestrator_enhanced_exports.py
from pathlib import Path
import os, time

# Valid PDF generation
from reportlab.pdfgen import canvas
from reportlab.lib.pagesizes import LETTER

# Valid minimal DXF generation
import ezdxf


def _make_pdf(path: Path, title: str, lines: list[str]):
    path.parent.mkdir(parents=True, exist_ok=True)
    c = canvas.Canvas(str(path), pagesize=LETTER)
    width, height = LETTER
    c.setTitle(title)
    y = height - 72
    c.setFont("Helvetica-Bold", 14)
    c.drawString(72, y, title)
    y -= 24
    c.setFont("Helvetica", 11)
    for ln in lines:
        c.drawString(72, y, ln)
        y -= 16
        if y < 72:
            c.showPage()
            y = height - 72
            c.setFont("Helvetica", 11)
    c.showPage()
    c.save()


def _make_dxf(path: Path):
    doc = ezdxf.new("R2010")
    msp = doc.modelspace()
    # Tiny cross so any viewer renders something
    msp.add_line((0, 0), (100, 0))
    msp.add_line((0, 0), (0, 100))
    doc.saveas(str(path))


def _make_ifc_stub(path: Path):
    # Minimal STEP/IFC skeleton; not a full model but syntactically parseable text
    txt = """ISO-10303-21;
HEADER;
FILE_DESCRIPTION(('ViewDefinition[CoordinationView_V2.0]'),'2;1');
FILE_NAME('dummy.ifc','','','','','','');
FILE_SCHEMA(('IFC4'));
ENDSEC;
DATA;
ENDSEC;
END-ISO-10303-21;
"""
    path.write_text(txt, encoding="utf-8")


def orchestrate_project(project_json: dict):
    """
    Fallback orchestrator that emits VALID PDFs and a minimal DXF.
    IFC is a light STEP stub. Use only for smoke tests.
    """
    proj_id = project_json.get("project_id", "unknown")
    root = Path(os.getenv("FIREAI_LOCAL_STORAGE", "./fireai_outputs"))
    out_dir = root / proj_id
    out_dir.mkdir(parents=True, exist_ok=True)

    # Simulate a bit of work
    time.sleep(1)

    # Make artifacts
    dxf_path = out_dir / "design.dxf"
    ifc_path = out_dir / "model.ifc"
    _make_dxf(dxf_path)
    _make_ifc_stub(ifc_path)

    _make_pdf(out_dir / "compliance.pdf", "Compliance Report", [
        f"Project: {proj_id}", "This is a dummy compliance report for pipeline testing."
    ])
    _make_pdf(out_dir / "hydraulics.pdf", "Hydraulics Report", [
        "Hydraulics summary (dummy data)", "Flow: 100 gpm @ 80 psi"
    ])
    _make_pdf(out_dir / "bom.pdf", "Bill of Materials", [
        "Item 001: Pipe 2\" Sch40 - 120 ft", "Item 002: Elbow 2\" - 20 pcs"
    ])
    _make_pdf(out_dir / "bracing.pdf", "Seismic Bracing", [
        "Zone: 4", "Bracing summary (dummy)"
    ])
    _make_pdf(out_dir / "multistandard.pdf", "Multi-Standard Report", [
        "NFPA 13 / FM / UL (dummy)"
    ])

    return {
        "ifc": str(ifc_path),
        "dxf": str(dxf_path),
        "pdfs": {
            "compliance": str(out_dir / "compliance.pdf"),
            "hydraulics": str(out_dir / "hydraulics.pdf"),
            "bom": str(out_dir / "bom.pdf"),
            "bracing": str(out_dir / "bracing.pdf"),
            "multistandard": str(out_dir / "multistandard.pdf"),
        },
        "extras": [],
        "metrics": {"duration_s": 1.0, "orchestrator": "dummy-valid"},
    }
