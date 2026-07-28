#!/usr/bin/env python3
r"""digest_push.py — batched, cumulative ntfy digest for pending editor suggestions.

WHY THIS EXISTS (editor plan decision 6): the admin **review page stays
canonical** — it is the single place all outstanding suggestions are reviewed in
one sweep. This push is *strictly additive*: it never becomes a second inbox and
never carries the suggestion content. It only taps Damien on the shoulder —
"N suggestions are waiting; open the review page" — and links straight to it.

SEMANTICS (batched + cumulative):
  * ONE digest summarizes ALL currently-pending suggestions. Suggestions
    accumulate across days; a single sweep on the review page clears them. We
    NEVER notify per-suggestion (no spam).
  * A small cron/systemd timer runs this a few times a day. On each run we read
    the current pending set from the Worker's admin store and, only if there is
    something to review AND the set has *changed* since the last notification,
    fire one ntfy push.

DEDUPE (state file):
  * We persist a tiny JSON state file (default: $XDG_CACHE_HOME/sonsteng-digest/
    last-notified.json) holding a signature = sha256 over the SORTED list of
    pending suggestion IDs. If the current signature equals the stored one, the
    pending set is unchanged since we last told Damien, so we stay quiet. Any
    add/accept/decline/apply that changes the pending membership flips the
    signature and re-notifies. When the pending set drains to zero we clear the
    state so the next accumulation notifies again.

READ PATH (no shared apply-engine code imported; this file is standalone):
  * GET {EDIT_API_BASE}/review  (admin scope) -> { ok, items:[<full rows>] }.
    Admin bookmark token in EDIT_SERVICE_TOKEN (Bearer; NEVER logged/committed).
    This is the same wire contract tools/apply_suggestions.py speaks, but we do
    not import it — one-writer rule: we touch zero pre-existing apply-engine code.

PUBLISH PATH:
  * POST {NTFY_SERVER}/{topic}  with a content-light body + a `Click` header set
    to the review-page URL (tapping the push opens the review page). Topic is
    resolved by path (~/.config/claude-rc/ntfy-topic — the home box's canonical
    rc-notify topic) so it stays a rotatable secret, overridable by env. Public
    ntfy.sh by default; no self-hosted ntfy is configured on this box.

Python 3, stdlib only. The fetch and publish steps are injectable so the digest
builder and dedupe logic are unit-testable with no network (see
tools/tests/test_digest_push.py). `--dry-run` prints the would-send payload and
touches no state file.
"""

from __future__ import annotations

import argparse
import dataclasses
import hashlib
import json
import os
import re
import sys
import urllib.error
import urllib.request

# ---- config (env-var names; NEVER hard-code secrets) ----------------------- #
ENV_API_BASE = "EDIT_API_BASE"          # e.g. https://<worker>/edit/v1  (no trailing slash)
ENV_SERVICE_TOKEN = "EDIT_SERVICE_TOKEN"  # admin/service bookmark token (opaque). NEVER commit/log.
ENV_REVIEW_URL = "EDIT_REVIEW_URL"      # click-through; defaults to EDIT_ORIGIN + /edit/review
ENV_EDIT_ORIGIN = "EDIT_ORIGIN"         # worker origin, used to derive the review URL
ENV_NTFY_SERVER = "SONSTENG_NTFY_SERVER"  # default https://ntfy.sh
ENV_NTFY_TOPIC = "SONSTENG_NTFY_TOPIC"    # override; else read from the topic file
ENV_STATE_FILE = "SONSTENG_DIGEST_STATE"  # override the dedupe state path

# The home box's canonical rc-notify topic file (path only — rotatable secret).
DEFAULT_TOPIC_FILE = os.path.expanduser("~/.config/claude-rc/ntfy-topic")
DEFAULT_NTFY_SERVER = "https://ntfy.sh"
DEFAULT_REVIEW_URL = "https://sonsteng-chat.damienriehl.workers.dev/edit/review"

# Statuses that count as "waiting for the reviewer". The trigger the task names is
# strictly `pending`; the review page also surfaces drift / needs_human /
# accepted_blocked as items still needing a human decision, so we count those too
# (all are "in the reviewer's court" — none is terminal, none is mid-apply).
REVIEWABLE_STATUSES = ("pending", "drift", "needs_human", "accepted_blocked")


