# Seven-point assessment: recording evidence

Status: two questions resolved; one mapping decision remains.

This note records the source check required by the outstanding-work execution plan before
implementing U8. The source recordings were located in Google Drive as Otter transcript exports.
No Drive file was modified.

## Sources checked

- August 6 call transcript: Drive file `19JmV3P7gw10qzG398W8gBOBzbh3JoClT`, especially
  00:10:53-00:11:53.
- August 12 call transcript: Drive file `1xX90cxvvLvkiW4EdhCuac6OJ6VANDHfd`, especially
  00:22:46-00:23:16.

## What the recordings settle

1. The 1-7 scale is an assessment rubric, not merely another name for the memo's seven-part
   drafting outline. On August 6 John describes numerical results on the assessment-and-feedback
   form and says a learner may redo “that section.” On August 12 he again identifies the provided
   assessment-and-feedback form as “the seven point scale.” Damien's 2026-08-17 proposed reading
   is therefore plausible and directly supported.
2. The scale is applied section by section. John describes a redo of an individual section and an
   average across results, rather than a single model-supplied holistic number.
3. The default competence threshold is 4. John says, “Four is average. So four would be
   competent.”
4. The default redo rule is below 6. Scores 4 and 5 are intentionally both competent and
   redo-eligible; competence and redo are not disjoint states.
5. Both defaults remain configurable by school and instructor, as the August 6 call expressly
   confirms.

These findings resolve the plan's Q1b in favor of 1-7 and Q1c in favor of competence at 4 and
redo below 6.

## What the recordings do not settle

The word “section” is not enumerated in either excerpt. The recordings do not by themselves prove
whether memo assessment sections are:

- the seven analytic headings in `data/curriculum/m2.md`; or
- the matter-specific criteria in `data/matters/*/rubric.json`.

The least-destructive implementation is to use the seven analytic headings as the scored dimensions
for memo deliverables and retain the matter-specific weighted criteria for non-memo and
exercise-level assessment. That preserves both authored systems and states their relationship in
data. This is the only product mapping still requiring confirmation before U8 can be encoded.

## Branch consequence

Do not merge `feat/seven-point-assessment` as written. It converts each matter's aggregate weighted
point total into one 1-7 result. That loses the recording's section-level scoring and redo semantics.
Its configuration-precedence and presentation work may be salvaged only after U8 defines the
instrument.
