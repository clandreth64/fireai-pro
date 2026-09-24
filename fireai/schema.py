"""Schema versioning and migration for persisted FireAI models.

Rules:
* Every persisted model carries ``schema_version``.
* Loading a model ALWAYS goes through ``load_model``: it migrates known older
  versions step by step and refuses unknown/newer versions. Persisted data never
  silently changes meaning.
* A migration may only add information that is derivable from the old record,
  or mark new information as unknown. It never invents values.
* Each migration step records itself in ``migrations_applied`` on the result.

Version line (see docs/SPATIAL_BIM_ARCHITECTURE.md §12 for the long-term plan):
    0.1.0  2D drawing understanding (Milestone 1)
    0.2.0  identity, frames, placement (unknown), provenance (Milestone 1.5)
    0.3.0  XREF records, view classification, wall analysis layer, human
           corrections + verification binding (Milestone 1.6)
    0.4.0  semantic spaces (category "space") and non-plan depictions (category
           "depiction") (Milestone 1.8)
    0.5.0  classified region boundaries, plan `opening` elements, content fingerprint in the
           verification binding (Milestone 1.9)
"""

from __future__ import annotations

import copy
import json
from typing import Any, Callable

from fireai.ingest.extract import source_uid_for, uid_for
from fireai.model import SCHEMA_VERSION, BuildingModel
from fireai.spatial import build_frames


class UnsupportedSchemaVersion(ValueError):
    pass


def _migrate_0_1_0_to_0_2_0(d: dict[str, Any]) -> dict[str, Any]:
    d = copy.deepcopy(d)
    src = d["source"]
    source_uid = source_uid_for(src["sha256"])
    src.setdefault("source_uid", source_uid)
    # 0.1.0 had no handle paths for block children; uids are rebuilt from what exists
    # (top-level handle, else the 0.1.0 display id) and marked as migrated.
    by_id = {e["id"]: e for e in d.get("entities", [])}
    for e in d.get("entities", []):
        if e.get("handle"):
            path = [e["handle"]]
            basis = "handle"
        else:
            path = [f"migrated-{e['id']}"]
            basis = "sequence"
        e.setdefault("handle_path", path)
        e.setdefault("uid_basis", basis)
        e.setdefault("uid", uid_for(source_uid, "/".join(path)))
        e.setdefault("z_range", None)             # not recorded in 0.1.0 -> unknown
        e.setdefault("source_object_key", None)
        e.setdefault("provenance", {"origin": "source"})
        for g in ("source", "normalized"):
            if e.get(g):
                e[g].setdefault("frame", "SRC" if g == "source" else "LOCAL")
    for el in d.get("elements", []):
        uids = sorted(by_id[i]["uid"] for i in el.get("source_entity_ids", []) if i in by_id)
        el.setdefault("uid", uid_for(source_uid, f"element|{el['category']}|{el.get('subtype')}|{'|'.join(uids)}|"
                                                 f"{','.join(sorted(el.get('rules', [])))}"))
        el.setdefault("placement", {})           # all unknown
        el.setdefault("provenance", {"origin": "deterministic_inference", "engine": "fireai.interpret",
                                     "engine_version": d.get("fireai_version"),
                                     "rule_ids": sorted(el.get("rules", [])), "derived_from": uids})
        if el.get("geometry"):
            el["geometry"].setdefault("frame", "LOCAL")
    t = d.get("transform") or {}
    units = d.get("units") or {}
    frames = build_frames(units.get("resolved_units"), t.get("scale"), tuple(t.get("origin", (0.0, 0.0))))
    d.setdefault("coordinate_frames", [f.model_dump() for f in frames])
    d.setdefault("spatial_structure", {})
    d["schema_version"] = "0.2.0"
    return d


