// editor-admin.test.js — the tokenless admin landing page (/edit/admin) and the
// editorial-flags projection it renders.
//
// Two things are under test and they fail in different ways:
//
//   buildEditorialFlags is a FILTER, and the ways a filter goes wrong are all
//   silent — showing your own edits back to you, re-showing what you already
//   saw, dumping the whole history on a first visit, or throwing on one bad row
//   and taking the page with it. Each of those gets its own case.
//
//   renderAdminPage is a RENDERER on the front door, so its failures are a blank
//   region where an empty state belonged, a dead link, or — the one that matters
//   — markup that executes. EDIT_CSP is `script-src 'self'` with no
//   'unsafe-inline', so a <script> on this page would be blocked rather than run;
//   the assertions below hold the stronger line anyway, that no <script token is
//   ever emitted and that hostile input arrives as inert text.

import { test } from "node:test";
import assert from "node:assert/strict";
import { buildEditorialFlags, renderAdminPage } from "../src/editor-admin.js";

const T = (iso) => Date.parse(iso);

// A suggestion row in the shape the store's listAll() returns (editor-store-core
// SELECT_COLS): server-resolved `editor` identity, "<relpath>#<pointer>"
// source_ref, epoch-ms created_at/updated_at.
function row(over = {}) {
  const at = over.updated_at ?? over.created_at ?? T("2026-07-27T12:00:00Z");
  return {
    id: "s-" + Math.random().toString(36).slice(2, 8),
    editor: "slot:john",
    scope: "edit",
    origin: "human",
    kind: "text",
    page: "platform/matters/m01/index.html",
    source_ref: "data/matters/m01/exercise.json#sections.intro.body_md.p0",
    status: "pending",
    created_at: at,
    updated_at: at,
    ...over,
  };
}

// ---- buildEditorialFlags ----------------------------------------------------

test("editorial flags exclude the viewer's own edits", () => {
  const items = [
    row({ editor: "slot:john", updated_at: T("2026-07-27T10:00:00Z") }),
    row({ editor: "slot:roger", updated_at: T("2026-07-27T11:00:00Z") }),
  ];
  const flags = buildEditorialFlags(items, "john", "2026-07-27T09:00:00Z");
  assert.equal(flags.length, 1);
  assert.equal(flags[0].attribution, "RSH");
});

test("editorial flags accept the viewer as a bare slot or a slot: identity", () => {
  const items = [row({ editor: "slot:john", updated_at: T("2026-07-27T10:00:00Z") })];
  assert.equal(buildEditorialFlags(items, "john", "2026-07-27T09:00:00Z").length, 0);
  assert.equal(buildEditorialFlags(items, "slot:john", "2026-07-27T09:00:00Z").length, 0);
  assert.equal(buildEditorialFlags(items, "JOHN", "2026-07-27T09:00:00Z").length, 0);
});

test("editorial flags include other editors' edits newer than last-seen, exclude older", () => {
  const items = [
    row({ editor: "slot:roger", updated_at: T("2026-07-26T08:00:00Z") }), // before
    row({ editor: "slot:roger", updated_at: T("2026-07-27T09:00:00Z") }), // exactly at
    row({ editor: "slot:roger", updated_at: T("2026-07-27T10:00:00Z") }), // after
  ];
  const flags = buildEditorialFlags(items, "john", "2026-07-27T09:00:00Z");
  assert.equal(flags.length, 1, "strictly newer only — an edit seen last visit must not repeat");
  assert.equal(flags[0].when, new Date(T("2026-07-27T10:00:00Z")).toISOString());
});

test("editorial flags are empty on a first visit (lastSeen null), not the whole history", () => {
  const items = [
    row({ editor: "slot:roger", updated_at: T("2026-07-27T10:00:00Z") }),
    row({ editor: "slot:damien", updated_at: T("2026-07-27T11:00:00Z") }),
  ];
  assert.deepEqual(buildEditorialFlags(items, "john", null), []);
  assert.deepEqual(buildEditorialFlags(items, "john", undefined), []);
  assert.deepEqual(buildEditorialFlags(items, "john", ""), []);
});

