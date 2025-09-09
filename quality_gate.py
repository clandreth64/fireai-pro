# quality_gate.py
from pathlib import Path

# Minimal structural checks; tighten these as your outputs mature.
MIN_SIZES = {
    "design.dxf": 20_000,         # bytes
    "model.ifc":  50_000,
    "compliance.pdf": 25_000,
    "hydraulics.pdf": 25_000,
    "bom.pdf": 10_000,
    "bracing.pdf": 20_000,
    "multistandard.pdf": 20_000,
}

def check_ifc_text(path: Path) -> bool:
    try:
        txt = path.read_text(errors="ignore")
        up = txt.upper()
        # Example: ensure some IFC elements typical for sprinkler designs exist
        return ("IFCFIRESUPPRESSIONTERMINAL" in up) and ("IFCFLOWSEGMENT" in up)
    except Exception:
        return False

def run(output_dir: str) -> dict:
    out = Path(output_dir)
    failures = []

    for name, min_bytes in MIN_SIZES.items():
        p = out / name
        if not p.exists():
            failures.append(f"missing: {name}")
            continue
        if p.stat().st_size < min_bytes:
            failures.append(f"too_small: {name} ({p.stat().st_size}B < {min_bytes}B)")

    if (out / "model.ifc").exists() and not check_ifc_text(out / "model.ifc"):
        failures.append("ifc_structure: required classes not found")

    return {"pass": not failures, "failures": failures}