def _migrate_0_2_0_to_0_3_0(d: dict[str, Any]) -> dict[str, Any]:
    """0.2.0 never loaded XREFs: each XREF block becomes a record with status
    "not_attempted" (geometry absent). View types, wall analysis and verification
    were not computed -> UNKNOWN / None / UNREVIEWED-without-binding."""
    d = copy.deepcopy(d)
    xrefs = []
    for b in d.get("blocks", []):
        if b.get("is_xref"):
            xrefs.append({"name": b["name"], "status": "not_attempted",
                          "note": "model produced by schema 0.2.0, which did not load XREFs"})
    d.setdefault("xrefs", xrefs)
    for r in d.get("view_regions", []):
        r.setdefault("view_type", "UNKNOWN")
        r.setdefault("view_type_confidence", 0.0)
        r.setdefault("view_type_evidence", ["not classified (schema 0.2.0)"])
        r.setdefault("review_state", "unreviewed")
    d.setdefault("wall_model", None)
    d.setdefault("human_corrections_applied", [])
    d.setdefault("verification", None)       # no binding: cannot be verified until reprocessed
    d["schema_version"] = "0.3.0"
    return d


def _migrate_0_3_0_to_0_4_0(d: dict[str, Any]) -> dict[str, Any]:
    """0.3.0 had no semantic spaces or depictions. Nothing is derived: rooms stay as they
    were (a 0.3.0 room was region+name in one), and doors/windows in non-plan views are
    NOT reclassified retroactively — reprocessing with the current engine does that."""
    d = copy.deepcopy(d)
    d["schema_version"] = "0.4.0"
    return d


def _migrate_0_4_0_to_0_5_0(d: dict[str, Any]) -> dict[str, Any]:
    """0.4.0 had no boundary classification, no `opening` elements and no content fingerprint.
    Nothing is derived: rooms get no ``boundary`` (None = NOT CLASSIFIED, not "all walls"), no
    openings are created, element provenance (including its engine_version) is left exactly as it
    was, and the verification binding keeps its original fingerprint. The engineering contract
    refuses such a model until it is reprocessed with the current engine."""
    d = copy.deepcopy(d)
    d["schema_version"] = "0.5.0"
    return d


MIGRATIONS: dict[str, tuple[str, Callable[[dict], dict]]] = {
    "0.1.0": ("0.2.0", _migrate_0_1_0_to_0_2_0),
    "0.2.0": ("0.3.0", _migrate_0_2_0_to_0_3_0),
    "0.3.0": ("0.4.0", _migrate_0_3_0_to_0_4_0),
    "0.4.0": ("0.5.0", _migrate_0_4_0_to_0_5_0),
}


def dump_model(model: BuildingModel) -> str:
    """Persisted form (0.3.0+): compact JSON; inside ``entities`` only non-default fields
    are written (the bulk of large models is default-valued geometry/provenance fields).
    Lossless: ``load_model`` restores every default. Top-level fields — including
    schema_version and the safety declarations (geometry_is_synthetic, ready_for_design,
    engineering_review_status, ai_inference_used) — are always written explicitly."""
    d = model.model_dump(mode="json", exclude={"entities"})
    d["entities"] = [e.model_dump(mode="json", exclude_defaults=True) for e in model.entities]
    return json.dumps(d, separators=(",", ":"), default=str)


def read_model_dict(path) -> dict[str, Any]:
    """Load a persisted model file and return the FULL (defaults-restored, migrated) dict."""
    from pathlib import Path
    model, _applied = load_model(json.loads(Path(path).read_text(encoding="utf-8")))
    return model.model_dump(mode="json")


def load_model(data: dict[str, Any]) -> tuple[BuildingModel, list[str]]:
    """Validate and (if needed) migrate a persisted model to the current schema.
    Returns (model, list of migrations applied)."""
    version = data.get("schema_version")
    applied: list[str] = []
    if version is None:
        raise UnsupportedSchemaVersion("model has no schema_version")
    while version != SCHEMA_VERSION:
        if version not in MIGRATIONS:
            raise UnsupportedSchemaVersion(
                f"schema_version {version!r} is not supported by this FireAI build (current {SCHEMA_VERSION}); "
                "refusing to guess its meaning")
        nxt, fn = MIGRATIONS[version]
        data = fn(data)
        applied.append(f"{version}->{nxt}")
        version = nxt
    return BuildingModel.model_validate(data), applied
