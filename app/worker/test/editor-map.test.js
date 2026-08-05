// editor-map.test.js — the universal allowlist + injector security helpers:
// map-allowlist rejection of unknown source_ref, injector SSRF path rejection,
// escaped-JSON-island XSS inertness, json_scalar path-forgery rejection, and
// instructor doc resolution (uniform-404 inputs).
import { test } from "node:test";
import assert from "node:assert/strict";
import {
  resolvePagePath, buildUpstreamUrl, lookupBlock, lookupBlocks, validateJsonScalar,
  resolveInstructorDoc, escapeJsonIsland, pageBlockDescriptors, projectPendingItems,
  EDITOR_MAP,
} from "../src/editor-map.js";

// A known-good page + block from the real bundle. The map registers EVERY
// hostable page, including navigational ones (platform home, matter library)
// that carry no editable blocks at all — so "first page" is not necessarily a
// page with blocks. Take the first page that actually has one.
const somePage = Object.keys(EDITOR_MAP.pages).find(
  (k) => (EDITOR_MAP.pages[k] || []).length > 0);
const someBlock = EDITOR_MAP.pages[somePage][0];

test("resolvePagePath accepts an allowlisted page (dir + index.html forms)", () => {
  assert.ok(resolvePagePath(somePage)); // full "…/index.html"
  const dir = somePage.replace(/index\.html$/, "");
  assert.ok(resolvePagePath(dir)); // trailing slash
  assert.ok(resolvePagePath(dir.replace(/\/$/, ""))); // no trailing slash
});

test("injector SSRF: traversal / absolute / protocol-relative / scheme rejected", () => {
  for (const hostile of [
    "../../etc/passwd",
    "..%2f..%2fetc%2fpasswd",
    "//evil.example.com/",
    "https://evil.example.com/x",
    "http:example",
    "matters/../../../secret",
    "\\windows\\path",
    "matters/m01\u0000/index.html",
    "not-a-real-page/index.html",
  ]) {
    assert.equal(resolvePagePath(hostile), null, `should reject: ${hostile}`);
  }
});

test("buildUpstreamUrl stays inside the EDIT_UPSTREAM origin+prefix", () => {
  const up = "https://sonsteng-dev.damienriehl.com/platform/";
  const url = buildUpstreamUrl(somePage, up);
  assert.ok(url);
  assert.equal(url.origin, "https://sonsteng-dev.damienriehl.com");
  assert.ok(url.pathname.startsWith("/platform/"));
  // A page key can never escape the origin, even if it looked absolute.
  assert.equal(buildUpstreamUrl("https://evil.com/x", up), null);
});

test("map allowlist: unknown source_ref does not resolve", () => {
  assert.equal(lookupBlock("data/matters/evil.json#pwned", "edit"), null);
  assert.equal(lookupBlocks("data/matters/evil.json#pwned", "edit"), null);
  assert.equal(lookupBlock("../../etc/passwd", "edit"), null);
  // a real one DOES resolve
  assert.ok(lookupBlock(someBlock.source_ref, "edit"));
});

test("shared source_ref resolves every render-site descriptor", () => {
  const sharedRef = "data/jurisdictions/meridian.json#name";
  const descriptors = lookupBlocks(sharedRef, "edit");
  assert.equal(descriptors.length, 10);
  assert.equal(new Set(descriptors.map((b) => b.page)).size, 10);
  assert.deepEqual(lookupBlock(sharedRef, "edit"), descriptors[0],
    "legacy single-block lookup remains the first descriptor");
});

test("page descriptor island carries all occurrences only for shared blocks", () => {
  const sharedRef = "data/jurisdictions/meridian.json#name";
  const sharedPage = EDITOR_MAP.occurrences[sharedRef][0].page;
  const sharedBlock = EDITOR_MAP.pages[sharedPage].find((b) => b.source_ref === sharedRef);
  const projected = pageBlockDescriptors([sharedBlock])[0];
  assert.deepEqual(projected.occurrences, EDITOR_MAP.occurrences[sharedRef]);

  const singleRef = Object.keys(EDITOR_MAP.occurrences)
    .find((ref) => EDITOR_MAP.occurrences[ref].length === 1);
  const singleOccurrence = EDITOR_MAP.occurrences[singleRef][0];
  const singleBlock = EDITOR_MAP.pages[singleOccurrence.page]
    .find((b) => b.source_ref === singleRef && b.index === singleOccurrence.index);
  const singleProjected = pageBlockDescriptors([singleBlock])[0];
  const legacyProjection = {
    index: singleBlock.index,
    kind: singleBlock.kind,
    source_ref: singleBlock.source_ref,
    json_path: singleBlock.json_path || null,
    original_text: singleBlock.original_text,
    original_hash: singleBlock.original_hash,
    has_inline_formatting: !!singleBlock.has_inline_formatting,
    context: singleBlock.context || "",
  };
  assert.equal(JSON.stringify(singleProjected), JSON.stringify(legacyProjection),
    "single-occurrence island descriptor stays byte-identical to the old shape");
});

