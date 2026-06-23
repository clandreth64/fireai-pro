"""
Load reference projects, run each through the design pipeline, score them,
and aggregate into a single report.

A run never crashes the suite: an exception inside one project is caught
and recorded as a failure for that project, so the gate still reports on
everything else.
"""
from __future__ import annotations
import glob
import json
import os
import traceback

from .adapter import run_design
from .scorer import score

PROJECTS_DIR = os.path.join(os.path.dirname(__file__), "projects")


def load_projects(path: str = PROJECTS_DIR) -> list[dict]:
    projects = []
    for fp in sorted(glob.glob(os.path.join(path, "*.json"))):
        with open(fp) as fh:
            projects.append(json.load(fh))
    return projects


def run_one(project: dict) -> dict:
    try:
        design = run_design(project["ctx"], project.get("geo"))
        return score(project, design)
    except Exception as e:  # a broken engine must surface as a FAIL, not a crash
        return {
            "id": project.get("id", "?"),
            "description": project.get("description", ""),
            "passed": False,
            "failures": [
                f"EXCEPTION {type(e).__name__}: {e}",
                traceback.format_exc().strip().splitlines()[-1],
            ],
            "metrics": {},
        }


def run_suite(projects: list[dict] | None = None) -> dict:
    projects = projects if projects is not None else load_projects()
    results = [run_one(p) for p in projects]
    passed = sum(1 for r in results if r["passed"])
    return {
        "total": len(results),
        "passed": passed,
        "failed": len(results) - passed,
        "results": results,
    }
