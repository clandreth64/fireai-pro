"""Deterministic CONTENT fingerprint of a building model's interpretation (schema 0.5.0).

A human review is valid for the interpretation content the person actually looked at, not for
the bytes of one output file. This digest covers everything that carries meaning:

* source identity (sha256) and the DWG conversion-loss result;
* units, transforms, coordinate frames, spatial structure, bounds, scale checks;
* every element: category, subtype, label, confidence, evidence, rules, source links, geometry,
  properties, placement, review state, classified boundary (segments, kinds) and openings;
* view regions (view types, evidence), the wall analysis layer, unclassified content;
* XREF identity and status (name, status, sha256, units, depth — not local file paths);
* applied human corrections; review triggers, warnings and errors; assumptions; schema version.

It deliberately EXCLUDES run metadata that carries no interpretation:

* ``model_id`` (random per run) and ``created_at`` (timestamp);
* converter labels, commands, logs and the converted-DXF bytes (``source.converter*``,
  ``converted_dxf_sha256``) — any effect of the converter on geometry shows up in the content;
* local file paths (``xrefs[].resolved_file``, ``source.filename``) and the DXF document GUIDs
  (a loader-invented GUID changes per load when the file has none; the sha256 identifies the source);
* engine version strings (element provenance and the verification binding): content decides.
  The ENGINEERING GATE is stricter — its verification fingerprint also includes the engine version;
* the source inventory (entities, layers, blocks): fixed by the source/XREF sha256.

Floats are rounded to 9 decimals (1e-9 ft) before hashing so platform noise cannot flip it.
Conservative by design: any change of interpreted content changes the fingerprint.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

CONTENT_FINGERPRINT_VERSION = "content/1"
_EXCLUDED_TOP = {"model_id", "created_at", "fireai_version", "verification", "entities", "layers", "blocks"}
# document_guid is NOT content: the source sha256 already fixes the source, and when a file has no
# $FINGERPRINTGUID the DXF loader invents a new one on every load (found on a real drawing, M1.9).
_SOURCE_KEEP = ("format", "sha256", "size_bytes", "source_uid", "dxf_version", "converted_from_dwg")
_XREF_DROP = {"resolved_file", "converter"}


def _round(x: Any) -> Any:
    if isinstance(x, float):
        return round(x, 9) + 0.0          # + 0.0 turns -0.0 into 0.0
    if isinstance(x, dict):
        return {k: _round(v) for k, v in x.items()}
    if isinstance(x, (list, tuple)):
        return [_round(v) for v in x]
    return x


def content_basis(model) -> dict[str, Any]:
    """The projection of the model that the content fingerprint hashes (for inspection/tests)."""
    if isinstance(model, dict):                 # a persisted dict: validate/migrate to one canonical form
        from fireai.schema import load_model
        model = load_model(model)[0]
    d = model.model_dump(mode="json", exclude={"entities", "layers", "blocks"})
    out = {k: v for k, v in d.items() if k not in _EXCLUDED_TOP}
    src = d.get("source") or {}
    audit = src.get("conversion_audit") or {}
    out["source"] = {**{k: src.get(k) for k in _SOURCE_KEEP},
                     "conversion": {"significance": audit.get("significance"), "lost": audit.get("lost"),
                                    "status": audit.get("status")} if audit else None}
    for el in out.get("elements", []):
        prov = el.get("provenance") or {}
        prov.pop("engine_version", None)
        if el.get("boundary"):
            el["boundary"].pop("engine_version", None)
    out["xrefs"] = [{k: v for k, v in x.items() if k not in _XREF_DROP} for x in d.get("xrefs", [])]
    wm = out.get("wall_model")
    if isinstance(wm, dict):
        wm.pop("engine_version", None)
    out["_fingerprint_version"] = CONTENT_FINGERPRINT_VERSION
    return _round(out)


def content_fingerprint(model) -> str:
    """sha256 of the canonical JSON of ``content_basis(model)``."""
    blob = json.dumps(content_basis(model), sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()