test("json_scalar path forgery is rejected (json_path must match the map)", () => {
  // Use a genuinely shared json_scalar so the multi-occurrence lookup cannot
  // accidentally weaken the existing source_ref -> sole json_path guard.
  const sharedRef = Object.keys(EDITOR_MAP.occurrences).find((ref) => {
    if (EDITOR_MAP.occurrences[ref].length < 2) return false;
    return lookupBlock(ref, "edit")?.kind === "json_scalar";
  });
  const jsBlock = lookupBlock(sharedRef, "edit");
  assert.ok(jsBlock, "bundle must contain a shared json_scalar block");
  assert.ok(lookupBlocks(sharedRef, "edit").length > 1);
  assert.ok(validateJsonScalar(jsBlock.source_ref, jsBlock.json_path, "edit"));
  // a forged json_path for the same source_ref is rejected
  assert.equal(validateJsonScalar(jsBlock.source_ref, "attacker.controlled.path", "edit"), null);
});

test("escaped JSON island renders a <script> XSS payload inert", () => {
  const payload = { items: [{ new_text: "</script><script>alert(document.cookie)</script>" }] };
  const island = escapeJsonIsland(payload);
  // No raw </script> or angle brackets or ampersand survive.
  assert.ok(!island.includes("</script>"));
  assert.ok(!island.includes("<"));
  assert.ok(!island.includes(">"));
  assert.ok(!island.includes("&"));
  assert.ok(island.includes("\\u003c") && island.includes("\\u003e"));
  // It is still valid JSON that round-trips to the ORIGINAL string (inert data).
  const back = JSON.parse(island);
  assert.equal(back.items[0].new_text, "</script><script>alert(document.cookie)</script>");
});

test("escaped JSON island escapes U+2028/U+2029 (JS line terminators)", () => {
  const island = escapeJsonIsland({ t: "a\u2028b\u2029c" });
  assert.ok(!/[\u2028\u2029]/.test(island));
  assert.ok(island.includes("\\u2028") && island.includes("\\u2029"));
});

test("projectPendingItems emits the client #edits-data item shape", () => {
  const rows = [
    { block_anchor: "matters/m05/index.html:4", source_ref: "x#a", status: "declined",
      kind: "comment", comment: "hi", new_text: null, decision_note: "nope" },
    { block_anchor: "matters/m05/index.html:1", source_ref: "x#b", status: "pending",
      kind: "prose", new_text: "better", comment: null },
  ];
  const out = projectPendingItems(rows);
  // comment row: no new_text overlay field (comments render as margin bubbles).
  assert.deepEqual(out[0], { block_index: 4, source_ref: "x#a", status: "declined", kind: "comment", preview: "hi", note: "nope" });
  // prose row: carries the FULL new_text for the WYSIWYG pending overlay (base_hash/
  // map_version/attribution are absent here because the row has none of them).
  assert.deepEqual(out[1], { block_index: 1, source_ref: "x#b", status: "pending", kind: "prose", preview: "better", new_text: "better" });
  // no decision_note -> no `note` key
  assert.ok(!("note" in out[1]));
});

test("instructor doc resolution: valid matter/doc resolves; junk is null", () => {
  assert.ok(resolveInstructorDoc("m01", "facts"));
  assert.ok(resolveInstructorDoc("m01", "answer-key"));
  assert.ok(resolveInstructorDoc("m01", "instructor_notes"));
  // uniform-404 inputs (bad matter id, bad doc type, traversal):
  assert.equal(resolveInstructorDoc("m99xx", "facts"), null);
  assert.equal(resolveInstructorDoc("m01", "../secret"), null);
  assert.equal(resolveInstructorDoc("m01", "personas"), null);
  assert.equal(resolveInstructorDoc("", ""), null);
});

test("public and instructor blocks live in DISJOINT maps (dual-scope resolution must try both, not prefer instructor)", () => {
  // Regression for the integration bug: an editor holding BOTH edit+instructor
  // scopes had public-page edits routed to the instructor index (priority-by-
  // scope) and rejected. A public block must resolve under 'edit' scope and NOT
  // under 'instructor', and vice-versa — so the endpoint must resolve scope by
  // which map holds the source_ref.
  const publicRef = someBlock.source_ref;
  assert.ok(lookupBlock(publicRef, "edit"), "public block resolves under edit scope");
  assert.equal(lookupBlock(publicRef, "instructor"), null, "public block must NOT resolve under instructor scope");

  // An instructor doc block resolves under instructor and not edit.
  const instrDoc = resolveInstructorDoc("m01", "facts");
  if (instrDoc && instrDoc.blocks && instrDoc.blocks.length) {
    const instrRef = instrDoc.blocks[0].source_ref;
    assert.ok(lookupBlock(instrRef, "instructor"), "instructor block resolves under instructor scope");
    assert.equal(lookupBlock(instrRef, "edit"), null, "instructor block must NOT resolve under edit scope");
  }
});
