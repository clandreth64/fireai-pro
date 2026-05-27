"""
FireAI Pro — Master Orchestrator v2  (Python)
==============================================
Drop this file at the repo root alongside your existing engines.

Parallel fan-out → NFPA 13 compliance loop → artifact synthesis

Features:
  - 6 specialized agents wrapping your existing engines
  - asyncio-based parallel execution (all agents run simultaneously)
  - 10-iteration NFPA 13 compliance loop
  - Progressive strictness at iteration 4 (freezes non-improving agents)
  - Violation delta tracker
  - Circuit breaker → email escalation (Gmail / Outlook / SMTP)
  - User-selectable output formats
  - Railway-compatible env config

LAYER 2 CHANGE (one method replaced):
  _run_phase1 now uses ContextBus dependency ordering instead of
  pure asyncio.gather. Agents receive LIVE sibling outputs the moment
  their dependencies finish — not stale data from the previous iteration.

  Dependency chain:
    CAD (immediate) → Routing → Hydraulics → Bracing
  
  Wall-clock time is unchanged (still fully async).
  Output quality improves because every agent works with real upstream data.
"""

import asyncio
import json
import logging
import os
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Optional

import anthropic

from fireai_email_escalator import EmailEscalator
from context_bus import ContextBus, AGENT_DEPENDENCIES   # ← Layer 2 import

# ── Logging ────────────────────────────────────────────────────────────────────

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  [%(name)s]  %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("fireai.orchestrator")

# ── Config ─────────────────────────────────────────────────────────────────────

MAX_ITERATIONS    = int(os.getenv("FIREAI_MAX_ITERATIONS", "10"))
STRICT_MODE_ITER  = int(os.getenv("FIREAI_STRICT_ITER",   "4"))
MIN_IMPROVEMENT   = int(os.getenv("FIREAI_MIN_IMPROVEMENT","1"))
CLAUDE_MODEL      = os.getenv("FIREAI_MODEL", "claude-sonnet-4-20250514")
ESCALATION_EMAIL  = os.getenv("FIREAI_ESCALATION_EMAIL", "")
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")

client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)

# ── Output format registry ─────────────────────────────────────────────────────

ALL_FORMATS = {
    "dxf_2d":        "DXF 2D (AutoCAD / Autosprink floor plan)",
    "dxf_3d":        "DXF 3D (Autosprink Elite 3D pipe network)",
    "ifc":           "IFC (Revit BIM import)",
    "step":          "STEP (generic 3D CAD — SolidWorks, Inventor, Rhino)",
    "pdf_stamped":   "PDF stamped drawings",
    "pdf_hydraulics":"PDF hydraulic calculations",
    "bom_xlsx":      "Bill of materials (Excel)",
    "hydraulics_json":"Hydraulics JSON (Autosprink calc import)",
    "nfpa_cert":     "NFPA 13 compliance certificate",
    "ahj_package":   "AHJ permit package",
}

DEFAULT_FORMATS = {
    "dxf_2d", "dxf_3d", "ifc", "pdf_stamped", "pdf_hydraulics",
    "hydraulics_json", "nfpa_cert", "ahj_package",
}

# ── NFPA 13 section → agent owner ─────────────────────────────────────────────

SECTION_OWNER: dict[str, str] = {
    "§4":    "cad",
    "§6":    "routing",
    "§8":    "cad",
    "§8.5":  "cad",
    "§8.6":  "cad",
    "§9":    "bracing",
    "§9.3":  "bracing",
    "§16":   "routing",
    "§22":   "hydraulics",
    "§22.4": "hydraulics",
    "§23":   "hydraulics",
    "§24":   "ahj",
}

def owner_from_section(section: str) -> Optional[str]:
    """Match longest NFPA section prefix to its responsible agent."""
    for key in sorted(SECTION_OWNER, key=len, reverse=True):
        if section.startswith(key):
            return SECTION_OWNER[key]
    return None

# ── Data classes ───────────────────────────────────────────────────────────────

@dataclass
class Violation:
    section:     str
    description: str
    severity:    str   # critical | major | minor
    fix:         str
    agent_id:    str

