// BYOK upstream resolution: provider/model validation, no_hosted_key path,
// skipBudget semantics, and the key-never-logged source scan.
import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync, readdirSync, statSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";

import { resolveUpstream, resolvePanelUpstreams, providerModelConfig } from "../src/byok.js";

const ENV = {
  ANTHROPIC_API_KEY: "sk-hosted-key",
  MODEL_DEFAULT_ANTHROPIC: "claude-haiku-4-5",
  MODEL_DEFAULT_OPENAI: "gpt-4o-mini",
  MODEL_DEFAULT_GOOGLE: "gemini-2.5-flash",
  MODEL_ALLOW_ANTHROPIC: "claude-haiku-4-5,claude-sonnet-4-5",
  MODEL_ALLOW_OPENAI: "gpt-4o-mini,gpt-4o",
  MODEL_ALLOW_GOOGLE: "gemini-2.5-flash",
};

test("no byok + hosted key present -> hosted anthropic, budget ENFORCED", () => {
  const up = resolveUpstream(ENV, undefined);
  assert.ok(up.ok);
  assert.equal(up.mode, "hosted");
  assert.equal(up.provider, "anthropic");
  assert.equal(up.model, "claude-haiku-4-5");
  assert.equal(up.skipBudget, false);
});

test("no byok + NO hosted key -> typed no_hosted_key error", () => {
  const up = resolveUpstream({ ...ENV, ANTHROPIC_API_KEY: undefined }, undefined);
  assert.equal(up.ok, false);
  assert.equal(up.code, "no_hosted_key");
  assert.match(up.message, /no hosted demo key/i);
});

test("valid byok -> that provider, budget SKIPPED (their money)", () => {
  for (const [provider, model] of [["anthropic", "claude-haiku-4-5"], ["openai", "gpt-4o-mini"], ["google", "gemini-2.5-flash"]]) {
    const up = resolveUpstream(ENV, { provider, api_key: "user-key-12345" });
    assert.ok(up.ok, provider);
    assert.equal(up.mode, "byok");
    assert.equal(up.provider, provider);
    assert.equal(up.model, model); // default when absent
    assert.equal(up.skipBudget, true);
    assert.equal(up.apiKey, "user-key-12345");
  }
});

test("byok works even when the hosted key is unset (the whole point)", () => {
  const up = resolveUpstream({ ...ENV, ANTHROPIC_API_KEY: undefined }, { provider: "google", api_key: "user-key-12345" });
  assert.ok(up.ok);
  assert.equal(up.mode, "byok");
});

test("byok model must be in the per-provider allowlist", () => {
  const ok = resolveUpstream(ENV, { provider: "openai", api_key: "user-key-12345", model: "gpt-4o" });
  assert.ok(ok.ok);
  assert.equal(ok.model, "gpt-4o");
  const bad = resolveUpstream(ENV, { provider: "openai", api_key: "user-key-12345", model: "gpt-3.5-turbo" });
  assert.equal(bad.ok, false);
  assert.equal(bad.code, "validation_error");
  const retired = resolveUpstream(ENV, {
    provider: "google",
    api_key: "user-key-12345",
    model: "gemini-2.0-flash",
  });
  assert.equal(retired.ok, false);
  assert.equal(retired.code, "validation_error");
});

test("byok rejects unknown providers and missing/short keys", () => {
  assert.equal(resolveUpstream(ENV, { provider: "mistral", api_key: "user-key-12345" }).ok, false);
  assert.equal(resolveUpstream(ENV, { provider: "openai" }).ok, false);
  assert.equal(resolveUpstream(ENV, { provider: "openai", api_key: "abc" }).ok, false);
  assert.equal(resolveUpstream(ENV, "not-an-object").ok, false);
  assert.equal(resolveUpstream(ENV, ["array"]).ok, false);
});

test("model config comes from env vars, with safe fallbacks", () => {
  const cfg = providerModelConfig({});
  assert.equal(cfg.anthropic.default, "claude-haiku-4-5");
  assert.equal(cfg.openai.default, "gpt-4o-mini");
  assert.equal(cfg.google.default, "gemini-2.5-flash");
  const custom = providerModelConfig({ MODEL_ALLOW_GOOGLE: "a,b , c" });
  assert.deepEqual(custom.google.allow, ["a", "b", "c"]);
});

