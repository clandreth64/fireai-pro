# FireAI — Agentic & Learning Architecture (long-term design)

**Status:** architecture only. Nothing in this document is implemented beyond what §11 marks
"exists today". No agent, orchestrator, optimizer or learning loop is built, and nothing is trained.

## 1. North Star

> A user supplies construction information and FireAI autonomously produces a coordinated,
> hydraulically validated, code-checked, fabrication-ready fire sprinkler system, with every
> important engineering decision traceable and independently verifiable.

FireAI should learn from **verified** project outcomes. It must never modify its own production
engineering behaviour without control:

**Experience → Structured feedback → Candidate improvement → Independent evaluation →
Regression testing → Controlled, versioned release.**

## 2. Non-negotiable principles

1. **Unknown beats invented.** Missing information stays missing and is requested, never defaulted.
2. **Explicit failure beats false success.** An incomplete engineering state is rejected, not shipped.
3. **Traceability.** Every derived object records its sources, rules, versions and human decisions.
4. **Deterministic where the answer is deterministic** (§5). AI never stands in for mathematics.
5. **Verified inputs only.** Engineering starts only from a HUMAN_VERIFIED model through the
   engineering input contract (`docs/ENGINEERING_INPUT_CONTRACT.md`).
6. **AI output is never its own ground truth.** Evaluation references are human-verified.
7. **Controlled learning.** Project knowledge may persist within a project; global behaviour
   changes only through the versioned release lifecycle (§8–§9).
8. **Reproducibility.** Any historical result can be recomputed with the engine, rule and model
   versions that produced it.

## 3. One authoritative engineering model

Every agent and engine reads from and writes to **one authoritative FireAI Engineering Model**.
Nothing reparses another component's drawings or exports:

```
Source adapters (DWG, DXF, IFC, Revit, manual input …)
        │  Drawing Understanding (today: fireai.ingest / fireai.interpret)
        ▼
Verified normalized building model  ──(human verification gate)──►  Engineering input contract
        │
        ▼
AUTHORITATIVE FIREAI ENGINEERING MODEL  (versioned; every object has uid + provenance)
   ├─► drawings / sheets        ├─► hydraulics          ├─► BIM export / coordination
   ├─► material take-off        ├─► fabrication         └─► labels / reports
```

Rejected anti-pattern: design → drawing → hydraulics reparses the drawing → BIM reparses hydraulic
output → fabrication reparses BIM. Every reparse loses identity and invites silent drift. Derived
outputs are **views** of the model, regenerated from it and traceable back to it.

## 4. FireAI Orchestrator (future)

The Orchestrator is a coordinator, not a calculator. It:

* reads **project state** from the authoritative model: what is verified, missing, stale or blocked;
* determines **which work is required** next and in which order (dependency graph);
* **invokes** specialized agents and deterministic engines with explicit, versioned inputs;
* tracks **unresolved assumptions** and **requests human input** when a decision is not
  derivable (hazard classification, ruleset edition, missing ceiling heights …);
* **enforces verification gates** (e.g. `require_verified_model`, the engineering input contract) and
  **rejects incomplete engineering states**;
* runs **iterative design loops** (§7) until constraints pass or a stop condition is reached;
* **records every decision** with provenance (who or what decided, on which inputs, which version);
* controls **release state** (draft → checked → approved → issued for fabrication), where each
  transition requires the gates for that state.

It never performs hydraulic, geometric or code arithmetic itself.

## 5. Deterministic engines vs AI

| Deterministic engines (exact, testable, versioned) | AI / agents |
|---|---|
| geometry intersection, distance, area, containment | semantic interpretation (what is this drawing / region / object?) |
| coverage area, spacing and clearance checks | orchestration and prioritisation of work |
| pipe network connectivity, topology validation | handling ambiguity; asking the right question |
| hydraulic equations and network solution | generating design alternatives to evaluate |
| clash detection on real 3D geometry | explaining results and trade-offs |
| cut-list / stock optimisation | natural-language interaction |
| numerical code constraints once a rule is encoded | learning from structured human feedback (§8) |

