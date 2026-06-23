# Stage 3 — Extraction self-check sub-agent

Files added: `extraction_check.py`, `demo_extraction.py`.

This sub-agent is the front half of the runtime plane. It grades every
design-critical field that came out of `extract_project_context`, re-runs vision
on the weak ones, and — crucially — blocks the design loop when something a
person must confirm is missing, instead of feeding a confident wrong number into
the design.

## Run the demo (no API key needed)

```bash
python -m agentic.demo_extraction
```

You'll see a clean set grade all-high and chain into the verify-repair loop, and
a Costco-like scanned set get blocked with a precise list of fields for a human.

## What it grades

| field             | rule                                                        |
|-------------------|-------------------------------------------------------------|
| occupancy         | must map to a known hazard keyword (else defaults to light) |
| total_area        | plausible SF range; flags absurd or missing                 |
| ceiling_height    | 8–55 ft normal                                              |
| static_pressure   | 30–200 psi; **required HIGH** (from flow test)              |
| residual_pressure | valid and ≤ static; **required HIGH**                       |
| water_supply_flow | 100–8000 gpm normal; **required HIGH**                      |
| seismic_zone      | valid category, else safe default D1                        |

The supply trio is held to a higher bar (must be confidently found) because a
wrong supply number silently breaks the §22 check in the design loop. A
user-entered value is always trusted over an extracted one.

## How to wire it in

In `api/app.py`, the runtime plane becomes:

```python
from agentic.extraction_check import self_check_extraction
from agentic.loop import run_design_with_repair

report = await self_check_extraction(pdf_path, prior_ctx=ctx)
if not report.confident:
    # surface report.needs_human to the user, collect the values, then proceed
    ...
else:
    result = run_design_with_repair(report.ctx)
    # result.design -> output;  result.decisions / result.escalation -> show user
```

`grade_extraction(extracted, prior_ctx)` is the pure, testable core (no API).
`self_check_extraction(pdf_path, ...)` is the async wrapper that calls the real
vision extractor behind one seam (`_run_extractor`).

## How it connects

- Feeds **stage 2** (verify-repair loop) only when confident.
- Every "needs_human" event is telemetry **stage 4** (the meta-loop) mines:
  repeated extraction gaps on a document type are exactly the signal that the
  extractor prompt or page-classifier needs improvement.
- Add a sparse scanned project to `evals/` and this behavior becomes a
  regression test too.
