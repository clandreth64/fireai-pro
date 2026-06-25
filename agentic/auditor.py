"""Stage 2 — Auditor.

Inspect a finished deterministic design and classify what's wrong. The engine
already computes a `compliant` flag and a list of NFPA `compliance_flags`; the
auditor turns those into a structured issue list the repair loop can act on.

Only ONE issue is safely auto-repairable today: a water-supply pressure deficit
(NFPA 13 §22), which is fixed by sizing a fire pump. Everything else is reported
but left for human / EOR review — the loop never silently "fixes" geometry.
"""

from __future__ import annotations


def audit_design(design: dict) -> list[dict]:
    """Return a list of issues: {type, severity, section, message, auto_fixable}."""
    issues: list[dict] = []
    if not design:
        return issues

    delta = float(design.get("pressure_delta", 0) or 0)
    req   = float(design.get("required_pressure", 0) or 0)

    # ── Water-supply pressure deficit (§22) — the one safe auto-repair ────────
    if delta < 0:
        issues.append({
            "type":        "supply_deficit",
            "severity":    "critical",
            "section":     "§22",
            "message":     (f"Water supply short by {abs(delta):.1f} psi "
                            f"(required {req:.1f} psi at demand flow). "
                            f"A fire pump is required."),
            "deficit_psi": round(abs(delta), 1),
            "auto_fixable": True,
        })

    # ── Any other engine-flagged critical (not auto-fixable) ─────────────────
    flags = (design.get("design_metadata", {}) or {}).get("compliance_flags", []) or []
    for f in flags:
        sec = f.get("section", "")
        if f.get("severity") not in ("pass", None) and sec != "§22":
            issues.append({
                "type":        "engine_flag",
                "severity":    f.get("severity", "major"),
                "section":     sec,
                "message":     f.get("description", ""),
                "auto_fixable": False,
            })

    return issues
