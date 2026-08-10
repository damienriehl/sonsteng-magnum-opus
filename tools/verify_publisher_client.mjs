/* Real-browser contract for the one-shot human Publisher authorization control. */
import { createRequire } from "node:module";
import http from "node:http";
import { PUBLISHER_JS } from "../app/worker/src/editor-assets.js";

const require = createRequire(import.meta.url);
function loadPuppeteer() {
  for (const candidate of [process.env.PUP_DIR, "puppeteer",
    "/home/damienriehl/.npm/_npx/7d92d9a2d2ccc630/node_modules/puppeteer"].filter(Boolean)) {
    try { return require(candidate); } catch {}
  }
  throw new Error("Puppeteer unavailable (set PUP_DIR or install puppeteer)");
}

const binding = { id:"release-1",target_batch_id:"batch-2",base_sha:"a".repeat(40),
  candidate_sha:"b".repeat(40),generator_id:"generator-1",evidence_hash:"evidence-1",
  manifest_hash:"manifest-1",membership_hash:"members-1" };

async function run() {
  const html = `<label><input id="pub-confirm" type="checkbox">Confirm</label>
    <button id="pub-authorize" disabled>Authorize</button><p id="pub-live" tabindex="-1"></p>
    <script id="publisher-binding" type="application/json">${JSON.stringify(binding)}</script>`;
  const server = http.createServer((_request, response) => {
    response.setHeader("content-type", "text/html; charset=utf-8"); response.end(html);
  });
  await new Promise((resolve) => server.listen(0, "127.0.0.1", resolve));
  const browser = await loadPuppeteer().launch({
    executablePath:process.env.CHROME_BIN || process.env.CHROMIUM_PATH || "/snap/bin/chromium",
    headless:process.env.HEADFUL !== "1" && process.env.HEADLESS !== "0",args:["--no-sandbox","--disable-dev-shm-usage"],
  });
  try {
    const page = await browser.newPage();
    await page.goto(`http://127.0.0.1:${server.address().port}/`, { waitUntil:"domcontentloaded" });
    await page.evaluate(() => {
      window.fetchCalls = 0;
      window.fetch = () => { window.fetchCalls += 1; return new Promise((resolve) => {
        window.finishAuthorization = () => resolve({ ok:true,json:async () => ({ ok:true }) });
      }); };
    });
    await page.addScriptTag({ content:PUBLISHER_JS });
    await page.click("#pub-confirm");
    await page.click("#pub-authorize");
    await page.waitForFunction(() => window.fetchCalls === 1);
    await page.click("#pub-confirm"); await page.click("#pub-confirm");
    await page.click("#pub-authorize");
    const pending = await page.evaluate(() => ({ calls:window.fetchCalls,
      disabled:document.querySelector("#pub-authorize").disabled,
      busy:document.querySelector("#pub-authorize").ariaBusy }));
    if (pending.calls !== 1 || !pending.disabled || pending.busy !== "true")
      throw new Error("authorization was not single-flight while pending");
    await page.evaluate(() => window.finishAuthorization());
    await page.waitForFunction(() => document.querySelector("#pub-authorize").textContent === "Authorized");
    const settled = await page.evaluate(() => ({ calls:window.fetchCalls,
      disabled:document.querySelector("#pub-authorize").disabled,
      busy:document.querySelector("#pub-authorize").ariaBusy,
      focused:document.activeElement?.id,status:document.querySelector("#pub-live").textContent }));
    if (settled.calls !== 1 || !settled.disabled || settled.busy !== "false" ||
        settled.focused !== "pub-live" || !settled.status.includes("Release authorized"))
      throw new Error("authorization did not settle accessibly and exactly once");
    console.log("PUBLISHER CLIENT PASS");
    return 0;
  } finally {
    await browser.close();
    await new Promise((resolve) => server.close(resolve));
  }
}

run().then((code) => process.exit(code)).catch((error) => {
  console.error("PUBLISHER CLIENT FAIL:", error.message); process.exit(1);
});
