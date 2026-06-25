"""FireAI Pro — agentic runtime.

Two planes:
  • Runtime  : extraction self-check (stage 3) + verify-repair design loop (stage 2)
  • Improve  : meta-loop telemetry (stage 4)

The deterministic NFPA 13 engine is authoritative. These modules only AUDIT the
engine's output and apply *safe, bounded* repairs (today: sizing a fire pump when
the water supply is short), escalating to a human when there is no safe automated
fix. Nothing here regenerates load-bearing geometry or hydraulics.
"""

__all__ = ["loop", "auditor", "repair", "extraction_check"]