test("editorial flags are empty (not an error) when nothing is new", () => {
  const items = [row({ editor: "slot:roger", updated_at: T("2026-07-01T10:00:00Z") })];
  let flags;
  assert.doesNotThrow(() => {
    flags = buildEditorialFlags(items, "john", "2026-07-27T09:00:00Z");
  });
  assert.deepEqual(flags, []);
  assert.deepEqual(buildEditorialFlags([], "john", "2026-07-27T09:00:00Z"), []);
});

test("editorial flags sort most-recent-first", () => {
  const items = [
    row({ editor: "slot:roger", updated_at: T("2026-07-27T10:00:00Z") }),
    row({ editor: "slot:damien", updated_at: T("2026-07-27T14:00:00Z") }),
    row({ editor: "slot:roger", updated_at: T("2026-07-27T12:00:00Z") }),
  ];
  const flags = buildEditorialFlags(items, "john", "2026-07-27T00:00:00Z");
  assert.deepEqual(flags.map((f) => f.when), [
    new Date(T("2026-07-27T14:00:00Z")).toISOString(),
    new Date(T("2026-07-27T12:00:00Z")).toISOString(),
    new Date(T("2026-07-27T10:00:00Z")).toISOString(),
  ]);
});

test("editorial flags carry the real attribution labels (JOS / RSH / DR)", () => {
  const items = [
    row({ editor: "slot:john", updated_at: T("2026-07-27T10:00:00Z") }),
    row({ editor: "slot:roger", updated_at: T("2026-07-27T11:00:00Z") }),
    row({ editor: "slot:damien", updated_at: T("2026-07-27T12:00:00Z") }),
  ];
  // Viewer is somebody else entirely, so all three are "other editors".
  const flags = buildEditorialFlags(items, "admin", "2026-07-27T00:00:00Z");
  assert.deepEqual(flags.map((f) => f.attribution), ["DR", "RSH", "JOS"]);
});

test("editorial flags project the document path and the status", () => {
  const items = [row({
    editor: "slot:roger",
    source_ref: "data/matters/m01/exercise.json#sections.intro.body_md.p0",
    status: "drift",
    updated_at: T("2026-07-27T10:00:00Z"),
  })];
  const [flag] = buildEditorialFlags(items, "john", "2026-07-27T00:00:00Z");
  assert.equal(flag.path, "data/matters/m01/exercise.json", "doc is source_ref before the '#'");
  assert.equal(flag.status, "drift");
  assert.deepEqual(Object.keys(flag).sort(), ["attribution", "path", "status", "when"]);
});

test("editorial flags tolerate malformed or missing rows without throwing", () => {
  const items = [
    null,
    undefined,
    42,
    "not a row",
    {},                                        // no editor
    { editor: "" },                            // empty editor
    { editor: "slot:" },                       // unattributable
    { editor: "slot:roger" },                  // no timestamp at all
    { editor: "slot:roger", updated_at: "not a date" },
    { editor: "slot:roger", updated_at: NaN },
    { editor: "slot:roger", updated_at: T("2026-07-27T10:00:00Z") }, // the one good row
  ];
  let flags;
  assert.doesNotThrow(() => {
    flags = buildEditorialFlags(items, "john", "2026-07-27T00:00:00Z");
  });
  assert.equal(flags.length, 1);
  assert.equal(flags[0].attribution, "RSH");
  assert.equal(flags[0].path, "", "a row with no source_ref/page still yields an honest empty path");
  // Non-array and unreadable last-seen inputs are first visits, not exceptions.
  assert.doesNotThrow(() => buildEditorialFlags(null, "john", "2026-07-27T00:00:00Z"));
  assert.deepEqual(buildEditorialFlags(null, "john", "2026-07-27T00:00:00Z"), []);
  assert.deepEqual(buildEditorialFlags(items, "john", "not a date"), []);
});

