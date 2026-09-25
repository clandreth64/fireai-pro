"""Engineering status vs release status of one design run, stated side by side (Milestone 2.2B).

An internally approved NFPA-derived rule package can produce a correct deterministic evaluation and
still be ineligible for any external use. This summary never calls a result customer-ready, ready for an
authority having jurisdiction, production-ready or commercially licensed; those appear only in the list of
what it is NOT.
"""

from __future__ import annotations

from fireai.engineering.design import EngineeringDesignResult
from fireai.rules.release import ReleaseDecision

NOT_CLAIMED = ("customer-ready", "ready for submission to an authority having jurisdiction", "production-ready",
               "commercially licensed")


def status_summary(result: EngineeringDesignResult, internal: ReleaseDecision, external: ReleaseDecision,
                   rule_sets=None, manifest=None) -> dict:
    """``rule_sets`` + ``manifest`` (M2.2B.2) add the rule-subset / envelope-completeness / compliance answers."""
    from fireai.rules.completeness import nfpa_status
    nfpa = [s for s in internal.rule_sets if s.source_key]
    approved = bool(nfpa) and all(s.engineering_rule_status == "approved_and_sourced" for s in nfpa)
    auth = sorted({f"{s.source_key}: {(s.authorization or {}).get('status', 'UNKNOWN')}" for s in nfpa})
    internal_only = any((s.authorization or {}).get("status") != "COMMERCIAL_AUTHORIZED" for s in nfpa)
    subset = nfpa_status(rule_sets or [], manifest,
                         {s.source_key: (s.authorization or {}).get("status", "UNKNOWN") for s in nfpa},
                         external_release_eligible=external.eligible)
    return {
        "RULE_APPROVAL": subset["RULE_APPROVAL"] if rule_sets else "NOT_EVALUATED",
        "NFPA_RULE_SUBSET_STATUS": subset["NFPA_RULE_SUBSET_STATUS"] if rule_sets else "NOT_EVALUATED",
        "NFPA_ENVELOPE_COMPLETENESS": subset["NFPA_ENVELOPE_COMPLETENESS"],
        "NFPA_COMPLIANCE": "NOT_CLAIMED",
        "EXTERNAL_RELEASE": subset["EXTERNAL_RELEASE"],
        "ENGINEERING RULE STATUS": ("INTERNAL R&D APPROVED" if approved and internal_only else
                                    "APPROVED" if approved else "NOT APPROVED"),
        "SOURCE AUTHORIZATION": auth or ["no NFPA-derived rule set"],
        "EXTERNAL RELEASE": (f"RELEASE ELIGIBLE for {external.context}" if external.eligible else
                             f"NOT RELEASE ELIGIBLE ({external.context}: "
                             + ", ".join(sorted({b.code for b in external.blockers})) + ")"),
        "DESIGN RESULT": {"status": result.status, "basis": result.basis, "engineering_use": result.engineering_use,
                          "disclaimers": list(result.disclaimers), "result_fingerprint": result.result_fingerprint},
        "LISTING": ("SYNTHETIC - rule-engine validation only; NOT a product-specific or fully authoritative design"
                    if result.basis in ("authoritative_rules_synthetic_listing", "rules_under_review")
                    else "as supplied"),
        "LABELS": sorted(set(internal.labels)),
        "THIS RESULT IS NOT": list(NOT_CLAIMED),
    }
