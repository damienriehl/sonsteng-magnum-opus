"""No collaborator email address may be committed to this repository.

WHY THIS EXISTS. The README pitches this project to open-source adopters, so the
repo is headed public, and git history is permanent — an address committed today
ships with the first public release and cannot be recalled by deleting it later.
The Cloudflare Access door needs John's and Roger's real addresses to map an
identity to an editor slot, and the whole design keeps them OUT of the tree:
`EDIT_ACCESS_EMAILS` is a deploy SECRET (`wrangler secret put`, or the Cloudflare
dashboard), never a var in `wrangler.jsonc`. That was a convention; this makes it
a gate.

WHAT IT DOES NOT SCAN, and why. `data/` and `site/` are the practicum itself —
fictional law-firm content full of deliberately invented client addresses in
engagement letters and matter files. Sweeping those would fail on the product's
own teaching material. The scan covers the places a real collaborator address
would plausibly be pasted: the Worker, its config, the docs, and the tooling.

The sweep deliberately does NOT name the people it protects. A guard that
hardcodes the addresses would leak exactly what it exists to prevent.
"""
import re
import subprocess
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]

EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")

# Where a real address could realistically be pasted by a future session.
SCAN_PREFIXES = ("app/worker/", "docs/", "tools/")
SCAN_FILES = ("app/worker/wrangler.jsonc", "RESUME.md", "README.md")

# Synthetic/host-local identities the project already uses on purpose.
ALLOWED_DOMAIN_SUFFIXES = (".local", ".example", ".invalid", ".test")
ALLOWED_DOMAINS = {
    "example.com", "example.org", "example.net",
    "w3.org", "schema.org",
    "users.noreply.github.com", "noreply.anthropic.com",
    "sentry.io", "ntfy.sh", "cloudflare.com",
}
ALLOWED_LOCALPARTS = {"noreply", "no-reply", "you", "your-email", "someone", "user", "seed"}

# The repository owner's own address is the committed git-author identity (see
# tools/build_history.py's slot->author map). It is his, it predates this gate,
# and it is not the collaborator PII this guard exists to stop.
OWNER_ADDRESS = "damienriehl@gmail.com"

SKIP_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp", ".ico", ".woff", ".woff2", ".pdf", ".zip"}
SKIP_PARTS = {"node_modules", "build", ".git"}


def scanned_files():
    out = subprocess.run(
        ["git", "ls-files", "-z"], cwd=REPO, capture_output=True, text=True, check=True
    ).stdout
    for rel in out.split("\0"):
        if not rel:
            continue
        if not (rel.startswith(SCAN_PREFIXES) or rel in SCAN_FILES):
            continue
        p = Path(rel)
        if p.suffix.lower() in SKIP_SUFFIXES or (SKIP_PARTS & set(p.parts)):
            continue
        yield rel, REPO / rel


# Source files contain literal escape sequences inside string literals — e.g.
# "sha2\tdamienriehl@gmail.com\tfeat: …" in a tab-separated git-log fixture. Read
# as raw text, the backslash is not part of an address but the letter after it
# is, so a naive scan reports "tdamienriehl@gmail.com" and then fails to match it
# against the owner allowlist. Neutralise the escapes before scanning.
ESCAPES = re.compile(r"\\[trn0]")


def offending(text):
    """Email-shaped strings that are not placeholders, synthetic, or the owner's."""
    text = ESCAPES.sub(" ", text)
    bad = []
    for m in EMAIL.finditer(text):
        addr = m.group(0).rstrip(".")
        local, _, domain = addr.rpartition("@")
        domain = domain.lower()
        if addr.lower() == OWNER_ADDRESS:
            continue
        if domain in ALLOWED_DOMAINS or domain.endswith(ALLOWED_DOMAIN_SUFFIXES):
            continue
        if local.lower() in ALLOWED_LOCALPARTS:
            continue
        bad.append(addr)
    return bad


def mask(addr):
    local, _, domain = addr.rpartition("@")
    return f"{local[:1]}***@***{domain[-4:]}"


class NoCommittedPII(unittest.TestCase):
    def test_no_collaborator_address_in_worker_docs_or_tools(self):
        hits = {}
        for rel, path in scanned_files():
            try:
                text = path.read_text(encoding="utf-8", errors="ignore")
            except (OSError, UnicodeDecodeError):
                continue
            bad = offending(text)
            if bad:
                # Report the FILE and a masked form only — printing the address in
                # a CI log would defeat the gate.
                hits[rel] = sorted({mask(a) for a in bad})
        self.assertEqual(
            hits, {},
            "A real email address is committed. This repo is headed public and git history is "
            "permanent. Collaborator addresses belong in the EDIT_ACCESS_EMAILS deploy secret "
            "(wrangler secret put, or the Cloudflare dashboard) — never in a tracked file. "
            f"Offending files: {hits}",
        )

    def test_edit_access_emails_is_never_a_var(self):
        """The map must be a secret. A var lives in wrangler.jsonc, i.e. in git."""
        cfg = (REPO / "app" / "worker" / "wrangler.jsonc").read_text(encoding="utf-8")
        stripped = re.sub(r"^\s*//.*$", "", cfg, flags=re.MULTILINE)
        self.assertNotIn(
            "EDIT_ACCESS_EMAILS", stripped,
            "EDIT_ACCESS_EMAILS must never appear in wrangler.jsonc — it maps real addresses to "
            "editor slots and is a deploy secret, not a var.",
        )

    def test_the_sweep_actually_catches_something(self):
        """A guard that cannot fail is not a guard."""
        self.assertEqual(offending("mail someone@example.com"), [])
        self.assertEqual(offending("apply@sonsteng.local"), [])
        self.assertEqual(offending(OWNER_ADDRESS), [])
        # The escape-sequence case that first broke this scanner.
        self.assertEqual(offending(r'"sha2\tdamienriehl@gmail.com\tfeat: x"'), [])
        caught = offending("a.person@a-real-university.edu")
        self.assertEqual(caught, ["a.person@a-real-university.edu"])
        self.assertEqual(mask(caught[0]), "a***@***.edu")


if __name__ == "__main__":
    unittest.main()