test("editorial flags fall back to created_at, then page, when updated_at/source_ref are absent", () => {
  const items = [{
    editor: "slot:roger",
    page: "platform/matters/m01/index.html",
    status: "pending",
    created_at: T("2026-07-27T10:00:00Z"),
  }];
  const [flag] = buildEditorialFlags(items, "john", "2026-07-27T00:00:00Z");
  assert.equal(flag.path, "platform/matters/m01/index.html");
  assert.equal(flag.when, new Date(T("2026-07-27T10:00:00Z")).toISOString());
});

// ---- renderAdminPage --------------------------------------------------------

const STUDENT_URL = "https://sonsteng-dev.damienriehl.com/platform/";

async function renderText(over = {}) {
  const res = renderAdminPage({
    items: [],
    reverts: [],
    flags: [],
    viewerLabel: "DR",
    studentViewUrl: STUDENT_URL,
    ...over,
  });
  assert.equal(res.status, 200);
  assert.equal(res.headers.get("content-type"), "text/html; charset=utf-8");
  return res.text();
}

test("admin page renders a populated queue and links to review and history", async () => {
  const items = [
    row({ status: "pending" }),
    row({ status: "pending" }),
    row({ status: "drift" }),
    row({ status: "accepted" }),
  ];
  const html = await renderText({ items });
  assert.match(html, /href="\/edit\/review"/, "must link to the review queue");
  assert.match(html, /href="\/edit\/history\/"/, "must link to the history index");
  assert.match(html, /Review queue/);
  assert.match(html, /<strong>4<\/strong>/, "must summarize the outstanding total");
  assert.match(html, /pending/);
  assert.match(html, /drift/);
  assert.match(html, /Signed in as/);
  assert.match(html, />DR</);
});

test("admin page summarizes revert requests and links to where they are reviewed", async () => {
  const reverts = [
    { id: "r1", editor: "slot:john", doc: "data/firm/firm.json", status: "requested" },
    { id: "r2", editor: "slot:roger", doc: "data/firm/firm.json", status: "done" },
  ];
  const html = await renderText({ reverts });
  assert.match(html, /Revert requests/);
  assert.match(html, /<strong>2<\/strong>/);
  assert.match(html, /1 still open/);
  assert.match(html, /href="\/edit\/review"/);
});

test("admin page renders the student-view link with the given URL", async () => {
  const html = await renderText();
  assert.match(html, /Student view/);
  assert.ok(
    html.includes('href="' + STUDENT_URL + '"'),
    "the student-view URL must be rendered as given"
  );
  assert.match(html, /as a student/i, "must say plainly what the link shows");
});

