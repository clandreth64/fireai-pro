"""Deterministic semantic interpretation: source entities -> building elements.

Every element carries: category, source entity ids, confidence, evidence,
rule ids, and ``requires_verification``. Nothing is generated that is not
backed by drawing entities. There is no area-based or template geometry.

All thresholds are in normalized feet (units must be resolved before this runs).
"""

from __future__ import annotations

import math
from collections import defaultdict
from dataclasses import dataclass, field

from shapely.geometry import LineString, Point, Polygon
from shapely.ops import polygonize, snap, unary_union
from shapely.prepared import prep
from shapely.validation import make_valid

from fireai import __version__
from fireai.ingest.extract import geometry_points, uid_for
from fireai.interpret import rules as R
from fireai.interpret.text import is_finish_note, parse_room_label, parse_title_block, split_lines
from fireai.model import BuildingElement, Geometry, Issue, Placement, Provenance, SourceEntity

VERIFY_BELOW = 0.80           # elements below this confidence require human verification
MIN_ROOM_AREA_SF = 4.0
MIN_POLYGONIZED_ROOM_SF = 16.0
WALL_CAVITY_MAX_WIDTH_FT = 1.5  # faces thinner than this between wall lines are wall cavities
CLUSTER_TOL_FT = 0.25
MAX_COLUMN_SIZE_FT = 5.0
AREA_LABEL_TOLERANCE = 0.05
DOOR_CLOSURE_MIN_FT = 1.5      # shorter pairs are wall thickness, not an opening
DOOR_CLOSURE_MAX_FT = 8.0      # widest opening bridged (pair of doors)
DOOR_CLOSURE_MARGIN_FT = 0.75  # search margin around a door's footprint
LABEL_NEAR_FT = 1.0            # R-LABEL-NEAR: max distance of an outside label from a boundary
# View-aware interpretation (M1.8): plan-network openings may only be created in plan views.
PLAN_VIEW_TYPES = {"FLOOR_PLAN", "REFLECTED_CEILING_PLAN"}
UNCONFIRMED_VIEW_TYPES = {"UNKNOWN"}
PLAN_NETWORK_OPENINGS = {"door", "window"}
ROOM_TAG_TOKENS = {"ROOM", "RM", "RMTAG", "ROOMTAG", "RMNAME", "ROOMNAME", "SPACE", "IDEN", "RMNO"}
SNAP_TOL_FT = 1e-4             # analysis closure endpoints are snapped onto wall linework within this
WALL_QUALIFIER_TOKENS = {"ABOVE", "BELOW", "OVHD", "OVERHEAD", "HIDDEN", "DEMO", "DEMOLITION", "DEMOLISH",
                         "FUTURE", "NIC", "EXIST", "EXISTING", "REMOVE", "RMV"}

DRAWABLE = {"line", "polyline", "arc", "circle", "hatch", "point"}
POINT_ROLES = {"existing_fire_protection", "existing_mep", "structural", "ceiling"}
CLUSTER_ROLES = {"door": "door", "window": "window", "column": "column", "stair": "stair", "shaft": "shaft"}


@dataclass
class InterpretResult:
    elements: list[BuildingElement] = field(default_factory=list)
    unclassified_entity_ids: list[str] = field(default_factory=list)
    issues: list[Issue] = field(default_factory=list)
    title_block: dict | None = None


def _bbox(pts):
    xs = [p[0] for p in pts]; ys = [p[1] for p in pts]
    return (min(xs), min(ys), max(xs), max(ys))


def _bbox_poly(b) -> Geometry:
    x0, y0, x1, y1 = b
    return Geometry(kind="polygon", points=[(x0, y0), (x1, y0), (x1, y1), (x0, y1)], closed=True)


def _conf_flag(c: float) -> bool:
    return c < VERIFY_BELOW


