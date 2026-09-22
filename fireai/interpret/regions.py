"""Separate drawing regions ("views") in model space, and what kind of view each is.

Real drawings often place several things in one model space: floor plans,
reflected ceiling plans, sections, elevations, details, riser diagrams,
legends, schedules, block libraries. FireAI reports the regions and a
*likely* view type for each, with evidence. It never decides which plan is
"the building" and never chooses which plan will be engineered — that is a
human decision recorded in the review store.

Rule G-VIEW-REGIONS (clustering): visible top-level model-space entities are
clustered by bounding-box proximity with gap max(MIN_GAP_FT, GAP_FRACTION x
largest extent).

Rule V-VIEW-TYPE (classification) combines weighted evidence:
* V-TITLE-VIEWPORT  a paper-space view title next to a viewport whose model
                    window covers the region (strongest: how sheets are labelled)
* V-TITLE-MODEL     a short title-like text inside the region ("STAIR SECTION 1",
                    "FIRST FLOOR PLAN", "RISER DIAGRAM") that is among the
                    largest texts in the region
* V-LAYER-TOKENS    most of the region's entities are on layers named for a view
                    kind (A-SECT, Arch_Section_Wall, A-ELEV, A-DETL, A-CLNG ...)
* V-GEOM-PLAN       plan evidence: doors, room labels / room boundaries, walls
* V-GEOM-TABLE      table entities (schedules)
* V-TITLE-BLOCK     the region consists only of title-block content
Evidence is tiered: titles > layer naming / tables / title-block content >
plan geometry (walls, doors and labels also appear in sections, so geometry
is only a fallback). The highest tier with evidence decides; within it the
leader must be clearly ahead, otherwise the region is UNKNOWN with all
candidates listed. Contradicting weaker evidence lowers confidence and forces
review. Confidence never exceeds 0.9; below 0.7 review is required.

Rule G-VIEW-TITLE-ATTACH: a cluster made only of text that sits just above or
below another cluster (horizontally overlapping, vertical gap <= max(2 x gap,
25% of that cluster's height)) is that view's title and is merged into it.

Room logic is applied only to regions whose type admits rooms (FLOOR_PLAN,
REFLECTED_CEILING_PLAN, UNKNOWN). It is never applied to sections,
elevations, details, site plans, riser diagrams, legends, schedules or title
blocks.
"""

from __future__ import annotations

import math
import re
from collections import Counter, defaultdict

from fireai.ingest.extract import geometry_points, uid_for
from fireai.interpret import rules as R
from fireai.model import BuildingModel, Issue

MIN_GAP_FT = 5.0
GAP_FRACTION = 0.03
SIGNIFICANT_FRACTION = 0.05

VIEW_TYPES = ("FLOOR_PLAN", "REFLECTED_CEILING_PLAN", "SECTION", "ELEVATION", "DETAIL", "SITE_PLAN",
              "RISER_DIAGRAM", "LEGEND", "SCHEDULE", "TITLE_BLOCK", "UNKNOWN")
ROOM_LOGIC_TYPES = {"FLOOR_PLAN", "REFLECTED_CEILING_PLAN", "UNKNOWN"}

W_VIEWPORT_TITLE = 0.8
W_MODEL_TITLE = 0.75
W_LAYER = 0.6
W_TABLE = 0.7
W_TITLE_BLOCK = 0.8
W_PLAN_STRONG = 0.5     # doors + (room labels or boundaries) + walls
W_PLAN_WEAK = 0.3       # walls with room labels or doors only
MIN_SCORE = 0.5
LEAD_RATIO = 0.6        # runner-up must be below LEAD_RATIO x winner
LAYER_SHARE = 0.5       # share of region entities on view-kind layers
TITLE_HEIGHT_FRACTION = 0.6

LAYER_VIEW_TOKENS = {
    "SECTION": {"SECT", "SECTION", "SECTIONS", "SECN"},
    "ELEVATION": {"ELEV", "ELEVATION", "ELEVATIONS", "ELEVS"},
    "DETAIL": {"DETL", "DETAIL", "DETAILS", "DTL", "DTLS"},
    "REFLECTED_CEILING_PLAN": {"CLNG", "CEIL", "CEILING", "RCP", "CLG"},
    "SITE_PLAN": {"SITE", "TOPO", "PROPERTY", "PROP", "PARCEL", "CURB", "PKNG", "PARKING"},
    "RISER_DIAGRAM": {"RISER", "RISR"},
    "SCHEDULE": {"SCHED", "SCHEDULE", "SCHD"},
    "LEGEND": {"LEGEND", "LGND"},
}

