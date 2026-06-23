"""
Stage 3 — Extraction self-check sub-agent.

The problem this solves: `extract_project_context` runs once and trusts whatever
comes back. On a clean vector set that's fine; on a 156 MB scanned Costco set it
can return sparse or wrong numbers, and right now those flow straight into the
design with full confidence.

This sub-agent sits in front of the design loop and does three things:

  1. GRADE   every design-critical field for presence, plausibility, and
             internal consistency -> a confidence level.
  2. REPAIR  re-run extraction (vision-focused) on the weak fields once.
  3. ESCALATE anything still weak — flag it for human entry instead of feeding
             a confident wrong number into the design loop.

The grading logic (`grade_extraction`) is pure Python and needs no API, so it's
fully testable. The actual extractor call lives behind one seam
(`_run_extractor`) and is the only part that needs the Vision API.
"""
from __future__ import annotations
import os
import sys
from dataclasses import dataclass, field

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

# Hazard map lives in the design engine; we reuse it to check occupancy sanity.
try:
    from fireai_nfpa13_design_engine import ZONE_MAP
except Exception:  # keep the sub-agent importable even if the engine moves
    ZONE_MAP = {}

VALID_SEISMIC = {"A", "B", "C", "D", "D1", "D2", "E"}

# Confidence levels, worst to best.
MISSING, LOW, MEDIUM, HIGH = "missing", "low", "medium", "high"


@dataclass
class FieldResult:
    name: str
    value: object
    confidence: str         # missing | low | medium | high
    status: str             # ok | repaired | needs_human
    reason: str
    human_label: str


@dataclass
class ExtractionReport:
    fields: dict            # name -> FieldResult
    ctx: dict               # assembled context for the design loop
    needs_human: list       # list[FieldResult] a person must confirm
    confident: bool         # True when no design-critical field needs a human

    def summary(self) -> str:
        if self.confident:
            weak = [f.name for f in self.fields.values() if f.confidence == MEDIUM]
            extra = f" ({len(weak)} medium-confidence)" if weak else ""
            return f"confident — design loop may proceed{extra}"
        names = ", ".join(f.name for f in self.needs_human)
        return f"needs human confirmation on: {names}"


# ── Field specs ───────────────────────────────────────────────────────────────
# Each design-critical field: which extractor keys to read (in priority order),
# whether it normally comes from outside the drawings (the flow test), a safe
# default when one exists, and a human-facing label.