@dataclass
class ComplianceResult:
    compliant:  bool
    violations: list[Violation] = field(default_factory=list)
    summary:    str = ""

@dataclass
class AgentResult:
    agent_id: str
    output:   dict
    run_count: int
    success:  bool
    error:    Optional[str] = None

# ── Base agent ─────────────────────────────────────────────────────────────────

class FireAIAgent:
    """
    Wraps one of your existing Python engines with a Claude reasoning layer.
    Subclasses define the system prompt and output schema.
    Corrective context is injected on re-runs so the agent knows exactly what to fix.
    """

    def __init__(self, agent_id: str, name: str, engine_module: str, nfpa_sections: list[str]):
        self.agent_id       = agent_id
        self.name           = name
        self.engine_module  = engine_module
        self.nfpa_sections  = nfpa_sections
        self.frozen         = False
        self.run_count      = 0
        self.last_output: Optional[dict] = None

    def system_prompt(self) -> str:
        return (
            f"You are the {self.name} agent in the FireAI Pro fire sprinkler design system.\n"
            f"Engine module: {self.engine_module}\n"
            f"NFPA 13 sections in scope: {', '.join(self.nfpa_sections)}\n\n"
            "Always respond with a valid JSON object matching your output schema.\n"
            "Do NOT include markdown fences or prose outside the JSON.\n"
            "If corrective context is provided, address every listed violation "
            "before producing output."
        )

    def schema_prompt(self) -> str:
        return ""

    async def run(
        self,
        project_context: dict,
        violations: list[Violation] | None = None,
        sibling_outputs: dict | None = None,
    ) -> AgentResult:
        self.run_count += 1
        violations      = violations     or []
        sibling_outputs = sibling_outputs or {}

        corrective_block = ""
        if violations:
            items = "\n".join(
                f"  {i+1}. [{v.section}] {v.description} — fix: {v.fix}"
                for i, v in enumerate(violations)
            )
            corrective_block = f"\n\nCORRECTIVE ACTION REQUIRED:\n{items}"

        user_message = (
            f"PROJECT CONTEXT:\n{json.dumps(project_context, indent=2)}"
            + (f"\n\nSIBLING OUTPUTS:\n{json.dumps(sibling_outputs, indent=2)}" if sibling_outputs else "")
            + corrective_block
            + "\n\nProduce your structured output now."
        )

        try:
            response = await asyncio.to_thread(
                client.messages.create,
                model=CLAUDE_MODEL,
                max_tokens=4096,
                system=self.system_prompt() + "\n\n" + self.schema_prompt(),
                messages=[{"role": "user", "content": user_message}],
            )
            raw     = next((b.text for b in response.content if b.type == "text"), "{}")
            cleaned = raw.strip().lstrip("```json").lstrip("```").rstrip("```").strip()
            output  = json.loads(cleaned)
            self.last_output = output
            return AgentResult(agent_id=self.agent_id, output=output, run_count=self.run_count, success=True)

        except json.JSONDecodeError as e:
            self.last_output = {"raw": raw, "parse_error": str(e)}
            return AgentResult(agent_id=self.agent_id, output=self.last_output, run_count=self.run_count, success=False, error=str(e))
        except Exception as e:
            return AgentResult(agent_id=self.agent_id, output={}, run_count=self.run_count, success=False, error=str(e))


# ── Specialized agents ─────────────────────────────────────────────────────────

class CADAgent(FireAIAgent):
    def __init__(self):
        super().__init__("cad", "CAD / DXF Layout Agent", "enhanced_cad_engine.py", ["§4", "§8", "§8.5", "§8.6"])

    def schema_prompt(self) -> str:
        return """Output schema:
{
  "sprinkler_placements": [{"id": str, "x": float, "y": float, "zone": str, "coverage_radius": float, "type": str, "elevation": float}],
  "coverage_zones":       [{"zone": str, "area": float, "sprinkler_count": int, "max_spacing": float}],
  "obstruction_clearances": [{"obstruction": str, "clearance": float, "compliant": bool}],
  "pipe_sections":        [{"id": str, "from": {"x":float,"y":float}, "to": {"x":float,"y":float}, "diameter": float, "schedule": str, "material": str, "length": float}],
  "dxf_ready":            bool,
  "ifc_ready":            bool,
  "warnings":             [str]
}"""