_SCALE_RE = re.compile(r"(SCALE\s*:?.*$)|(\d+(\s+\d+/\d+|/\d+)?\s*\"\s*=\s*\d+\s*'.*$)|(\b1\s*:\s*\d+\b)|(\bN\.?T\.?S\.?\b)")
_IDENT = r"(\s+([A-Z]?\d{1,3}[A-Z]?|[A-Z]{1,2}(-[A-Z0-9]{1,2})?|[A-Z0-9]{1,3}/[A-Z0-9.\-]{1,6}))?"
_TITLE_PATTERNS: list[tuple[str, re.Pattern]] = [(t, re.compile(p)) for t, p in [
    ("REFLECTED_CEILING_PLAN", r"^(\S+\s+){0,4}(REFLECTED\s+CEILING(\s+PLAN)?|CEILING\s+PLAN|RCP)" + _IDENT + r"$"),
    ("SITE_PLAN", r"^(\S+\s+){0,3}(SITE|PLOT)\s+PLAN" + _IDENT + r"$|^VICINITY\s+MAP$"),
    ("RISER_DIAGRAM", r"^(\S+\s+){0,4}(RISER|FLOW)\s+(DIAGRAM|SCHEMATIC|ELEVATION)S?" + _IDENT + r"$"
                      r"|^(\S+\s+){0,3}RISER" + _IDENT + r"$"),
    ("SECTION", r"^(\S+\s+){0,4}SECTIONS?" + _IDENT + r"$"),
    ("ELEVATION", r"^(\S+\s+){0,4}(ELEVATIONS?|ELEV\.?)" + _IDENT + r"$"),
    ("DETAIL", r"^(\S+\s+){0,4}DETAILS?" + _IDENT + r"$"),
    ("LEGEND", r"^(\S+\s+){0,3}(LEGEND|SYMBOLS|ABBREVIATIONS)$"),
    ("SCHEDULE", r"^(\S+\s+){0,4}SCHEDULES?$"),
    ("FLOOR_PLAN", r"^(\S+\s+){0,5}PLAN" + _IDENT + r"$"),
]]
_NOT_TITLE = re.compile(r"^(SEE|REFER|REF\.?|PER|TYP\.? AT|AS SHOWN|SIM\.?)\b")
_NON_FLOOR_PLAN = {"ROOF": "roof plan", "FOUNDATION": "foundation plan", "FRAMING": "framing plan",
                   "KEY": "key plan (overview, not the working plan)"}


def classify_title(text: str) -> tuple[str | None, str | None]:
    """Map one title-like string to a view type. Returns (type, note)."""
    t = " ".join(text.upper().replace("\\P", " ").split())
    t = _SCALE_RE.sub("", t).strip(" -:.,/")
    if not t or len(t) > 60 or len(t.split()) > 7 or _NOT_TITLE.match(t):
        return None, None
    for vtype, pat in _TITLE_PATTERNS:
        if pat.match(t):
            if vtype == "FLOOR_PLAN":
                for tok, note in _NON_FLOOR_PLAN.items():
                    if re.search(rf"\b{tok}\b", t):
                        return (None, note) if tok != "KEY" else ("FLOOR_PLAN", note)
            return vtype, None
    return None, None


# ── clustering ───────────────────────────────────────────────────────────────

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


def _children_of(model: BuildingModel) -> dict[str, list]:
    children_of: dict[str, list] = defaultdict(list)
    for e in model.entities:
        if e.parent_id:
            children_of[e.parent_id].append(e)
    return children_of


def cluster_regions(model: BuildingModel) -> list[dict]:
    """G-VIEW-REGIONS. Returns regions with member top-level entity ids."""
    b = model.bounds_normalized
    if b is None:
        return []
    children_of = _children_of(model)
    items = []
    for e in model.entities:
        if e.space != "model" or e.parent_id or not e.visible or not e.supported or e.normalized is None:
            continue
        bb = _entity_bbox(e, children_of)
        if bb:
            items.append((e, bb))
    if not items:
        return []
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
    attached = _attach_titles(groups, items, gap)

    src_uid = model.source.source_uid
    regions = []
    for n, idxs in enumerate(sorted(groups.values(), key=lambda g: (min(items[i][1][0] for i in g),
                                                                     min(items[i][1][1] for i in g))), start=1):
        ents = [items[i][0] for i in idxs]
        x0 = min(items[i][1][0] for i in idxs); y0 = min(items[i][1][1] for i in idxs)
        x1 = max(items[i][1][2] for i in idxs); y1 = max(items[i][1][3] for i in idxs)
        uids = sorted(e.uid for e in ents if e.uid)
        regions.append({
            "id": f"V{n}",
            # persistent: same member entities (by uid) -> same region uid across reprocessing
            "uid": uid_for(src_uid, "view_region|" + "|".join(uids)) if src_uid and uids else None,
            "bbox_ft": [round(x0, 3), round(y0, 3), round(x1, 3), round(y1, 3)],
            "entity_count": len(ents),
            "top_level_entity_ids": [e.id for e in ents],
            "attached_title_entity_ids": [e.id for e in ents if e.id in attached],
        })
    return regions


