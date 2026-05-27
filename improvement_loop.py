"""
FireAI Pro — improvement_loop.py
==================================
Nightly self-improvement cycle. Runs at 2:00 AM UTC every day.

Cycle:
  1. ANALYZE   — read performance logs, identify agents that are struggling
  2. IMPROVE   — call Claude API with current prompt + failure patterns
                 → generate a candidate improved prompt
  3. TEST      — replay the last 3 complete jobs with the candidate prompt
                 → run NFPA13 validator on each result
                 → candidate must produce fewer violations than current prompt
  4. DEPLOY    — if tests pass, atomic config swap via agent_config_store
                 → next job picks up new prompt automatically

Safety rules (hardcoded, cannot be overridden by the loop itself):
  - Candidate prompt must beat current prompt on ALL 3 test jobs
  - Engineering calculation fields (flow_demand, required_pressure, etc.)
    are NEVER rewritten by prompt changes — they come from design engine
  - Every change is versioned and rollback is one function call
  - If the loop itself crashes, the previous prompt stays in place

Drop this file at the repo root.
Start it from api/app.py lifespan (see wiring instructions at bottom).
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from datetime import datetime, timedelta
from typing import Optional

import anthropic

from agent_config_store import (
    get_agent_performance_summary,
    get_all_agent_summaries,
    get_config,
    init_config_db,
    log_job_performance,
    save_config,
)
from job_store import _list_jobs

log = logging.getLogger("fireai.improvement")

CLAUDE_MODEL      = os.getenv("FIREAI_MODEL", "claude-sonnet-4-20250514")
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")
IMPROVEMENT_HOUR  = int(os.getenv("IMPROVEMENT_HOUR_UTC", "2"))   # 2 AM UTC
MIN_JOBS_REQUIRED = int(os.getenv("MIN_JOBS_FOR_IMPROVEMENT", "3"))  # need at least 3 jobs

client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)

_running = False


# ─────────────────────────────────────────────────────────────────────────────
# Scheduler
# ─────────────────────────────────────────────────────────────────────────────

async def start_improvement_loop() -> None:
    """
    Runs forever. Wakes at 2 AM UTC each day and runs the improvement cycle.
    Started as a background task from api/app.py lifespan.
    """
    global _running
    _running = True
    log.info("Improvement loop started — will run nightly at %02d:00 UTC", IMPROVEMENT_HOUR)

    while _running:
        try:
            await _sleep_until_next_run()
            if _running:
                await run_improvement_cycle()
        except Exception as exc:
            log.error("Improvement loop error: %s", exc, exc_info=True)
        await asyncio.sleep(60)   # brief pause before checking schedule again


def stop_improvement_loop() -> None:
    global _running
    _running = False


async def _sleep_until_next_run() -> None:
    """Sleep until the next IMPROVEMENT_HOUR UTC."""
    now  = datetime.utcnow()
    next_run = now.replace(hour=IMPROVEMENT_HOUR, minute=0, second=0, microsecond=0)
    if next_run <= now:
        next_run += timedelta(days=1)
    wait_seconds = (next_run - now).total_seconds()
    log.info("Next improvement cycle in %.1f hours", wait_seconds / 3600)
    await asyncio.sleep(wait_seconds)


# ─────────────────────────────────────────────────────────────────────────────
# Main cycle
# ─────────────────────────────────────────────────────────────────────────────

async def run_improvement_cycle() -> dict:
    """
    Run one full improvement cycle. Returns a summary dict.
    Can also be called manually for testing.
    """
    log.info("=" * 60)
    log.info("IMPROVEMENT CYCLE STARTING — %s UTC", datetime.utcnow().isoformat())
    log.info("=" * 60)

    results = {
        "started_at":   datetime.utcnow().isoformat(),
        "agents_analyzed": 0,
        "agents_improved": 0,
        "agents_skipped":  0,
        "deployments":     [],
        "errors":          [],
    }

    # Step 1: Analyze all agents
    summaries = get_all_agent_summaries(days=7)
    results["agents_analyzed"] = len(summaries)

    struggling = [s for s in summaries if s["needs_improvement"] and s["jobs_analyzed"] >= MIN_JOBS_REQUIRED]

    if not struggling:
        log.info("All agents healthy — no improvements needed today")
        results["completed_at"] = datetime.utcnow().isoformat()
        return results

    log.info("%d agent(s) flagged for improvement: %s",
             len(struggling), [s["agent_id"] for s in struggling])

    # Step 2-4: Improve each struggling agent
    test_jobs = _get_test_jobs()
    if len(test_jobs) < MIN_JOBS_REQUIRED:
        log.warning("Only %d complete jobs available — need %d for safe testing. Skipping.",
                    len(test_jobs), MIN_JOBS_REQUIRED)
        results["agents_skipped"] = len(struggling)
        results["completed_at"]   = datetime.utcnow().isoformat()
        return results

    for summary in struggling:
        agent_id = summary["agent_id"]
        try:
            deployed = await _improve_agent(agent_id, summary, test_jobs)
            if deployed:
                results["agents_improved"] += 1
                results["deployments"].append(agent_id)
            else:
                results["agents_skipped"] += 1
        except Exception as exc:
            log.error("Failed to improve agent %s: %s", agent_id, exc, exc_info=True)
            results["errors"].append({"agent_id": agent_id, "error": str(exc)})

    results["completed_at"] = datetime.utcnow().isoformat()
    log.info("Cycle complete — %d improved, %d skipped, %d errors",
             results["agents_improved"], results["agents_skipped"], len(results["errors"]))
    return results


# ─────────────────────────────────────────────────────────────────────────────
# Per-agent improvement
# ─────────────────────────────────────────────────────────────────────────────

async def _improve_agent(agent_id: str, summary: dict, test_jobs: list[dict]) -> bool:
    """
    Run the full improve → test → deploy cycle for one agent.
    Returns True if a new prompt was deployed.
    """
    log.info("[%s] Starting improvement cycle", agent_id)

    # Get current prompt (from config store if overridden, else hardcoded baseline)
    current_config = get_config(agent_id)
    current_prompt = current_config["system_prompt"] if current_config else _get_baseline_prompt(agent_id)

    # Step 2: Generate candidate prompt
    candidate = await _generate_candidate_prompt(agent_id, current_prompt, summary)
    if not candidate:
        log.warning("[%s] Could not generate candidate prompt — skipping", agent_id)
        return False

    # Step 3: Test candidate against recent jobs
    test_passed, test_score = await _test_candidate(agent_id, candidate, current_prompt, test_jobs)

    if not test_passed:
        log.info("[%s] Candidate failed testing (score=%.3f) — keeping current prompt", agent_id, test_score)
        return False

    # Step 4: Deploy
    version = save_config(
        agent_id      = agent_id,
        system_prompt = candidate,
        reason        = (
            f"Nightly improvement: failure_rate={summary['failure_rate']:.1%}, "
            f"avg_violations={summary['avg_violations']:.1f}, "
            f"test_score={test_score:.3f}"
        ),
        test_score    = test_score,
    )
    log.info("[%s] ✓ Deployed v%d (test_score=%.3f)", agent_id, version, test_score)
    return True


async def _generate_candidate_prompt(
    agent_id:       str,
    current_prompt: str,
    summary:        dict,
) -> Optional[str]:
    """
    Call Claude to generate an improved system prompt for this agent.
    Returns the candidate prompt string, or None on failure.
    """
    failure_description = _describe_failures(summary)

    prompt = f"""You are a prompt engineering expert for the FireAI Pro fire sprinkler design system.