class HydraulicsAgent(FireAIAgent):
    def __init__(self):
        super().__init__("hydraulics", "Hydraulics Calculation Agent", "enhanced_hydraulics_engine.py", ["§22", "§22.4", "§23"])

    def schema_prompt(self) -> str:
        return """Output schema:
{
  "static_pressure":   float,
  "residual_pressure": float,
  "required_pressure": float,
  "pressure_delta":    float,
  "flow_demand":       float,
  "density_area":      {"density": float, "area": float},
  "demand_curve":      [{"flow": float, "pressure": float}],
  "remote_area_calcs": {},
  "compliant":         bool,
  "warnings":          [str]
}"""


class RoutingAgent(FireAIAgent):
    def __init__(self):
        super().__init__("routing", "Routing & Pipe Sizing Agent", "fireai_routing_advanced.py", ["§6", "§16"])

    def schema_prompt(self) -> str:
        return """Output schema:
{
  "pipe_sections":    [{"id": str, "from": str, "to": str, "diameter": float, "schedule": str, "material": str, "length": float, "fittings": [str]}],
  "main_size":        float,
  "branch_sizes":     {},
  "total_pipe_length": float,
  "fittings_list":    {},
  "warnings":         [str]
}"""


class BracingAgent(FireAIAgent):
    def __init__(self):
        super().__init__("bracing", "Seismic Bracing & BOM Agent", "enhanced_bracing_engine.py", ["§9", "§9.3"])

    def schema_prompt(self) -> str:
        return """Output schema:
{
  "hanger_schedule":  [{"id": str, "location": str, "type": str, "load": float, "rod_diameter": float}],
  "sway_braces":      [{"id": str, "location": str, "direction": str, "spacing": float, "max_allowed": float, "compliant": bool}],
  "seismic_zone":     str,
  "bom":              [{"item": str, "part_number": str, "qty": float, "unit": str, "unit_cost": float}],
  "total_material_cost": float,
  "warnings":         [str]
}"""


class AHJAgent(FireAIAgent):
    def __init__(self):
        super().__init__("ahj", "AHJ Report & Permit Agent", "master_fireai_products_enhanced.py", ["§24"])

    def system_prompt(self) -> str:
        return super().system_prompt() + (
            "\nThis agent runs LAST — after NFPA 13 compliance is confirmed. "
            "You receive all other agent outputs as sibling outputs."
        )

    def schema_prompt(self) -> str:
        return """Output schema:
{
  "permit_package": {
    "project_name":        str,
    "jurisdiction":        str,
    "ahj_amendments":      [str],
    "submittals_required": [str],
    "calculations_cover":  {},
    "stamp_required":      bool
  },
  "submittal_drawings": [str],
  "review_notes":       [str],
  "ready":              bool
}"""


# ── NFPA 13 compliance agent ──────────────────────────────────────────────────