def _attach_titles(groups: dict[int, list[int]], items, gap: float) -> set[str]:
    """G-VIEW-TITLE-ATTACH (mutates groups). Returns ids of attached title entities."""
    def bbox(idxs):
        return (min(items[i][1][0] for i in idxs), min(items[i][1][1] for i in idxs),
                max(items[i][1][2] for i in idxs), max(items[i][1][3] for i in idxs))
    text_only = [k for k, idxs in groups.items() if all(items[i][0].type in ("TEXT", "MTEXT") for i in idxs)]
    others = [k for k in groups if k not in text_only]
    attached: set[str] = set()
    for k in text_only:
        g = bbox(groups[k])
        cands = []
        for o in others:
            h = bbox(groups[o])
            if g[0] > h[2] or h[0] > g[2]:
                continue
            d = max(h[1] - g[3], g[1] - h[3], 0.0)
            if d <= max(2 * gap, 0.25 * (h[3] - h[1])):
                cands.append((d, o))
        cands.sort()
        if not cands or (len(cands) > 1 and cands[1][0] <= 1.5 * cands[0][0]):
            continue      # no unique nearest view: leave as its own region
        attached.update(items[i][0].id for i in groups[k])
        groups[cands[0][1]].extend(groups.pop(k))
    return attached


# ── classification ───────────────────────────────────────────────────────────

def _rect_dist(p, r) -> float:
    dx = max(r[0] - p[0], 0.0, p[0] - r[2])
    dy = max(r[1] - p[1], 0.0, p[1] - r[3])
    return math.hypot(dx, dy)


def _overlap_fraction(a, b) -> float:
    """Fraction of rect a covered by rect b."""
    w = min(a[2], b[2]) - max(a[0], b[0])
    h = min(a[3], b[3]) - max(a[1], b[1])
    if w < 0 or h < 0:
        return 0.0
    area = (a[2] - a[0]) * (a[3] - a[1])
    if area < 1e-9:      # degenerate region (point/line) that touches b
        return 1.0
    return (w * h) / area


def _entity_texts(ent, children_of) -> list[tuple[str, float]]:
    out = []
    stack = [ent]
    while stack:
        c = stack.pop()
        stack.extend(children_of.get(c.id, []))
        g = c.normalized or c.source
        if g is not None and g.kind == "text" and g.text and c.visible:
            out.append((g.text, g.height or 0.0))
    return out


def viewport_titles(model: BuildingModel, children_of, view_title_blocks: set[str]) -> list[dict]:
    """Associate paper-space titles with the nearest viewport on the same layout."""
    vps = [v for v in model.scale.viewport_scales if v.get("paper_rect")]
    if not vps:
        return []
    t = model.transform
    s = t.scale
    out = []
    by_layout: dict[str, list[dict]] = defaultdict(list)
    for v in vps:
        by_layout[v["layout"]].append(v)
    for e in model.entities:
        if e.space != "paper" or e.parent_id or not e.visible:
            continue
        cands = by_layout.get(e.attributes.get("layout") or "", [])
        if not cands or e.source is None:
            continue
        is_title_block = e.type == "INSERT" and e.attributes.get("block") in view_title_blocks
        parts = [p for txt, _h in _entity_texts(e, children_of)
                 for p in re.split(r"\\P|\n", txt.upper()) if p.strip()]
        found = []
        for p in parts:
            vt, note = classify_title(p)
            if vt or note:
                found.append((vt, note, p))
        if not found:
            continue
        pts = geometry_points(e.source)
        anchor = e.source.insert or (pts[0] if pts else None)
        if anchor is None:
            continue
        dists = sorted((_rect_dist(anchor, v["paper_rect"]), i) for i, v in enumerate(cands))
        d, i = dists[0]
        v = cands[i]
        ph = v["paper_rect"][3] - v["paper_rect"][1]
        if d > (1.0 if is_title_block else 0.6) * ph:
            continue
        if len(dists) > 1 and dists[1][0] - d < 0.1 * ph:
            continue    # about equally close to two viewports: not attributable
        win = v.get("model_window_src")
        local = None
        if win and s:
            local = [(win[0] - t.origin[0]) * s, (win[1] - t.origin[1]) * s,
                     (win[2] - t.origin[0]) * s, (win[3] - t.origin[1]) * s]
        for vt, note, p in found:
            out.append({"viewport": v["handle"], "layout": e.attributes.get("layout"),
                        "title": " ".join(p.split())[:80], "view_type": vt, "note": note,
                        "title_entity_id": e.id, "model_window_local": local, "distance_paper": round(d, 4)})
    return out


