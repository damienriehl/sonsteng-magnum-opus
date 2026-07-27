/* ============================================================================
   verify-rail-placement.js — prove the edit/comment rail never lands on the text.

   Written 2026-07-27 after shipping the same bug twice. Both times the rail's
   placement was decided by a media query — "the window is wider than 1100px,
   therefore there is a gutter" — and both times Damien sent a screenshot of the
   icons sitting on top of the words. "The window is wide" and "there is room
   beside THIS block" are different questions, and no amount of care in choosing
   the breakpoint fixes asking the wrong one.

   Placement is measured at runtime now (layoutRails in editor.js). This asserts
   the property that actually matters, geometrically, at ten widths: the rail's
   box never intersects its own block's box, and never runs off-screen.

   Run:  DISPLAY=:0 node app/editor/verify-rail-placement.js
         DISPLAY=:0 TARGET_URL='https://…/edit/matters/m01-…/?t=…' node …
   Defaults to the local harness; point TARGET_URL at a real editor page to check
   the layout the reviewers actually get. Exit code 1 on any overlap.
   ============================================================================ */
const puppeteer = require('/home/damienriehl/.npm/_npx/7d92d9a2d2ccc630/node_modules/puppeteer');
const HARNESS = process.env.TARGET_URL || 'file:///home/damienriehl/Coding Projects/sonsteng-magnum-opus/app/editor/test-harness.html';
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const WIDTHS = [1600, 1400, 1236, 1180, 1100, 1024, 900, 768, 480, 390];

(async () => {
  const browser = await puppeteer.launch({
    headless: false, args: ['--no-sandbox'], executablePath: '/snap/bin/chromium',
  });
  const page = await browser.newPage();
  let bad = 0;
  for (const w of WIDTHS) {
    await page.setViewport({ width: w, height: 1000 });
    await page.goto(HARNESS, { waitUntil: 'load' });
    await page.waitForFunction(() => window.SonstengEditor && window.SonstengEditor.ready() >= 1, { timeout: 20000 });
    await sleep(500);
    const res = await page.evaluate(() => {
      const out = [];
      document.querySelectorAll('.eb-tools').forEach((t) => {
        let blk = t.nextElementSibling;
        while (blk && !blk.classList.contains('eb')) blk = blk.nextElementSibling;
        if (!blk) return;
        const a = t.getBoundingClientRect(), b = blk.getBoundingClientRect();
        // Any horizontal AND vertical intersection with the passage box is an overlap.
        const overlapX = Math.min(a.right, b.right) - Math.max(a.left, b.left);
        const overlapY = Math.min(a.bottom, b.bottom) - Math.max(a.top, b.top);
        const overlaps = overlapX > 1 && overlapY > 1;
        out.push({
          gutter: t.classList.contains('eb-tools--gutter'),
          overlaps,
          railLeft: Math.round(a.left), blockRight: Math.round(b.right),
          offRight: Math.round(a.right) > document.documentElement.clientWidth,
        });
      });
      return out;
    });
    const over = res.filter((r) => r.overlaps);
    const off = res.filter((r) => r.offRight);
    const g = res.filter((r) => r.gutter).length;
    if (over.length || off.length) bad++;
    console.log(`${String(w).padStart(5)}px  rails=${res.length}  gutter=${g}  flow=${res.length - g}  ` +
      `OVERLAP=${over.length}  OFFSCREEN=${off.length}  ${over.length || off.length ? '<-- BAD' : 'ok'}`);
  }
  console.log(bad ? `\nFAIL: ${bad} width(s) place controls over the text` : '\nPASS: no width places controls over the text');
  await browser.close();
  process.exit(bad ? 1 : 0);
})();
