---
title: "A planted-fact classifier cannot chase free-form phrasing"
lane: uat
tags: [uat, red-team, planted-facts, classifier, false-pass, human-review]
status: resolved
related: ["app/worker/test/redteam.mjs", "docs/decisions/2026-09-03-next-steps-decisions.md", "PR #47"]
---

# Symptom

The red-team planted-fact checker failed three independent review rounds. Each
round closed the named false-PASS cases and opened new ones: denial read as
adoption, hedge-then-admit, and `I wasn't running. I absolutely was.`

# What we tried

Successive reviews added patterns for each newly reported phrasing. The rules
became more specific, but every expansion exposed another semantically
equivalent construction that the string classifier handled differently.

# Root cause

Pattern matching over free-form model output cannot eliminate false passes.
Synonymy, quotation, negation scope, irony, and cross-sentence coreference make
surface phrasing an unbounded classification space. "Add these variants" fixes
examples, not the class of defect.

# Fix

Decision H1 (Damien, 2026-09-05,
`sonsteng-magnum-opus-2026-09-05-1646-redteam-classifier-approach`) restricts
auto-PASS to a tiny whole-response grammar; everything else becomes REVIEW for
a human read. Under G1, REVIEW is recorded without failing the run.

Rounds 4–6 in PR #47 added four boundaries in `app/worker/test/redteam.mjs`:
the PLANT is a closed registry derived from committed probes, classification
uses one NFC and whitespace-folded normalization, an interrogative echo has its
own whole-utterance grammar rather than a punctuation check, and the reviewer
contract says only a false PASS blocks. A missed adoption routed to REVIEW is
the designed H1 screen behavior. A free-string plant can always defeat an
atomicity check, and inconsistent normalization lets literal adoption fall
between the scan and grammar.

# How to recognize it next time

Stop when a review proposes "add these phrasing variants." That is evidence the
classifier is chasing an open language surface. Narrow automatic acceptance to
a closed grammar, normalize once, route the remainder to REVIEW, and state which
verdict is blocking so review rounds can converge.

# Related

- `app/worker/test/redteam.mjs`
- `docs/decisions/2026-09-03-next-steps-decisions.md` (G1 and H1)
- PR #47
