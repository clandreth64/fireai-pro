"""Detect separate drawing regions ("views") in model space.

Real drawings often place several things in one model space: multiple floor
plans, sections, details, riser diagrams, block libraries. FireAI does not
decide which region is "the building" — it reports the regions so a human can.

Rule G-VIEW-REGIONS: visible top-level model-space entities are clustered by
bounding-box proximity with gap threshold max(MIN_GAP_FT, GAP_FRACTION x largest
extent). A region is *significant* if it holds walls/rooms or at least
SIGNIFICANT_FRACTION of the entities and is not solely a title block.
"""

from __future__ import annotations

from collections import Counter, defaultdict

from fireai.ingest.extract import geometry_points
from fireai.model import BuildingModel, Issue

MIN_GAP_FT = 5.0
GAP_FRACTION = 0.03
SIGNIFICANT_FRACTION = 0.05


def _entity_bbox(ent, children_of):
    pts = []
    if ent.type == "INSERT":
        stack = list(children_of.get(ent.id, []))
        while stack:
            c = stack.pop()
            stack.extend(children_of.get(c.id, []))
            if c.visible and c.supported and c.type != "INSERT":
                pts.extend(geometry_points(c.normalized))
        if not pts and ent.normalized and ent.normalized.insert:
            pts = [ent.normalized.insert]
    else:
        pts = geometry_points(ent.normalized)
    if not pts:
        return None
    xs = [p[0] for p in pts]; ys = [p[1] for p in pts]
    return (min(xs), min(ys), max(xs), max(ys))


def detect_regions(model: BuildingModel) -> tuple[list[dict], list[Issue]]:
    b = model.bounds_normalized
    if b is None:
        return [], []
    children_of: dict[str, list] = defaultdict(list)
    for e in model.entities:
        if e.parent_id:
            children_of[e.parent_id].append(e)
    items = []
    for e in model.entities:
        if e.space != "model" or e.parent_id or not e.visible or not e.supported or e.normalized is None:
            continue
        bb = _entity_bbox(e, children_of)
        if bb:
            items.append((e, bb))
    if not items:
        return [], []
    gap = max(MIN_GAP_FT, GAP_FRACTION * max(b.width, b.height))

    parent = list(range(len(items)))

    def find(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    order = sorted(range(len(items)), key=lambda i: items[i][1][0])
    active: list[int] = []
    for i in order:
        bx = items[i][1]
        active = [j for j in active if items[j][1][2] + gap >= bx[0]]
        for j in active:
            by = items[j][1]
            if bx[1] <= by[3] + gap and by[1] <= bx[3] + gap:
                parent[find(i)] = find(j)
        active.append(i)
    groups: dict[int, list[int]] = defaultdict(list)
    for i in range(len(items)):
        groups[find(i)].append(i)

    elem_by_entity: dict[str, list] = defaultdict(list)
    for el in model.elements:
        for sid in el.source_entity_ids:
            elem_by_entity[sid].append(el)
    tb_entities = {sid for el in model.elements_of("title_block") for sid in el.source_entity_ids}

    regions = []
    total = len(items)
    for n, idxs in enumerate(sorted(groups.values(), key=lambda g: min(items[i][1][0] for i in g)), start=1):
        ents = [items[i][0] for i in idxs]
        x0 = min(items[i][1][0] for i in idxs); y0 = min(items[i][1][1] for i in idxs)
        x1 = max(items[i][1][2] for i in idxs); y1 = max(items[i][1][3] for i in idxs)
        cats = Counter()
        seen = set()
        for e in ents:
            for el in elem_by_entity.get(e.id, []):
                if el.id not in seen:
                    seen.add(el.id); cats[el.category] += 1
        only_title = all(e.id in tb_entities for e in ents)
        significant = not only_title and (cats.get("wall", 0) > 0 or cats.get("room", 0) > 0
                                          or len(ents) >= SIGNIFICANT_FRACTION * total)
        regions.append({"id": f"V{n}", "bbox_ft": [round(x0, 3), round(y0, 3), round(x1, 3), round(y1, 3)],
                        "entity_count": len(ents), "element_counts": dict(cats),
                        "title_block_only": only_title, "significant": significant})
        for el_list in (elem_by_entity.get(e.id, []) for e in ents):
            for el in el_list:
                el.properties.setdefault("view_region", f"V{n}")
    sig = [r for r in regions if r["significant"]]
    issues = []
    if len(sig) >= 2:
        desc = "; ".join(f"{r['id']}: {r['entity_count']} entities, walls={r['element_counts'].get('wall', 0)}, "
                         f"rooms={r['element_counts'].get('room', 0)}" for r in sig[:8])
        issues.append(Issue(code="MULTIPLE_DRAWING_REGIONS",
                            message=f"Model space contains {len(sig)} separate drawing regions (e.g. several plans, "
                                    f"sections, details or diagrams). FireAI does not decide which is the building "
                                    f"plan or whether they are different levels. {desc}"))
    return regions, issues
