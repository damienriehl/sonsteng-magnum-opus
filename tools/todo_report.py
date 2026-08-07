#!/usr/bin/env python3
"""todo_report.py — read docs/TODO.md, report what is open, nudge when it changes.

The repo remembers the work list in `docs/TODO.md` (git-tracked, human-editable).
This script is the *reminding* half: it parses that file, prints a report, and
fires one ntfy notification when the open set has changed or something is overdue.

Deliberately mirrors `tools/digest_push.py`:
  * topic resolved by path from ~/.config/claude-rc/ntfy-topic (rotatable secret,
    never a literal in the repo), overridable with SONSTENG_NTFY_TOPIC
  * POST {NTFY_SERVER}/{topic} with Title / Priority / Click headers
  * a dedupe state file so a timer firing on a schedule does not spam

One convention for notifications in this repo, not two.

Task line grammar (see docs/TODO.md, which is the normative description):

    - [ ] **T01 — Title** `@owner` `due:2026-08-06` `origin:call-2026-08-06`
          indented detail lines, ignored by the parser

  [ ] open · [x] done · [-] dropped

Exit codes: 0 report produced (whether or not it notified), 2 no task file,
3 the file parsed to zero tasks (almost certainly a format break).
"""

from __future__ import annotations

import argparse
import datetime as _dt
import hashlib
import json
import os
import re
import sys
import urllib.error
import urllib.request

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_TODO = os.path.join(REPO_ROOT, "docs", "TODO.md")

ENV_TOPIC = "SONSTENG_NTFY_TOPIC"
ENV_SERVER = "SONSTENG_NTFY_SERVER"
ENV_STATE_FILE = "SONSTENG_TODO_STATE"
ENV_TODO_FILE = "SONSTENG_TODO_FILE"
ENV_TODO_URL = "SONSTENG_TODO_URL"

DEFAULT_TOPIC_FILE = os.path.expanduser("~/.config/claude-rc/ntfy-topic")
DEFAULT_SERVER = "https://ntfy.sh"
DEFAULT_URL = "https://github.com/damienriehl/sonsteng-magnum-opus/blob/main/docs/TODO.md"

STATUS = {" ": "open", "x": "done", "X": "done", "-": "dropped"}

TASK_RE = re.compile(
    r"^\s*-\s*\[(?P<box>[ xX-])\]\s*"          # the checkbox
    r"\*\*(?P<id>[A-Za-z]+\d+)\s*[—\-–]\s*"    # **T01 —
    r"(?P<title>.+?)\*\*"                       # Title**
    r"(?P<tags>.*)$"                            # trailing `@owner` `due:` `origin:`
)
OWNER_RE = re.compile(r"`@(?P<owner>[A-Za-z0-9_.-]+)`")
DUE_RE = re.compile(r"`due:(?P<due>\d{4}-\d{2}-\d{2})`")
ORIGIN_RE = re.compile(r"`origin:(?P<origin>[^`]+)`")
HEADING_RE = re.compile(r"^##\s+(?P<heading>.+?)\s*$")


class Task:
    __slots__ = ("id", "title", "status", "owner", "due", "origin", "section")

    def __init__(self, tid, title, status, owner, due, origin, section):
        self.id = tid
        self.title = title
        self.status = status
        self.owner = owner
        self.due = due
        self.origin = origin
        self.section = section

    @property
    def is_open(self):
        return self.status == "open"

    def overdue(self, today):
        return self.is_open and self.due is not None and self.due < today

    def due_today(self, today):
        return self.is_open and self.due == today

    def __repr__(self):  # pragma: no cover - debugging aid
        return "Task(%s, %s, %s)" % (self.id, self.status, self.due)


def parse_tasks(text):
    """Parse TODO markdown into Task objects, in file order.

    Lines inside fenced code blocks are skipped so the format example in the
    document itself never registers as a real task.
    """
    tasks = []
    section = ""
    fenced = False
    for raw in text.splitlines():
        if raw.lstrip().startswith("```"):
            fenced = not fenced
            continue
        if fenced:
            continue
        head = HEADING_RE.match(raw)
        if head:
            section = head.group("heading")
            continue
        m = TASK_RE.match(raw)
        if not m:
            continue
        tags = m.group("tags") or ""
        owner_m = OWNER_RE.search(tags)
        due_m = DUE_RE.search(tags)
        origin_m = ORIGIN_RE.search(tags)
        tasks.append(
            Task(
                m.group("id"),
                m.group("title").strip(),
                STATUS[m.group("box")],
                owner_m.group("owner") if owner_m else None,
                due_m.group("due") if due_m else None,
                origin_m.group("origin").strip() if origin_m else None,
                section,
            )
        )
    return tasks


