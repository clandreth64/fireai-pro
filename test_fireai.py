"""
FireAI Pro — Local test runner
================================
Run this from your repo root to test the full pipeline end-to-end
WITHOUT deploying to Railway.

Usage:
    python test_fireai.py                          # full run, all defaults
    python test_fireai.py --sheets fp10 fp30       # only floor plan + hydraulics
    python test_fireai.py --formats dwg_pdf ifc    # only PDF + IFC
    python test_fireai.py --quick                  # 1 compliance iteration max

Requirements (run once):
    pip install anthropic ezdxf reportlab openpyxl httpx fastapi uvicorn

Env vars needed (set in your shell or .env):
    ANTHROPIC_API_KEY=sk-ant-...
    FIREAI_ESCALATION_EMAIL=you@yourcompany.com
    FIREAI_FROM_EMAIL=you@yourcompany.com
"""

import argparse
import asyncio
import json
import os
import sys
from datetime import datetime
from pathlib import Path

# ── Make sure repo root is on the path ───────────────────────────────────────
sys.path.insert(0, str(Path(__file__).parent))

from fireai_orchestrator_v2 import FireAIOrchestrator
from fireai_drawing_engine   import FireAIDrawingEngine

# ── Realistic test project ────────────────────────────────────────────────────
# A 2-floor office building — representative of a typical contractor project.
# Coordinates are in feet from lower-left origin.

