# FireAI Pro — Eval Harness

The regression gate every agent in the system stands on. It runs reference
projects through the design pipeline, scores them against NFPA-13 invariants,
and detects whether a change improved, regressed, or merely shifted the suite.

This is **stage 1** of the agentic build-out: nothing self-improving is safe
to turn on until this exists, because "better" is undefined without a fitness
function. This harness is that function.

## Run it

```bash
# from the repo root
python -m evals.run_evals                              # run + print table
python -m evals.run_evals --json                       # machine-readable
python -m evals.run_evals --save-baseline evals/baseline.json
python -m evals.run_evals --baseline evals/baseline.json   # gate on regressions
```

Exit code is `0` when all invariants pass and there are no regressions vs the
baseline, `1` otherwise. Wire that exit code into CI and into the meta-loop's
eval gate.

## How it's wired

```
run_evals.py   CLI + exit code (the gate)
  harness.py     load projects -> run_one -> aggregate
    adapter.py     THE SEAM -> calls NFPA13DesignEngine(...).design()
    scorer.py      invariants (pass/fail) + metric capture
  baseline.py    diff current metrics vs saved baseline (the fitness signal)
projects/*.json  reference projects
baseline.json    saved metric snapshot (golden numbers)
```

Today `adapter.run_design()` drives the **deterministic design engine** only —
no network, no API key, no document parsing — so the suite is fast and
reproducible. When you want the harness to exercise the full
document → extraction → design pipeline, change only `adapter.py`; everything
else is written against the design-result dict.

## A reference project

```json
{
  "id": "esfr_warehouse",
  "description": "ESFR warehouse storage, adequate supply",
  "ctx": { "occupancy": "warehouse storage", "total_area": 60000,
           "ceiling_height": 30, "static_pressure": 95,
           "residual_pressure": 82, "water_supply_flow": 2500,
           "seismic_zone": "D1", "pipe_material": "Schedule 40 Steel" },
  "expect": {
    "compliant": true,
    "max_critical_flags": 0,
    "must_pass_sections": ["§8.5.2", "§22"],
    "metrics": { "total_sprinklers": { "min": 1 } }
  }
}
```

`expect` fields (all optional):

| field                 | meaning                                              |
|-----------------------|------------------------------------------------------|
| `compliant`           | design's `compliant` flag must equal this            |
| `max_critical_flags`  | ceiling on `severity != "pass"` flags (default 0)    |
| `must_pass_sections`  | these NFPA sections must be present **and** pass     |
| `must_flag_sections`  | these sections **must** be flagged (negative tests)  |
| `metrics`             | per-metric `{ "min": x, "max": y }` bounds           |

## Adding projects

Drop a new `projects/<id>.json` in, run `--save-baseline` to capture its golden
numbers, commit both. The more real projects (especially ones that have bitten
you — scanned sets, starved supplies, odd geometry), the stronger the gate.

## What the meta-loop does with this

The self-improvement loop, after an agent proposes a change, runs:

```bash
python -m evals.run_evals --baseline evals/baseline.json --json
```

Exit `0` + no regressions → the change may proceed to human review.
Exit `1` → the change is rejected and the failing projects are handed back to
the engineer agent as the repair signal. A held-out set of projects (not in
this folder) should be kept so agents can't optimize the test instead of the
product.