class Interpreter:
    def __init__(self, entities: list[SourceEntity], layer_roles: dict[str, R.RoleMatch | None],
                 block_roles: dict[str, R.RoleMatch | None], source_uid: str | None = None,
                 region_of: dict[str, dict] | None = None, engine_version: str | None = None):
        self.source_uid = source_uid
        # the pipeline passes THE engine version (fireai.pipeline.ENGINE_VERSION); the bare package
        # version is only a fallback for direct use of the interpreter
        self.engine_version = engine_version or __version__
        self.region_of = region_of or {}
        self.all = entities
        self.by_id = {e.id: e for e in entities}
        self.children: dict[str, list[SourceEntity]] = defaultdict(list)
        for e in entities:
            if e.parent_id:
                self.children[e.parent_id].append(e)
        self.layer_roles = layer_roles
        self.block_roles = block_roles
        self.claimed: set[str] = set()
        self.out = InterpretResult()
        self._n = 0

    # ── helpers ──────────────────────────────────────────────────────────────
    def _id(self, prefix: str) -> str:
        self._n += 1
        return f"{prefix}{self._n:05d}"

    def descendants(self, ent: SourceEntity) -> list[SourceEntity]:
        out, stack = [], list(self.children.get(ent.id, []))
        while stack:
            c = stack.pop()
            out.append(c)
            stack.extend(self.children.get(c.id, []))
        return out

    def pts(self, ent: SourceEntity) -> list[tuple[float, float]]:
        if ent.type == "INSERT":
            p = []
            for d in self.descendants(ent):
                if d.visible and d.supported and d.type != "INSERT":
                    p.extend(geometry_points(d.normalized))
            if not p and ent.normalized and ent.normalized.insert:
                p = [ent.normalized.insert]
            return p
        return geometry_points(ent.normalized)

    def room_logic_allowed(self, ent: SourceEntity) -> bool:
        """False inside regions classified as non-plan views (sections, details ...)."""
        r = self.region_of.get(ent.id)
        return r is None or r.get("room_logic", "applied") == "applied"

    def role(self, ent: SourceEntity) -> R.RoleMatch | None:
        return self.layer_roles.get(ent.layer)

    def model_entities(self, top_level_only: bool = False):
        for e in self.all:
            if e.space != "model" or not e.visible or not e.supported:
                continue
            if top_level_only and e.parent_id:
                continue
            yield e

    def claim(self, ents) -> list[str]:
        ids = []
        for e in ents:
            self.claimed.add(e.id)
            ids.append(e.id)
            for d in (self.descendants(e) if e.type == "INSERT" else []):
                self.claimed.add(d.id)
        return ids

    def view_type_of(self, ents) -> str | None:
        """View type of the region the entities belong to (None when outside every region)."""
        for e in ents:
            r = self.region_of.get(e.id)
            if r is not None:
                return r.get("view_type", "UNKNOWN")
        return None

    def add(self, category, confidence, evidence, rules, source_ents, geometry=None, label=None,
            subtype=None, properties=None, force_verify=False, placement: Placement | None = None) -> BuildingElement:
        # V-VIEW-AWARE-INTERPRETATION: a door/window recognised by plan heuristics (layer/block
        # names, linework groups) inside a SECTION / ELEVATION / DETAIL / ... view is what that
        # view DEPICTS, not a plan-network opening. It is kept, traceable and flagged, as a
        # `depiction`; it never becomes a door/window used for room topology or engineering.
        view = self.view_type_of(source_ents)
        if (category in PLAN_NETWORK_OPENINGS and view is not None and view not in PLAN_VIEW_TYPES
                and view not in UNCONFIRMED_VIEW_TYPES):
            properties = {**(properties or {}), "depicted_role": category, "view_type": view,
                          "plan_semantic": False, "recognized_as": subtype}
            evidence = list(evidence) + [
                f"inside a {view} view: kept as {view.lower()} content (a depicted {category}), NOT a plan "
                f"{category}/opening; not used for room topology or engineering"]
            rules = list(rules) + ["V-VIEW-AWARE-INTERPRETATION"]
            subtype = f"{category}_in_{view.lower()}"
            category = "depiction"
            confidence = min(confidence, 0.5)
            force_verify = True
        if geometry is not None and geometry.frame is None:
            geometry = geometry.model_copy(update={"frame": "LOCAL"})
        src_uids = [e.uid for e in source_ents if e.uid]
        rule_ids = sorted(set(rules))
        uid = (uid_for(self.source_uid, f"element|{category}|{subtype}|{'|'.join(sorted(src_uids))}|{','.join(rule_ids)}")
               if self.source_uid and src_uids else None)
        el = BuildingElement(
            uid=uid,
            placement=placement or Placement(),
            provenance=Provenance(origin="deterministic_inference", engine="fireai.interpret",
                                  engine_version=self.engine_version, rule_ids=rule_ids, derived_from=src_uids),
            id=self._id({"wall": "W", "door": "D", "window": "WN", "room": "R", "area": "A", "column": "C",
                         "stair": "ST", "shaft": "SH", "structural": "S", "grid_line": "G",
                         "text_annotation": "T", "dimension": "DM", "title_block": "TB", "ceiling": "CL",
                         "existing_fire_protection": "FP", "existing_mep": "MEP", "space": "SP",
                         "depiction": "DP"}[category]),
            category=category, subtype=subtype, label=label, confidence=round(confidence, 3),
            evidence=evidence, rules=sorted(set(rules)), source_entity_ids=self.claim(source_ents),
            requires_verification=force_verify or _conf_flag(confidence),
            geometry=geometry, properties=properties or {},
        )
        self.out.elements.append(el)
        return el

    # ── pipeline ─────────────────────────────────────────────────────────────
    def run(self) -> InterpretResult:
        self._inserts()
        self._title_block_layers()
        self._dimensions()
        self._walls()
        self._clustered_roles()
        self._grid()
        self._point_roles()
        self._rooms()
        self._text()
        self._unclassified()
        self._depiction_issue()
        return self.out

    def _depiction_issue(self):
        """A SPECIFIC review trigger for door/window content found in non-plan views (the
        generic "elements require verification" count is not enough to notice it)."""
        deps = [e for e in self.out.elements if e.category == "depiction"]
        if not deps:
            return
        by_view = defaultdict(list)
        for e in deps:
            by_view[(e.properties.get("view_type"), e.properties.get("depicted_role"))].append(e.id)
        parts = "; ".join(f"{len(ids)} {role}(s) in {view} view(s)" for (view, role), ids in sorted(by_view.items()))
        self.out.issues.append(Issue(
            code="NON_PLAN_OPENING_DEPICTIONS", element_ids=[e.id for e in deps],
            message=f"Door/window-like content was found in non-plan views ({parts}). It is kept as what that view "
                    "depicts, NOT as plan doors/windows: it does not affect rooms, walls or engineering. Verify what it "
                    "shows."))

    # Block references (doors, windows, columns, title blocks, FP symbols ...)
    def _inserts(self):
        for e in self.all:
            if e.type != "INSERT" or e.id in self.claimed or not e.visible:
                continue
            block = e.attributes.get("block", "")
            b = self.block_roles.get(block)
            lr = self.role(e)
            lr = lr if lr and lr.role in {"door", "window", "column", "stair", "title_block", "existing_fire_protection"} else None
            if not b and not lr:
                continue
            role = (b or lr).role
            if role == "view_title":
                texts = [d for d in self.descendants(e) if d.source and d.source.kind == "text" and d.source.text]
                label = " / ".join(" ".join(d.source.text.split()) for d in texts)[:120] or None
                self.add("text_annotation", b.confidence, [b.evidence, f"{len(texts)} text item(s) in view title"],
                         [b.rule_id], [e], label=label, subtype="view_title",
                         properties={"space": e.space, "block": block})
                continue
            conf, evidence, rule_ids = 0.0, [], []
            for m in (b, lr):
                if m and m.role == role:
                    conf = max(conf, m.confidence)
                    evidence.append(m.evidence); rule_ids.append(m.rule_id)
            if b and lr and b.role == lr.role:
                conf = min(0.95, conf + 0.05)
                evidence.append("block name and layer agree")
            elif b and lr and b.role != lr.role:
                evidence.append(f"CONFLICT: block suggests {b.role}, layer suggests {lr.role}")
                conf = min(conf, 0.5)
            p = self.pts(e)
            if role == "title_block":
                self._title_block([e], conf, evidence, rule_ids)
                continue
            if not p and e.space == "model":
                continue
            bb = _bbox(p) if p else None
            props = {"block": block, "insert_point": e.normalized.insert if e.normalized else None,
                     "rotation_deg": e.attributes.get("rotation")}
            if bb:
                props["footprint_ft"] = (round(bb[2] - bb[0], 3), round(bb[3] - bb[1], 3))
            if role in ("door", "window") and bb:
                props["nominal_width_ft"] = round(max(bb[2] - bb[0], bb[3] - bb[1]), 3)
                evidence.append("width = larger side of block footprint (not a verified opening width)")
            category = role if role != "stair" else "stair"
            self.add(category, conf, evidence, rule_ids, [e], geometry=_bbox_poly(bb) if bb else None,
                     label=block, subtype="block_reference", properties=props,
                     placement=Placement(rotation_deg=e.attributes.get("rotation")))

    def _title_block_layers(self):
        ents = [e for e in self.model_entities() if e.id not in self.claimed and e.type != "INSERT"
                and (r := self.role(e)) and r.role == "title_block"]
        if ents:
            m = self.role(ents[0])
            self._title_block(ents, m.confidence, [m.evidence], [m.rule_id])

    def _title_block(self, ents, conf, evidence, rule_ids):
        pts = [p for e in ents for p in self.pts(e)]
        bb = _bbox(pts) if pts else None
        texts, text_ents = [], []
        for e in ents:
            for t in [e] + (self.descendants(e) if e.type == "INSERT" else []):
                if t.source and t.source.kind == "text" and t.source.text:
                    texts.append(t.source.text); text_ents.append(t)
            for tag, val in (e.attributes.get("attribs") or {}).items():
                texts.append(f"{tag}: {val}")
        if bb:  # loose text inside the title-block footprint belongs to it
            x0, y0, x1, y1 = bb
            for t in self.model_entities():
                if t.id in self.claimed or t.source is None or t.source.kind != "text" or t.parent_id:
                    continue
                ip = t.normalized.insert if t.normalized else None
                if ip and x0 <= ip[0] <= x1 and y0 <= ip[1] <= y1:
                    texts.append(t.source.text or ""); text_ents.append(t)
        fields = parse_title_block("\n".join(texts))
        el = self.add("title_block", conf, evidence + [f"{len(texts)} text item(s) read"], rule_ids,
                      list(ents) + text_ents, geometry=_bbox_poly(bb) if bb else None,
                      properties={"fields": fields, "raw_text": texts[:200],
                                  "space": ents[0].space}, force_verify=True)
        tb = self.out.title_block or {"element_ids": [], "fields": {}}
        tb["element_ids"].append(el.id)
        for k, v in fields.items():
            tb["fields"].setdefault(k, v)
        tb["note"] = "Regex extraction from drawing text; every field requires human verification."
        self.out.title_block = tb

    def _dimensions(self):
        for e in self.model_entities(top_level_only=True):
            if e.type != "DIMENSION" or e.id in self.claimed:
                continue
            g = e.normalized
            self.add("dimension", 1.0, ["DIMENSION entity"], ["E-DIMENSION"], [e],
                     geometry=g, label=(g.text if g and g.text else None),
                     properties={"measurement_ft": g.measurement if g else None,
                                 "text_override": e.attributes.get("text_override"),
                                 "dimtype": e.attributes.get("dimtype")})

    def _walls(self):
        for e in self.model_entities():
            if e.id in self.claimed or e.type == "INSERT":
                continue
            r = self.role(e)
            if not r or r.role != "wall" or e.normalized is None or e.normalized.kind not in DRAWABLE:
                continue
            subtype = "poche_fill" if e.normalized.kind == "hatch" else "wall_linework"
            ev = [r.evidence, f"{e.type} geometry on wall layer",
                  "single linework entity; wall thickness and wall pairing not determined in this milestone"]
            qual = sorted(set(R.tokens(e.layer)) & WALL_QUALIFIER_TOKENS)
            if qual:
                ev.append(f"layer qualifier {qual}: wall may be above/below the cut plane, existing-to-remove, or "
                          "future - it may not be a wall at this level")
                self.add("wall", min(r.confidence, 0.5), ev, [r.rule_id, "L-WALL-QUALIFIER"], [e], geometry=e.normalized,
                         subtype="qualified_" + qual[0].lower(), force_verify=True,
                         properties={"length_ft": round(_length(e.normalized), 3), "layer_qualifiers": qual})
                continue
            if e.block_path:
                ev.append(f"inside block(s) {' > '.join(e.block_path)}")
            self.add("wall", r.confidence, ev, [r.rule_id], [e], geometry=e.normalized, subtype=subtype,
                     properties={"length_ft": round(_length(e.normalized), 3)})

    def _clustered_roles(self):
        by_role: dict[str, list[SourceEntity]] = defaultdict(list)
        for e in self.model_entities():
            if e.id in self.claimed or e.type in ("INSERT", "DIMENSION") or e.normalized is None:
                continue
            if e.normalized.kind == "text":
                continue
            r = self.role(e)
            if r and r.role in CLUSTER_ROLES:
                by_role[r.role].append(e)
        for role, ents in by_role.items():
            for cluster, nested in _merge_nested(_cluster(ents, self.pts, CLUSTER_TOL_FT), self.pts, CLUSTER_TOL_FT):
                pts = [p for e in cluster for p in self.pts(e)]
                bb = _bbox(pts)
                r = self.role(cluster[0])
                conf = r.confidence
                ev = [r.evidence, f"{len(cluster)} linework entit{'y' if len(cluster) == 1 else 'ies'} grouped by proximity"]
                if nested:
                    ev.append(f"includes {nested} nested part group(s) drawn inside it (G-NESTED-PARTS)")
                subtype = "linework_group"
                size = max(bb[2] - bb[0], bb[3] - bb[1])
                props = {"footprint_ft": (round(bb[2] - bb[0], 3), round(bb[3] - bb[1], 3))}
                if role == "door":
                    has_arc = any(e.normalized.kind == "arc" for e in cluster)
                    if has_arc:
                        ev.append("contains a door-swing arc")
                        conf = min(conf, 0.7)
                        radius = max(e.normalized.radius or 0 for e in cluster if e.normalized.kind == "arc")
                        props["swing_radius_ft"] = round(radius, 3)
                    else:
                        ev.append("no swing arc found")
                        conf = min(conf, 0.5)
                if role == "column":
                    if size > MAX_COLUMN_SIZE_FT:
                        ev.append(f"group is {size:.1f} ft across — larger than a typical column ({MAX_COLUMN_SIZE_FT} ft)")
                        conf = min(conf, 0.4)
                    else:
                        subtype = "column_outline"
                self.add(role, conf, ev, [r.rule_id], cluster, geometry=_bbox_poly(bb), subtype=subtype,
                         properties=props)

    def _grid(self):
        for e in self.model_entities():
            if e.id in self.claimed or e.normalized is None:
                continue
            r = self.role(e)
            if r and r.role == "grid" and e.normalized.kind == "line":
                self.add("grid_line", r.confidence, [r.evidence, "LINE on grid layer"], [r.rule_id], [e],
                         geometry=e.normalized, properties={"length_ft": round(_length(e.normalized), 3)})

    def _point_roles(self):
        for e in self.model_entities(top_level_only=True):
            if e.id in self.claimed or e.normalized is None or e.normalized.kind == "text":
                continue
            r = self.role(e)
            if not r or r.role not in POINT_ROLES:
                continue
            pts = self.pts(e)
            if not pts:
                continue
            self.add(r.role, r.confidence, [r.evidence, f"{e.type} on {r.role.replace('_', ' ')} layer"],
                     [r.rule_id], [e], geometry=_bbox_poly(_bbox(pts)), subtype=e.type.lower())

    # Rooms / areas
    def _rooms(self):
        cands = []  # (polygon, source entities, confidence, evidence, rules, method)
        for e in self.model_entities():
            if e.id in self.claimed or e.normalized is None:
                continue
            r = self.role(e)
            if not r or r.role != "room_boundary" or not self.room_logic_allowed(e):
                continue
            g = e.normalized
            if g.kind == "polyline" and g.closed and len(g.points) >= 3:
                poly, repaired = _polygon(g.points)
                if poly is None or poly.area < MIN_ROOM_AREA_SF:
                    continue
                ev = [r.evidence, "closed polyline on room/area layer"]
                if repaired:
                    ev.append("self-intersecting boundary was repaired (make_valid)")
                cands.append((poly, [e], r.confidence if not repaired else min(r.confidence, 0.6), ev,
                              [r.rule_id], "area_layer_polyline", []))

        method = "area_layer_polyline"
        if not cands:
            walls = [el for el in self.out.elements if el.category == "wall" and el.subtype == "wall_linework"
                     and all(self.room_logic_allowed(self.by_id[i]) for i in el.source_entity_ids)]
            lines = []
            for w in walls:
                pts = w.geometry.points if w.geometry else []
                if w.geometry and w.geometry.kind == "polyline" and w.geometry.closed and pts:
                    pts = pts + [pts[0]]
                if len(pts) >= 2:
                    lines.append(LineString(pts))
            if lines:
                method = "polygonized_wall_linework"
                closures = self._door_closures(walls, lines)
                # analysis lines must be NODED into the wall linework or they separate nothing
                wall_union = unary_union(lines)
                closure_lines = [snap(c["line"], wall_union, SNAP_TOL_FT) for c in closures]
                for face in polygonize(unary_union(lines + closure_lines)):
                    if face.area < MIN_POLYGONIZED_ROOM_SF:
                        continue
                    mrr = face.minimum_rotated_rectangle
                    xs, ys = mrr.exterior.coords.xy
                    sides = [math.dist((xs[i], ys[i]), (xs[i + 1], ys[i + 1])) for i in range(2)]
                    mean_width = 2.0 * face.area / face.length if face.length else 0.0
                    if min(sides) < WALL_CAVITY_MAX_WIDTH_FT or mean_width < WALL_CAVITY_MAX_WIDTH_FT:
                        continue  # cavity between parallel wall lines (incl. rings around the building)
                    ring = face.exterior.buffer(0.05)
                    src_ids = [sid for w in walls for sid in w.source_entity_ids
                               if ring.intersects(LineString(w.geometry.points))] if walls else []
                    src = [self.by_id[i] for i in dict.fromkeys(src_ids)]
                    used = [c for c in closures if ring.intersects(c["line"])]
                    ev = ["region enclosed by wall linework (polygonize)",
                          "no room/area-layer boundary exists in this drawing"]
                    rules_used = ["G-POLYGONIZE-WALLS"]
                    conf = 0.6
                    if used:
                        door_ids = ", ".join(sorted({c["door_id"] for c in used}))
                        ev.append(f"closed across {len(used)} door opening line(s) ({door_ids}) with analysis lines "
                                  "between wall vertices at the door; these lines are not walls")
                        rules_used.append("G-DOOR-OPENING-CLOSURE")
                        conf = 0.55
                    cands.append((face, src, conf, ev, rules_used, method,
                                  [{"door_id": c["door_id"], "from": list(c["line"].coords[0]),
                                    "to": list(c["line"].coords[-1])} for c in used]))

        # Gross boundaries: polygons containing >= 2 other candidates are areas, not rooms.
        rooms, areas = [], []
        for i, c in enumerate(cands):
            inner = sum(1 for j, o in enumerate(cands)
                        if j != i and o[0].area < c[0].area and c[0].buffer(0.01).contains(o[0]))
            (areas if inner >= 2 else rooms).append((c, inner))

        # Label association (text inside boundary, smallest containing polygon wins)
        texts = [t for t in self.model_entities() if t.id not in self.claimed and t.normalized is not None
                 and t.normalized.kind == "text" and t.normalized.text and self.room_logic_allowed(t)
                 and not self._symbol_text(t)]
        prepared = [(prep(c[0]), c) for c, _ in rooms]
        labels: dict[int, list[SourceEntity]] = defaultdict(list)
        near_labels: dict[int, list[SourceEntity]] = defaultdict(list)
        for t in texts:
            p = Point(t.normalized.insert)
            inside = [(c[0].area, idx) for idx, (pp, c) in enumerate(prepared) if pp.contains(p)]
            if inside:
                labels[min(inside)[1]].append(t)
                continue
            # R-LABEL-NEAR: a room-label-layer text just outside exactly one boundary
            r = self.role(t)
            if not (r and r.role == "room_label"):
                continue
            reach = max(LABEL_NEAR_FT, 2.0 * (t.normalized.height or 0.0))
            near = [idx for idx, (_pp, c) in enumerate(prepared) if c[0].exterior.distance(p) <= reach]
            if len(near) == 1:
                near_labels[near[0]].append(t)
        label_role_texts = [t for t in texts if (r := self.role(t)) and r.role == "room_label"]

        for idx, ((poly, src, conf, ev, rule_ids, meth, door_closures), _) in enumerate(rooms):
            ev = list(ev)
            rule_ids = list(rule_ids)
            candidates = labels.get(idx, [])
            # Prefer text on room-label layers; otherwise short text only.
            preferred = [t for t in candidates if (r := self.role(t)) and r.role in ("room_label", "room_boundary")]
            chosen = preferred or [t for t in candidates if len(t.normalized.text) <= 40]
            near = False
            if not chosen and near_labels.get(idx):
                chosen = preferred = near_labels[idx]
                near = True
                rule_ids.append("R-LABEL-NEAR")
                conf = min(conf, 0.5)
            # Parse each text entity separately; never concatenate different entities into one name.
            names, numbers, stated_areas, notes = [], [], [], []
            per_label: list[tuple[SourceEntity, str | None, str | None]] = []
            for t in chosen:
                lines = split_lines(t.normalized.text)
                keep = [ln for ln in lines if not is_finish_note(ln)]
                notes += [ln for ln in lines if is_finish_note(ln)]
                pt = parse_room_label(keep)
                per_label.append((t, " ".join(pt["name_parts"]) or None, pt["number"]))
                if pt["name_parts"]:
                    names.append(" ".join(pt["name_parts"]))
                if pt["number"]:
                    numbers.append(pt["number"])
                if pt["stated_area_sf"] is not None:
                    stated_areas.append(pt["stated_area_sf"])
            names_u, numbers_u = list(dict.fromkeys(names)), list(dict.fromkeys(numbers))
            ambiguous = len(names_u) > 1 or len(numbers_u) > 1 or len(set(stated_areas)) > 1
            parsed = {"stated_area_sf": stated_areas[0] if len(set(stated_areas)) == 1 else None}
            name = names_u[0] if len(names_u) == 1 else None
            number = numbers_u[0] if len(numbers_u) == 1 else None
            props = {"area_sf": round(poly.area, 2), "perimeter_ft": round(poly.length, 2),
                     "name": None if ambiguous else name, "number": None if ambiguous else number,
                     "detection_method": meth, "label_entity_ids": [t.id for t in chosen]}
            if door_closures:
                props["door_closures"] = door_closures
            if notes:
                props["finish_notes"] = notes
                rule_ids.append("T-FINISH-NOTE")
            verify = False
            subtype = None
            if near:
                ev.append(f"label text {[t.normalized.text for t in chosen]} is OUTSIDE the boundary, within "
                          f"{LABEL_NEAR_FT:g} ft (or 2 text heights) of it and near no other room")
                verify = True
            elif chosen:
                ev.append(f"label text inside boundary: {[t.normalized.text for t in chosen]}")
                if not preferred:
                    ev.append("label text is not on a room-label layer")
                if notes:
                    ev.append(f"finish/annotation notes not used as names: {notes}")
            else:
                ev.append("no label text found inside boundary")
                verify = True
            if ambiguous:
                props["name_candidates"] = names_u
                props["number_candidates"] = numbers_u
                ev.append(f"{len(names_u)} distinct names / {len(numbers_u)} numbers inside ONE physical region: either "
                          "several named spaces share one open region, or rooms were not separated; the region itself "
                          "gets no name and the named spaces are recorded separately with UNRESOLVED boundaries")
                subtype = "suspected_merged_region"
                conf = min(conf, 0.3)
                verify = True
            if parsed["stated_area_sf"] is not None:
                stated = parsed["stated_area_sf"]
                err = abs(stated - poly.area) / poly.area
                props["stated_area_sf"] = round(stated, 2)
                props["stated_vs_computed_error"] = round(err, 4)
                if err <= AREA_LABEL_TOLERANCE:
                    ev.append(f"stated area {stated:,.0f} sf agrees with computed {poly.area:,.0f} sf ({err:.1%})")
                else:
                    ev.append(f"stated area {stated:,.0f} sf DISAGREES with computed {poly.area:,.0f} sf ({err:.1%})")
                    verify = True
            label = None if ambiguous else (" ".join(x for x in (name, number) if x) or None)
            el = self.add("room", conf, ev, rule_ids, list(src) + chosen, geometry=_poly_geom(poly),
                          label=label, properties=props, force_verify=verify, subtype=subtype)
            spaces = self._semantic_spaces(el, poly, per_label, ambiguous, conf, near)
            el.properties["semantic_space_ids"] = [s.id for s in spaces]
            el.properties["semantic_space_names"] = [s.label for s in spaces]
            el.properties["semantic_space_boundaries"] = ("none" if not spaces else
                                                          "unresolved" if ambiguous else "known")
            if ambiguous:
                shown = ", ".join(names_u[:6]) + ("..." if len(names_u) > 6 else "")
                self.out.issues.append(Issue(
                    code="ROOM_BOUNDARY_SPANS_MULTIPLE_LABELS", element_ids=[el.id] + [s.id for s in spaces],
                    message=f"Physical region {el.id} ({poly.area:,.0f} sf) contains {len(names_u)} named spaces ({shown}). "
                            "FireAI cannot tell whether this is one open area shared by several named spaces or rooms "
                            "it failed to separate; the spaces are recorded with UNRESOLVED boundaries (no walls or "
                            "boundaries are invented). Confirm, or draw the space boundaries."))
            if "stated_area_sf" in props and props["stated_vs_computed_error"] > AREA_LABEL_TOLERANCE:
                self.out.issues.append(Issue(code="ROOM_AREA_LABEL_MISMATCH", element_ids=[el.id],
                                             message=f"Room {label or el.id}: stated area {props['stated_area_sf']:,.0f} sf vs "
                                                     f"computed {props['area_sf']:,.0f} sf. Units, scale, or boundary may be wrong."))

        for (poly, src, conf, ev, rule_ids, meth, _dc), inner in areas:
            self.add("area", conf, ev + [f"contains {inner} other boundaries — treated as a gross/overall area, not a room"],
                     rule_ids, src, geometry=_poly_geom(poly), subtype="gross_boundary",
                     properties={"area_sf": round(poly.area, 2), "detection_method": meth})

        unassoc = [t for t in label_role_texts if t.id not in self.claimed]
        if unassoc:
            self.out.issues.append(Issue(code="UNASSOCIATED_ROOM_LABELS", entity_ids=[t.id for t in unassoc],
                                         message=f"{len(unassoc)} room-label text item(s) are not inside any detected room boundary: "
                                                 + ", ".join(repr(t.normalized.text) for t in unassoc[:10])))

    def _symbol_text(self, t: SourceEntity) -> bool:
        """R-SYMBOL-TEXT: text embedded in a symbol block (a switch's "3", a receptacle's "GFI", a
        window tag number) belongs to that symbol, not to a room label. Block text counts as a
        room label only when its layer, or the block's name, says it is a room tag."""
        if not t.parent_id:
            return False
        r = self.role(t)
        if r and r.role in ("room_label", "room_boundary"):
            return False
        p = self.by_id.get(t.parent_id)
        while p is not None:
            pr = self.role(p)
            if pr and pr.role in ("room_label", "room_boundary"):
                return False
            name = (p.attributes.get("effective_block") or p.attributes.get("block") or "").upper()
            if set(R.tokens(name)) & ROOM_TAG_TOKENS:
                return False
            p = self.by_id.get(p.parent_id) if p.parent_id else None
        return True

    def _semantic_spaces(self, region: BuildingElement, poly, per_label, ambiguous: bool, region_conf: float,
                         near: bool) -> list[BuildingElement]:
        """S-SEMANTIC-SPACE: the named/use-defined spaces inside a physical region.

        One label name -> the space coincides with the region (boundary KNOWN = the region's
        boundary). Several names -> one space per distinct name, located only by its label
        anchor(s); boundary UNRESOLVED (the drawing gives no internal boundary, so none is
        invented). Unlabelled regions have no semantic space."""
        by_name: dict[str, list[tuple[SourceEntity, str | None]]] = defaultdict(list)
        for t, name, number in per_label:
            key = name or (f"#{number}" if number else None)
            if key:
                by_name[key].append((t, number))
        out = []
        for key, items in by_name.items():
            texts = [t for t, _n in items]
            nums = list(dict.fromkeys(n for _t, n in items if n))
            name = None if key.startswith("#") else key
            label = " ".join(x for x in (name, nums[0] if len(nums) == 1 else None) if x) or key
            anchors = [list(t.normalized.insert) for t in texts if t.normalized and t.normalized.insert]
            props = {"region_uid": region.uid, "region_id": region.id, "name": name,
                     "number": nums[0] if len(nums) == 1 else None, "label_entity_ids": [t.id for t in texts],
                     "label_anchors_local": anchors,
                     "boundary_state": "unresolved" if ambiguous else "known"}
            if ambiguous:
                geom = Geometry(kind="point", points=[tuple(anchors[0])] if anchors else [], frame="LOCAL")
                ev = [f"label {key!r} inside physical region {region.id}, which holds several named spaces",
                      "space boundary UNRESOLVED: the drawing shows no boundary between these named spaces; only the "
                      "label position is known"]
                conf, verify = min(region_conf, 0.5), True
            else:
                geom = _poly_geom(poly)
                props["area_sf"] = round(poly.area, 2)
                ev = [f"label {key!r} is the only name inside physical region {region.id}; the space boundary is the "
                      "region boundary"]
                conf, verify = region_conf, region.requires_verification or near
            out.append(self.add("space", conf, ev, ["S-SEMANTIC-SPACE"], texts, geometry=geom, label=label,
                                subtype="semantic_space", properties=props, force_verify=verify))
        return out

    def _door_closures(self, walls, wall_lines) -> list[dict]:
        """Analysis-only segments bridging wall openings at detected doors (rule
        G-DOOR-OPENING-CLOSURE). Vertices of wall linework inside a door's footprint
        (+margin) are paired shortest-first within [MIN, MAX] ft; pairs that would cross
        existing wall linework are rejected. Never stored as walls."""
        doors = [el for el in self.out.elements if el.category == "door" and el.geometry and el.geometry.points]
        if not doors:
            return []
        # Keep the EXACT vertex coordinates (rounding is only used as a de-duplication key):
        # a closure endpoint that is even 1e-7 ft off the wall linework is not noded by
        # polygonize and silently fails to separate rooms (found on a real drawing, M1.7).
        exact: dict[tuple, tuple] = {}
        for w in walls:
            for p in (w.geometry.points if w.geometry else []):
                exact.setdefault((round(p[0], 6), round(p[1], 6)), (float(p[0]), float(p[1])))
        verts = list(exact.values())
        wall_union = unary_union(wall_lines)
        out = []
        for d in doors:
            xs = [p[0] for p in d.geometry.points]
            ys = [p[1] for p in d.geometry.points]
            m = DOOR_CLOSURE_MARGIN_FT
            near = [v for v in verts if min(xs) - m <= v[0] <= max(xs) + m and min(ys) - m <= v[1] <= max(ys) + m]
            pairs = sorted((math.dist(a, b), a, b) for i, a in enumerate(near) for b in near[i + 1:]
                           if DOOR_CLOSURE_MIN_FT <= math.dist(a, b) <= DOOR_CLOSURE_MAX_FT)
            used: set = set()
            for _dist, a, b in pairs:
                if a in used or b in used:
                    continue
                seg = LineString([a, b])
                if seg.within(wall_union.buffer(1e-6)):
                    continue  # runs along an existing wall line: not an opening
                inter = seg.intersection(wall_union)
                crossing = False
                if not inter.is_empty:
                    if inter.geom_type not in ("Point", "MultiPoint"):
                        crossing = True
                    else:
                        for q in getattr(inter, "geoms", [inter]):
                            if min(math.dist((q.x, q.y), a), math.dist((q.x, q.y), b)) > 1e-4:
                                crossing = True
                if crossing:
                    continue  # would cross a wall between its endpoints
                used.update((a, b))
                out.append({"door_id": d.id, "line": seg})
        return out

    def _text(self):
        for e in self.all:
            if e.id in self.claimed or not e.visible or e.type not in ("TEXT", "MTEXT") or e.parent_id:
                continue
            g = e.normalized if e.space == "model" else None
            self.add("text_annotation", 1.0, [f"{e.type} entity ({e.space} space)"], ["E-TEXT"], [e],
                     geometry=g, label=(e.source.text if e.source else None),
                     properties={"space": e.space, "layer_role": (self.role(e).role if self.role(e) else None)})

    def _unclassified(self):
        referenced = set(self.claimed)
        out = []
        for e in self.all:
            if e.space != "model" or not e.visible or e.id in referenced:
                continue
            if e.parent_id and e.parent_id not in referenced:
                continue  # represented by its (unclassified) parent INSERT
            if e.type == "INSERT" and any(d.id in referenced for d in self.descendants(e)):
                # partially interpreted block: list its uninterpreted children individually
                out.extend(d.id for d in self.descendants(e) if d.id not in referenced and d.type != "INSERT"
                           and d.visible)
                continue
            out.append(e.id)
        self.out.unclassified_entity_ids = out