Rules:
* AI may **choose what to evaluate**, never **replace the evaluation**. Every agent proposal is
  checked by the deterministic engines, and only engine results count as pass/fail.
* AI must never be used merely to make deterministic output *appear* intelligent.
* Encoded rules are versioned data with provenance (source, edition, section reference, adoption,
  amendments). They are never recalled from model memory (see `MILESTONE_2_0_SPEC.md` §6).

## 6. Specialized components (future) — responsibilities and boundaries

| Component | Does | Must not |
|---|---|---|
| **Drawing Understanding Agent** | Source construction information → normalized spatial/building model with provenance, uncertainty and review triggers (exists today as deterministic code, §11) | invent geometry, decide which plan is engineered, pass unverified data on |
| **Design Agent** | propose sprinkler-layout alternatives from the verified model plus explicit design criteria | self-certify; use unverified inputs; pick hazard or ruleset itself |
| **Rules / Code Agent** | determine which constraints apply and evaluate them via versioned rules and approved sources; label each item as **deterministic rule**, **interpretation**, **project assumption** or **human decision** | encode requirements from memory; hide interpretations as rules |
| **Routing Agent** | create and optimise connected piping networks | declare a network valid (connectivity is checked by an engine) |
| **Hydraulic Engine / Agent** | the deterministic solver computes; the agent chooses what to calculate and compares alternatives | replace or approximate hydraulic mathematics |
| **Coordination Agent** | detect clashes on actual 3D geometry; propose routing alternatives | coordinate against guessed elevations |
| **Fabrication Agent** | coordinated network → fabrication-ready structured components (spools, cuts, fittings) | alter engineering intent without a new check cycle |
| **Optimization Agent** | rank VALID alternatives across objectives (§10) | trade away any constraint for an objective |
| **QA / Adversarial Review Agent** | independently challenge the solution: unsupported assumptions, rule violations, geometry conflicts, hydraulic failures, incomplete information, inconsistent state | be the same component that produced the design |
| **Human Review / Approval Interface** | let qualified people inspect, correct, verify and approve, with attributable identity | be bypassed for gated transitions |

## 7. Multi-agent engineering loop (future)

```
Design proposes ─► Rules evaluate ─► Hydraulics calculate ─► Coordination finds clashes
      ▲                                                            │
      │                    Routing proposes alternative ◄──────────┘
      │                              │
      │         Hydraulics recalculate ─► Rules re-evaluate ─► Fabrication checks manufacturability
      │                                                            │
      └──── Optimization compares VALID alternatives ◄─────────────┘
                                   │
                   QA / adversarial review challenges
                                   │
        Orchestrator verdict: VALID | INVALID | UNRESOLVED | HUMAN REVIEW REQUIRED
```

The loop repeats until all deterministic constraints pass, required information is resolved and no
critical QA finding remains. Otherwise it stops with an explicit UNRESOLVED or HUMAN REVIEW REQUIRED
state, never a best-effort "pass". Iteration limits and every intermediate state are recorded.

## 8. Two learning loops

### 8.1 Project learning (safe, scoped, persistent within a project)

Examples: "this XREF is the architectural background", "this region is Level 2", "this room
boundary was corrected", "this space is Ordinary Hazard (per the engineer)", "this layer holds
existing sprinkler pipe".

* Stored as human decisions attached to the project's source identity (today: the review store,
  keyed by source sha256, with document-GUID revision links).
* Applied deterministically on every re-run of that project. It is invalidated or flagged when
  its context changes (source, XREFs, engine version).
* **Never** changes behaviour for any other project.

### 8.2 System learning (global, controlled)

