"""
FireAI Pro — context_bus.py
============================
Async publish/subscribe context bus for agent-to-agent communication.

How it works:
  - Each agent publishes its output when it finishes
  - Downstream agents declare their dependencies and wait for them
  - asyncio.Event signals each dependency as it arrives
  - No agent blocks the event loop — all waiting is async

Dependency graph (natural engineering order):
  CAD        → no dependencies  (runs immediately)
  routing    → needs cad        (pipe paths need sprinkler positions)
  hydraulics → needs cad + routing  (flow calcs need geometry + pipe sizes)
  bracing    → needs routing + hydraulics  (hangers need pipe layout + demands)
  ahj        → needs everything (permit package is last)

This means:
  - CAD fires immediately
  - Routing starts the moment CAD finishes — with real geometry
  - Hydraulics starts the moment both CAD and Routing finish — with real pipe data
  - Bracing starts the moment both Routing and Hydraulics finish
  - Total wall-clock time ≈ longest sequential chain, not sum of all agents

Drop this file at the repo root.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime
from typing import Any

log = logging.getLogger("fireai.context_bus")

# ── Dependency graph ──────────────────────────────────────────────────────────
# Keys are agent IDs. Values are the agent IDs that must publish before this
# agent can start. Empty list = no dependencies = fires immediately.

AGENT_DEPENDENCIES: dict[str, list[str]] = {
    "cad":        [],
    "routing":    ["cad"],
    "hydraulics": ["cad", "routing"],
    "bracing":    ["routing", "hydraulics"],
    "ahj":        ["cad", "routing", "hydraulics", "bracing"],
}


class ContextBus:
    """
    Shared context store for one pipeline run.

    Usage in an agent:
        # Wait for dependencies, then get their outputs
        await bus.wait_for(["cad", "routing"])
        cad_data     = bus.get("cad")
        routing_data = bus.get("routing")

        # ... do work ...

        # Publish own output so downstream agents unblock
        bus.publish("hydraulics", my_output)
    """

    def __init__(self, job_id: str = ""):
        self._job_id  = job_id
        self._outputs: dict[str, Any]           = {}
        self._events:  dict[str, asyncio.Event] = {}
        self._log:     list[dict]               = []

        # Pre-create an event for every known agent
        for agent_id in AGENT_DEPENDENCIES:
            self._events[agent_id] = asyncio.Event()

    # ── Write ─────────────────────────────────────────────────────────────────

    def publish(self, agent_id: str, output: Any) -> None:
        """
        Called by an agent when it has finished producing its output.
        Signals all agents waiting on this agent_id to unblock.
        """
        self._outputs[agent_id] = output

        # Create event on the fly if not pre-registered (e.g. dynamic agents)
        if agent_id not in self._events:
            self._events[agent_id] = asyncio.Event()

        self._events[agent_id].set()

        entry = {
            "ts":       datetime.utcnow().isoformat(),
            "agent_id": agent_id,
            "event":    "published",
            "keys":     list(output.keys()) if isinstance(output, dict) else str(type(output)),
        }
        self._log.append(entry)
        log.info("[%s] ContextBus: %s published (%s keys)",
                 self._job_id[:8] if self._job_id else "?",
                 agent_id,
                 len(output) if isinstance(output, dict) else "?")

    # ── Read ──────────────────────────────────────────────────────────────────

    def get(self, agent_id: str) -> Any:
        """Return a published output. Returns {} if not yet published."""
        return self._outputs.get(agent_id, {})

    def snapshot(self) -> dict[str, Any]:
        """Return all currently published outputs."""
        return dict(self._outputs)

    # ── Wait ──────────────────────────────────────────────────────────────────

    async def wait_for(self, agent_ids: list[str], timeout: float = 300.0) -> bool:
        """
        Async wait until all listed agent_ids have published.
        Returns True if all arrived within timeout, False otherwise.
        timeout defaults to 5 minutes — enough for the slowest Claude call.
        """
        if not agent_ids:
            return True

        pending = [aid for aid in agent_ids if aid not in self._outputs]
        if not pending:
            return True

        log.debug("[%s] ContextBus: waiting for %s",
                  self._job_id[:8] if self._job_id else "?", pending)

        wait_tasks = []
        for aid in pending:
            if aid not in self._events:
                self._events[aid] = asyncio.Event()
            wait_tasks.append(self._events[aid].wait())

        try:
            await asyncio.wait_for(asyncio.gather(*wait_tasks), timeout=timeout)
            return True
        except asyncio.TimeoutError:
            still_missing = [aid for aid in pending if aid not in self._outputs]
            log.error("[%s] ContextBus: timeout waiting for %s",
                      self._job_id[:8] if self._job_id else "?", still_missing)
            return False

    # ── Dependencies helper ───────────────────────────────────────────────────

    def dependencies_for(self, agent_id: str) -> list[str]:
        """Return the list of agents this agent depends on."""
        return AGENT_DEPENDENCIES.get(agent_id, [])

    # ── Diagnostics ───────────────────────────────────────────────────────────

    def published_agents(self) -> list[str]:
        return list(self._outputs.keys())

    def pending_agents(self) -> list[str]:
        return [aid for aid in AGENT_DEPENDENCIES if aid not in self._outputs]

    def event_log(self) -> list[dict]:
        return list(self._log)