TEST_PROJECT = {
    # ── Identity ──────────────────────────────────────────────────────────────
    "project_name":            "Riverside Office Complex — Building A",
    "project_number_internal": "FP-2024-0142",
    "project_number_customer": "ROC-A-FP-001",
    "location":                "4200 Riverside Dr, Austin TX 78741",
    "occupancy":               "Business (Group B)",
    "ahj_jurisdiction":        "Austin Fire Department",
    "ibc_year":                "2021",

    # ── Building ──────────────────────────────────────────────────────────────
    "floors":           2,
    "total_area":       24000,      # 24,000 sq ft per floor
    "ceiling_height":   14,         # ft
    "construction_type":"II-B",
    "north_rotation":   15,         # degrees from true north

    # ── System ───────────────────────────────────────────────────────────────
    "system_type":          "wet",
    "pipe_material":        "Schedule 40 Steel",
    "seismic_zone":         "D1",
    "static_pressure":      72,     # psi
    "water_supply_flow":    1800,   # gpm
    "density_required":     0.10,   # gpm/sqft (light hazard)
    "design_area":          1500,   # sqft
    "ahj_amendments":       ["Austin amendment 2022 — head spacing max 14ft"],

    # ── People ────────────────────────────────────────────────────────────────
    "designer": {
        "name":  "Jane Smith PE",
        "cert":  "NICET Level IV — #123456",
        "email": "jsmith@contractor.com",
        "phone": "512-555-0100",
    },
    "checker_name":    "Bob Jones PE",
    "company_name":    "ABC Fire Protection Inc.",
    "company_address": "1234 Industrial Blvd, Austin TX 78702",
    "company_phone":   "512-555-0200",
    "company_email":   "info@abcfireprotection.com",

    # ── Sheet metadata ────────────────────────────────────────────────────────
    "issue_date": datetime.now().strftime("%m/%d/%Y"),
    "revisions":  [],

    # ── Floor plan geometry (realistic 120ft x 200ft floorplate) ─────────────
    "walls": [
        # Exterior perimeter
        {"points": [{"x":0,"y":0},{"x":200,"y":0},{"x":200,"y":120},{"x":0,"y":120}],
         "closed": True, "exterior": True},
        # Interior corridor N-S
        {"points": [{"x":95,"y":0},{"x":95,"y":120}], "exterior": False},
        {"points": [{"x":105,"y":0},{"x":105,"y":120}], "exterior": False},
        # Cross walls
        {"points": [{"x":0,"y":60},{"x":95,"y":60}], "exterior": False},
        {"points": [{"x":105,"y":60},{"x":200,"y":60}], "exterior": False},
    ],
    "columns": [
        {"x": 0,   "y": 0,   "width": 1.5, "depth": 1.5},
        {"x": 100, "y": 0,   "width": 1.5, "depth": 1.5},
        {"x": 200, "y": 0,   "width": 1.5, "depth": 1.5},
        {"x": 0,   "y": 60,  "width": 1.5, "depth": 1.5},
        {"x": 100, "y": 60,  "width": 1.5, "depth": 1.5},
        {"x": 200, "y": 60,  "width": 1.5, "depth": 1.5},
        {"x": 0,   "y": 120, "width": 1.5, "depth": 1.5},
        {"x": 100, "y": 120, "width": 1.5, "depth": 1.5},
        {"x": 200, "y": 120, "width": 1.5, "depth": 1.5},
    ],
    "rooms": [
        {"name": "Suite 100",      "area": "3,000 SF", "boundary": [{"x":0,"y":0},{"x":95,"y":0},{"x":95,"y":60},{"x":0,"y":60}]},
        {"name": "Suite 101",      "area": "3,000 SF", "boundary": [{"x":0,"y":60},{"x":95,"y":60},{"x":95,"y":120},{"x":0,"y":120}]},
        {"name": "Suite 102",      "area": "3,000 SF", "boundary": [{"x":105,"y":0},{"x":200,"y":0},{"x":200,"y":60},{"x":105,"y":60}]},
        {"name": "Suite 103",      "area": "3,000 SF", "boundary": [{"x":105,"y":60},{"x":200,"y":60},{"x":200,"y":120},{"x":105,"y":120}]},
        {"name": "Corridor",       "area": "1,200 SF", "boundary": [{"x":95,"y":0},{"x":105,"y":0},{"x":105,"y":120},{"x":95,"y":120}]},
    ],

    # ── Sprinkler placements (pendant, light hazard, 12ft grid) ───────────────
    "sprinkler_placements": [
        # Suite 100 — 5 x 5 grid
        *[{"id": f"S{r+1:03d}", "x": 10 + c*17, "y": 10 + r*14,
           "type": "pendant", "zone": "A", "coverage_radius": 7.5,
           "k_factor": 5.6, "temp_rating": 155, "hazard": "Light",
           "elevation": 14}
          for r in range(4) for c in range(5)],
        # Suite 101
        *[{"id": f"S{20+r+1:03d}", "x": 10 + c*17, "y": 65 + r*14,
           "type": "pendant", "zone": "B", "coverage_radius": 7.5,
           "k_factor": 5.6, "temp_rating": 155, "hazard": "Light",
           "elevation": 14}
          for r in range(4) for c in range(5)],
        # Corridor
        *[{"id": f"S{40+i+1:03d}", "x": 100, "y": 15 + i*20,
           "type": "sidewall", "zone": "C", "coverage_radius": 6.0,
           "k_factor": 4.2, "temp_rating": 155, "hazard": "Light",
           "elevation": 14}
          for i in range(6)],
    ],

    # ── Pipe sections ─────────────────────────────────────────────────────────
    "pipe_sections": [
        # Main feed
        {"id":"M-01","from":{"x":100,"y":0},"to":{"x":100,"y":120},
         "diameter":4.0,"schedule":"Sch 40","material":"Steel","pipe_type":"main","length":120},
        # Cross mains
        {"id":"X-01","from":{"x":100,"y":30},"to":{"x":10,"y":30},
         "diameter":3.0,"schedule":"Sch 40","material":"Steel","pipe_type":"cross","length":90},
        {"id":"X-02","from":{"x":100,"y":30},"to":{"x":190,"y":30},
         "diameter":3.0,"schedule":"Sch 40","material":"Steel","pipe_type":"cross","length":90},
        {"id":"X-03","from":{"x":100,"y":90},"to":{"x":10,"y":90},
         "diameter":3.0,"schedule":"Sch 40","material":"Steel","pipe_type":"cross","length":90},
        {"id":"X-04","from":{"x":100,"y":90},"to":{"x":190,"y":90},
         "diameter":3.0,"schedule":"Sch 40","material":"Steel","pipe_type":"cross","length":90},
        # Branch lines (every 12ft)
        *[{"id":f"B-{i+1:02d}","from":{"x":10+i*17,"y":30},"to":{"x":10+i*17,"y":10},
           "diameter":1.5,"schedule":"Sch 40","material":"Steel",
           "pipe_type":"branch","length":20}
          for i in range(5)],
        *[{"id":f"B-{i+6:02d}","from":{"x":10+i*17,"y":30},"to":{"x":10+i*17,"y":56},
           "diameter":1.5,"schedule":"Sch 40","material":"Steel",
           "pipe_type":"branch","length":26}
          for i in range(5)],
    ],

    # ── Valves ────────────────────────────────────────────────────────────────
    "valves": [
        {"id":"OS&Y-1","type":"osy",       "x":100,"y":2,  "zone":"Main"},
        {"id":"CV-1",  "type":"check",     "x":100,"y":8,  "zone":"Main"},
        {"id":"AV-1",  "type":"alarm",     "x":100,"y":14, "zone":"Main"},
        {"id":"BFV-1", "type":"butterfly", "x":50, "y":30, "zone":"A"},
        {"id":"BFV-2", "type":"butterfly", "x":150,"y":30, "zone":"B"},
        {"id":"IT-1",  "type":"inspector_test","x":190,"y":115,"zone":"Remote"},
        {"id":"DR-1",  "type":"drain",     "x":100,"y":118,"zone":"Main"},
    ],

    # ── Equipment ─────────────────────────────────────────────────────────────
    "equipment": [
        {"type":"riser", "x":100, "y":6,  "label":"RISER #1\n4\" WET"},
        {"type":"fdc",   "x":200, "y":10, "label":"FDC\n4\"×2.5\"×2.5\""},
    ],
}