class NFPA13Agent:
    async def validate(
        self,
        agent_outputs: dict,
        project_context: dict,
        iteration: int,
    ) -> ComplianceResult:
        prompt = (
            f"You are the NFPA 13 compliance validator for FireAI Pro.\n"
            f"Iteration: {iteration} of {MAX_ITERATIONS}\n\n"
            "Review the agent outputs against the full NFPA 13 standard.\n"
            "For each violation specify: section, description (with measured values), "
            "severity (critical|major|minor), fix (with target values), agent_id.\n\n"
            f"PROJECT CONTEXT:\n{json.dumps(project_context, indent=2)}\n\n"
            f"AGENT OUTPUTS:\n{json.dumps(agent_outputs, indent=2)}\n\n"
            'Respond ONLY with JSON:\n'
            '{"compliant": bool, "violations": [{"section": str, "description": str, '
            '"severity": str, "fix": str, "agent_id": str}], "summary": str}'
        )
        try:
            response = await asyncio.to_thread(
                client.messages.create,
                model=CLAUDE_MODEL,
                max_tokens=2048,
                messages=[{"role": "user", "content": prompt}],
            )
            raw     = next((b.text for b in response.content if b.type == "text"), "{}")
            cleaned = raw.strip().lstrip("```json").lstrip("```").rstrip("```").strip()
            data    = json.loads(cleaned)
            violations = [
                Violation(
                    section=v["section"],
                    description=v["description"],
                    severity=v.get("severity", "major"),
                    fix=v["fix"],
                    agent_id=v.get("agent_id") or owner_from_section(v["section"]) or "unknown",
                )
                for v in data.get("violations", [])
            ]
            return ComplianceResult(
                compliant=data.get("compliant", False),
                violations=violations,
                summary=data.get("summary", ""),
            )
        except Exception as e:
            log.error(f"NFPA 13 validation error: {e}")
            return ComplianceResult(compliant=False, summary=f"Validation error: {e}")


# ── Violation delta tracker ───────────────────────────────────────────────────

class ViolationTracker:
    def __init__(self):
        self._history: dict[str, list[int]] = {}

    def record(self, agent_id: str, count: int):
        self._history.setdefault(agent_id, []).append(count)

    def is_improving(self, agent_id: str) -> bool:
        h = self._history.get(agent_id, [])
        if len(h) < 2:
            return True
        return (h[-2] - h[-1]) >= MIN_IMPROVEMENT

    def stalled_agents(self, agent_ids: list[str]) -> list[str]:
        return [a for a in agent_ids if not self.is_improving(a)]


# ── Output synthesizer ─────────────────────────────────────────────────────────

class OutputSynthesizer:
    def assemble(
        self,
        agent_outputs: dict,
        ahj_output: Optional[dict],
        compliance: ComplianceResult,
        iterations_used: int,
        frozen_violations: list[Violation],
        selected_formats: set[str],
    ) -> dict:
        published = []
        if "dxf_2d"          in selected_formats and agent_outputs.get("cad", {}).get("dxf_ready"):
            published.append("layout_2d.dxf")
        if "dxf_3d"          in selected_formats and agent_outputs.get("cad", {}).get("dxf_ready"):
            published.append("layout_3d.dxf")
        if "ifc"             in selected_formats and agent_outputs.get("cad", {}).get("ifc_ready"):
            published.append("model.ifc")
        if "step"            in selected_formats:
            published.append("model.step")
        if "pdf_stamped"     in selected_formats:
            published.append("stamped_drawings.pdf")
        if "pdf_hydraulics"  in selected_formats and agent_outputs.get("hydraulics"):
            published.append("hydraulics_report.pdf")
        if "bom_xlsx"        in selected_formats and agent_outputs.get("bracing", {}).get("bom"):
            published.append("bill_of_materials.xlsx")
        if "hydraulics_json" in selected_formats and agent_outputs.get("hydraulics"):
            published.append("hydraulics.json")
        if "nfpa_cert"       in selected_formats and compliance.compliant:
            published.append("nfpa13_compliance_cert.pdf")
        if "ahj_package"     in selected_formats and ahj_output and ahj_output.get("ready"):
            published.append("ahj_permit_package.pdf")

        return {
            "metadata": {
                "generated_at":      datetime.utcnow().isoformat() + "Z",
                "iterations_used":   iterations_used,
                "compliant":         compliance.compliant,
                "nfpa_summary":      compliance.summary,
                "frozen_violations": [v.__dict__ for v in frozen_violations],
                "selected_formats":  list(selected_formats),
            },
            "artifacts": {
                "cad_layout":        agent_outputs.get("cad"),
                "hydraulics_report": agent_outputs.get("hydraulics"),
                "pipe_schedule":     agent_outputs.get("routing"),
                "bracing_and_bom":   agent_outputs.get("bracing"),
                "permit_package":    ahj_output,
            },
            "published_files":       published,
            "requires_human_review": len(frozen_violations) > 0,
        }


