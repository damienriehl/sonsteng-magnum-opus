# Decision Sheet — Domain cutover human gate

Date: August 23, 2026
Scope: the only U10 check that cannot be completed without a real allowlisted identity

The Cloudflare, DNS, Pages, Worker, redirect, CORS, and unauthenticated Access checks are
complete. This sheet queues the one remaining human-owned verification. It asks for no
architecture or product decision.

## G1 — Sign in and complete one reversible editor round-trip

**Why you are needed.** Cloudflare Access must send a one-time code to an allowlisted
person. No repository or machine credential can honestly substitute for that identity.

**Recommended test.** Make a tiny punctuation-only change in a low-risk explanatory
paragraph, confirm it completes, then change the same character back. This proves the
new hostname, identity-to-scope mapping, editor submission, status lifecycle, and
reversibility while leaving public meaning unchanged.

### Step by step

1. Open `https://edit.legalpracticum.org/` in your normal browser.
2. Complete the Cloudflare Access one-time-code login with your allowlisted address.
3. Confirm the bare hostname lands inside `/edit/` and shows the editor/review option
   appropriate to your role. Stop and record the visible error if it does not.
4. Open a low-risk explanatory page. Before editing, copy the exact original sentence
   into a temporary note so restoration is unambiguous.
5. Change one punctuation character only—for example, add or remove a comma—and submit.
6. Wait for the item to reach its normal completed/applied state. Record the page name
   and the displayed suggestion or batch identifier; do not include the email code.
7. Edit the same sentence back to the exact original text and wait for that restoration
   to complete. Refresh once and confirm the original text is visible.

### Paste back this compact result

```text
G1 result: PASS | FAIL
Page:
First suggestion/batch ID:
Restoration suggestion/batch ID:
Original text restored: YES | NO
Error shown (if any):
```

**Success condition.** `PASS`, both identifiers recorded, and `Original text restored: YES`.
After that evidence is added to `docs/uat/editor-publisher-matrix.md`, U10 is fully closed.
