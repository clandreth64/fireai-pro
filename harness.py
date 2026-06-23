"""
Score one design result against a reference project's expectations, and
capture its metrics so the baseline/regression layer can diff runs.

Two kinds of checks live here:

  * Invariants  — absolute correctness the design must always satisfy
                  (sprinklers placed, compliant flag matches, required
                  NFPA sections pass, negative-test sections flag, metric
                  bounds). These catch real breakage.

  * Metrics     — a numeric snapshot of the design used by baseline.py to
                  detect drift between two versions of the system. This is
                  the signal the self-improvement loop optimizes against.
"""
from __future__ import annotations


def extract_metrics(design: dict) -> dict:
    """Pull the numeric fingerprint of a design for baseline diffing."""
    md = design.get("design_metadata", {})
    flags = md.get("compliance_flags", [])
    crit = [f for f in flags if f.get("severity") != "pass"]
    return {
        "compliant":           bool(design.get("compliant")),
        "total_sprinklers":    md.get("total_sprinklers", 0),
        "flow_demand":         round(float(design.get("flow_demand", 0)), 1),
        "required_pressure":   round(float(design.get("required_pressure", 0)), 1),
        "pressure_delta":      round(float(design.get("pressure_delta", 0)), 1),
        "total_material_cost": round(float(design.get("total_material_cost", 0)), 0),
        "bom_items":           len(design.get("bom", [])),
        "flags_total":         len(flags),
        "critical_flags":      len(crit),
    }


def score(project: dict, design: dict) -> dict:
    """Return {id, passed, failures, metrics} for one project."""
    expect = project.get("expect", {})
    md = design.get("design_metadata", {})
    flags = md.get("compliance_flags", [])
    by_section_pass = {f["section"]: (f.get("severity") == "pass") for f in flags}
    crit = [f for f in flags if f.get("severity") != "pass"]
    metrics = extract_metrics(design)
    failures: list[str] = []

    # 1. Sanity — a real design places sprinklers.
    if metrics["total_sprinklers"] <= 0:
        failures.append("no sprinklers placed")

    # 2. The compliant flag matches expectation.
    if "compliant" in expect and metrics["compliant"] != expect["compliant"]:
        failures.append(
            f"compliant={metrics['compliant']} expected {expect['compliant']}")

    # 3. Critical-flag ceiling (default: none allowed).
    max_crit = expect.get("max_critical_flags", 0)
    if len(crit) > max_crit:
        failures.append(
            f"{len(crit)} critical flags > allowed {max_crit}: "
            + "; ".join(f["section"] for f in crit))

    # 4. Required NFPA sections must be present AND pass.
    for sec in expect.get("must_pass_sections", []):
        if sec not in by_section_pass:
            failures.append(f"missing required section {sec}")
        elif not by_section_pass[sec]:
            failures.append(f"section {sec} did not pass")

    # 5. Negative tests — these sections MUST be flagged critical.
    for sec in expect.get("must_flag_sections", []):
        flagged = any(
            f["section"] == sec and f.get("severity") != "pass" for f in flags)
        if not flagged:
            failures.append(f"expected a critical flag on {sec}, found none")

    # 6. Metric bounds.
    for name, bound in expect.get("metrics", {}).items():
        val = metrics.get(name)
        if val is None:
            continue
        if "min" in bound and val < bound["min"]:
            failures.append(f"{name}={val} < min {bound['min']}")
        if "max" in bound and val > bound["max"]:
            failures.append(f"{name}={val} > max {bound['max']}")

    return {
        "id": project.get("id", "?"),
        "description": project.get("description", ""),
        "passed": not failures,
        "failures": failures,
        "metrics": metrics,
    }
