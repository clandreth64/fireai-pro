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
"""

from __future__ import annotations

import copy
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


MIGRATIONS: dict[str, tuple[str, Callable[[dict], dict]]] = {
    "0.1.0": ("0.2.0", _migrate_0_1_0_to_0_2_0),
}


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
