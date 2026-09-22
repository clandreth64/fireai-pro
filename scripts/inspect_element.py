"""Inspect one interpreted object (or source entity) in a FireAI building model.

Usage:
    python scripts/inspect_element.py <building_model.json> <element id | uid | entity id>

Prints: FireAI id/uid, category, rule(s), confidence, evidence, review status,
placement, source entity handle(s) + handle paths, layers, blocks, original (SRC)
and transformed (LOCAL, SRC_FT) coordinates, and related warnings/review triggers.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fireai.schema import load_model  # noqa: E402
from fireai.spatial import FrameUnresolved, transform_point  # noqa: E402


def _fmt(p):
    return "(" + ", ".join(f"{v:,.4f}" for v in p) + ")"


def main():
    model_path, key = sys.argv[1], sys.argv[2]
    model, applied = load_model(json.loads(Path(model_path).read_text(encoding="utf-8")))
    if applied:
        print(f"[migrated: {applied}]")
    ents = {e.id: e for e in model.entities}
    el = next((e for e in model.elements if key in (e.id, e.uid)), None)
    ent_ids = el.source_entity_ids if el else [e.id for e in model.entities if key in (e.id, e.uid, e.handle)]
    if el:
        print(f"ELEMENT {el.id}  uid={el.uid}\n  category={el.category} subtype={el.subtype} label={el.label!r}")
        print(f"  confidence={el.confidence}  requires_verification={el.requires_verification}  "
              f"review={el.provenance.review.status}")
        print(f"  rules={el.rules}  engine={el.provenance.engine} {el.provenance.engine_version}")
        print("  evidence:")
        for ev in el.evidence:
            print(f"    - {ev}")
        print(f"  placement={el.placement.model_dump()}")
        props = {k: v for k, v in el.properties.items() if k not in ("raw_text",)}
        print(f"  properties={json.dumps(props, default=str)[:800]}")
        issues = [i for i in model.diagnostics.review_triggers + model.diagnostics.warnings if el.id in i.element_ids]
        for i in issues:
            print(f"  ISSUE {i.code}: {i.message}")
    print(f"\nSOURCE ENTITIES ({len(ent_ids)}):")
    for sid in ent_ids[:25]:
        e = ents[sid]
        print(f"  {e.id} uid={e.uid} type={e.type} layer={e.layer!r} handle={e.handle} path={'/'.join(e.handle_path)}"
              f" block_path={e.block_path} visible={e.visible} supported={e.supported} z_range={e.z_range}")
        pts = (e.source.points or ([e.source.insert] if e.source and e.source.insert else [])) if e.source else []
        for p in pts[:3]:
            line = f"      SRC {_fmt(p)}"
            try:
                line += f"  SRC_FT {_fmt(transform_point(model.coordinate_frames, p, 'SRC', 'SRC_FT'))}"
                line += f"  LOCAL {_fmt(transform_point(model.coordinate_frames, p, 'SRC', 'LOCAL'))}"
            except FrameUnresolved:
                line += "  (units unresolved: SRC_FT/LOCAL unavailable)"
            print(line)
        if len(pts) > 3:
            print(f"      ... {len(pts) - 3} more points")
    if len(ent_ids) > 25:
        print(f"  ... {len(ent_ids) - 25} more entities")


if __name__ == "__main__":
    main()
