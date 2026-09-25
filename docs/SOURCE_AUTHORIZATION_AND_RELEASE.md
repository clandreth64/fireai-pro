# Source Authorization and the External Release Gate (M2.2A.1)

**Product rule (owner decision):** FireAI must not release NFPA-derived engineering content to beta
users, customers, public users, production deployments, commercial API consumers or external
engineering workflows. The exception is content whose source the owner has recorded as commercially
authorized for that use. FireAI does not interpret any agreement; it only records and enforces the
state the owner supplies.

Code: `fireai/rules/authorization.py` and `fireai/rules/release.py`. Tests:
`tests/test_m22a1_orientation_release.py` (tests 24–35).

## Two separate questions

| | Engineering source PROVENANCE | Commercial release AUTHORIZATION |
|---|---|---|
| Question | Where did the rule content come from? Is it reviewed and approved? | May this product context use content derived from that source? |
| Where it lives | `RuleSource` / `RuleSet` review status (`fireai/rules/store.py`) | `AuthorizationStore` records keyed by source (`NFPA 13:2019`) |
| Who changes it | Rule author, then reviewer, then approver (two-person) | **Owner or admin only**: never FireAI, an agent, an LLM, a rule author or a reviewer |
| Reported as | `engineering_rule_status` | `external_release_status` |

A rule set can be fully approved for engineering and still not be release-eligible. `RuleSet` has no
authorization field, so approving rules can never change authorization.

## Authorization records

These fields exist:
- `source_key`, `version` (write-once, with each record superseding the last) and `status`;
- `authorization_reference`: an owner-controlled `FIREAI-AUTH-…` pointer to wherever the owner keeps
  the agreement;
- `effective_date`, `expiration_date`, `permitted_uses`, `notes`, `change_reason`;
- `entered_by` and `entered_by_role` (`owner` or `admin`), `entered_at`;
- `identity_assurance`, which is always "unauthenticated_name (NOT PRODUCTION SAFE)".

Statuses:
- `INTERNAL_R_AND_D_ONLY`
- `COMMERCIAL_AUTHORIZATION_PENDING`
- `COMMERCIAL_AUTHORIZED`
- `REVOKED`
- `EXPIRED`
- `NOT_AVAILABLE`
- `UNKNOWN` (no record)

Only `COMMERCIAL_AUTHORIZED` may permit external uses. It needs a reference, an effective date and
permitted uses.

The records never contain contract text, customer IDs, license, order or subscription numbers, or
personal data. The model rejects unknown fields, and free text is screened for e-mail addresses, long
numbers and identifier phrases.

Every change, including every refused attempt, is appended to `events.jsonl` with the from and to
status, the actor, their role and the record digest.

**Owner-declared state (2026-09-24),** recorded by `seed_owner_declared`:

| Source | Status | Permitted uses |
|---|---|---|
| NFPA 13:2019 | `INTERNAL_R_AND_D_ONLY` | `internal_development` |
| NFPA 13:2025 | `NOT_AVAILABLE` | none |

The repository contains no persisted authorization record. The seed is written only when a person
runs it against a store.

## Release eligibility (`evaluate_release`, `assert_ruleset_release_eligible`)

The gate is deterministic, evaluated per rule set, and fingerprinted, and it reports every blocker.

**External contexts** (`beta`, `production`, `commercial_api`, `external_engineering`): NFPA-derived
content is eligible only when **all** of these hold:
1. The rule set is approved, non-empty, and every rule passes the provenance and approval checks.
2. The source is `COMMERCIAL_AUTHORIZED`.
3. The authorization's `permitted_uses` include the context.
4. The authorization is in effect on the evaluation date.

These are always refused in external contexts:
- synthetic (TEST ONLY) content;
- any development override.

**Internal development:** content that is `INTERNAL_R_AND_D_ONLY` or `COMMERCIAL_AUTHORIZATION_PENDING`
may be used only with an explicit `DevelopmentOverride` object. It is never enabled by an environment
flag. The override:
- is recorded in the decision;
- is audit-logged;
- labels the output INTERNAL R&D / NOT FOR EXTERNAL USE.

Content that is `NOT_AVAILABLE`, `UNKNOWN`, `REVOKED` or `EXPIRED` is refused even internally.

**Deployment guard:** `guard_rule_set_activation(settings, …)` maps
`Settings.deployment_mode`, taken from `FIREAI_DEPLOYMENT_MODE`, to a context:
- the default is **production**, which is fail-closed;
- `development` alone unlocks nothing;
- an unknown mode is an error;
- there is no `force` parameter.

## Current results

| Content | Internal development | Beta, production or other external |
|---|---|---|
| NFPA 13-2019-derived | Only with an explicit override, labelled INTERNAL R&D | **NOT_RELEASE_ELIGIBLE** (`SOURCE_AUTHORIZATION_INTERNAL_R_AND_D_ONLY`) |
| NFPA 13-2025-derived | Refused (`SOURCE_AUTHORIZATION_NOT_AVAILABLE`) | **NOT_RELEASE_ELIGIBLE** |
| Synthetic (TEST ONLY) | Allowed and labelled TEST ONLY | **NOT_RELEASE_ELIGIBLE** (`SYNTHETIC_CONTENT_NOT_RELEASABLE`) |

## Known gaps

- Actor identity is an unauthenticated name. Real owner and admin authentication, for example signed
  records, is required before commercial use.
- The gate is a library check. The API does not serve rule content today, so nothing calls it yet.
  Any future route that activates rule sets must call `guard_rule_set_activation`.
- Non-NFPA layers (jurisdiction amendments, manufacturer listings, project documents) are checked for
  approval only. Whether their sources also need release authorization is an open owner decision.