# --------------------------------------------------------------------------- #
# Digest model (pure)
# --------------------------------------------------------------------------- #
@dataclasses.dataclass
class Digest:
    count: int
    by_matter: list          # [(matter, n), ...] sorted desc then name
    by_page: list            # [(page, n), ...]
    by_source: list          # [(source_ref, n), ...]
    by_status: dict          # {status: n} over the reviewable set
    signature: str           # sha256 over sorted reviewable IDs ("" when count==0)
    review_url: str

    def title(self) -> str:
        noun = "suggestion" if self.count == 1 else "suggestions"
        return "Sonsteng: %d %s to review" % (self.count, noun)

    def body(self) -> str:
        """Content-LIGHT: counts + where, never the suggestion text itself."""
        if self.count == 0:
            return "No suggestions are waiting for review."
        lines = []
        noun = "suggestion" if self.count == 1 else "suggestions"
        lines.append("%d %s waiting in the review sweep." % (self.count, noun))
        if self.by_matter:
            top = ", ".join("%s (%d)" % (m or "firm/other", n) for m, n in self.by_matter[:6])
            more = "" if len(self.by_matter) <= 6 else ", +%d more" % (len(self.by_matter) - 6)
            lines.append("By matter: " + top + more)
        # Surface non-pending reviewable states so a drift/needs-human item is not
        # silently buried under the headline pending count.
        extra = {k: v for k, v in self.by_status.items() if k != "pending" and v}
        if extra:
            lines.append("Includes: " + ", ".join("%s %d" % (k, v) for k, v in sorted(extra.items())))
        lines.append("Open the review page to sweep them all at once.")
        return "\n".join(lines)


def _matter_of(source_ref: str):
    """matter id (m03) from a source_ref, matching tools/apply_suggestions.matter_of."""
    if not source_ref:
        return None
    m = re.search(r"data/matters/(m\d{2})", source_ref)
    return m.group(1) if m else None


def _counts(pairs):
    """[(key, n)...] sorted by n desc, then key asc; None keys sort last."""
    return sorted(pairs, key=lambda kv: (-kv[1], (kv[0] is None, kv[0] or "")))


def build_digest(rows, review_url):
    """Pure: turn the raw admin /review items into a cumulative Digest.

    Only the reviewable set (pending + drift + needs_human + accepted_blocked)
    contributes. Signature is sha256 over the SORTED reviewable IDs so the dedupe
    is membership-exact — order-independent and immune to count-collisions."""
    reviewable = [r for r in rows if (r.get("status") in REVIEWABLE_STATUSES) and r.get("id")]
    ids = sorted(str(r["id"]) for r in reviewable)
    signature = hashlib.sha256("\n".join(ids).encode("utf-8")).hexdigest() if ids else ""

    by_matter, by_page, by_source, by_status = {}, {}, {}, {}
    for r in reviewable:
        matter = _matter_of(r.get("source_ref") or "")
        by_matter[matter] = by_matter.get(matter, 0) + 1
        page = r.get("page")
        by_page[page] = by_page.get(page, 0) + 1
        src = r.get("source_ref")
        by_source[src] = by_source.get(src, 0) + 1
        st = r.get("status")
        by_status[st] = by_status.get(st, 0) + 1

    return Digest(
        count=len(reviewable),
        by_matter=_counts(list(by_matter.items())),
        by_page=_counts(list(by_page.items())),
        by_source=_counts(list(by_source.items())),
        by_status=by_status,
        signature=signature,
        review_url=review_url,
    )


def should_notify(digest: Digest, prev_signature) -> bool:
    """Notify iff there is something to review AND the pending set changed."""
    return digest.count > 0 and digest.signature != (prev_signature or "")


# --------------------------------------------------------------------------- #
# State file (dedupe persistence)
# --------------------------------------------------------------------------- #
def default_state_path():
    override = os.environ.get(ENV_STATE_FILE)
    if override:
        return override
    base = os.environ.get("XDG_CACHE_HOME") or os.path.expanduser("~/.cache")
    return os.path.join(base, "sonsteng-digest", "last-notified.json")


def load_state(path):
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return json.load(fh)
    except (FileNotFoundError, ValueError):
        return {}


def save_state(path, signature, count, now_iso):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump({"signature": signature, "count": count, "notified_at": now_iso}, fh)
    os.replace(tmp, path)


def clear_state(path):
    """Drained to zero: forget the last signature so the next batch re-notifies."""
    try:
        os.remove(path)
    except FileNotFoundError:
        pass


# --------------------------------------------------------------------------- #
# I/O adapters (injectable for tests)
# --------------------------------------------------------------------------- #
def fetch_rows(api_base, token, timeout=30):
    """GET {api_base}/review (admin) -> list of full suggestion rows."""
    if not api_base:
        raise RuntimeError("EDIT_API_BASE is required (e.g. https://<worker>/edit/v1).")
    url = api_base.rstrip("/") + "/review"
    req = urllib.request.Request(url, method="GET")
    req.add_header("Accept", "application/json")
    req.add_header("X-Edit-Request", "1")
    # Cloudflare edge bans the default python-urllib UA (error 1010); send a UA.
    req.add_header("User-Agent", "sonsteng-digest-push/1.0")
    if token:
        req.add_header("Authorization", "Bearer " + token)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", "replace")  # token is never echoed in it
        raise RuntimeError("GET /review -> HTTP %d: %s" % (exc.code, body))
    except urllib.error.URLError as exc:
        raise RuntimeError("GET /review unreachable: %s" % (exc.reason,))
    return payload.get("items") or payload.get("suggestions") or []


