# M2.2B: owner input needed (NFPA 13-2019 first rule package, internal R&D only)

**Status: STOPPED AT THE HUMAN-INPUT BOUNDARY.** The repository contains no NFPA 13-2019 value, no
reviewer or approver identity, no known-answer case and no listing. FireAI will not supply any of
them.

The NFPA 13-2019 source status stays `INTERNAL_R_AND_D_ONLY`. Nothing entered here can be released to
beta, production or customers until you record a commercial authorization
(`docs/SOURCE_AUTHORIZATION_AND_RELEASE.md`).

## What to fill

| File | What | Who |
|---|---|---|
| `NFPA13_2019_RULE_PACKAGE.template.json` → copy to `…input.json` | For each of the 7 entries (A, B, C, D, E, F_MIN, F_MAX): `rule_id`, your own `title`, the value and unit (for C: the **factor** only), confirmation of the proposed applicability (or your replacement), and the exceptions you are deliberately leaving outside the envelope. For F: whether a minimum and/or maximum applies. Also: source access method and date, an internal document-control reference (not a license, order or customer number), and **three different people**: author, reviewer and approver. | Author; the reviewer confirms |
| `KNOWN_ANSWER_CASES.template.json` | At least **two cases per rule**, worked **by hand**: positions in room-frame feet on the 57.5 × 38 ft fixture (or a stated rectangular room), the branch-line direction, the expected measurement and limit, PASS or FAIL, and the calculation. One passing and one failing case per rule is recommended. The case author and case reviewer must be different people, and not every case may be by the rule's author. | Anyone qualified, independent of FireAI |
| `SPRINKLER_LISTING.template.json` | Optional for M2.2B. Fill only from a real manufacturer document. Without it, M2.2B runs in `rule_validation` mode with a SYNTHETIC listing and is **not a product-specific design**. | Author and reviewer |

## Fixed measurement mapping

This mapping comes from your M2.2B instruction and is in `fireai/rules/intake.py` (`M22B_MAPPINGS`).
The input file can't change it.

| Id | Locator | Constraint | Measurement | Bound | Wall reference |
|---|---|---|---|---|---|
| A | Table 10.2.4.2.1(a) | `sprinkler.max_protection_area` | `array_sxl_protection_area` (S along the branch lines; never Voronoi) | max | wall only |
| B | Table 10.2.4.2.1(a) | `sprinkler.max_spacing` | `array_axis_spacing` | max | — |
| C | 10.2.5.2.1 | `sprinkler.max_wall_distance` | `perpendicular_wall_distance`, **derived**: factor × effective max spacing (B) | max | wall only |
| D | 10.2.5.3 | `sprinkler.min_wall_distance` | `min_perpendicular_wall_distance` (minimum clearance to ANY solid wall; M2.2B.1) | min | wall only |
| E | 10.2.5.4.1 | `sprinkler.min_spacing` | `pairwise_min_distance` | min | — |
| F_MIN / F_MAX | 10.2.6.1.1.1 | `sprinkler.min/max_deflector_below_ceiling` | `ceiling_to_deflector_vertical_distance` | min / max | — |

**Rule D (corrected in M2.2B.1).** D uses `min_perpendicular_wall_distance`. For each sprinkler it takes the perpendicular distance from the sprinkler centre to every solid wall segment whose perpendicular foot lies on the segment, and uses the minimum. Neighbouring sprinklers make no difference. The measurement refuses, as not evaluable, in two cases:
- a nearer wall END, such as a re-entrant corner or door jamb;
- no wall with its perpendicular foot on it.

The end-condition measurement `perpendicular_wall_distance`, used by C and S×L, accepts only a MAX bound. A minimum-wall rule mapped to it is refused (`MEASUREMENT_BOUND_MISMATCH`).

## Outside the first envelope (refused, not approximated)

- door openings, open openings, windows and unknown segments on the space boundary; window treatment
  is deferred;
- angled or irregular walls;
- small-room provisions;
- storage;
- pipe schedule;
- beams, soffits, clouds and obstructions;
- ceiling elevation changes;
- sloped ceilings;
- recessed, flush or concealed installation;
- baffle and in-rack exceptions to E.

## After you fill the files

The next checkpoint will:
1. Validate the input with `load_rule_package`. Every missing or unconfirmed field is reported at once.
2. Have the author author the rules into `NFPA13-2019-BASE` v1 in DRAFT, then submit it for review.
3. Record the known-answer cases, have them reviewed, and run each through the engine in `rule_review`
   mode. A mismatch is recorded and never "fixed" by copying FireAI's number.
4. Have the reviewer approve each rule. This is blocked until each rule has at least 2 reviewed,
   verified cases for its exact content.
5. Have the approver approve the set. They must not have authored any rule in it.
6. Run the commercial fixture in `rule_validation` mode. The expected status is: INTERNAL R&D
   APPROVED · INTERNAL_R_AND_D_ONLY · **NOT RELEASE ELIGIBLE**.

`tests/test_m22b_rule_package.py` proves this whole path works, using a hypothetical edition with
fictitious placeholder numbers.
