# Sprinkler Listing — Input Checklist (Milestone 2.1)

**No manufacturer value appears here.** This lists what must be taken from the manufacturer's
current data sheet / listing document for the first real sprinkler, and how it is entered through
`fireai/engineering/listings.py` (`ListingStore`). Nothing may be entered from memory, from a
catalogue summary or from an LLM. Unknown fields stay empty — FireAI never fills them.

Target (envelope `NFPA13-2025-DEV-ENVELOPE-1`): one **standard spray, pendent** sprinkler suitable
for a wet-pipe Light Hazard, non-storage space. Which product is chosen is an owner / engineer
decision.

## 1. Document (all required for approval — `listing_problems`)

| Field | What to enter |
|---|---|
| `document.kind` | `manufacturer_listing_document` |
| `document.document` | the manufacturer's document id / title |
| `document.revision` | revision printed on the document |
| `document.publication_date` | publication or effective date |
| `document.access_method` | how you obtained it (manufacturer website download, distributor, …) |
| `document.accessed_at` | date obtained |
| `document.source_id` | optional: file hash or internal document-control id |

## 2. Product identity

| Field | Required | Notes |
|---|---|---|
| `listing_id` / `version` | yes | stable id of your choosing; revisions create a new version |
| `manufacturer`, `model` | yes | as printed |
| `sin` | if printed | sprinkler identification number |
| `product_family` | optional | |
| `sprinkler_type` | yes | must be `standard_spray` to fall inside the envelope |
| `orientation` | yes | must be `pendent` to fall inside the envelope |
| `response_type` | if printed | e.g. as the document states it |
| `k_factor` | if printed | `Quantity` with unit exactly as printed (recorded; not yet consumed by placement) |
| `temperature_rating` | if printed | `Quantity` with unit (recorded; not yet consumed) |

## 3. Listed limits → listing-layer rules

Each listed limit that affects placement is a **rule** in the listing's `rules` (layer `listing`),
with the same fields as a standard rule (see `NFPA13_2025_FIRST_RULESET_INPUT_CHECKLIST.md` §1),
except that the source is the listing document (revision + publication date instead of an edition).

| Listed item (if the document gives one) | Measurement | Supported now? |
|---|---|---|
| listed maximum coverage / protection area | `nearest_sprinkler_cell_area` only if the definitions match; otherwise `UNSUPPORTED_MEASUREMENT` | conditional |
| listed maximum spacing | `array_axis_spacing` | yes |
| listed minimum spacing | `pairwise_min_distance` | yes |
| listed minimum / maximum distance to walls | as in the NFPA checklist rows 4–5 | min yes; max needs a mapping decision |
| deflector distance, escutcheon / recess limits | vertical — none | no (record as `UNSUPPORTED_MEASUREMENT`) |
| other installation constraints | `installation_constraints` (recorded, not evaluated) | recorded only |

Precedence: listing rules combine with the standard's rules by **most restrictive**; a listing may
replace a standard rule only if that standard rule lists the `listing` layer in `replaceable_by`.

## 4. Workflow

1. `ListingStore.create(listing, author)` — draft.
2. `submit_for_review(lid, version, by)`.
3. `review(lid, version, reviewer, "approve" | "reject", note)` — reviewer ≠ author; approval runs
   `listing_problems` (document fields, units, listing-rule approval checks) and refuses otherwise.
4. `select_for_design(lid[, version])` — only approved authoritative listings are selectable.
5. A new document revision: `revise(lid, from_version, new_version, by, change_reason)`; the old
   approved version is superseded and designs that used it become **STALE**.