# ── geometry utilities ───────────────────────────────────────────────────────

def _length(g: Geometry) -> float:
    pts = g.points
    total = sum(math.dist(pts[i], pts[i + 1]) for i in range(len(pts) - 1)) if len(pts) > 1 else 0.0
    if g.closed and len(pts) > 2:
        total += math.dist(pts[-1], pts[0])
    return total


def _polygon(points) -> tuple[Polygon | None, bool]:
    try:
        poly = Polygon(points)
    except Exception:
        return None, False
    if poly.is_valid:
        return poly, False
    fixed = make_valid(poly)
    polys = [g for g in getattr(fixed, "geoms", [fixed]) if isinstance(g, Polygon)]
    return (max(polys, key=lambda p: p.area), True) if polys else (None, True)


def _poly_geom(poly: Polygon) -> Geometry:
    return Geometry(kind="polygon", points=[(float(x), float(y)) for x, y in list(poly.exterior.coords)[:-1]],
                    closed=True)


def _merge_nested(clusters, pts_fn, tol):
    """G-NESTED-PARTS: a group drawn entirely INSIDE another group's extent on the same layer
    role (a door's glass lite or handle inside the door leaf) is part of that object, not a
    separate one. Returns [(entities, number_of_nested_groups_merged)]."""
    boxes = [_bbox([p for e in c for p in pts_fn(e)]) for c in clusters]
    order = sorted(range(len(clusters)), key=lambda i: -((boxes[i][2] - boxes[i][0]) * (boxes[i][3] - boxes[i][1])))
    owner = {}
    for i in order:
        for j in order:
            if j == i or (boxes[j][2] - boxes[j][0]) * (boxes[j][3] - boxes[j][1]) <= \
                    (boxes[i][2] - boxes[i][0]) * (boxes[i][3] - boxes[i][1]):
                continue
            bj, bi = boxes[j], boxes[i]
            if bj[0] - tol <= bi[0] and bj[1] - tol <= bi[1] and bi[2] <= bj[2] + tol and bi[3] <= bj[3] + tol:
                owner[i] = j
                break
    def root(i):
        while i in owner:
            i = owner[i]
        return i
    groups: dict[int, list[int]] = defaultdict(list)
    for i in range(len(clusters)):
        groups[root(i)].append(i)
    return [([e for k in sorted(ks) for e in clusters[k]], len(ks) - 1) for _r, ks in sorted(groups.items())]


def _cluster(ents, pts_fn, tol):
    boxes = []
    for e in ents:
        p = pts_fn(e)
        if p:
            boxes.append((e, _bbox(p)))
    parent = list(range(len(boxes)))

    def find(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    order = sorted(range(len(boxes)), key=lambda i: boxes[i][1][0])
    active: list[int] = []
    for i in order:
        bx = boxes[i][1]
        active = [j for j in active if boxes[j][1][2] + tol >= bx[0]]
        for j in active:
            by = boxes[j][1]
            if bx[1] <= by[3] + tol and by[1] <= bx[3] + tol:
                parent[find(i)] = find(j)
        active.append(i)
    groups: dict[int, list] = defaultdict(list)
    for i, (e, _) in enumerate(boxes):
        groups[find(i)].append(e)
    # Deterministic order: by the first (lowest-id) entity in each group.
    return sorted(groups.values(), key=lambda g: g[0].id)