test("published API contract matches the Worker contract", () => {
  const testDir = dirname(fileURLToPath(import.meta.url));
  const workerContract = readFileSync(join(testDir, "..", "API-CONTRACTS.md"), "utf8");
  const publishedContract = readFileSync(
    join(testDir, "..", "..", "..", "site", "platform", "data", "api-contracts.md"),
    "utf8",
  );
  assert.equal(publishedContract, workerContract);
});

test("panel resolution never multiplies one hosted or BYOK key into synthetic graders", () => {
  const hosted = resolvePanelUpstreams(ENV, {});
  assert.equal(hosted.ok, true);
  assert.equal(hosted.graders.length, 1);
  assert.equal(hosted.graders[0].mode, "hosted");

  const single = resolvePanelUpstreams(ENV, {
    byok: { provider: "openai", api_key: "single-key-12345" },
  });
  assert.equal(single.ok, true);
  assert.equal(single.graders.length, 1);
  assert.equal(single.graders[0].provider, "openai");
});

test("a multi-provider panel requires distinct, valid explicit BYOK providers", () => {
  const panel = resolvePanelUpstreams(ENV, {
    byokPanel: [
      { provider: "anthropic", api_key: "anthropic-key-123" },
      { provider: "openai", api_key: "openai-key-12345" },
      { provider: "google", api_key: "google-key-12345" },
    ],
  });
  assert.equal(panel.ok, true);
  assert.equal(panel.graders.length, 3);

  const duplicate = resolvePanelUpstreams(ENV, {
    byokPanel: [
      { provider: "openai", api_key: "openai-key-12345" },
      { provider: "openai", api_key: "another-key-123" },
      { provider: "google", api_key: "google-key-12345" },
    ],
  });
  assert.equal(duplicate.ok, false);
  assert.match(duplicate.message, /distinct providers/i);

  assert.equal(resolvePanelUpstreams(ENV, { byok: {}, byokPanel: [] }).ok, false);
  assert.equal(resolvePanelUpstreams(ENV, { byokPanel: [{ provider: "openai", api_key: "one-key-12345" }] }).ok, false);
  assert.equal(resolvePanelUpstreams(ENV, { byokPanel: [
    { provider: "openai", api_key: "one-key-12345" },
    { provider: "google", api_key: "two-key-12345" },
  ] }).ok, false);
});

// ---- Key-never-logged guarantee (source scan) -------------------------------
// Every logMeta(...) call in src/ must be free of api_key / apiKey / byok
// references, and the providers + byok modules must not call console.* at all.
test("no logging path touches the API key", () => {
  const __dirname = dirname(fileURLToPath(import.meta.url));
  const srcDir = join(__dirname, "..", "src");
  const files = [];
  (function walk(dir) {
    for (const name of readdirSync(dir)) {
      const p = join(dir, name);
      if (statSync(p).isDirectory()) walk(p);
      else if (name.endsWith(".js")) files.push(p);
    }
  })(srcDir);
  assert.ok(files.length >= 10, "source scan must actually find the modules");

  for (const f of files) {
    const src = readFileSync(f, "utf8");
    // 1) logMeta call sites never reference the key or the byok block.
    for (const line of src.split("\n")) {
      if (line.includes("logMeta(")) {
        assert.ok(!/api_?key/i.test(line), `${f}: logMeta line references a key: ${line.trim()}`);
        assert.ok(!/\bbyok\b/.test(line), `${f}: logMeta line references byok: ${line.trim()}`);
      }
    }
    // 2) The modules that HOLD the key (byok + providers) never log at all.
    if (f.includes("/providers/") || f.endsWith("/byok.js")) {
      assert.ok(!src.includes("console."), `${f}: provider/byok modules must not log`);
      assert.ok(!src.includes("logMeta"), `${f}: provider/byok modules must not log`);
    }
  }
});