def _num(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _grade_occupancy(v, ctx):
    if not v or not str(v).strip():
        return MISSING, "no occupancy found"
    low = str(v).lower()
    if any(k in low for k in ZONE_MAP):
        return HIGH, "maps to a known hazard class"
    return MEDIUM, "found, but doesn't match a known hazard keyword (would default to light hazard)"


def _grade_area(v, ctx):
    a = _num(v)
    if not a or a <= 0:
        return MISSING, "no usable building area"
    if 500 <= a <= 3_000_000:
        return HIGH, f"{a:,.0f} SF is in normal range"
    if 100 <= a < 500 or 3_000_000 < a <= 10_000_000:
        return MEDIUM, f"{a:,.0f} SF is unusual — confirm units"
    return LOW, f"{a:,.0f} SF is implausible"


def _grade_ceiling(v, ctx):
    h = _num(v)
    if not h or h <= 0:
        return MISSING, "no ceiling height found"
    if 8 <= h <= 55:
        return HIGH, f"{h:g} ft is normal"
    if 7 <= h < 8 or 55 < h <= 70:
        return MEDIUM, f"{h:g} ft is unusual — confirm"
    return LOW, f"{h:g} ft is implausible"


def _grade_static(v, ctx):
    p = _num(v)
    if not p or p <= 0:
        return MISSING, "no static pressure (usually from a flow test, not drawings)"
    if 30 <= p <= 200:
        return HIGH, f"{p:g} psi is normal"
    if 20 <= p < 30 or 200 < p <= 300:
        return MEDIUM, f"{p:g} psi is unusual — confirm"
    return LOW, f"{p:g} psi is implausible"


def _grade_residual(v, ctx):
    p = _num(v)
    if not p or p <= 0:
        return MISSING, "no residual pressure (usually from a flow test)"
    stat = _num(ctx.get("static_pressure"))
    if stat and p > stat:
        return LOW, f"residual {p:g} > static {stat:g} psi — impossible, recheck"
    if p >= 10:
        return HIGH, f"{p:g} psi looks valid"
    return LOW, f"{p:g} psi is implausibly low"


def _grade_flow(v, ctx):
    q = _num(v)
    if not q or q <= 0:
        return MISSING, "no available flow (usually from a flow test)"
    if 100 <= q <= 8000:
        return HIGH, f"{q:,.0f} gpm is normal"
    if 50 <= q < 100 or 8000 < q <= 12000:
        return MEDIUM, f"{q:,.0f} gpm is unusual — confirm"
    return LOW, f"{q:,.0f} gpm is implausible"


def _grade_seismic(v, ctx):
    if not v or not str(v).strip():
        return MISSING, "no seismic design category found"
    if str(v).upper() in VALID_SEISMIC:
        return HIGH, f"category {v} is valid"
    return LOW, f"'{v}' is not a valid seismic design category"


# name: (aliases, grade_fn, design_critical, external_source, default, human_label)
FIELD_SPECS = {
    "occupancy":         (["occupancy"], _grade_occupancy, True,  False, None,  "Occupancy / use"),
    "total_area":        (["total_area", "building_area_sf", "area_sf"], _grade_area, True, False, None, "Total floor area (SF)"),
    "ceiling_height":    (["ceiling_height"], _grade_ceiling, True, False, None, "Ceiling height (ft)"),
    "static_pressure":   (["static_pressure"], _grade_static, True, True, None, "Static pressure (psi)"),
    "residual_pressure": (["residual_pressure"], _grade_residual, True, True, None, "Residual pressure (psi)"),
    "water_supply_flow": (["water_supply_flow"], _grade_flow, True, True, None, "Available flow (gpm)"),
    "seismic_zone":      (["seismic_zone"], _grade_seismic, False, False, "D1", "Seismic design category"),
}

# Minimum confidence per field to be usable without a human. MEDIUM is allowed
# for non-external fields (we proceed but note it); external fields (the flow
# test trio) require HIGH because a wrong supply number silently breaks §22.
def _acceptable(spec_external: bool, conf: str) -> bool:
    if spec_external:
        return conf == HIGH
    return conf in (HIGH, MEDIUM)


def _resolve(extracted: dict, aliases: list):
    for key in aliases:
        if key in extracted and extracted[key] not in (None, "", 0, 0.0, [], {}):
            return extracted[key]
    return None


def grade_extraction(extracted: dict, prior_ctx: dict | None = None) -> ExtractionReport:
    """Pure scorer: grade an extraction dict (no API). prior_ctx holds any
    user-entered values that should be trusted over extracted ones."""
    prior_ctx = prior_ctx or {}
    ctx = dict(prior_ctx)
    fields: dict = {}
    needs_human: list = []

    for name, (aliases, grade_fn, critical, external, default, label) in FIELD_SPECS.items():
        # A user-entered value is authoritative — never second-guess it.
        if name in prior_ctx and prior_ctx[name] not in (None, "", 0, 0.0):
            value = prior_ctx[name]
            fr = FieldResult(name, value, HIGH, "ok", "user-entered", label)
            fields[name] = fr
            continue

        value = _resolve(extracted, aliases)
        conf, reason = grade_fn(value, ctx)

        if _acceptable(external, conf):
            status = "ok"
            ctx[name] = value
        elif default is not None and conf == MISSING:
            # A safe conservative default exists (e.g. seismic D1).
            status = "ok"
            value = default
            ctx[name] = default
            reason = f"{reason}; defaulted to {default}"
            conf = MEDIUM
        else:
            status = "needs_human"
            if critical:
                fr = FieldResult(name, value, conf, status, reason, label)
                needs_human.append(fr)

        fields[name] = FieldResult(name, value, conf, status, reason, label)

    confident = not needs_human
    return ExtractionReport(fields=fields, ctx=ctx,
                            needs_human=needs_human, confident=confident)


# ── The seam to the real extractor (needs the Vision API) ─────────────────────

async def _run_extractor(pdf_path: str, run_vision: bool = True) -> dict:
    from fireai_project_extractor import extract_project_context
    return await extract_project_context(pdf_path, run_vision=run_vision)


async def self_check_extraction(pdf_path: str, prior_ctx: dict | None = None,
                                run_vision: bool = True,
                                reextract_weak: bool = True) -> ExtractionReport:
    """Full sub-agent: extract -> grade -> (re-extract weak fields once) -> report.

    The re-extract step is where 'run vision again on just the weak sheets' would
    go; here it re-runs the extractor once more and keeps whichever fields graded
    higher. Anything still weak after that is left for a human.
    """
    extracted = await _run_extractor(pdf_path, run_vision=run_vision)
    report = grade_extraction(extracted, prior_ctx)

    if report.confident or not reextract_weak:
        return report

    # One focused re-extraction pass, then re-grade with the best of both.
    extracted2 = await _run_extractor(pdf_path, run_vision=True)
    merged = dict(extracted)
    for k, v in extracted2.items():
        if v not in (None, "", 0, 0.0, [], {}):
            merged.setdefault(k, v)
            # prefer a non-empty second-pass value where the first was empty
            if merged.get(k) in (None, "", 0, 0.0, [], {}):
                merged[k] = v
    report2 = grade_extraction(merged, prior_ctx)

    # Mark fields that the second pass rescued as "repaired".
    for name, fr in report2.fields.items():
        if (name in report.fields
                and report.fields[name].status == "needs_human"
                and fr.status == "ok"):
            fr.status = "repaired"
    return report2
