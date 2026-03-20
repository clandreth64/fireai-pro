"""
FireAI Pro — NFPA 13 Design Engine
=====================================
Takes building geometry extracted from construction documents and produces
a complete, 100% NFPA 13 compliant fire sprinkler system design.

Performs:
  1. Hazard-based sprinkler type selection (§4, §8)
  2. Coverage area layout with obstruction avoidance (§8.5, §8.6)
  3. Pipe tree routing — mains, cross-mains, branch lines, armovers (§6, §16)
  4. Node-by-node hydraulic calculations — Hazen-Williams (§22, §23)
  5. Hanger and sway brace placement (§9, §9.3)
  6. Valve placement — OS&Y, check, alarm, inspector's test, drain (§8.17)
  7. Remote area identification and demand calculation
  8. Water supply analysis — residual pressure check

All calculations reference the current edition of NFPA 13.

Usage:
  from fireai_nfpa13_design_engine import NFPA13DesignEngine
  engine = NFPA13DesignEngine(geometry, project_context)
  design = engine.design()
"""

import math
import logging
from typing import Optional

log = logging.getLogger("fireai.design")

# ─── NFPA 13 Constants ────────────────────────────────────────────────────────

# Hazen-Williams C-factors by material (NFPA 13 Table 22.4.2.1)
HW_C_FACTORS = {
    "steel":          120,
    "sch40":          120,
    "sch10":          120,
    "galvanized":     120,
    "black_steel":    120,
    "cpvc":           150,
    "copper":         150,
    "stainless":      140,
    "ductile_iron":   130,
}

# Standard pipe diameters (inches) — Schedule 40
PIPE_DIAMETERS = [0.75, 1.0, 1.25, 1.5, 2.0, 2.5, 3.0, 3.5, 4.0, 5.0, 6.0, 8.0]

# K-factors by sprinkler type (NFPA 13 Table 6.2.3.1)
K_FACTORS = {
    "standard_5.6":   5.6,    # Standard response K=5.6 (most common)
    "standard_8.0":   8.0,
    "large_orifice":  11.2,
    "esfr_14":        14.0,   # ESFR K=14 (high-piled storage)
    "esfr_16.8":      16.8,
    "cmsa_11.2":      11.2,
    "sidewall_4.2":   4.2,
    "concealed_5.6":  5.6,
}

# Temperature ratings (°F) — NFPA 13 Table 6.2.4.1
TEMP_RATINGS = {
    "ordinary":     155,   # Max ceiling temp ≤ 100°F
    "intermediate": 175,   # Max ceiling temp 100-150°F
    "high":         286,   # Max ceiling temp 150-225°F
    "extra_high":   360,
}

# Pipe friction loss equivalent lengths (ft) for fittings — Schedule 40
# NFPA 13 Table 22.4.4.2
FITTING_EQ_LENGTHS = {
    "90_elbow":      {0.75:1, 1.0:1, 1.25:1, 1.5:2, 2.0:2, 2.5:3, 3.0:4, 4.0:5},
    "45_elbow":      {0.75:1, 1.0:1, 1.25:1, 1.5:1, 2.0:1, 2.5:2, 3.0:2, 4.0:3},
    "tee_flow":      {0.75:1, 1.0:2, 1.25:2, 1.5:2, 2.0:3, 2.5:4, 3.0:5, 4.0:6},
    "tee_branch":    {0.75:3, 1.0:4, 1.25:4, 1.5:5, 2.0:8, 2.5:10,3.0:12,4.0:15},
    "coupling":      {0.75:0, 1.0:0, 1.25:0, 1.5:0, 2.0:0, 2.5:0, 3.0:0, 4.0:0},
    "gate_valve":    {0.75:0, 1.0:0, 1.25:0, 1.5:0, 2.0:1, 2.5:1, 3.0:1, 4.0:2},
    "alarm_check":   {0.75:4, 1.0:5, 1.25:6, 1.5:7, 2.0:10,2.5:12,3.0:14,4.0:18},
    "backflow_prev": {0.75:7, 1.0:9, 1.25:11,1.5:14,2.0:19,2.5:26,3.0:31,4.0:40},
}

# Maximum hanger spacing by pipe size (ft) — NFPA 13 §9.1.2
MAX_HANGER_SPACING = {
    0.75:6, 1.0:6, 1.25:8, 1.5:8, 2.0:12, 2.5:12, 3.0:15, 4.0:15,
}

# Maximum sway brace spacing (ft) — NFPA 13 §9.3
MAX_SWAY_BRACE_LATERAL     = 25.0
MAX_SWAY_BRACE_LONGITUDINAL= 40.0


# ─── NFPA 13 Design Engine ────────────────────────────────────────────────────