def duplicate_ids(tasks):
    seen, dupes = set(), []
    for t in tasks:
        if t.id in seen:
            dupes.append(t.id)
        seen.add(t.id)
    return dupes


def build_report(tasks, today):
    open_tasks = [t for t in tasks if t.is_open]
    overdue = [t for t in open_tasks if t.overdue(today)]
    today_due = [t for t in open_tasks if t.due_today(today)]
    by_owner = {}
    for t in open_tasks:
        by_owner.setdefault(t.owner or "unassigned", []).append(t)
    return {
        "total": len(tasks),
        "open": open_tasks,
        "done": [t for t in tasks if t.status == "done"],
        "dropped": [t for t in tasks if t.status == "dropped"],
        "overdue": overdue,
        "due_today": today_due,
        "by_owner": by_owner,
    }


def signature(report, today):
    """Identity of the *state*, not the prose.

    Open-set membership and due dates, plus which items are overdue or due today,
    plus the date so an overdue pile re-nudges once per day. Retitling a task does
    not re-fire, but changing a due date does.
    """
    parts = [
        ",".join(sorted(t.id for t in report["open"])),
        ",".join(sorted("%s=%s" % (t.id, t.due or "") for t in report["open"])),
        ",".join(sorted(t.id for t in report["overdue"])),
        ",".join(sorted(t.id for t in report["due_today"])),
    ]
    stamp = today if (report["overdue"] or report["due_today"]) else ""
    payload = "|".join(parts) + "|" + stamp
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def render_text(report, today):
    lines = []
    lines.append(
        "Legal Practicum TODO — %d open, %d done, %d dropped (%d total)"
        % (len(report["open"]), len(report["done"]), len(report["dropped"]), report["total"])
    )
    if report["overdue"]:
        lines.append("")
        lines.append("OVERDUE:")
        for t in report["overdue"]:
            lines.append("  %s  %s  (@%s, due %s)" % (t.id, t.title, t.owner or "?", t.due))
    if report["due_today"]:
        lines.append("")
        lines.append("DUE TODAY (%s):" % today)
        for t in report["due_today"]:
            lines.append("  %s  %s  (@%s)" % (t.id, t.title, t.owner or "?"))
    lines.append("")
    for owner in sorted(report["by_owner"]):
        items = report["by_owner"][owner]
        lines.append("@%s — %d open" % (owner, len(items)))
        for t in items:
            flag = " !" if t.overdue(today) else ""
            due = "  due %s" % t.due if t.due else ""
            lines.append("  %s %s%s%s" % (t.id, t.title, due, flag))
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


#: Typographic characters that must not reach an HTTP header. See header_safe().
_ASCII_FOLD = {
    "—": "-", "–": "-", "‒": "-", "−": "-",   # dashes
    "‘": "'", "’": "'", "“": '"', "”": '"',   # quotes
    "…": "...", "•": "*", " ": " ",                # ellipsis, bullet, nbsp
}


def header_safe(value):
    """Fold a string to something an HTTP header can actually carry.

    urllib encodes header values as latin-1, so a single em dash raises
    UnicodeEncodeError and the whole notification is lost. This bit us for real:
    the title read "Legal Practicum — N open" and the timer failed at run time,
    not at install time. Anything still unencodable after folding is dropped
    rather than allowed to kill the send.
    """
    for bad, good in _ASCII_FOLD.items():
        value = value.replace(bad, good)
    return value.encode("latin-1", "replace").decode("latin-1")


def notify_title(report):
    # ASCII hyphen deliberately, not an em dash — this string becomes a header.
    if report["overdue"]:
        return "Legal Practicum - %d overdue" % len(report["overdue"])
    if report["due_today"]:
        return "Legal Practicum - %d due today" % len(report["due_today"])
    return "Legal Practicum - %d open" % len(report["open"])


def notify_body(report, today):
    """Content-light: counts, ids, owners. Never the task detail prose."""
    bits = []
    for t in report["overdue"]:
        bits.append("! %s (@%s, due %s)" % (t.id, t.owner or "?", t.due))
    for t in report["due_today"]:
        bits.append("• %s (@%s, today)" % (t.id, t.owner or "?"))
    counts = ", ".join(
        "@%s %d" % (o, len(report["by_owner"][o])) for o in sorted(report["by_owner"])
    )
    if counts:
        bits.append(counts)
    bits.append("%d open of %d" % (len(report["open"]), report["total"]))
    return "\n".join(bits)