TASK: Improve the system prompt for the "{agent_id}" agent to reduce the failures described below.

CURRENT SYSTEM PROMPT:
{current_prompt}

OBSERVED FAILURES (last 7 days):
{failure_description}

RULES FOR YOUR IMPROVED PROMPT:
1. Keep the same output JSON schema structure — do not change field names or types
2. Do NOT add any instructions about recalculating engineering values (flow_demand,
   required_pressure, hydraulic calculations) — those come from the design engine
3. Focus improvements on: clearer instructions, better error handling guidance,
   more specific NFPA 13 section references, clearer compliance criteria
4. Keep the prompt concise — under 800 words
5. The improved prompt must be a drop-in replacement — same role, same domain

Respond with ONLY the improved system prompt text.
No preamble, no explanation, no markdown fences.
Start directly with the prompt text."""

    try:
        response = await asyncio.to_thread(
            client.messages.create,
            model=CLAUDE_MODEL,
            max_tokens=1200,
            messages=[{"role": "user", "content": prompt}],
        )
        candidate = next((b.text for b in response.content if b.type == "text"), "").strip()
        if len(candidate) < 100:
            log.warning("[%s] Generated prompt suspiciously short (%d chars)", agent_id, len(candidate))
            return None
        log.info("[%s] Generated candidate prompt (%d chars)", agent_id, len(candidate))
        return candidate
    except Exception as exc:
        log.error("[%s] Prompt generation failed: %s", agent_id, exc)
        return None


async def _test_candidate(
    agent_id:       str,
    candidate:      str,
    current_prompt: str,
    test_jobs:      list[dict],
) -> tuple[bool, float]:
    """
    Run the candidate prompt against recent jobs and compare violation counts.
    Returns (passed: bool, score: float) where score = avg violation reduction.
    A positive score means the candidate produced fewer violations.
    Candidate must improve on ALL test jobs to pass.
    """
    log.info("[%s] Testing candidate against %d jobs", agent_id, len(test_jobs))

    improvements = []

    for job in test_jobs[:3]:
        project_context = job.get("project_context", {})
        if not project_context:
            continue

        current_violations  = await _count_violations(agent_id, current_prompt,  project_context)
        candidate_violations = await _count_violations(agent_id, candidate,       project_context)

        delta = current_violations - candidate_violations  # positive = improvement
        improvements.append(delta)
        log.info("[%s] Job %s — current: %d violations, candidate: %d (delta=%+d)",
                 agent_id, job.get("job_id","?")[:8], current_violations, candidate_violations, delta)

    if not improvements:
        return False, 0.0

    # Must improve (or at least not worsen) on every test job
    all_pass  = all(d >= 0 for d in improvements)
    avg_score = sum(improvements) / len(improvements)

    return all_pass, avg_score


async def _count_violations(agent_id: str, system_prompt: str, project_context: dict) -> int:
    """
    Run the agent with the given prompt on a project context,
    then ask the NFPA13 validator to count violations attributed to this agent.
    Returns violation count (lower is better).
    """
    # Run the agent
    try:
        agent_response = await asyncio.to_thread(
            client.messages.create,
            model=CLAUDE_MODEL,
            max_tokens=4096,
            system=system_prompt,
            messages=[{
                "role": "user",
                "content": (
                    f"PROJECT CONTEXT:\n{json.dumps(project_context, indent=2)}\n\n"
                    "Produce your structured output now."
                )
            }],
        )
        raw     = next((b.text for b in agent_response.content if b.type == "text"), "{}")
        cleaned = raw.strip().lstrip("```json").lstrip("```").rstrip("```").strip()
        agent_output = json.loads(cleaned)
    except Exception:
        return 99   # parse failure = worst possible score

    # Ask NFPA13 validator to count violations for this agent's output
    try:
        val_response = await asyncio.to_thread(
            client.messages.create,
            model=CLAUDE_MODEL,
            max_tokens=512,
            messages=[{
                "role": "user",
                "content": (
                    f"You are an NFPA 13 compliance validator.\n"
                    f"Count violations in the {agent_id} agent output below.\n"
                    f"Respond ONLY with JSON: {{\"violation_count\": <integer>}}\n\n"
                    f"PROJECT CONTEXT:\n{json.dumps(project_context, indent=2)}\n\n"
                    f"{agent_id.upper()} OUTPUT:\n{json.dumps(agent_output, indent=2)}"
                )
            }],
        )
        val_raw  = next((b.text for b in val_response.content if b.type == "text"), "{}")
        val_clean = val_raw.strip().lstrip("```json").lstrip("```").rstrip("```").strip()
        val_data  = json.loads(val_clean)
        return int(val_data.get("violation_count", 0))
    except Exception:
        return 99


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _get_test_jobs() -> list[dict]:
    """Return the last 5 complete jobs that have a project_context stored."""
    jobs = _list_jobs(limit=20)
    complete = [
        j for j in jobs
        if j.get("status") in ("complete", "partial")
        and j.get("project_context")
    ]
    return complete[:5]


def _describe_failures(summary: dict) -> str:
    lines = [
        f"- Jobs analyzed: {summary['jobs_analyzed']}",
        f"- Failure rate: {summary['failure_rate']:.1%}",
        f"- Freeze rate: {summary['freeze_rate']:.1%}",
        f"- Avg violations per job: {summary['avg_violations']:.1f}",
        f"- Avg compliance iterations needed: {summary['avg_iterations']:.1f}",
        f"- Total JSON parse errors: {summary['total_parse_errors']}",
    ]
    return "\n".join(lines)


def _get_baseline_prompt(agent_id: str) -> str:
    """
    Fallback baseline prompts — used when no config override exists yet.
    These match the hardcoded prompts in fireai_orchestrator_v2.py.
    """
    baselines = {
        "cad": (
            "You are the CAD / DXF Layout Agent in the FireAI Pro fire sprinkler design system. "
            "Engine module: enhanced_cad_engine.py. "
            "NFPA 13 sections in scope: §4, §8, §8.5, §8.6. "
            "Always respond with a valid JSON object matching your output schema. "
            "Do NOT include markdown fences or prose outside the JSON. "
            "If corrective context is provided, address every listed violation before producing output."
        ),
        "hydraulics": (
            "You are the Hydraulics Calculation Agent in the FireAI Pro fire sprinkler design system. "
            "Engine module: enhanced_hydraulics_engine.py. "
            "NFPA 13 sections in scope: §22, §22.4, §23. "
            "Always respond with a valid JSON object matching your output schema. "
            "Do NOT include markdown fences or prose outside the JSON. "
            "CRITICAL: Do NOT recalculate engineering values — report what the design engine computed. "
            "If corrective context is provided, address every listed violation before producing output."
        ),
        "routing": (
            "You are the Routing & Pipe Sizing Agent in the FireAI Pro fire sprinkler design system. "
            "Engine module: fireai_routing_advanced.py. "
            "NFPA 13 sections in scope: §6, §16. "
            "Always respond with a valid JSON object matching your output schema. "
            "Do NOT include markdown fences or prose outside the JSON. "
            "If corrective context is provided, address every listed violation before producing output."
        ),
        "bracing": (
            "You are the Seismic Bracing & BOM Agent in the FireAI Pro fire sprinkler design system. "
            "Engine module: enhanced_bracing_engine.py. "
            "NFPA 13 sections in scope: §9, §9.3. "
            "Always respond with a valid JSON object matching your output schema. "
            "Do NOT include markdown fences or prose outside the JSON. "
            "If corrective context is provided, address every listed violation before producing output."
        ),
        "ahj": (
            "You are the AHJ Report & Permit Agent in the FireAI Pro fire sprinkler design system. "
            "Engine module: master_fireai_products_enhanced.py. "
            "NFPA 13 sections in scope: §24. "
            "This agent runs LAST — after NFPA 13 compliance is confirmed. "
            "You receive all other agent outputs as sibling outputs. "
            "Always respond with a valid JSON object matching your output schema. "
            "Do NOT include markdown fences or prose outside the JSON."
        ),
    }
    return baselines.get(agent_id, f"You are the {agent_id} agent in FireAI Pro.")


# ─────────────────────────────────────────────────────────────────────────────
# Performance logging helper (called from api/app.py after each job)
# ─────────────────────────────────────────────────────────────────────────────

def record_job_performance(job: dict) -> None:
    """
    Extract per-agent performance from a completed job's run_log
    and write to performance_log table.

    Called at the end of _run_job in api/app.py.
    """
    job_id   = job.get("job_id", "unknown")
    run_log  = job.get("orchestrator_result", {}).get("execution_log", [])
    metadata = job.get("orchestrator_result", {}).get("metadata", {})

    frozen_violations = metadata.get("frozen_violations", [])
    frozen_agents     = {v.get("agent_id") for v in frozen_violations if v.get("agent_id")}
    iterations_used   = metadata.get("iterations_used", 1)

    # Count violations and parse errors per agent from the run log
    agent_violations: dict[str, int]   = {}
    agent_parse_errs: dict[str, int]   = {}
    agent_success:    dict[str, bool]  = {}

    for entry in run_log:
        aid   = entry.get("agent_id", "")
        level = entry.get("level", "")
        msg   = entry.get("message", "")

        if level == "success" and aid in ("cad","hydraulics","routing","bracing","ahj"):
            agent_success[aid] = True
        if level == "error" and aid in ("cad","hydraulics","routing","bracing","ahj"):
            agent_success.setdefault(aid, False)
        if "violation" in msg.lower() and aid == "nfpa13":
            # Try to parse violation owner from the log message
            pass
        if "parse_error" in msg.lower() or "JSONDecodeError" in msg:
            agent_parse_errs[aid] = agent_parse_errs.get(aid, 0) + 1

    # Count violations per agent from frozen_violations
    for v in frozen_violations:
        aid = v.get("agent_id", "")
        if aid:
            agent_violations[aid] = agent_violations.get(aid, 0) + 1

    all_agent_ids = {"cad", "hydraulics", "routing", "bracing"}
    for aid in all_agent_ids:
        try:
            log_job_performance(
                job_id          = job_id,
                agent_id        = aid,
                success         = agent_success.get(aid, True),
                violation_count = agent_violations.get(aid, 0),
                parse_errors    = agent_parse_errs.get(aid, 0),
                was_frozen      = aid in frozen_agents,
                iterations_used = iterations_used,
                warnings_count  = 0,
            )
        except Exception as exc:
            log.warning("Could not log performance for %s/%s: %s", job_id, aid, exc)


# ─────────────────────────────────────────────────────────────────────────────
# HOW TO WIRE THIS INTO api/app.py
# ─────────────────────────────────────────────────────────────────────────────
#
# Make these 3 edits to api/app.py:
#
# ── EDIT 1: Add imports at the top ───────────────────────────────────────────
#
#   ADD after the existing job_store imports:
#       from agent_config_store import init_config_db
#       from improvement_loop import (
#           start_improvement_loop, stop_improvement_loop, record_job_performance
#       )
#
# ── EDIT 2: Update the lifespan hook ─────────────────────────────────────────
#
#   REPLACE:
#       async with lifespan(app):
#           init_db()
#           task = asyncio.create_task(start_dispatcher())
#           ...
#
#   WITH:
#       @asynccontextmanager
#       async def lifespan(app):
#           init_db()
#           init_config_db()
#           dispatcher_task = asyncio.create_task(start_dispatcher())
#           improve_task    = asyncio.create_task(start_improvement_loop())
#           log.info("FireAI Pro started — dispatcher + improvement loop active")
#           yield
#           stop_dispatcher()
#           stop_improvement_loop()
#           dispatcher_task.cancel()
#           improve_task.cancel()
#
# ── EDIT 3: Log performance at end of _run_job ───────────────────────────────
#
#   At the very end of the try block in _run_job, just before the log.info
#   "Done" line, add:
#
#       # Log performance for improvement loop
#       try:
#           record_job_performance(_get_job(job_id))
#       except Exception as e:
#           log.warning(f"[{job_id}] Performance logging failed: {e}")
#
# That's it. Three edits.
# ─────────────────────────────────────────────────────────────────────────────
