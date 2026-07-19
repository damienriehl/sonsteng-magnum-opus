# Weekend fast-follow wave — Damien's decision answers (2026-07-18 evening)

Answered via the dashboard form (`dashboard.damienriehl.com/sonsteng-weekend-2026-07-18.html`,
brief `coding-projects:briefs/qa/sonsteng-2026-07-18-weekend.json`), pasted back in chat
2026-07-18 ~20:45 CT. Dispositions applied the same evening unless marked next-session.

| # | Decision | Answer | Disposition |
|---|---|---|---|
| 1 | Walkthrough date | **Confirm Wed Jul 23** | Recorded. Runbook `docs/demo-runbook-2026-07-18.md` is the script; rehearsed on DEV. |
| 2 | John's magic link (+ Roger's) | **Test-drive this weekend; remind Sunday evening** | Reminder standing (Sun 7/19 eve). Links NOT sent yet; tokens staged in `~/.secrets/sonsteng-editor-tokens`. |
| 3 | Merge `feat/weekend-fast-follows` → main | **Merge now** | ✅ DONE — merge `97cbd5a`, pushed. Gates re-run on the merge commit: worker **175/175**, apply+digest pytest **88/88**. (`test_validate_spine.py` has a pre-existing missing-fixture ERROR — unchanged file, never part of the gate set; queued as a nit.) |
| 4 | PROD editor origin (`EDIT_ORIGIN`) | **Keep workers.dev default** | No change needed — committed default stands; see `docs/prod-enable.md`. |
| 5 | Streaming default | **Keep OFF through walkthrough** | No change — flag ships OFF everywhere. |
| 6 | Turnstile posture | **Managed mode OK + PROD gated day-one OK** | Confirmed as built; `env.production` enforces at enable. |
| 7 | Security hardening (P1 + C1) | **Do both next session** | Queued (see RESUME next-session list). |
| 8 | Rule 4.2 demo beat | **Demo live Wednesday with a key** | Recorded in walkthrough plan; requires a pasted API key on the day. |
| 9 | Digest timer | **Install now on home box** | ✅ DONE — `sonsteng-digest.timer` enabled (09/13/17/21 CT), proof run exit 0 ("nothing pending; quiet"). Fixed en route: installer's ExecStart didn't quote the space in "Coding Projects" (unit failed status=2); `tools/install-digest-timer.sh` patched. |
| 10 | Firm-dashboard copy fix | **Next session, neutral product copy** | Queued (see RESUME next-session list). |

**Next-session queue (from these answers):** P1 anti-encoding persona clause + C1 fail-closed
scorecard redaction (decision 7) · firm-dashboard aside reword (decision 10) ·
`test_validate_spine.py` fixture nit.