def classify_regions(model: BuildingModel, regions: list[dict], layer_roles, block_roles) -> None:
    ents = {e.id: e for e in model.entities}
    children_of = _children_of(model)
    view_title_blocks = {n for n, m in block_roles.items() if m and m.role == "view_title"}
    vp_titles = viewport_titles(model, children_of, view_title_blocks)

    for r in regions:
        # tier 0: titles; tier 1: layer naming / tables / title block; tier 2: plan geometry.
        tiers: list[Counter] = [Counter(), Counter(), Counter()]
        evidence: list[str] = []
        rules: set[str] = set()
        notes: list[str] = []
        members = [ents[i] for i in r["top_level_entity_ids"]]

        # 1. viewport titles
        links = []
        for vt in vp_titles:
            win = vt["model_window_local"]
            if not win:
                continue
            if _overlap_fraction(r["bbox_ft"], win) < 0.5:
                if _overlap_fraction(win, r["bbox_ft"]) >= 0.8:
                    # an enlarged view of PART of this region: informative, not the region's type
                    links.append({**{k: vt[k] for k in ("layout", "viewport", "title", "view_type")},
                                  "relation": "viewport_shows_part_of_region"})
                    notes.append(f"viewport {vt['viewport']} titled '{vt['title']}' shows an enlarged part of this region")
                continue
            links.append({**{k: vt[k] for k in ("layout", "viewport", "title", "view_type")},
                          "relation": "viewport_covers_region"})
            if vt["view_type"]:
                tiers[0][vt["view_type"]] += W_VIEWPORT_TITLE
                evidence.append(f"paper-space title '{vt['title']}' (layout '{vt['layout']}') labels viewport "
                                f"{vt['viewport']}, whose model window covers this region")
                rules.add("V-TITLE-VIEWPORT")
            if vt["note"]:
                notes.append(f"viewport title '{vt['title']}': {vt['note']}")
        r["viewport_links"] = links
        windows = [v for v in model.scale.viewport_scales if v.get("model_window_src")]
        if windows and model.transform.scale:
            t = model.transform
            shown = any(_overlap_fraction(r["bbox_ft"], [(w["model_window_src"][0] - t.origin[0]) * t.scale,
                                                          (w["model_window_src"][1] - t.origin[1]) * t.scale,
                                                          (w["model_window_src"][2] - t.origin[0]) * t.scale,
                                                          (w["model_window_src"][3] - t.origin[1]) * t.scale]) > 0
                        for w in windows)
            r["shown_in_paper_viewport"] = shown
            if not shown:
                notes.append("region is not shown in any paper-space viewport (may be unused or work-in-progress content)")

        # 2. model-space titles (largest texts in the region)
        texts = [(txt, h) for m in members for txt, h in _entity_texts(m, children_of)]
        hmax = max((h for _t, h in texts), default=0.0)
        seen_titles = set()
        for txt, h in texts:
            if hmax and h < TITLE_HEIGHT_FRACTION * hmax:
                continue
            for part in re.split(r"\\P|\n", txt):
                vt, note = classify_title(part)
                key = (vt, " ".join(part.split()).upper())
                if key in seen_titles:
                    continue
                seen_titles.add(key)
                if vt:
                    tiers[0][vt] += W_MODEL_TITLE
                    evidence.append(f"title text '{' '.join(part.split())[:60]}' in region (among its largest text)")
                    rules.add("V-TITLE-MODEL")
                if note:
                    notes.append(f"title '{' '.join(part.split())[:60]}': {note}")

        # 3. layer-name tokens (every visible entity incl. block contents)
        layer_count: Counter = Counter()
        total = 0
        stack = list(members)
        while stack:
            c = stack.pop()
            stack.extend(children_of.get(c.id, []))
            if c.type == "INSERT" or not c.visible:
                continue
            total += 1
            toks = set(R.tokens(c.layer))
            for vt, vtoks in LAYER_VIEW_TOKENS.items():
                if toks & vtoks:
                    layer_count[vt] += 1
        for vt, n in layer_count.items():
            if total and n / total >= LAYER_SHARE:
                tiers[1][vt] += W_LAYER
                evidence.append(f"{n}/{total} entities are on layers named for {vt.lower().replace('_', ' ')} views")
                rules.add("V-LAYER-TOKENS")

        # 4. geometry / roles
        roles: Counter = Counter()
        for m in members:
            lr = layer_roles.get(m.layer)
            br = block_roles.get(m.attributes.get("block")) if m.type == "INSERT" else None
            for x in (lr, br):
                if x:
                    roles[x.role] += 1
            if m.type in ("TABLE", "ACAD_TABLE"):
                roles["table"] += 1
        if roles.get("table"):
            tiers[1]["SCHEDULE"] += W_TABLE
            evidence.append(f"{roles['table']} table entit(ies)")
            rules.add("V-GEOM-TABLE")
        if members and all((layer_roles.get(m.layer) and layer_roles[m.layer].role == "title_block")
                           or (m.type == "INSERT" and block_roles.get(m.attributes.get("block"))
                               and block_roles[m.attributes.get("block")].role == "title_block") for m in members):
            tiers[1]["TITLE_BLOCK"] += W_TITLE_BLOCK
            evidence.append("region contains only title-block content")
            rules.add("V-TITLE-BLOCK")
        has_walls = roles.get("wall", 0) > 0
        has_doors = roles.get("door", 0) > 0
        has_rooms = roles.get("room_label", 0) + roles.get("room_boundary", 0) > 0
        if has_walls and has_doors and has_rooms:
            tiers[2]["FLOOR_PLAN"] += W_PLAN_STRONG
            evidence.append("walls, doors and room labels/boundaries present (plan evidence)")
            rules.add("V-GEOM-PLAN")
        elif has_walls and (has_doors or has_rooms):
            tiers[2]["FLOOR_PLAN"] += W_PLAN_WEAK
            evidence.append("walls with " + ("doors" if has_doors else "room labels/boundaries")
                            + " present (weak plan evidence; sections also show walls and doors)")
            rules.add("V-GEOM-PLAN")

        # The highest tier with evidence decides; lower tiers only support or contradict.
        scores: Counter = Counter()
        for tier in tiers:
            scores.update(tier)
        ranked = scores.most_common()
        vtype, conf, review = "UNKNOWN", 0.0, "unreviewed"
        decisive = next((t for t in tiers if t), None)
        if decisive is not None:
            dr = decisive.most_common()
            top, s1 = dr[0]
            s2 = dr[1][1] if len(dr) > 1 else 0.0
            if s1 < MIN_SCORE:
                evidence.append("insufficient evidence for a view type: " + ", ".join(f"{t} {s:.2f}" for t, s in ranked[:4]))
            elif s2 >= LEAD_RATIO * s1:
                review = "review_required"
                evidence.append("conflicting view-type evidence: " + ", ".join(f"{t} {s:.2f}" for t, s in dr[:4]))
            else:
                vtype, conf = top, min(0.9, s1)
                weaker = tiers[tiers.index(decisive) + 1:]
                contra = sorted({k for t in weaker for k in t if k != top})
                support = sum(t[top] for t in weaker)
                if contra:
                    evidence.append(f"weaker evidence points elsewhere ({', '.join(contra)}); decided by the "
                                    "stronger evidence, human review required")
                    conf = max(0.0, conf - 0.1)
                    review = "review_required"
                elif support:
                    conf = min(0.9, conf + 0.05)
        if not evidence:
            evidence.append("no title, layer-name or geometric evidence of the view type")
        evidence.extend(notes)
        if vtype != "UNKNOWN" and conf < 0.7:
            review = "review_required"
        r.update({"view_type": vtype, "view_type_confidence": round(conf, 3),
                  "view_type_candidates": {t: round(s, 3) for t, s in ranked},
                  "view_type_evidence": evidence, "view_type_rules": sorted(rules) or ["V-VIEW-TYPE"],
                  "view_type_source": "fireai", "review_state": review,
                  "room_logic": "applied" if vtype in ROOM_LOGIC_TYPES else "skipped"})


