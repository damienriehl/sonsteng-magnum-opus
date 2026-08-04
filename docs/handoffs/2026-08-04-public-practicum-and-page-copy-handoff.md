# Handoff — the practicum went public, page copy became editable (2026-08-04)

**Read cold?** This, then `docs/plans/2026-07-28-002-feat-word-like-practicum-editing-plan.md`
for the *why* of the editing design, then `docs/solutions/editor/` for the traps.
Everything below is **merged to `main` and deployed** unless it says otherwise.

---

## 1. The one decision that is blocking everything else

**An authored scalar that is shared across surfaces, or embedded in generated
framing, has no representation in the editor map contract.** The map registers a
block as editable by pointing at one JSON leaf and requiring that the rendered
element contain *exactly one* scalar. Neither half of that holds when:

- the same leaf renders on more than one page (a module title on the home page
  **and** its module cover page), or
- one sentence mixes an authored value with a generated one
  (`{shape} · {jname}` in every matter packet header).

This blocked work **three times in one migration** and every resolution so far
has been a retreat:

| What | Resolution | Cost |
|---|---|---|
| 10 Matter Library shape labels | left unregistered | Matter Library 13 → 3 blocks |
| firm provenance line | sentence split into two inline `<p>` around the mono span | works, but a workaround |
| 8 home module/section leaves | left unregistered | home 29 → 21 blocks |

**24 registrations were withdrawn in total.** Do not migrate more page copy
before this is decided — you will just withdraw more. This deserves a real
`ce-brainstorm`; it is the top of the queue and it sits **ahead of the roles
work**, which will hit the same wall.

---

## 2. What is live now

- **The practicum is public and world-readable** at
  `https://sonsteng.damienriehl.com/platform/` — all 20 matters, skills browser,
  firm dashboard, three curriculum volumes. Instructor material is NOT there
  (`/platform/instructor/` returns the pitch page; the instructor bundle is
  server-only by construction).
- **The pitch page has a way in** — "Enter the Practicum — Explore the
  Scenarios" in the hero. Before 2026-08-04 the page had only in-page anchors,
  which is why nobody could find the scenarios.
- **Page copy is editable**: home **21**, Matter Library **3**, firm dashboard
  **33** (was 0/0/2). Copy now lives in `data/copy/{home,matters,firm}.json`
  with `data/schemas/page-copy.schema.json`. The firm dashboard's KPI teaching
  lessons are editable; its numbers, tables and chart marks stay derived.
- **Numeric facts are editable** (20 fee/rate leaves across the matters) — they
  used to look editable and silently fail.
- **The Inconsistency checker catches 16/16** seeded inconsistencies with 0 false
  flags (was 6/16). The no-history mode is deleted. Still **not wired to
  anything** — manual `--since` only, by Damien's call.
- **Live map: 5,917 blocks.** Verified against the deployed worker, not assumed.

### Topology (settled 2026-08-04, do not "fix" it)

| Host | What | Who |
|---|---|---|
| `sonsteng.damienriehl.com` | pitch + public practicum at `/platform/` | world |
| `edit.sonsteng.damienriehl.com` | the editor, Cloudflare Access, one-time PIN | Damien + John |
| `sonsteng-dev.damienriehl.com` | DEV site the editor proxies | us |

**R7 stands.** PROD hosts no editor. An earlier attempt to move
`edit.sonsteng.damienriehl.com` to a PROD worker was reverted, and the
`EDIT_TOKEN_*` secrets were deleted from `env.production` so PROD's door is
closed by construction. `SESSION_SIGNING_KEY`, `TURNSTILE_SECRET` and
`DEMO_BYPASS_TOKEN` remain there for the deferred chat worker.

