# Day Zero holdout and anchor agent proposal — decision sheet

This is the U14b **agent-proposal pass only**. The governed Day Zero JSON remains pending; nothing here is human approval or authority to run U15.

## Live coverage

- Pending holdout candidates: **645**
- Attention-required anchor cases: **41**
- Total one-to-one proposals: **686**
- `needs_john: true`: **0**

The plan's 674/41 (715 total) snapshot is stale on this branch; the live governed files contain 645/41 (686 total).

## Summaries

### By disposition

| Value | Count |
|---|---:|
| convertible | 16 |
| convertible_after_durable_locator_added | 35 |
| declared_holdout | 629 |
| declared_out_of_anchor_holdout | 6 |

### By matter

| Value | Count |
|---|---:|
| m01-arbitration-meridian | 42 |
| m02-discipline-meridian | 58 |
| m03-tort-meridian | 23 |
| m04-realestate-meridian | 58 |
| m05-dwi-meridian | 3 |
| m06-noncompete-meridian | 26 |
| m07-ucc-meridian | 15 |
| m08-juvenile-meridian | 2 |
| m09-dissolution-meridian | 62 |
| m10-probate-meridian | 61 |
| m11-arbitration-il | 46 |
| m12-discipline-mn | 35 |
| m13-tort-fl | 29 |
| m14-realestate-tx | 29 |
| m15-dwi-mn | 1 |
| m16-noncompete-ny | 21 |
| m17-ucc-ny | 16 |
| m18-juvenile-ca | 12 |
| m19-dissolution-ca | 75 |
| m20-probate-fl | 66 |
| out_of_matter | 6 |

### By reason

| Value | Count |
|---|---:|
| global_manifest_date_is_fixed_fact | 3 |
| heuristic_effective_date_is_matter_event | 16 |
| legal_citation_is_fixed_fact | 9 |
| matter_date_missing_durable_locator | 35 |
| outside_per_matter_anchor_model | 3 |
| year_only_has_insufficient_day_precision | 620 |

### By confidence

| Value | Count |
|---|---:|
| high | 684 |
| medium | 2 |

### By `needs_john`

| Value | Count |
|---|---:|
| false | 686 |

## Judgment review batches

No low-confidence, subject-matter, or `needs_john` proposals were identified.

## Locator ambiguity to confirm mechanically

- `anchor_attention|data/matters/m03-tort-meridian/case-file/exhibit-medical-summary.md|line:9:raw-occurrence:1|2025-02-24` — the governed raw-census locator is shared by another date on the same table row. The proposal key adds the literal; disposition remains a matter-relative conversion after durable locator remediation.
- `anchor_attention|data/matters/m03-tort-meridian/case-file/exhibit-medical-summary.md|line:9:raw-occurrence:1|2025-07-18` — the governed raw-census locator is shared by another date on the same table row. The proposal key adds the literal; disposition remains a matter-relative conversion after durable locator remediation.

## How Damien's approval is applied

1. Review this sheet and the full evidence JSON; record approval or edits as a separate human decision artifact.
2. In a later human-confirmation change, match each proposal by its exact `category|source|locator|literal` key. Do not use array position.
3. For approved holdouts, update the matching governed holdout entry to the schema's human-confirmed status and approved fixed-fact reason; for approved convertible items, remove them from the holdout set only while adding them to the conversion inventory.
4. For anchor cases, first add the proposed durable locator where required, then move the matching item from `attention_required` into the governed conversion audit or declared out-of-anchor holdout set. Recompute summaries and validate both schemas.
5. Re-run the Day Zero dry-run and proposal validator. U15 may begin only after every governed item is resolved and the human approval artifact is present.

No governed state should be bulk-replaced from this proposal JSON; applying approval is a reviewed, key-by-key mutation so omissions and the duplicate raw locator cannot be hidden.

## Explicit U14b question

Should the `needs_john` subset go to John? The proposed subset is currently empty; if Damien changes any item to a legal or pedagogical judgment during review, should those changed items be routed to John before governed JSON is updated?