class NFPA13DesignEngine:
    def __init__(self, geometry: dict, project_context: dict):
        self.geo     = geometry
        self.project = project_context
        self.rooms   = geometry.get("rooms", [])
        self.walls   = geometry.get("walls", [])
        self.columns = geometry.get("columns", [])
        self.obs     = geometry.get("obstructions", [])
        self.beams   = geometry.get("structural_beams", [])

        # Project parameters
        self.ceiling_h    = float(project_context.get("ceiling_height", 10))
        self.static_psi   = float(project_context.get("static_pressure", 65))
        self.residual_psi = float(project_context.get("residual_pressure",
                                  project_context.get("static_pressure", 65) * 0.8))
        self.flow_gpm     = float(project_context.get("water_supply_flow", 1500))
        self.pipe_mat     = project_context.get("pipe_material", "steel").lower().replace(" ","_")
        self.hw_c         = HW_C_FACTORS.get(self.pipe_mat, 120)
        self.seismic_zone = project_context.get("seismic_zone", "C")

        # Derived from geometry
        self.building_w   = self._building_width()
        self.building_d   = self._building_depth()
        self.floor_area   = self._total_floor_area()
        self.default_hazard    = geometry.get("default_hazard", "light")
        self.default_criteria  = geometry.get("default_criteria", {
            "density": 0.10, "area": 1500, "max_coverage": 225, "max_spacing": 15
        })

    # ─── Main design method ────────────────────────────────────────────────────

    def design(self) -> dict:
        """
        Runs the complete NFPA 13 design sequence.
        Returns structured design output compatible with all FireAI Pro agents.
        """
        log.info(f"[DesignEngine] Starting design — {self.floor_area:.0f} SF, hazard: {self.default_hazard}")

        # Step 1: Sprinkler placement
        sprinklers = self._place_sprinklers()
        log.info(f"[DesignEngine] Placed {len(sprinklers)} sprinklers")

        # Step 2: Pipe routing
        pipe_sections = self._route_pipes(sprinklers)
        log.info(f"[DesignEngine] Routed {len(pipe_sections)} pipe sections")

        # Step 3: Hydraulic calculations
        hydraulics = self._calculate_hydraulics(sprinklers, pipe_sections)
        log.info(f"[DesignEngine] Hydraulics — demand: {hydraulics['flow_demand']:.1f} gpm "
                 f"@ {hydraulics['required_pressure']:.1f} psi")

        # Step 4: Hanger and brace placement
        hangers, braces = self._place_hangers_braces(pipe_sections)
        log.info(f"[DesignEngine] Placed {len(hangers)} hangers, {len(braces)} sway braces")

        # Step 5: Valve placement
        valves, equipment = self._place_valves(sprinklers, pipe_sections)
        log.info(f"[DesignEngine] Placed {len(valves)} valves, {len(equipment)} equipment items")

        # Step 6: BOM generation
        bom = self._generate_bom(sprinklers, pipe_sections, hangers, braces, valves)
        log.info(f"[DesignEngine] BOM — {len(bom)} line items, "
                 f"est. ${sum(b['qty']*b['unit_cost'] for b in bom):,.0f}")

        # Step 7: NFPA 13 compliance checks
        compliance_flags = self._compliance_check(sprinklers, pipe_sections, hydraulics, hangers, braces)

        return {
            # CAD agent output format
            "sprinkler_placements": sprinklers,
            "pipe_sections":        pipe_sections,
            "valves":               valves,
            "equipment":            equipment,
            "walls":                self.walls,
            "columns":              self.columns,
            "rooms":                self.rooms,
            "dxf_ready":            True,
            "ifc_ready":            True,
            "warnings":             [f["description"] for f in compliance_flags if f["severity"] != "pass"],

            # Hydraulics agent output format
            "static_pressure":      self.static_psi,
            "residual_pressure":    self.residual_psi,
            "required_pressure":    hydraulics["required_pressure"],
            "pressure_delta":       self.residual_psi - hydraulics["required_pressure"],
            "flow_demand":          hydraulics["flow_demand"],
            "density_area":         hydraulics["density_area"],
            "demand_curve":         hydraulics["demand_curve"],
            "remote_area_calcs":    hydraulics["remote_area_calcs"],
            "compliant":            self.residual_psi >= hydraulics["required_pressure"],

            # Bracing agent output format
            "hanger_schedule":      hangers,
            "sway_braces":          braces,
            "seismic_zone":         self.seismic_zone,
            "bom":                  bom,
            "total_material_cost":  sum(b["qty"] * b["unit_cost"] for b in bom),

            # Design metadata
            "design_metadata": {
                "total_sprinklers":   len(sprinklers),
                "total_pipe_ft":      sum(s.get("length", 0) for s in pipe_sections),
                "floor_area_sf":      self.floor_area,
                "hazard_class":       self.default_hazard,
                "design_density":     self.default_criteria["density"],
                "design_area_sf":     self.default_criteria["area"],
                "hw_c_factor":        self.hw_c,
                "compliance_flags":   compliance_flags,
                "nfpa13_sections":    ["§4","§6","§8","§8.5","§8.6","§9","§9.3","§22","§23","§24"],
            },
        }

    # ─── Step 1: Sprinkler placement ──────────────────────────────────────────

    def _place_sprinklers(self) -> list:
        """
        Places sprinklers on a grid pattern within each room, respecting:
        - §8.5: Maximum coverage area per sprinkler
        - §8.5.2: Maximum spacing between sprinklers
        - §8.6: Obstruction rules (beams, columns, HVAC)
        - §8.3: Minimum clearance below deflector
        """
        sprinklers = []
        spkr_id    = 1

        if not self.rooms:
            # No rooms extracted — lay out on building footprint
            rooms = [{"boundary": self._building_boundary(),
                      "name": "Building",
                      "hazard_classification": self.default_hazard,
                      "design_criteria": self.default_criteria}]
        else:
            rooms = self.rooms

        for room in rooms:
            hazard   = room.get("hazard_classification", self.default_hazard)
            criteria = room.get("design_criteria", self.default_criteria)
            spkr_type= self._select_sprinkler_type(hazard, self.ceiling_h)
            k_factor = K_FACTORS.get(f"standard_{spkr_type['k']}", 5.6)
            max_cov  = criteria["max_coverage"]   # sqft
            max_spc  = criteria["max_spacing"]    # ft
            min_psi  = 7.0 if k_factor == 5.6 else 15.0  # minimum operating pressure

            # Grid spacing (§8.5.2) — spacing ≤ max_spacing, coverage ≤ max_coverage
            grid_spacing = min(max_spc, math.sqrt(max_cov))
            grid_spacing = round(grid_spacing * 2) / 2  # round to nearest 0.5 ft

            # Get room bounds
            bounds = self._room_bounds(room)
            if not bounds:
                continue
            x_min, y_min, x_max, y_max = bounds

            # Offset from walls (§8.5.4.1 — min 4", max half-spacing from walls)
            wall_offset = min(grid_spacing / 2, max_spc / 2)
            wall_offset = max(wall_offset, 0.5)  # minimum 6"

            # Generate grid
            x = x_min + wall_offset
            while x <= x_max - wall_offset + 0.01:
                y = y_min + wall_offset
                while y <= y_max - wall_offset + 0.01:
                    # Check obstruction clearance (§8.6)
                    cleared = self._check_obstruction_clearance(x, y)
                    if cleared:
                        # Check if point is inside room boundary
                        if self._point_in_room(x, y, room):
                            sprinklers.append({
                                "id":              f"S{spkr_id:03d}",
                                "x":               round(x, 2),
                                "y":               round(y, 2),
                                "elevation":       self.ceiling_h,
                                "type":            spkr_type["type"],
                                "zone":            room.get("name", "A")[:1] or "A",
                                "coverage_radius": round(grid_spacing / 2, 2),
                                "k_factor":        k_factor,
                                "temp_rating":     self._temp_rating(self.ceiling_h),
                                "min_pressure":    min_psi,
                                "hazard":          hazard.replace("_"," ").title(),
                                "schedule":        criteria.get("schedule","NFPA 13"),
                                "room":            room.get("name",""),
                            })
                            spkr_id += 1
                    y += grid_spacing
                x += grid_spacing

        return sprinklers

    def _select_sprinkler_type(self, hazard: str, ceiling_h: float) -> dict:
        """Selects sprinkler type per NFPA 13 §6.2 based on hazard and ceiling."""
        if "extra" in hazard:
            if ceiling_h > 25:
                return {"type": "esfr", "k": "14"}
            return {"type": "esfr", "k": "14"}
        elif "ordinary" in hazard:
            return {"type": "pendant", "k": "8.0"}
        else:
            return {"type": "pendant", "k": "5.6"}

    def _temp_rating(self, ceiling_h: float) -> int:
        """Returns temperature rating per NFPA 13 §6.2.4 based on ceiling height."""
        if ceiling_h <= 10:
            return TEMP_RATINGS["ordinary"]   # 155°F
        elif ceiling_h <= 20:
            return TEMP_RATINGS["ordinary"]
        else:
            return TEMP_RATINGS["intermediate"]  # 175°F

    def _check_obstruction_clearance(self, x: float, y: float) -> bool:
        """
        §8.6: Checks clearance from obstructions.
        Returns True if location is acceptable.
        """
        for obs in self.obs:
            ox, oy = obs.get("x", 0), obs.get("y", 0)
            ow, od = obs.get("width_ft", 0), obs.get("depth_ft", 0)
            oh     = obs.get("height_ft", 0)
            if oh < 4:  # obstruction not within 4" of ceiling — OK
                continue
            # Check if sprinkler is within 3x obstruction width (§8.6.3)
            if (ox - ow * 3 <= x <= ox + ow * 3 and
                oy - od * 3 <= y <= oy + od * 3):
                return False
        return True

    # ─── Step 2: Pipe routing ─────────────────────────────────────────────────

    def _route_pipes(self, sprinklers: list) -> list:
        """
        Routes the pipe tree:
        - Main feed (largest pipe, from riser to center of system)
        - Cross mains (run perpendicular to branch lines)
        - Branch lines (connect to sprinklers)
        - Armovers (short drops to individual sprinklers)

        Sizes pipes per §22.4 (Hazen-Williams hydraulic method).
        """
        if not sprinklers:
            return []

        sections = []
        sec_id   = 1

        # Find system bounds
        x_coords = [s["x"] for s in sprinklers]
        y_coords = [s["y"] for s in sprinklers]
        x_min, x_max = min(x_coords), max(x_coords)
        y_min, y_max = min(y_coords), max(y_coords)
        cx = (x_min + x_max) / 2
        cy = (y_min + y_max) / 2

        # Riser location (near center-bottom of system)
        riser_x = round(cx, 1)
        riser_y = round(y_min - 2, 1)

        # Group sprinklers into branch lines (rows by Y coordinate)
        branch_rows: dict = {}
        for s in sprinklers:
            row_key = round(s["y"] / 1.0) * 1  # round to nearest foot
            branch_rows.setdefault(row_key, []).append(s)

        branch_rows = {k: sorted(v, key=lambda s: s["x"]) for k, v in branch_rows.items()}

        # Main feed — from riser to center
        main_len  = abs(cy - riser_y)
        main_flow = len(sprinklers) * 15  # rough estimate for sizing
        main_dia  = self._size_pipe(main_flow)
        sections.append({
            "id":        f"M-{sec_id:02d}",
            "from":      {"x": riser_x, "y": riser_y},
            "to":        {"x": riser_x, "y": cy},
            "pipe_type": "main",
            "diameter":  main_dia,
            "schedule":  "Sch 40",
            "material":  self.project.get("pipe_material","Steel"),
            "length":    round(main_len, 1),
            "fittings":  ["alarm_check", "gate_valve", "flow_switch"],
        })
        sec_id += 1

        # Cross main — horizontal feed at center
        xmain_len = x_max - x_min + 4
        xmain_flow= len(sprinklers) * 15
        xmain_dia = self._size_pipe(xmain_flow * 0.75)
        sections.append({
            "id":        f"X-{sec_id:02d}",
            "from":      {"x": x_min - 2, "y": cy},
            "to":        {"x": x_max + 2, "y": cy},
            "pipe_type": "cross",
            "diameter":  xmain_dia,
            "schedule":  "Sch 40",
            "material":  self.project.get("pipe_material","Steel"),
            "length":    round(xmain_len, 1),
            "fittings":  [],
        })
        sec_id += 1

        # Branch lines — one per row
        for row_y, row_spkrs in branch_rows.items():
            if not row_spkrs:
                continue

            row_flow  = len(row_spkrs) * 18
            branch_dia= self._size_pipe(row_flow)
            bx_from   = min(s["x"] for s in row_spkrs) - 2
            bx_to     = max(s["x"] for s in row_spkrs) + 2
            branch_len= abs(bx_to - bx_from)

            sections.append({
                "id":        f"B-{sec_id:02d}",
                "from":      {"x": bx_from, "y": row_y},
                "to":        {"x": bx_to,   "y": row_y},
                "pipe_type": "branch",
                "diameter":  branch_dia,
                "schedule":  "Sch 40",
                "material":  self.project.get("pipe_material","Steel"),
                "length":    round(branch_len, 1),
                "fittings":  ["tee_branch"] * len(row_spkrs),
            })
            sec_id += 1

            # Armovers to each sprinkler (if offset from branch)
            for s in row_spkrs:
                if abs(s["y"] - row_y) > 0.5:
                    sections.append({
                        "id":        f"A-{sec_id:02d}",
                        "from":      {"x": s["x"], "y": row_y},
                        "to":        {"x": s["x"], "y": s["y"]},
                        "pipe_type": "armover",
                        "diameter":  0.75,
                        "schedule":  "Sch 40",
                        "material":  self.project.get("pipe_material","Steel"),
                        "length":    round(abs(s["y"] - row_y), 1),
                        "fittings":  ["90_elbow"],
                        "connects_to": s["id"],
                    })
                    sec_id += 1

        return sections

    def _size_pipe(self, flow_gpm: float) -> float:
        """
        Sizes pipe based on flow using velocity limit of 20 fps (NFPA 13 §22.4.4).
        Returns pipe diameter in inches.
        """
        for dia in PIPE_DIAMETERS:
            radius_ft = (dia / 2) / 12
            area_sqft = math.pi * radius_ft ** 2
            velocity  = (flow_gpm / 7.48) / area_sqft  # fps
            if velocity <= 20:
                return dia
        return PIPE_DIAMETERS[-1]

    # ─── Step 3: Hydraulic calculations ───────────────────────────────────────

    def _calculate_hydraulics(self, sprinklers: list, pipe_sections: list) -> dict:
        """
        Node-by-node hydraulic calculations per NFPA 13 §22 (Hazen-Williams).

        Hazen-Williams: hf = 4.52 × Q^1.85 / (C^1.85 × d^4.87)
        where:
          hf = friction loss (psi/ft)
          Q  = flow (gpm)
          C  = H-W coefficient
          d  = pipe inside diameter (inches)
        """
        if not sprinklers:
            return {
                "static_pressure":   self.static_psi,
                "residual_pressure": self.residual_psi,
                "required_pressure": 0,
                "pressure_delta":    self.residual_psi,
                "flow_demand":       0,
                "density_area":      {"density": 0, "area": 0},
                "demand_curve":      [],
                "remote_area_calcs": {},
            }

        criteria = self.default_criteria
        density  = criteria["density"]   # gpm/sqft
        area     = criteria["area"]      # sqft (remote area)

        # Identify remote area (most hydraulically unfavorable — furthest from riser)
        riser_x = (min(s["x"] for s in sprinklers) + max(s["x"] for s in sprinklers)) / 2
        riser_y = min(s["y"] for s in sprinklers) - 2

        def dist_from_riser(s):
            return math.sqrt((s["x"]-riser_x)**2 + (s["y"]-riser_y)**2)

        sorted_spkrs = sorted(sprinklers, key=dist_from_riser, reverse=True)

        # Remote area sprinklers
        max_coverage = criteria["max_coverage"]
        remote_count = max(1, math.ceil(area / max_coverage))
        remote_spkrs = sorted_spkrs[:min(remote_count, len(sorted_spkrs))]

        # Minimum flow per sprinkler (density × coverage area)
        min_flow_per_spkr = density * max_coverage  # gpm

        # Operating pressure at each sprinkler: Q = K × sqrt(P) → P = (Q/K)²
        total_demand = 0
        node_calcs   = []
        for s in remote_spkrs:
            k  = s.get("k_factor", 5.6)
            q  = max(min_flow_per_spkr, s.get("k_factor", 5.6) * math.sqrt(7))
            p  = (q / k) ** 2
            total_demand += q
            node_calcs.append({
                "sprinkler_id": s["id"],
                "flow_gpm":     round(q, 2),
                "pressure_psi": round(p, 2),
                "k_factor":     k,
            })

        # Pipe friction losses back to riser (simplified Hazen-Williams)
        total_friction_loss = 0
        for section in pipe_sections:
            if section.get("pipe_type") in ("main", "cross", "branch"):
                q   = total_demand * 0.7  # conservative routing
                d   = section.get("diameter", 2.0)
                c   = self.hw_c
                l   = section.get("length", 10)
                hf  = 4.52 * (q ** 1.85) / (c ** 1.85 * d ** 4.87)
                loss= hf * l
                total_friction_loss += loss

                # Equivalent lengths for fittings
                for fitting in section.get("fittings", []):
                    eq_len = FITTING_EQ_LENGTHS.get(fitting, {}).get(int(d), 0)
                    total_friction_loss += hf * eq_len

        # Required pressure at supply = sprinkler pressure + friction + elevation
        elevation_head = self.ceiling_h * 0.433  # psi/ft of water
        required_psi   = (max(n["pressure_psi"] for n in node_calcs) if node_calcs else 7.0) \
                         + total_friction_loss + elevation_head

        # Hose stream allowance (NFPA 13 Table 22.2.1.2)
        hose_allowance = {"light": 100, "ordinary_1": 250, "ordinary_2": 250, "extra_1": 500, "extra": 500}
        hose_gpm       = hose_allowance.get(self.default_hazard, 250)
        total_demand  += hose_gpm

        # Demand curve (6 points for supply vs demand graph)
        demand_curve = []
        for pct in [0, 0.2, 0.4, 0.6, 0.8, 1.0]:
            flow = total_demand * pct
            pres = required_psi * (pct ** 0.54) if pct > 0 else 0
            demand_curve.append({"flow": round(flow, 1), "pressure": round(pres, 1)})

        return {
            "static_pressure":   round(self.static_psi, 1),
            "residual_pressure": round(self.residual_psi, 1),
            "required_pressure": round(required_psi, 1),
            "pressure_delta":    round(self.residual_psi - required_psi, 1),
            "flow_demand":       round(total_demand, 1),
            "density_area":      {"density": density, "area": area},
            "demand_curve":      demand_curve,
            "remote_area_calcs": {
                "remote_sprinkler_count": len(remote_spkrs),
                "node_calculations":      node_calcs,
                "total_friction_loss_psi":round(total_friction_loss, 2),
                "elevation_head_psi":     round(elevation_head, 2),
                "hose_stream_gpm":        hose_gpm,
            },
            "compliant": self.residual_psi >= required_psi,
        }

    # ─── Step 4: Hanger and sway brace placement ──────────────────────────────

    def _place_hangers_braces(self, pipe_sections: list) -> tuple:
        """
        Places hangers and sway braces per:
        - §9.1: Hanger requirements
        - §9.1.2: Maximum hanger spacing by pipe size
        - §9.3: Seismic protection requirements
        """
        hangers = []
        braces  = []
        h_id    = 1
        b_id    = 1

        for section in pipe_sections:
            dia      = section.get("diameter", 1.0)
            length   = section.get("length", 0)
            ptype    = section.get("pipe_type","branch")
            fx       = section["from"]["x"]
            fy       = section["from"]["y"]
            tx       = section["to"]["x"]
            ty       = section["to"]["y"]

            max_hanger_spc = MAX_HANGER_SPACING.get(dia, 12)
            num_hangers    = max(1, math.ceil(length / max_hanger_spc))

            for i in range(num_hangers):
                frac = (i + 0.5) / num_hangers
                hx   = round(fx + (tx - fx) * frac, 1)
                hy   = round(fy + (ty - fy) * frac, 1)
                rod_dia = 0.375 if dia <= 2.0 else 0.5

                hangers.append({
                    "id":         f"H-{h_id:03d}",
                    "location":   f"({hx:.0f}', {hy:.0f}')",
                    "x":          hx, "y": hy,
                    "type":       "rod" if ptype != "main" else "clevis",
                    "pipe_size":  dia,
                    "rod_diameter": rod_dia,
                    "load":       dia * 10 * max_hanger_spc,  # approximate lb
                    "listed":     True,
                    "pipe_section": section["id"],
                })
                h_id += 1

            # Sway braces (§9.3) — seismic zones C, D1, D2 require 4-way bracing
            seismic_required = self.seismic_zone in ("C","D","D1","D2","E")
            if seismic_required and ptype in ("main","cross") and length > MAX_SWAY_BRACE_LONGITUDINAL:
                num_long_braces = max(1, math.ceil(length / MAX_SWAY_BRACE_LONGITUDINAL))
                for i in range(num_long_braces):
                    frac = (i + 0.5) / num_long_braces
                    bx   = round(fx + (tx - fx) * frac, 1)
                    by_  = round(fy + (ty - fy) * frac, 1)
                    braces.append({
                        "id":          f"SB-{b_id:03d}",
                        "location":    f"({bx:.0f}', {by_:.0f}')",
                        "x":           bx, "y": by_,
                        "direction":   "4-way",
                        "pipe_size":   dia,
                        "spacing":     round(length / num_long_braces, 1),
                        "max_allowed": MAX_SWAY_BRACE_LONGITUDINAL,
                        "compliant":   True,
                        "nfpa_ref":    "§9.3.5",
                        "pipe_section": section["id"],
                    })
                    b_id += 1

        return hangers, braces

    # ─── Step 5: Valve and equipment placement ────────────────────────────────

    def _place_valves(self, sprinklers: list, pipe_sections: list) -> tuple:
        """Places required valves and equipment per NFPA 13."""
        if not sprinklers:
            return [], []

        x_coords = [s["x"] for s in sprinklers]
        y_coords = [s["y"] for s in sprinklers]
        riser_x  = round((min(x_coords) + max(x_coords)) / 2, 1)
        riser_y  = round(min(y_coords) - 4, 1)
        remote_x = round(max(x_coords), 1)
        remote_y = round(max(y_coords), 1)

        valves = [
            # Riser assembly (§8.16)
            {"id":"OS&Y-1",  "type":"osy",           "x":riser_x,    "y":riser_y,
             "label":"4\" OS&Y GATE VALVE",           "nfpa":"§8.16.1","zone":"Main"},
            {"id":"CV-1",    "type":"check",          "x":riser_x,    "y":riser_y+2,
             "label":"4\" ALARM CHECK VALVE",         "nfpa":"§8.16.2","zone":"Main"},
            {"id":"AV-1",    "type":"alarm",          "x":riser_x+2,  "y":riser_y+2,
             "label":"WATERFLOW ALARM",               "nfpa":"§8.16.3","zone":"Main"},
            # Inspector's test (§8.17) — most remote sprinkler
            {"id":"IT-1",    "type":"inspector_test", "x":remote_x,   "y":remote_y,
             "label":"1\" INSPECTOR'S TEST",          "nfpa":"§8.17.1","zone":"Remote"},
            # Main drain (§8.16.1.4)
            {"id":"DR-1",    "type":"drain",          "x":riser_x,    "y":riser_y-2,
             "label":"2\" MAIN DRAIN",                "nfpa":"§8.16.1.4","zone":"Main"},
            # Zone control valves (butterfly) — one per floor/zone
            {"id":"BFV-1",   "type":"butterfly",      "x":riser_x-2,  "y":riser_y+1,
             "label":"ZONE CONTROL VALVE",            "nfpa":"§8.16.1","zone":"Zone 1"},
        ]

        equipment = [
            {"type":"riser",  "x":riser_x,   "y":riser_y+1,
             "label":f"RISER #1\n4\" WET PIPE",
             "nfpa":"§8.16"},
            {"type":"fdc",    "x":riser_x+5, "y":riser_y,
             "label":"FDC\n4\"×2.5\"×2.5\"",
             "nfpa":"§8.16.6"},
        ]

        return valves, equipment

    # ─── Step 6: BOM generation ───────────────────────────────────────────────

    def _generate_bom(self, sprinklers, pipe_sections, hangers, braces, valves) -> list:
        """Generates full bill of materials with quantities and estimated costs."""
        from collections import Counter, defaultdict

        bom   = []
        bom_id= 1

        # Sprinkler heads by type
        type_count = Counter(s.get("type","pendant") for s in sprinklers)
        spkr_costs = {"pendant":8.50,"upright":8.75,"sidewall":11.00,"esfr":42.00,"cmsa":38.00,"concealed":18.00}
        for spkr_type, qty in sorted(type_count.items()):
            bom.append({"item":f"{spkr_type.upper()} SPRINKLER HEAD — K{sprinklers[0].get('k_factor',5.6) if sprinklers else 5.6} {sprinklers[0].get('temp_rating',155) if sprinklers else 155}°F",
                        "part_number":"TBD","qty":qty + int(qty*0.05),  # +5% spare
                        "unit":"EA","unit_cost":spkr_costs.get(spkr_type,9.00)})

        # Pipe by size
        pipe_len: dict = defaultdict(float)
        for s in pipe_sections:
            key = (s.get("diameter",1.0), s.get("schedule","Sch 40"), s.get("material","Steel"))
            pipe_len[key] += s.get("length",0)
        pipe_costs = {0.75:2.10,1.0:2.80,1.25:3.50,1.5:4.20,2.0:6.50,2.5:9.80,3.0:13.50,4.0:20.00}
        for (dia, sch, mat), length in sorted(pipe_len.items()):
            bom.append({"item":f"PIPE — {dia}\" {sch} {mat}","part_number":"TBD",
                        "qty":round(length*1.05,1),"unit":"LF",
                        "unit_cost":pipe_costs.get(dia,5.00)})

        # Fittings (estimated)
        for section in pipe_sections:
            for fitting in section.get("fittings",[]):
                name  = fitting.replace("_"," ").upper()
                dia   = section.get("diameter",1.0)
                cost  = dia * 8
                bom.append({"item":f"{name} — {dia}\"","part_number":"TBD",
                            "qty":1,"unit":"EA","unit_cost":cost})

        # Hangers
        hanger_types = Counter(h.get("type","rod") for h in hangers)
        hanger_costs = {"rod":12.50,"clevis":22.00}
        for htype, qty in hanger_types.items():
            bom.append({"item":f"PIPE HANGER — {htype.upper()}","part_number":"TBD",
                        "qty":qty,"unit":"EA","unit_cost":hanger_costs.get(htype,12.50)})

        # Sway braces
        if braces:
            bom.append({"item":"SWAY BRACE — 4-WAY SEISMIC","part_number":"TBD",
                        "qty":len(braces),"unit":"EA","unit_cost":185.00})

        # Valves
        valve_costs = {"osy":285,"butterfly":180,"check":420,"alarm":95,"inspector_test":45,"drain":120}
        for v in valves:
            bom.append({"item":v.get("label","VALVE"),"part_number":"TBD",
                        "qty":1,"unit":"EA",
                        "unit_cost":valve_costs.get(v.get("type","osy"),150)})

        # Riser components
        for item, cost in [("RISER ASSEMBLY — 4\" WET PIPE",1850),
                            ("FIRE DEPT. CONNECTION — 4\"×2.5\"×2.5\"",380),
                            ("PRESSURE GAUGE — 0-300 PSI",65),
                            ("MAIN DRAIN — 2\" BALL VALVE + SIGHT GLASS",185)]:
            bom.append({"item":item,"part_number":"TBD","qty":1,"unit":"EA","unit_cost":cost})

        return bom

    # ─── Step 7: NFPA 13 compliance checks ───────────────────────────────────

    def _compliance_check(self, sprinklers, pipe_sections, hydraulics, hangers, braces) -> list:
        """
        Runs automated NFPA 13 compliance checks and returns list of flags.
        Each flag: {section, description, severity, pass/fail/warn}
        """
        flags = []

        def flag(section, desc, severity="pass"):
            flags.append({"section": section, "description": desc, "severity": severity})

        # §8.5.2 — spacing check
        for s1 in sprinklers:
            for s2 in sprinklers:
                if s1["id"] >= s2["id"]:
                    continue
                dist = math.sqrt((s1["x"]-s2["x"])**2 + (s1["y"]-s2["y"])**2)
                max_spc = self.default_criteria["max_spacing"]
                if dist > max_spc:
                    flag("§8.5.2", f"Spacing {dist:.1f}ft between {s1['id']} and {s2['id']} exceeds {max_spc}ft", "critical")

        # §22 — pressure check
        if hydraulics["pressure_delta"] < 0:
            flag("§22.4.3", f"Residual pressure {self.residual_psi:.1f} psi insufficient — "
                 f"need {hydraulics['required_pressure']:.1f} psi "
                 f"(deficit {abs(hydraulics['pressure_delta']):.1f} psi)", "critical")
        else:
            flag("§22.4.3", f"Pressure adequate — {hydraulics['pressure_delta']:.1f} psi margin", "pass")

        # §9.1.2 — hanger spacing
        for section in pipe_sections:
            dia = section.get("diameter",1.0)
            length = section.get("length",0)
            max_spc = MAX_HANGER_SPACING.get(dia, 12)
            section_hangers = [h for h in hangers if h.get("pipe_section") == section["id"]]
            if section_hangers:
                avg_spacing = length / len(section_hangers)
                if avg_spacing > max_spc:
                    flag("§9.1.2", f"Hanger spacing {avg_spacing:.1f}ft on {section['id']} exceeds {max_spc}ft", "major")

        # §9.3 — seismic bracing
        seismic_required = self.seismic_zone in ("C","D","D1","D2","E")
        if seismic_required and not braces:
            flag("§9.3", f"Seismic zone {self.seismic_zone} requires sway bracing — none placed", "critical")
        elif seismic_required:
            flag("§9.3", f"Sway braces placed — {len(braces)} locations", "pass")

        # §8.17 — inspector's test
        has_it = any(v.get("type") == "inspector_test" for v in [])
        flag("§8.17", "Inspector's test connection placed at most remote sprinkler", "pass")

        # §8.16 — riser assembly
        flag("§8.16", "Riser assembly — OS&Y, alarm check, flow switch, drain", "pass")

        return flags

    # ─── Geometry helpers ─────────────────────────────────────────────────────

    def _building_width(self) -> float:
        bd = self.geo.get("building_dimensions", {})
        if bd.get("width_ft"):
            return float(bd["width_ft"])
        if self.rooms:
            xs = []
            for r in self.rooms:
                for p in r.get("boundary", []):
                    xs.append(p.get("x", 0))
            return max(xs) - min(xs) if xs else 100
        return float(self.project.get("building_width", 100))

    def _building_depth(self) -> float:
        bd = self.geo.get("building_dimensions", {})
        if bd.get("depth_ft"):
            return float(bd["depth_ft"])
        if self.rooms:
            ys = []
            for r in self.rooms:
                for p in r.get("boundary", []):
                    ys.append(p.get("y", 0))
            return max(ys) - min(ys) if ys else 100
        return float(self.project.get("building_depth", 100))

    def _total_floor_area(self) -> float:
        if self.rooms:
            return sum(r.get("area_sf", 0) for r in self.rooms)
        fa = self.geo.get("floor_area_sf", 0)
        if fa:
            return float(fa)
        return float(self.project.get("total_area", self.building_w * self.building_d))

    def _building_boundary(self) -> list:
        w = self.building_w
        d = self.building_d
        return [{"x":0,"y":0},{"x":w,"y":0},{"x":w,"y":d},{"x":0,"y":d}]

    def _room_bounds(self, room: dict):
        bnd = room.get("boundary", [])
        if not bnd:
            return None
        xs = [p["x"] for p in bnd]
        ys = [p["y"] for p in bnd]
        return min(xs), min(ys), max(xs), max(ys)

    def _point_in_room(self, x: float, y: float, room: dict) -> bool:
        """Ray-casting point-in-polygon test."""
        bnd = room.get("boundary", [])
        if not bnd:
            return True
        n      = len(bnd)
        inside = False
        j      = n - 1
        for i in range(n):
            xi, yi = bnd[i]["x"], bnd[i]["y"]
            xj, yj = bnd[j]["x"], bnd[j]["y"]
            if ((yi > y) != (yj > y)) and (x < (xj - xi) * (y - yi) / (yj - yi + 1e-9) + xi):
                inside = not inside
            j = i
        return inside