def resolve_topic():
    topic = os.environ.get(ENV_NTFY_TOPIC)
    if topic:
        return topic.strip()
    try:
        with open(DEFAULT_TOPIC_FILE, "r", encoding="utf-8") as fh:
            topic = fh.read().strip()
            if topic:
                return topic
    except FileNotFoundError:
        pass
    raise RuntimeError(
        "No ntfy topic: set %s or create %s" % (ENV_NTFY_TOPIC, DEFAULT_TOPIC_FILE))


def publish_ntfy(topic, title, body, click_url, timeout=15,
                 server=None, priority="default", tags="pencil2"):
    """POST the digest to ntfy. Content-light body; Click header = review URL."""
    server = (server or os.environ.get(ENV_NTFY_SERVER) or DEFAULT_NTFY_SERVER).rstrip("/")
    url = "%s/%s" % (server, topic)
    req = urllib.request.Request(url, data=body.encode("utf-8"), method="POST")
    req.add_header("Title", title)
    req.add_header("Tags", tags)
    req.add_header("Priority", priority)
    if click_url:
        req.add_header("Click", click_url)
    req.add_header("User-Agent", "sonsteng-digest-push/1.0")
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.status


# --------------------------------------------------------------------------- #
# Orchestration (thin; all the logic above is pure + tested)
# --------------------------------------------------------------------------- #
def review_url_from_env():
    explicit = os.environ.get(ENV_REVIEW_URL)
    if explicit:
        return explicit.rstrip("/")
    origin = os.environ.get(ENV_EDIT_ORIGIN)
    if origin:
        # EDIT_ORIGIN is a COMMA-SEPARATED LIST in the Worker's own config as of
        # the Access door (plan KTD6) — the Worker answers on both the Access
        # hostname and the workers.dev fallback. This reads a *process* env var,
        # not the Worker's, so the two are not the same value today; but the names
        # are identical and copying one into the other is the obvious mistake.
        # Take the first entry rather than concatenating the whole list into a
        # URL, which would have produced a click-through that silently 404s.
        first = origin.split(",")[0].strip()
        if first:
            return first.rstrip("/") + "/edit/review"
    return DEFAULT_REVIEW_URL


def run(*, dry_run=False, fetch=fetch_rows, publish=publish_ntfy,
        topic_resolver=resolve_topic, state_path=None, now_iso=None, out=None):
    """Returns a dict describing the outcome (also the machine-readable summary)."""
    out = out or sys.stdout
    state_path = state_path or default_state_path()
    now_iso = now_iso or __import__("datetime").datetime.now(
        __import__("datetime").timezone.utc).replace(microsecond=0).isoformat()

    review_url = review_url_from_env()
    rows = fetch(os.environ.get(ENV_API_BASE), os.environ.get(ENV_SERVICE_TOKEN))
    digest = build_digest(rows, review_url)
    prev = load_state(state_path)
    prev_sig = prev.get("signature", "")

    result = {
        "count": digest.count,
        "signature": digest.signature,
        "review_url": review_url,
        "by_matter": digest.by_matter,
        "notified": False,
        "reason": "",
        "dry_run": dry_run,
    }

    if digest.count == 0:
        result["reason"] = "nothing_pending"
        if not dry_run:
            clear_state(state_path)  # so the next accumulation notifies again
        print("[digest] nothing pending; quiet.", file=out)
        return result

    if not should_notify(digest, prev_sig):
        result["reason"] = "unchanged_since_last_notify"
        print("[digest] %d pending but set unchanged since last notify; quiet." % digest.count,
              file=out)
        return result

    title, body = digest.title(), digest.body()
    print("[digest] WOULD NOTIFY" if dry_run else "[digest] NOTIFY", file=out)
    print("  title: " + title, file=out)
    print("  click: " + review_url, file=out)
    print("  body:\n" + "\n".join("    " + ln for ln in body.splitlines()), file=out)

    if dry_run:
        result["reason"] = "dry_run"
        return result

    topic = topic_resolver()
    publish(topic, title, body, review_url)
    save_state(state_path, digest.signature, digest.count, now_iso)
    result["notified"] = True
    result["reason"] = "sent"
    return result


def main(argv=None):
    ap = argparse.ArgumentParser(description="Batched cumulative ntfy digest of pending editor suggestions.")
    ap.add_argument("--dry-run", action="store_true",
                    help="Print the would-send payload; publish nothing and touch no state file.")
    ap.add_argument("--state-file", default=None, help="Override the dedupe state path.")
    args = ap.parse_args(argv)
    try:
        run(dry_run=args.dry_run, state_path=args.state_file)
    except RuntimeError as exc:
        print("[digest] ERROR: %s" % exc, file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
