---
title: "Checks that cannot fail: four green gates that were measuring nothing"
category: editor
tags: [verification, silent-pass, adversarial-review, test-quality, catch-rate, preflight]
module: editor
symptom: "Gates reported PASS and a checker reported a clean corpus while the properties they claimed to prove were untested or unmeasurable"
root_cause: "Every one asserted the ABSENCE of a bad signal without ever proving the check could produce one"
related: [docs/plans/2026-07-28-002-feat-word-like-practicum-editing-plan.md, docs/solutions/editor/2026-07-28-durable-block-identity.md]
---

# The pattern

In one day of building the word-like-editing feature, **four** separate
verification mechanisms were green while proving nothing. They are unrelated
in mechanism and identical in shape:

> The check asserted that nothing bad appeared. Nobody ever asked whether it
> was *capable* of seeing something bad.

A false-positive rate of zero is trivially achievable by a check that always
says "clean". Measuring only that number is how all four survived.

## The four

1. **The Inconsistency checker had a 0% catch rate.** It matched dates as ISO
   (`2025-02-13`); the corpus writes them as `February 13, 2025` in 359
   blocks. It reported "0 flags across all 20 matters" and that was read as
   *working*. An adversarial pass measured the other half — perturb all 80
   participating facts, count flags: **zero**. Zero false flags AND zero true
   flags. Worse than absent, because it manufactures confidence.
2. **`preflight.sh` grepped for the literal `"43/43 PASS"`.** Every assertion
   added to the headful gate turned the gate red — so the pressure was to
   never add assertions. Fixed to trust the exit code.
3. **`SC3` asserted "the resend uses the SAME id"** by checking
   `typeof id === 'string'`. It never compared the two ids. A
   fresh-uuid-per-click regression passes it — and that exact regression
   *shipped* in the sibling add-a-fact composer, which had no test at all.
4. **A "corpus-wide" absence assertion ran against a partial build.** The
   test asserted no real-jurisdiction ref appears in "the whole map", but its
   fixture built 5 of the ~10 page builders. A future ref on an unbuilt page
   passes green while production is red.

## The rule

**For any check whose success condition is an absence, prove it can produce a
presence.** Concretely, in order of preference:

- **Measure catch rate, not just false-positive rate.** Perturb a real input,
  assert the check fires. A canary test is now pinned for the consistency
  checker (`TestCatchPowerCanary`).
- **Compare the two values** an idempotency claim is about — never assert the
  type of one of them.
- **Trust exit codes, never a hardcoded count** in a gate's grep. A gate that
  penalizes adding assertions will get none.
- **Build the same surface the production artifact builds**, or move the
  assertion to the fixture that does.

## When a check genuinely cannot be made strong

Say so *in the tool's own output*. The consistency checker's no-history mode
still cannot fire on real prose (correspondence needs the fact's label words
in the same paragraph as the literal; prose writes "On February 13, 2025, I
met with the client", never "intake date"). Rather than delete the mode or
pretend, `run()` now prints that a clean result there means **"not checked"**,
not "consistent", and names `--since` as the mode with real catch power.

An honest weak check is useful. A weak check that looks strong is a liability
— it is the mechanism by which "we have a consistency checker" becomes a
reason not to look.

## How these were found

Not by the test suite (all four were green in it). By an **adversarial
reviewer prompted with one question**: *"where can a guard go green while the
real thing is red?"* — and given licence to run the code to measure rather
than reason about it. The measurement is what made finding #1 undeniable.