```
OBSERVE
 → RECORD STRUCTURED LEARNING EVENT            (§11: LearningEvent — exists as a derivation today)
 → IDENTIFY REPEATED PATTERN                   (e.g. 400 corrections of the same view-type miss)
 → PROPOSE CANDIDATE IMPROVEMENT               (new rule, threshold, vocabulary, model weights)
 → RUN OFFLINE EVALUATION                      (on held-out, human-verified data)
 → RUN FULL REGRESSION CORPUS                  (every verified project and known-answer case)
 → COMPARE AGAINST CURRENT PRODUCTION VERSION  (per category: improvements AND regressions)
 → HUMAN / CONTROLLED APPROVAL
 → VERSION
 → DEPLOY
```

FireAI must not rewrite production engineering rules and deploy them itself. Corrections never
silently change global rules. A learning event becomes evidence for a *candidate*, nothing more.

## 9. Versioned improvement

* `production_engine = v4.2`, `candidate_engine = v4.3`. A candidate may be derived from thousands of
  verified corrections, but is still only a candidate.
* Evaluation reports **per category and per case**: improved, unchanged, regressed. Aggregate
  scores are never the release criterion alone.
* **Critical regressions block release** (any safety-relevant category, any known-answer engineering
  case, any gate behaviour). Non-critical regressions need explicit, recorded acceptance.
* Versions are pinned everywhere: every model records `engine_version` (today: `ENGINE_VERSION` in
  `fireai/pipeline.py`), every rule its ruleset version, every AI component its model id. Historical
  results remain reproducible with the versions that created them.
* A version change invalidates human verification of models it would reinterpret. This works today:
  the verification fingerprint includes the engine version.

## 10. Design search and optimisation (future)

FireAI should search many **valid** alternatives rather than stop at the first design that passes.
Candidates are generated by agents, filtered by deterministic constraints, and ranked on declared
objectives:

code compliance (a hard filter, not a score) · hydraulic demand · available pressure margin · pipe
quantity and diameters · fitting count · fabrication complexity · installation effort · clash count ·
seismic requirements · stock utilisation · project-specific cost objectives.

Goal: *find high-quality engineering solutions within deterministic constraints*. Objective weights
are explicit, project-recorded inputs, and the chosen trade-off is explained and traceable.

## 11. What exists today (foundations only)

| Foundation | Where |
|---|---|
| Stable uids, provenance, frames, unknown Z, schema versioning | `fireai/model.py`, `fireai/schema.py` |
| Human corrections persisted per project, with context and **machine snapshot** (FireAI's original value, confidence, evidence, rules) | `fireai/review/store.py` |
| Verification gate + invalidation; `require_verified_model` | `fireai/review/store.py`, `fireai/review/gate.py` |
| Engineering input contract (the only door to engineering) | `fireai/contract/`, `docs/ENGINEERING_INPUT_CONTRACT.md` |
| `LearningEvent` schema (`learning_event/0`), **derived on demand, read-only**, scope `project` | `fireai/review/learning.py` |
| Human ground truth separated from Claude drafts and FireAI output | `tests/real_drawings/gt.py`, `docs/HUMAN_VALIDATION_GUIDE.md` |
| Regression corpus with write-once runs, per-category evaluation against human truth | `scripts/validate_corpus.py`, `scripts/gt_review_summary.py` |

A future learning event carries: project/model version, object uid, source context, FireAI's
original interpretation, confidence, evidence, human correction and value, reason, reviewer (an
unauthenticated name today), timestamp, resulting verification state, downstream outcome (unknown
today) and engine/rule/model version.

Not built, by design: an event store or event sourcing, pattern mining, candidate generation,
training or fine-tuning, automatic rule changes, the orchestrator and all agents.

## 12. Risks

| Risk | Control |
|---|---|
| Learning from unverified or wrong corrections | Only human-verified events are eligible; reviewer identity must be authenticated before system learning starts |
| Feedback loops (training on own output) | AI output never becomes ground truth; evaluation sets are human-verified and held out |
| Silent behaviour drift | Versioned releases only; regression corpus; critical regressions block |
| Overfitting to one office's CAD standards | Per-source statistics; project learning stays project-scoped |
| Agents appearing authoritative | Every pass/fail comes from a deterministic engine or an attributable human |