def default_state_path():
    override = os.environ.get(ENV_STATE_FILE)
    if override:
        return os.path.expanduser(override)
    base = os.environ.get("XDG_STATE_HOME") or os.path.expanduser("~/.local/state")
    return os.path.join(base, "sonsteng", "todo-report.json")


def load_state(path):
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return {}


def save_state(path, sig, open_count, now_iso):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump({"signature": sig, "open": open_count, "at": now_iso}, fh)
    os.replace(tmp, path)


def resolve_topic():
    override = os.environ.get(ENV_TOPIC)
    if override:
        return override.strip()
    try:
        with open(DEFAULT_TOPIC_FILE, "r", encoding="utf-8") as fh:
            topic = fh.read().strip()
    except OSError:
        return None
    return topic or None


def publish_ntfy(topic, title, body, click_url, timeout=15, server=None, priority="default"):
    base = (server or os.environ.get(ENV_SERVER) or DEFAULT_SERVER).rstrip("/")
    url = "%s/%s" % (base, topic)
    # Body goes out as UTF-8 and may hold anything; headers are latin-1 only.
    req = urllib.request.Request(url, data=body.encode("utf-8"), method="POST")
    req.add_header("Title", header_safe(title))
    req.add_header("Priority", header_safe(priority))
    if click_url:
        req.add_header("Click", header_safe(click_url))
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.status


def run(*, todo_path=None, dry_run=False, force=False, state_path=None,
        publish=publish_ntfy, topic=None, today=None, out=sys.stdout):
    todo_path = todo_path or os.environ.get(ENV_TODO_FILE) or DEFAULT_TODO
    if not os.path.exists(todo_path):
        print("ERROR: no task file at %s" % todo_path, file=sys.stderr)
        return 2
    with open(todo_path, "r", encoding="utf-8") as fh:
        tasks = parse_tasks(fh.read())
    if not tasks:
        print("ERROR: %s parsed to zero tasks — the format is probably broken."
              % todo_path, file=sys.stderr)
        return 3

    dupes = duplicate_ids(tasks)
    if dupes:
        print("WARNING: duplicate task ids: %s" % ", ".join(sorted(set(dupes))),
              file=sys.stderr)

    today = today or _dt.date.today().isoformat()
    report = build_report(tasks, today)
    out.write(render_text(report, today))

    if not report["open"]:
        out.write("\nNothing open. No nudge.\n")
        return 0

    sig = signature(report, today)
    state_path = state_path or default_state_path()
    prev = load_state(state_path).get("signature")
    changed = force or (sig != prev)

    if not changed:
        out.write("\nUnchanged since the last nudge. Silent.\n")
        return 0

    title = notify_title(report)
    body = notify_body(report, today)
    click = os.environ.get(ENV_TODO_URL, DEFAULT_URL)
    priority = "high" if report["overdue"] else "default"

    if dry_run:
        out.write("\n--- would notify ---\nTitle: %s\nPriority: %s\nClick: %s\n%s\n"
                  % (title, priority, click, body))
        return 0

    if topic is None:
        topic = resolve_topic()
    if not topic:
        print("ERROR: no ntfy topic (%s or %s)" % (ENV_TOPIC, DEFAULT_TOPIC_FILE),
              file=sys.stderr)
        return 0

    try:
        publish(topic, title, body, click, priority=priority)
    except (urllib.error.URLError, OSError) as exc:
        print("ERROR: ntfy publish failed: %s" % exc, file=sys.stderr)
        return 0

    save_state(state_path, sig, len(report["open"]),
               _dt.datetime.now().astimezone().isoformat())
    out.write("\nNudged: %s\n" % title)
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(
        description="Report the Legal Practicum TODO list and nudge when it changes.")
    ap.add_argument("--todo-file", default=None, help="Path to TODO.md (default: docs/TODO.md).")
    ap.add_argument("--dry-run", action="store_true",
                    help="Print the report and the notification that would be sent; send nothing.")
    ap.add_argument("--force", action="store_true",
                    help="Notify even if the state is unchanged since the last nudge.")
    ap.add_argument("--state-file", default=None, help="Override the dedupe state path.")
    ap.add_argument("--today", default=None, help="Override today's date (YYYY-MM-DD), for testing.")
    args = ap.parse_args(argv)
    return run(todo_path=args.todo_file, dry_run=args.dry_run, force=args.force,
               state_path=args.state_file, today=args.today)


if __name__ == "__main__":
    sys.exit(main())