**Roger is no longer an active editor** (chairing Saint Mary's board). Say
"John", not "John and Roger".

---

## 3. Decided but NOT built

Damien answered these on `sonsteng-2026-08-04-roles-and-publish`. Nothing
implements them yet.

- **Roles: Author / Approver / Publisher / Instructor / Admin.** Today the code
  has `edit` / `instructor` / `admin` scopes only. The splits that matter:
  *Author* writes the canon (John) vs *Instructor* = adopting professors who
  read plus instructor material and must never edit it; *Approver* accepts into
  the corpus vs *Publisher* pushes to the world.
- **AI Editor: auto-approve a narrow safe class, triage the rest.** Typos,
  grammar, formatting with no factual/numeric/legal change land automatically;
  everything else is annotated and ranked so review is short. It needs its own
  identity in the audit trail — never Damien's initials. **Build Approver ≠
  Publisher first**: auto-approval is only safe because publishing is a separate
  human act, and that separation does not exist in code yet.
- **Publishing is deliberate**, not automatic on apply.

---

## 4. Ready for `ce-work` — specified, no plan needed

- **D2 client half.** `feat/d2-ceiling-wip` has a verified worker change: an
  over-ceiling scoped request now returns an HMAC-signed token committing to
  scope + radius + wording + editor identity, 10-minute expiry, re-enumerated on
  every retry. It **rejects a bare `confirmed: true` outright, no grace period**
  — deliberately, since a compatibility window would keep the laundering path
  open. So the deployed client breaks the moment it ships. The client must echo
  the opaque `confirmation_token` from the 409 on retry.
  **Ships as a pair or not at all. The headful suite will NOT catch the gap — it
  drives a mock, not the real worker.**
- **D8 live UAT.** Authorized by Damien: run add → edit → move → delete → undo
  on one throwaway block against live DEV, let the text edit auto-apply
  (`sonsteng-apply.timer` fires every 2 min), then revert through the documented
  path and prove the block returns byte-identical.
- **PROD chat worker** — deferred to a fresh weekly budget. `docs/prod-enable.md`
  is the sequence; secrets are already set. End users get a scripted sample
  consultation without it, so the public site is not inert.
- **R7 guard tests** — no longer needed; the hostname move was reverted. Ignore
  the task if you see it queued.

---

## 5. Traps this session hit — do not re-learn them

1. **Never `git checkout`/`git switch` in the shared working tree while workers
   run.** It switches the branch under every one of them and commingles their
   output. Partition workers by *file*, and give each an explicit "do not run
   checkout" instruction.
2. **Never `git add -A` on a tree with unresolved conflicts.** It stages `UU`
   files verbatim; 14 conflict markers were committed and the files were not
   valid JavaScript.
3. **A proof must be able to fail in the dimension it claims.** The copy
   migration "proved" byte-identity by comparing text bytes inside `<main>` —
   which by construction cannot detect a markup change, and a markup regression
   is exactly what a reviewer then found. Compare at the level of the claim.
4. **Behind Cloudflare Access, an HTTP status from an unauthenticated fetch is
   meaningless.** `curl -L` follows the redirect to the Access login page and
   reports *its* 200. Two non-existent URLs "passed". Check the file on the box.
5. **Cockpit sheet paths:** `briefs/board/` rsyncs to the docroot **root**, so
   there is no `/board/` segment in the URL. And **render the ask locally before
   committing** — `python3 tools/render-board-form.py <ask.json> /tmp/t.html`,
   then grep for `could not be built`. `json.load()` proving the file parses says
   nothing about whether the renderer accepts it. The ask schema changed on
   2026-08-03: `summary_html`/`context_html` are out, `body` bullet-nodes are in.
6. **Always run `check_build_parity.py` after any `data/` change.** It moves the
   spine build id and leaves the persona and instructor bundles stale. Missed
   twice; preflight caught it both times because it fails closed. Put it in every
   worker's verification list.
7. **A model subagent cannot hold a long watch** — observed twice. It returns
   after its first sleep. Use a bounded bash `while pgrep` loop for detection and
   a cheap model only for one-shot judgment when something looks wrong.
8. **The headful suite needs `XAUTHORITY` as well as `DISPLAY`**, and cannot run
   from a Codex sandbox at all (private PID namespace). Orchestrator-only. See
   `docs/solutions/editor/2026-07-28-headful-harness-needs-xauthority.md`.
9. **When appending to a reused prompt file, update its output path.** Round-2
   reviewers were told to write to the round-1 filenames, so `codex-run.sh`
   reported nothing while four reports sat on disk — and round 1's were
   overwritten.

---

## 6. How to verify it still works

```bash
bash tools/preflight.sh        # 9 passed / 0 failed / 0 skipped (needs DISPLAY + XAUTHORITY)
python3 tools/check_build_parity.py
python3 tools/editor_consistency.py --since <rev>   # the only real mode; no-history is gone
```

Merges go to `main` from `~/.local/share/sonsteng-daemon/checkout` under
`flock .locks/daemon.lock`; regenerate all four bundles and check parity before
pushing. Deploy order: **worker first, then sites** — so the allowlist never lags
the pages. `deploy/deploy-dev.sh main` and `deploy/deploy-prod.sh`.

Current: **320 python tests · 63/63 headful · preflight 9-0-0.**