test("admin page refuses a non-http student-view URL rather than linking it", async () => {
  for (const hostile of ["javascript:alert(1)", "data:text/html,<b>x", "", null, undefined]) {
    const html = await renderText({ studentViewUrl: hostile });
    assert.doesNotMatch(html, /javascript:/i);
    assert.doesNotMatch(html, /href="data:/i);
    assert.match(html, /not configured/i, "an unusable URL gets an honest empty state");
  }
});

test("admin page renders honest empty states for an empty queue and empty flags", async () => {
  const html = await renderText({ items: [], reverts: [], flags: [] });
  assert.match(html, /The queue is clear/i);
  assert.match(html, /Nothing new since your last visit/i);
  assert.match(html, /Nobody has asked for a change to be rolled back/i);
  assert.doesNotMatch(html, /error|failed|undefined|\bNaN\b/i);
  // The empty page is still a working door.
  assert.match(html, /href="\/edit\/review"/);
  assert.match(html, /href="\/edit\/history\/"/);
});

test("admin page renders the editorial flags with attribution, path, status and time", async () => {
  const flags = buildEditorialFlags(
    [
      row({ editor: "slot:roger", status: "pending", updated_at: T("2026-07-27T10:05:00Z") }),
      row({
        editor: "slot:damien",
        status: "needs_human",
        source_ref: "data/firm/firm.json#profile.summary",
        updated_at: T("2026-07-27T14:30:00Z"),
      }),
    ],
    "john",
    "2026-07-27T00:00:00Z"
  );
  const html = await renderText({ flags, viewerLabel: "JOS" });
  assert.match(html, /Since your last visit/);
  assert.match(html, />DR</);
  assert.match(html, />RSH</);
  assert.match(html, /data\/firm\/firm\.json/);
  assert.match(html, /27 Jul 2026, 14:30 UTC/, "timestamps render deterministically in UTC");
  // Each flagged doc links into its history entry (slug = relpath with / -> __).
  assert.match(html, /href="\/edit\/history\/data__firm__firm\.json"/);
  assert.match(html, /needs human/, "underscored statuses read as words");
});

test("admin page emits no <script tag at all (CSP: script-src 'self', no inline)", async () => {
  const html = await renderText({
    items: [row({ status: "pending" })],
    reverts: [{ id: "r1", editor: "slot:john", doc: "d.json", status: "requested" }],
    flags: buildEditorialFlags(
      [row({ editor: "slot:roger", updated_at: T("2026-07-27T10:00:00Z") })],
      "john",
      "2026-07-27T00:00:00Z"
    ),
  });
  assert.doesNotMatch(html, /<script/i, "the admin page must be fully functional with zero JS");
  assert.doesNotMatch(html, /\son[a-z]+\s*=/i, "no inline event-handler attributes either");
});

test("admin page contains no token value or token-bearing URL", async () => {
  const html = await renderText({
    items: [row({ status: "pending" })],
    flags: buildEditorialFlags(
      [row({ editor: "slot:roger", updated_at: T("2026-07-27T10:00:00Z") })],
      "john",
      "2026-07-27T00:00:00Z"
    ),
  });
  assert.doesNotMatch(html, /[?&]t=/, "no ?t= anywhere — this door carries no secret");
  assert.doesNotMatch(html, /token|bearer|EDIT_TOKEN|Authorization/i);
});

test("admin page HTML-escapes a hostile path, label and status", async () => {
  const hostile = "\"><script>alert(1)</script>";
  const flags = buildEditorialFlags(
    [row({
      editor: "slot:" + hostile,
      source_ref: hostile + "#p0",
      status: hostile,
      updated_at: T("2026-07-27T10:00:00Z"),
    })],
    "john",
    "2026-07-27T00:00:00Z"
  );
  assert.equal(flags.length, 1, "the hostile row is rendered, not silently dropped");
  const html = await renderText({ flags });
  assert.doesNotMatch(html, /<script/i, "hostile input must never become executable markup");
  assert.ok(html.includes("&lt;script&gt;"), "it must survive as inert escaped text");
  assert.doesNotMatch(html, /href="[^"]*script/i, "and must never become an href");
});

test("admin page escapes a hostile viewer label", async () => {
  const hostile = "<img src=x onerror=alert(1)>";
  const html = await renderText({ viewerLabel: hostile });
  assert.ok(!html.includes(hostile), "the raw tag must not survive into the markup");
  assert.doesNotMatch(html, /<img/i, "no element is ever created from a label");
  // It IS still shown, as inert text — the escaped form carries the whole string,
  // handler and all, which is exactly what escaping means.
  assert.ok(html.includes("&lt;img src=x onerror=alert(1)&gt;"));
});

test("renderAdminPage survives being called with nothing at all", async () => {
  let res;
  assert.doesNotThrow(() => { res = renderAdminPage(); });
  assert.equal(res.status, 200);
  const html = await res.text();
  assert.match(html, /The queue is clear/i);
  assert.match(html, /Nothing new since your last visit/i);
  assert.doesNotMatch(html, /<script/i);
});
