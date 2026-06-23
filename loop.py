# FireAI Pro — Agentic Runtime (stage 2: verify-repair loop)

The first closed loop in the system. A design is never emitted until it either
passes NFPA 13 or is escalated to a human PE with a clear reason.

```
design  ->  audit  ->  compliant?  --yes-->  done
                          |  no
                          v
                       repair the specific violations
                          |
                          +--> re-design (capped at MAX_ITERATIONS)
                          |
                       no safe fix?  --> escalate to human PE
```

## Files

```
auditor.py   reads compliance flags off a design -> classified Violations
repair.py    maps a Violation to a real engineering fix (or None -> escalate)
loop.py      the loop: design -> audit -> repair -> re-design, with full trace
demo.py      runnable example on a starved supply
```

`loop.py` calls your real `NFPA13DesignEngine` directly. No network, no API key.

## Run the demo

```bash
python -m agentic.demo
```

You'll see a starved ESFR warehouse hit a §22 supply deficit, the loop size a
fire pump to cover it (deficit + 10 psi margin), and the re-design come back
compliant — with the pump recorded as an explicit decision for the PE.

## Use it

```python
from agentic.loop import run_design_with_repair

result = run_design_with_repair(ctx, geo=None, max_iterations=4)

result.status      # "compliant" | "escalated" | "max_iterations"
result.design      # the final design dict (engine output)
result.decisions   # engineering decisions the loop made (e.g. fire pump)
result.trace       # per-iteration record of violations + repairs
result.escalation  # why a human is needed (when status != compliant)
```

Drop this in front of the bare `NFPA13DesignEngine(...).design()` call in
`api/app.py`'s `_run_job` and every generated design now self-corrects before
it reaches output.

## The two rules that keep this safe

1. **Repairs are real engineering, never checker-gaming.** A §22 repair sizes a
   fire pump to the computed deficit — the actual real-world fix — and records
   it for the PE. It never just inflates a number to silence the flag.

2. **When there's no safe fix, it escalates — it does not guess.** Head-spacing
   (§8.5.2) and unknown violations have no tunable input in this engine, so the
   loop hands them to a human rather than fabricating a repair. Better a clear
   "PE required" than a confident wrong design.

## How this connects to the rest

- The **eval harness** (`evals/`) is how you prove a change to this loop didn't
  regress anything: the `insufficient_pressure` project is already a test that
  the loop's pump logic stays correct.
- **Stage 3** adds repair strategies for more violation kinds and turns
  extraction into a self-checking sub-agent (the Costco scanned-PDF path).
- **Stage 4** (the meta-loop) watches these traces — escalations and repeated
  repairs are exactly the telemetry the analyst agent mines for what to improve.