def apply_view_type_overrides(regions: list[dict], overrides: dict[str, str]) -> list[str]:
    """Human view-type corrections (region uid -> type). Returns uids not found."""
    missing = []
    by_uid = {r.get("uid"): r for r in regions}
    for uid, vt in overrides.items():
        r = by_uid.get(uid)
        if r is None:
            missing.append(uid)
            continue
        r["machine_view_type"] = r.get("view_type")
        r.update({"view_type": vt, "view_type_source": "human", "review_state": "human_corrected",
                  "room_logic": "applied" if vt in ROOM_LOGIC_TYPES else "skipped"})
    return missing


def region_index(model: BuildingModel, regions: list[dict]) -> dict[str, dict]:
    """entity id (top-level and every descendant) -> region."""
    out: dict[str, dict] = {}
    children_of = _children_of(model)
    for r in regions:
        stack = list(r["top_level_entity_ids"])
        while stack:
            i = stack.pop()
            out[i] = r
            stack.extend(c.id for c in children_of.get(i, []))
    return out


def summarize_regions(model: BuildingModel, regions: list[dict]) -> list[Issue]:
    """After interpretation: element counts, significance and region issues."""
    elem_by_entity: dict[str, list] = defaultdict(list)
    for el in model.elements:
        for sid in el.source_entity_ids:
            elem_by_entity[sid].append(el)
    tb_entities = {sid for el in model.elements_of("title_block") for sid in el.source_entity_ids}
    total = sum(r["entity_count"] for r in regions)
    for r in regions:
        cats: Counter = Counter()
        seen = set()
        for eid in r["top_level_entity_ids"]:
            for el in elem_by_entity.get(eid, []):
                if el.id not in seen:
                    seen.add(el.id); cats[el.category] += 1
        only_title = all(eid in tb_entities for eid in r["top_level_entity_ids"])
        significant = not only_title and (cats.get("wall", 0) > 0 or cats.get("room", 0) > 0
                                          or r["entity_count"] >= SIGNIFICANT_FRACTION * total)
        r.update({"element_counts": dict(cats), "title_block_only": only_title, "significant": significant})
        for eid in r["top_level_entity_ids"]:
            for el in elem_by_entity.get(eid, []):
                el.properties.setdefault("view_region", r["id"])
                el.properties.setdefault("view_type", r.get("view_type", "UNKNOWN"))
    sig = [r for r in regions if r["significant"]]
    issues = []
    if len(sig) >= 2:
        desc = "; ".join(f"{r['id']} ({r.get('view_type', 'UNKNOWN')}): {r['entity_count']} entities, "
                         f"walls={r['element_counts'].get('wall', 0)}, rooms={r['element_counts'].get('room', 0)}"
                         for r in sig[:8])
        issues.append(Issue(code="MULTIPLE_DRAWING_REGIONS",
                            message=f"Model space contains {len(sig)} separate drawing regions (e.g. several plans, "
                                    f"sections, details or diagrams). FireAI does not decide which is the building "
                                    f"plan, whether they are different levels, or which one will be engineered. {desc}"))
    plans = [r for r in sig if r.get("view_type") == "FLOOR_PLAN"]
    if len(plans) >= 2:
        issues.append(Issue(code="MULTIPLE_FLOOR_PLANS",
                            message=f"{len(plans)} regions look like floor plans ({', '.join(r['id'] for r in plans)}). "
                                    "A person must select which plan(s) are in scope; FireAI will not choose."))
    unsure = [r for r in sig if r.get("review_state") == "review_required"]
    if unsure:
        issues.append(Issue(code="VIEW_TYPE_UNCERTAIN",
                            message="View type is uncertain for: " + "; ".join(
                                f"{r['id']} ({r['view_type']}, candidates {r['view_type_candidates']})" for r in unsure)))
    return issues


def region_warnings(regions: list[dict]) -> list[Issue]:
    skipped = [r for r in regions if r.get("significant") and r.get("room_logic") == "skipped"]
    if not skipped:
        return []
    return [Issue(code="ROOM_LOGIC_SKIPPED_FOR_NON_PLAN_VIEWS", severity="info",
                  message="Room detection was not applied to regions classified as non-plan views: "
                          + ", ".join(f"{r['id']} ({r['view_type']}, {r['view_type_source']}, "
                                      f"conf {r['view_type_confidence']})" for r in skipped)
                          + ". If a classification is wrong, correct it in review and reprocess.")]