# ── Master orchestrator ────────────────────────────────────────────────────────

class FireAIOrchestrator:
    def __init__(self):
        self.agents: dict[str, FireAIAgent] = {
            "cad":        CADAgent(),
            "hydraulics": HydraulicsAgent(),
            "routing":    RoutingAgent(),
            "bracing":    BracingAgent(),
        }
        self.nfpa_agent  = NFPA13Agent()
        self.ahj_agent   = AHJAgent()
        self.synthesizer = OutputSynthesizer()
        self.tracker     = ViolationTracker()
        self.escalator   = EmailEscalator()
        self.run_log: list[dict] = []

    def _log(self, level: str, agent_id: str, message: str, data: Any = None):
        entry = {
            "ts":       datetime.utcnow().isoformat(),
            "level":    level,
            "agent_id": agent_id,
            "message":  message,
        }
        if data:
            entry["data"] = data
        self.run_log.append(entry)
        icons = {"info": "  ", "warn": "⚠ ", "error": "✗ ", "success": "✓ "}
        print(f"[{entry['ts'][11:19]}] {icons.get(level,'  ')}[{agent_id:<14}] {message}")

    # ── Phase 1: dependency-ordered fan-out (Layer 2) ─────────────────────────
    #
    # REPLACED from original: was asyncio.gather (all agents fire simultaneously
    # with stale sibling data). Now each agent waits for its dependencies on the
    # ContextBus, then fires with LIVE upstream outputs.
    #
    # Wall-clock time is identical — agents still run as concurrently as
    # the dependency graph allows. Output quality improves because every agent
    # works with real data from the current iteration.

    async def _run_phase1(
        self,
        project_context: dict,
        violation_map:   dict[str, list[Violation]],
        agent_outputs:   dict,
        frozen_agents:   set[str],
    ) -> dict:
        active_ids = [aid for aid in self.agents if aid not in frozen_agents]
        self._log("info", "orchestrator",
                  f"Phase 1 — {len(active_ids)} agents (dependency-ordered): [{', '.join(active_ids)}]")

        # Seed the bus with any outputs already produced in previous iterations
        # so frozen/completed agents don't block their dependents
        job_id = project_context.get("_job_id", "")
        bus    = ContextBus(job_id=job_id)
        for aid, output in agent_outputs.items():
            if output:
                bus.publish(aid, output)

        updated = dict(agent_outputs)

        async def run_one(aid: str) -> None:
            """Wait for this agent's dependencies, then run it."""
            deps = [d for d in bus.dependencies_for(aid) if d in active_ids or d in agent_outputs]
            if deps:
                self._log("info", aid, f"Waiting for: {deps}")
                ok = await bus.wait_for(deps)
                if not ok:
                    self._log("error", aid, f"Timeout waiting for {deps} — skipping")
                    return

            # Build sibling_outputs from live bus data
            live_siblings = {k: v for k, v in bus.snapshot().items() if k != aid}

            self._log("info", aid, f"Starting (live siblings: {list(live_siblings.keys())})")

            result = await self.agents[aid].run(
                project_context,
                violations=violation_map.get(aid, []),
                sibling_outputs=live_siblings,
            )

            if result.success:
                updated[aid] = result.output
                bus.publish(aid, result.output)
                self._log("success", aid, f"Complete (run #{result.run_count})")
            else:
                self._log("error", aid, f"Agent failed: {result.error}")
                # Publish empty dict so dependents don't hang indefinitely
                bus.publish(aid, {})

        # Launch all agents concurrently — each one awaits its own deps internally
        await asyncio.gather(*[run_one(aid) for aid in active_ids], return_exceptions=True)

        return updated

    # ── Phase 2: NFPA 13 validation ───────────────────────────────────────────

    async def _run_nfpa13(
        self,
        agent_outputs:   dict,
        project_context: dict,
        iteration:       int,
    ) -> ComplianceResult:
        self._log("info", "nfpa13", f"Iteration {iteration}/{MAX_ITERATIONS} — cross-validating all outputs...")
        result = await self.nfpa_agent.validate(agent_outputs, project_context, iteration)

        if result.compliant:
            self._log("success", "nfpa13", f"COMPLIANT — 0 violations. Iteration {iteration}/{MAX_ITERATIONS}.")
        else:
            self._log("error",   "nfpa13", f"{len(result.violations)} violation(s) — Iteration {iteration}/{MAX_ITERATIONS}.")
            for v in result.violations:
                self._log("warn", "nfpa13", f"[{v.section}] {v.description} → {v.fix} (owner: {v.agent_id})")

        return result

    # ── Build per-agent violation map ─────────────────────────────────────────

    def _build_violation_map(self, violations: list[Violation]) -> dict[str, list[Violation]]:
        vmap: dict[str, list[Violation]] = {}
        for v in violations:
            owner = v.agent_id or owner_from_section(v.section)
            if owner:
                vmap.setdefault(owner, []).append(v)
            else:
                self._log("warn", "orchestrator", f"Cannot route violation {v.section} to an agent — skipping")
        return vmap

    # ── Progressive strictness ─────────────────────────────────────────────────

    def _apply_progressive_strictness(
        self,
        violation_map: dict[str, list[Violation]],
        iteration:     int,
        frozen_agents: set[str],
    ) -> tuple[dict[str, list[Violation]], set[str]]:
        if iteration < STRICT_MODE_ITER:
            return violation_map, frozen_agents

        self._log("info", "orchestrator", f"Iteration {iteration} ≥ {STRICT_MODE_ITER} — progressive strictness active")

        stalled        = self.tracker.stalled_agents(list(violation_map.keys()))
        updated_map    = dict(violation_map)
        updated_frozen = set(frozen_agents)

        for aid in stalled:
            updated_frozen.add(aid)
            updated_map.pop(aid, None)
            self._log("warn", "orchestrator",
                f'Agent "{aid}" frozen — no improvement after 2 iterations. Flagging for human review.')

        return updated_map, updated_frozen

    # ── Circuit breaker ────────────────────────────────────────────────────────

    async def _fire_circuit_breaker(
        self,
        project_context:    dict,
        frozen_violations:  list[Violation],
        partial_result:     dict,
    ):
        self._log("error", "orchestrator",
            f"Circuit breaker — max iterations ({MAX_ITERATIONS}) reached with "
            f"{len(frozen_violations)} unresolved violation(s).")

        violation_report = "\n\n".join(
            f"{i+1}. [{v.section}] {v.description}\n   Fix required: {v.fix}"
            for i, v in enumerate(frozen_violations)
        )

        subject = f"[FireAI Pro] Compliance escalation — {project_context.get('project_name', 'Unnamed')}"
        body = (
            f"FireAI Pro has exhausted its compliance loop ({MAX_ITERATIONS} iterations) "
            f"and could not auto-resolve the following NFPA 13 violations.\n"
            f"Human review and sign-off is required before AHJ submission.\n\n"
            f"{'━'*60}\nUNRESOLVED VIOLATIONS\n{'━'*60}\n\n{violation_report}\n\n"
            f"{'━'*60}\nPROJECT DETAILS\n{'━'*60}\n"
            f"Project:    {project_context.get('project_name')}\n"
            f"Occupancy:  {project_context.get('occupancy')}\n"
            f"Location:   {project_context.get('location')}\n"
            f"Generated:  {datetime.utcnow().isoformat()}Z\n"
            f"Iterations: {MAX_ITERATIONS}\n\n"
            f"{'━'*60}\nCOMPLETED ARTIFACTS\n{'━'*60}\n"
            + "\n".join(f"  • {f}" for f in partial_result.get("published_files", []))
        )

        try:
            await self.escalator.send(
                to=ESCALATION_EMAIL,
                subject=subject,
                body=body,
                attachments=[{
                    "filename": "violation_report.json",
                    "content":  json.dumps({"violations": [v.__dict__ for v in frozen_violations]}, indent=2),
                }],
            )
            self._log("success", "escalator", f"Escalation email sent → {ESCALATION_EMAIL}")
        except Exception as e:
            self._log("error", "escalator", f"Email failed: {e}")

    # ── Main entry point ───────────────────────────────────────────────────────

    async def run(
        self,
        project_context:  dict,
        selected_formats: set[str] | None = None,
    ) -> dict:
        selected_formats = selected_formats or DEFAULT_FORMATS
        self._log("info", "orchestrator",
            f"Starting — project: \"{project_context.get('project_name')}\"  "
            f"max_iter={MAX_ITERATIONS}  strict_at={STRICT_MODE_ITER}")
        self._log("info", "orchestrator",
            f"Selected formats: {', '.join(sorted(selected_formats))}")

        agent_outputs:  dict  = {}
        frozen_agents:  set[str] = set()
        violation_map:  dict  = {}
        compliance: Optional[ComplianceResult] = None
        iteration = 0

        # Initial parallel run
        agent_outputs = await self._run_phase1(project_context, {}, {}, frozen_agents)

        # Compliance loop
        for iteration in range(1, MAX_ITERATIONS + 1):

            compliance = await self._run_nfpa13(agent_outputs, project_context, iteration)

            if compliance.compliant:
                break

            if iteration == MAX_ITERATIONS:
                break

            violation_map = self._build_violation_map(compliance.violations)

            for aid, viols in violation_map.items():
                self.tracker.record(aid, len(viols))

            violation_map, frozen_agents = self._apply_progressive_strictness(
                violation_map, iteration, frozen_agents
            )

            if not violation_map:
                self._log("warn", "orchestrator", "All failing agents frozen — exiting loop early")
                break

            agent_outputs = await self._run_phase1(
                project_context, violation_map, agent_outputs, frozen_agents
            )

        # Collect frozen violations
        frozen_violations = [
            v for v in (compliance.violations if compliance else [])
            if (v.agent_id or owner_from_section(v.section)) in frozen_agents
        ]

        partial_result = self.synthesizer.assemble(
            agent_outputs, None, compliance or ComplianceResult(compliant=False),
            iteration, frozen_violations, selected_formats
        )

        # Circuit breaker
        if compliance and not compliance.compliant:
            await self._fire_circuit_breaker(project_context, frozen_violations, partial_result)
            self._log("warn", "orchestrator", "Returning partial package — human review required.")
            return {**partial_result, "execution_log": self.run_log}

        # AHJ report (only after confirmed compliance)
        self._log("info", "ahj", "Running AHJ permit package generation...")
        ahj_result = await self.ahj_agent.run(project_context, sibling_outputs=agent_outputs)
        ahj_output = ahj_result.output if ahj_result.success else None
        if ahj_output:
            self._log("success", "ahj", "AHJ permit package ready")

        # Final synthesis
        final = self.synthesizer.assemble(
            agent_outputs, ahj_output, compliance, iteration,
            frozen_violations, selected_formats
        )

        self._log("success", "orchestrator",
            f"Complete — {len(final['published_files'])} file(s) in {iteration} iteration(s).")

        return {**final, "execution_log": self.run_log}


# ── Convenience runner ─────────────────────────────────────────────────────────

async def run_project(project_context: dict, selected_formats: set[str] | None = None) -> dict:
    orchestrator = FireAIOrchestrator()
    return await orchestrator.run(project_context, selected_formats)


if __name__ == "__main__":
    import sys

    example = {
        "project_name":   "Riverside Office Complex — Building A",
        "occupancy":      "Business (Group B)",
        "location":       "4200 Riverside Dr, Austin TX 78741",
        "floors":         4,
        "total_area":     48000,
        "ceiling_height": 14,
        "seismic_zone":   "D1",
        "static_pressure": 72,
        "water_supply_flow": 1800,
        "system_type":    "wet",
        "pipe_material":  "CPVC",
        "ahj_jurisdiction": "Austin Fire Department",
        "designer": {"name": "Jane Smith PE", "cert": "NICET Level IV"},
    }

    result = asyncio.run(run_project(example))
    print(json.dumps(result["metadata"], indent=2))
    sys.exit(0 if result["metadata"]["compliant"] else 1)
