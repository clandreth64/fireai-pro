"""Owner-input intake for the first NFPA 13-2019 rule package (Milestone 2.2B).

FireAI does not supply rule VALUES. The owner fills ``docs/m2_2b/NFPA13_2019_RULE_PACKAGE.input.json``
(from the template beside it); ``load_rule_package`` validates it and produces DRAFT ``Rule`` records
that then go through the normal two-person workflow (``RuleStore``: author → independent reviewer
(+ verified known-answer cases) → independent rule-set approver).

``M22B_MAPPINGS`` is the owner-approved MEASUREMENT MAPPING of each first-package rule (M2.2B
instruction): locator → constraint key / measurement / bound / wall reference kinds. It holds no value.
The template cannot change it; a rule whose mapping should differ is a change to this registry
(reviewed engine change), not an input edit. Maximum protection area is ALWAYS the S x L measurement,
never the Voronoi cell.

Nothing here copies standard text: titles are owner-written labels (length-limited), locators are
section / table identifiers, and the document-control reference is screened for personal / license
identifiers.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional

from fireai.rules.authorization import _screen
from fireai.rules.catalog import NFPA13_2019_CONSTRUCTION_SCHEME
from fireai.rules.model import (Condition, ConstraintTemplate, DerivedLimit, Quantity, Rule, RuleApplicability,
                                RuleParameter, RuleSource)
from fireai.rules.units import UnitError, dimension

TEMPLATE_ID = "fireai.nfpa13_2019.first_rule_package/1"
MAX_TITLE = 120
MAX_NOTE = 600

_LH = [Condition(fact="hazard.scheme", op="eq", value="NFPA 13 occupancy hazard classification"),
       Condition(fact="hazard.classification", op="eq", value="Light Hazard")]
_SSP = [Condition(fact="sprinkler.type", op="eq", value="standard_spray"),
        Condition(fact="sprinkler.orientation", op="in", value=["pendent", "upright"])]
_ROW = [Condition(fact="system.design_method", op="eq", value="hydraulically_calculated"),
        Condition(fact="ceiling.construction_classification.scheme", op="eq", value=NFPA13_2019_CONSTRUCTION_SCHEME),
        Condition(fact="ceiling.construction_classification", op="eq", value="noncombustible_unobstructed")]
_FLAT = [Condition(fact="ceiling.surface", op="eq", value="flat"),
         Condition(fact="ceiling.construction_classification", op="eq", value="noncombustible_unobstructed")]


@dataclass(frozen=True)
class RuleMapping:
    mapping_id: str
    locator: str
    key: str
    measurement: str
    bound: str
    category: str
    reference_kinds: tuple[str, ...]
    dimension: str                            # "length" | "area" | "factor" (derived)
    proposed_applicability: tuple[Condition, ...]
    derived_from_key: Optional[str] = None
    note: str = ""


M22B_MAPPINGS: dict[str, RuleMapping] = {m.mapping_id: m for m in (
    RuleMapping("A", "Table 10.2.4.2.1(a)", "sprinkler.max_protection_area", "array_sxl_protection_area", "max",
                "protection_area", ("wall",), "area", tuple(_LH + _SSP + _ROW),
                note="S x L with S along the explicit branch-line direction (SXL-ARRAY/2); never the Voronoi cell"),
    RuleMapping("B", "Table 10.2.4.2.1(a)", "sprinkler.max_spacing", "array_axis_spacing", "max", "spacing", (),
                "length", tuple(_LH + _SSP + _ROW), note="maximum spacing, evaluated separately from protection area"),
    RuleMapping("C", "10.2.5.2.1", "sprinkler.max_wall_distance", "perpendicular_wall_distance", "max",
                "distance_to_boundary", ("wall",), "factor", tuple(_SSP), derived_from_key="sprinkler.max_spacing",
                note="DERIVED: factor x the effective maximum spacing (rule B); solid walls only"),
    RuleMapping("D", "10.2.5.3", "sprinkler.min_wall_distance", "min_perpendicular_wall_distance", "min",
                "distance_to_boundary", ("wall",), "length", tuple(_SSP),
                note="M2.2B.1: minimum perpendicular distance from each sprinkler to ANY solid wall segment "
                     "(MIN-WALL-CLEARANCE/1), independent of neighbouring sprinklers; a nearer wall end refuses"),
    RuleMapping("E", "10.2.5.4.1", "sprinkler.min_spacing", "pairwise_min_distance", "min", "spacing", (), "length",
                tuple(_SSP), note="general rule only; baffle / in-rack exception paths are outside the envelope"),
    RuleMapping("F_MIN", "10.2.6.1.1.1", "sprinkler.min_deflector_below_ceiling",
                "ceiling_to_deflector_vertical_distance", "min", "ceiling_configuration", (), "length",
                tuple(_SSP + _FLAT), note="recessed / flush / concealed and ceiling-elevation-change paths are outside "
                                          "the envelope"),
    RuleMapping("F_MAX", "10.2.6.1.1.1", "sprinkler.max_deflector_below_ceiling",
                "ceiling_to_deflector_vertical_distance", "max", "ceiling_configuration", (), "length",
                tuple(_SSP + _FLAT), note="as F_MIN"),
)}


class IntakeError(ValueError):
    def __init__(self, problems: list[str]):
        self.problems = problems
        super().__init__("rule package input is incomplete:\n  - " + "\n  - ".join(problems))


@dataclass
class RulePackage:
    rules: list[Rule]
    source: RuleSource
    author: str
    reviewer: str
    approver: str
    omitted: dict[str, str]                   # mapping id -> owner's reason (e.g. F_MIN not applicable)


def _missing(v) -> bool:
    return v is None or (isinstance(v, str) and not v.strip())


def load_rule_package(data: dict[str, Any], *, edition: str = "2019") -> RulePackage:
    """Validate an owner-filled package and build DRAFT rules. Every problem is reported at once."""
    p: list[str] = []
    if data.get("template") != TEMPLATE_ID:
        p.append(f"template must be {TEMPLATE_ID!r}")
    src = data.get("source") or {}
    for f in ("access_method", "accessed_at", "internal_document_control_reference"):
        if _missing(src.get(f)):
            p.append(f"source.{f} is required")
    if src.get("edition") != edition:
        p.append(f"source.edition must be {edition!r} (editions are never mixed)")
    try:
        _screen("source.internal_document_control_reference", src.get("internal_document_control_reference"))
        _screen("source.access_method", src.get("access_method"))
    except ValueError as exc:
        p.append(str(exc))
    people = data.get("people") or {}
    for f in ("author", "reviewer", "approver"):
        if _missing(people.get(f)):
            p.append(f"people.{f} is required")
    names = [people.get(f) for f in ("author", "reviewer", "approver") if not _missing(people.get(f))]
    if len(set(names)) != len(names):
        p.append("author, reviewer and approver must be three different people")

    entries = {e.get("mapping_id"): e for e in data.get("rules") or []}
    unknown = sorted(set(entries) - set(M22B_MAPPINGS))
    if unknown:
        p.append(f"unknown mapping ids {unknown} (the first package is exactly {sorted(M22B_MAPPINGS)})")
    rules, omitted = [], {}
    source = None
    if not p or all(not x.startswith("source.") for x in p):
        source = RuleSource(kind="authoritative_standard", document="NFPA 13", edition=edition,
                            access_method=src.get("access_method"), accessed_at=src.get("accessed_at"),
                            source_id=src.get("internal_document_control_reference"))
    for mid, m in M22B_MAPPINGS.items():
        e = entries.get(mid)
        if e is None:
            p.append(f"{mid}: entry missing")
            continue
        if mid.startswith("F_") and e.get("applies") is False:
            if _missing(e.get("not_applicable_reason")):
                p.append(f"{mid}: give the reason it does not apply")
            else:
                omitted[mid] = e["not_applicable_reason"]
            continue
        if mid.startswith("F_") and e.get("applies") is not True:
            p.append(f"{mid}: state applies = true / false")
            continue
        if e.get("locator") != m.locator:
            p.append(f"{mid}: locator must be {m.locator!r} (the owner-approved mapping)")
        for f in ("rule_id", "title"):
            if _missing(e.get(f)):
                p.append(f"{mid}: {f} is required")
        if len(e.get("title") or "") > MAX_TITLE or len(e.get("notes") or "") > MAX_NOTE:
            p.append(f"{mid}: title <= {MAX_TITLE} and notes <= {MAX_NOTE} characters (own words, never standard text)")
        if e.get("applicability_confirmed") is not True:
            p.append(f"{mid}: applicability_confirmed must be true (confirm or replace the proposed conditions)")
        if e.get("exceptions_outside_envelope_confirmed") is not True:
            p.append(f"{mid}: exceptions_outside_envelope_confirmed must be true")
        params, derived, limit_param = [], None, ""
        if m.dimension == "factor":
            fac = (e.get("derived") or {}).get("factor")
            if isinstance(fac, bool) or not isinstance(fac, (int, float)):
                p.append(f"{mid}: derived.factor (a dimensionless number) is required")
            else:
                params.append(RuleParameter(name="factor", value=float(fac), meaning="owner-supplied factor"))
                derived = DerivedLimit(from_key=m.derived_from_key, factor_parameter="factor")
        else:
            lim = e.get("limit") or {}
            val, unit = lim.get("value"), lim.get("unit")
            if isinstance(val, bool) or not isinstance(val, (int, float)) or _missing(unit):
                p.append(f"{mid}: limit.value (number) and limit.unit are required")
            else:
                try:
                    if dimension(unit) != m.dimension:
                        p.append(f"{mid}: unit {unit!r} is not a {m.dimension}")
                    else:
                        params.append(RuleParameter(name="limit", value=Quantity(value=float(val), unit=unit),
                                                    meaning="owner-supplied value"))
                        limit_param = "limit"
                except UnitError as exc:
                    p.append(f"{mid}: {exc}")
        conds = e.get("applicability_override")
        try:
            applic = ([Condition(**c) for c in conds] if conds else list(m.proposed_applicability))
        except Exception as exc:                                    # noqa: BLE001 — reported, never swallowed
            p.append(f"{mid}: applicability_override invalid: {exc}")
            continue
        if p or source is None:
            continue
        rules.append(Rule(
            rule_id=e["rule_id"], category=m.category, title=e["title"], parameters=params,
            applicability=RuleApplicability(all_of=applic),
            constraint=ConstraintTemplate(key=m.key, measurement=m.measurement, bound=m.bound,
                                          limit_parameter=limit_param, derived=derived,
                                          reference_kinds=list(m.reference_kinds)),
            source=source.model_copy(update={"reference": m.locator}), author=people["author"],
            authored_at=src.get("accessed_at"), version=str(e.get("rule_version") or "1"),
            notes=("; ".join(filter(None, [m.note, e.get("notes") or "",
                                            "exceptions outside envelope: " + ", ".join(e.get("exceptions_outside_envelope")
                                                                                         or [])])))))
    if p:
        raise IntakeError(p)
    return RulePackage(rules=rules, source=source, author=people["author"], reviewer=people["reviewer"],
                       approver=people["approver"], omitted=omitted)


def template() -> dict:
    """The blank owner input file (no values)."""
    return {
        "template": TEMPLATE_ID,
        "status": "AWAITING_OWNER_INPUT",
        "instructions": "Fill every null from your lawfully accessed NFPA 13-2019 copy. Do not paste standard text: "
                        "titles and notes are your own words. Do not enter license / customer / order numbers or "
                        "personal data. Source status stays INTERNAL_R_AND_D_ONLY (not releasable).",
        "rule_set": {"rule_set_id": "NFPA13-2019-BASE", "edition": "2019"},
        "source": {"document": "NFPA 13", "edition": "2019", "access_method": None, "accessed_at": None,
                   "internal_document_control_reference": None},
        "people": {"author": None, "reviewer": None, "approver": None},
        "rules": [
            {"mapping_id": m.mapping_id, "rule_id": None, "rule_version": "1", "locator": m.locator, "title": None,
             **({"applies": None, "not_applicable_reason": None} if m.mapping_id.startswith("F_") else {}),
             **({"derived": {"factor": None, "from_key": m.derived_from_key}} if m.dimension == "factor"
                else {"limit": {"value": None, "unit": None, "dimension": m.dimension}}),
             "mapping_read_only": {"constraint_key": m.key, "measurement": m.measurement, "bound": m.bound,
                                   "reference_kinds": list(m.reference_kinds), "note": m.note},
             "proposed_applicability": [c.model_dump(mode="json") for c in m.proposed_applicability],
             "applicability_confirmed": False, "applicability_override": None,
             "exceptions_outside_envelope": [], "exceptions_outside_envelope_confirmed": False, "notes": ""}
            for m in M22B_MAPPINGS.values()],
    }
