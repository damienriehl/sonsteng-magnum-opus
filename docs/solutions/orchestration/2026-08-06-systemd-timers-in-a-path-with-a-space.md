---
title: "A systemd timer in a path with a space, and the em dash that ate the notification"
category: orchestration
tags: [systemd, user-timer, ntfy, notifications, latin-1, quoting, todo-reminder]
module: tools
symptom: "A newly installed systemd --user timer either refuses to load ('path is not absolute'), runs python3 against a truncated path, or starts cleanly and then dies at run time with UnicodeEncodeError from urllib"
root_cause: "Three settings in one unit file each treat a space differently, and HTTP header values are latin-1 while the request body is UTF-8"
---

# What happened

`tools/install-todo-timer.sh` installs a `systemd --user` timer that runs
`tools/todo_report.py` each morning and pushes an ntfy nudge. It took three
failures to install correctly, and each failed in a different place — the last
one **after** the unit loaded cleanly, which is the dangerous kind.

Every repo on this box lives under `~/Coding Projects/`. **That space is a
standing hazard for anything that writes a unit file**, and the hazard is not
uniform: the three settings you naturally reach for handle it three different ways.

## The three rules, which disagree with each other

| Setting | Kind | Space handling |
|---|---|---|
| `ExecStart=` | command line, split on whitespace | **must** be double-quoted |
| `WorkingDirectory=` | a path, taken literally | **must not** be quoted — quoting yields `path is not absolute` |
| `Documentation=` | URL list | `file://` with a raw space is rejected and silently dropped |

Unquoted `ExecStart` produced the most confusing symptom, because it is not a
config error at all — the unit loads, systemd runs `python3 /home/damienriehl/Coding`,
and Python reports a missing file. The error names a path that appears nowhere in
your source.

Then quoting *both* fixed `ExecStart` and broke `WorkingDirectory`, which fails at
load time with `path is not absolute: "/home/damienriehl/Coding Projects/..."` —
the quotes are visible right there in the message, which is the one helpful thing
in the whole sequence.

Resolution: quote `ExecStart` only, leave `WorkingDirectory` bare, and point
`Documentation` at the remote URL instead of a local `file://` path.

## Then the unit ran, and the notification died anyway

With the unit correct, the service started, produced its report, and exited 1:

```
UnicodeEncodeError: 'latin-1' codec can't encode character '—' in position 16
```

`urllib` encodes **HTTP header values as latin-1**. The ntfy title read
`Legal Practicum — 25 open`, and that single em dash raised inside `urlopen`,
losing the entire notification. The request **body** is fine — it is explicitly
`.encode("utf-8")` — so the split is: body may hold anything, headers may not.

This is a bad failure mode for a reminder specifically, because **nothing is
watching the thing whose job is to watch things.** It would have failed silently
at 09:00 the next morning and every morning after.

Fix, in `tools/todo_report.py`:

- `header_safe()` folds typographic punctuation (— – ' ' " " … • nbsp) to ASCII
  and then `.encode("latin-1", "replace")` so an unmapped character degrades
  instead of killing the send.
- Applied to **every** header — `Title`, `Priority`, `Click` — not just the one
  that broke.
- The title strings themselves use an ASCII hyphen, with a comment saying why,
  because the next person will reach for an em dash on style grounds.

`tools/digest_push.py` was checked for the same bug and is clean — its titles are
ASCII by construction. That is luck, not design.

# Repeat these

1. **After installing a timer, actually fire the service once.** `systemctl --user
   start <unit>` and read the journal. Install-time success proves the unit
   parses, nothing more. Two of these three failures survived a clean install.
2. **Treat the cockpit's space as a known hazard**, and remember the three
   settings disagree. Do not pattern-match one working unit file onto another
   setting.
3. **ASCII-fold anything that becomes an HTTP header.** Prose written for humans
   drifts toward typographic punctuation, and headers cannot carry it.
4. **A silent reminder is worse than no reminder.** The parser exits 3 on an
   empty parse rather than reporting "nothing open" for the same reason: a format
   break must look like a failure, never like success.

# See also

- `tools/install-todo-timer.sh` — the corrected unit, with the three rules in a comment
- `tools/install-digest-timer.sh` — the sibling this was modelled on
- `docs/TODO.md` — what the timer reminds about