# ── Test runner ───────────────────────────────────────────────────────────────

async def run_test(selected_sheets=None, selected_formats=None, quick=False):
    print("\n" + "═"*60)
    print("  FireAI Pro — Local Test Run")
    print(f"  Project: {TEST_PROJECT['project_name']}")
    print(f"  Quick mode: {quick}")
    print("═"*60 + "\n")

    if quick:
        os.environ["FIREAI_MAX_ITERATIONS"] = "2"

    # ── Step 1: Orchestrator ──────────────────────────────────────────────────
    orchestrator = FireAIOrchestrator()
    orch_result  = await orchestrator.run(
        project_context  = TEST_PROJECT,
        selected_formats = set(selected_formats or [
            "dwg_pdf","ifc","hydraulics_json","nfpa_cert","ahj_package","bom_xlsx"
        ]),
    )

    print("\n── Orchestrator result ──────────────────────────────────────")
    print(json.dumps(orch_result["metadata"], indent=2))

    # ── Step 2: Drawing engine ────────────────────────────────────────────────
    output_dir = f"./test_outputs/{datetime.now().strftime('%Y%m%d_%H%M%S')}"

    drawing_engine = FireAIDrawingEngine(
        project           = TEST_PROJECT,
        cad_output        = orch_result.get("artifacts", {}).get("cad_layout")        or TEST_PROJECT,
        hydraulics_output = orch_result.get("artifacts", {}).get("hydraulics_report") or {},
        bracing_output    = orch_result.get("artifacts", {}).get("bracing_and_bom")   or {},
        compliance_result = type("CR", (), {
            "compliant":  orch_result["metadata"]["compliant"],
            "violations": [],
            "summary":    orch_result["metadata"].get("nfpa_summary",""),
        })(),
    )

    sheets = set(selected_sheets or [
        "sheet_fp00","sheet_fp10","sheet_fp20","sheet_fp30",
        "sheet_fp40","sheet_fp50","sheet_fp60",
    ])

    # Call generate_all for the test (generates everything)
    manifest = drawing_engine.generate_all(output_dir)

    print("\n── Drawing manifest ─────────────────────────────────────────")
    for m in manifest:
        status = f"{m['size_kb']:.1f} KB" if m.get("size_kb") else f"ERROR: {m.get('error')}"
        print(f"  {'✓' if m.get('path') else '✗'}  {m['sheet']:<35} {status}")

    print(f"\n── Output directory: {output_dir}")
    print("── Open the .dxf files in AutoCAD, Autosprink, or DraftSight to verify.")

    compliant = orch_result["metadata"]["compliant"]
    print(f"\n{'✓ COMPLIANT' if compliant else '⚠ PARTIAL — see frozen_violations'}")
    print(f"Iterations used: {orch_result['metadata']['iterations_used']}\n")

    return orch_result, manifest


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="FireAI Pro local test runner")
    parser.add_argument("--sheets",  nargs="+", help="Sheet keys to generate")
    parser.add_argument("--formats", nargs="+", help="Format keys to generate")
    parser.add_argument("--quick",   action="store_true", help="Limit to 2 compliance iterations")
    args = parser.parse_args()

    if not os.getenv("ANTHROPIC_API_KEY"):
        print("ERROR: ANTHROPIC_API_KEY is not set.")
        print("Set it with:  export ANTHROPIC_API_KEY=sk-ant-...")
        sys.exit(1)

    asyncio.run(run_test(args.sheets, args.formats, args.quick))
