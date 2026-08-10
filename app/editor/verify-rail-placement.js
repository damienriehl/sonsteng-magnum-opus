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

   WHY THE DEFAULT MATTERS: preflight used to run this gate ONLY when TARGET_URL
   was set, so it was skipped on every ordinary run — and once the Access door
   retires the ?t= tokens there would be no way to produce such a URL at all, and
   the gate would have sat "SKIP" forever without anyone deciding to drop it.
   It now always runs against the harness. Do NOT "fix" that by pointing it at
   the Access hostname with a Cloudflare service token: a service-token assertion
   carries `common_name` and no `email` claim, and access-jwt.js deliberately
   returns null for exactly that shape, so making this work would mean loosening
   the auth path to satisfy a test. The geometry is the point, and the harness
   reproduces it.
   ============================================================================ */
const puppeteer = require('/home/damienriehl/.npm/_npx/7d92d9a2d2ccc630/node_modules/puppeteer');
const HARNESS = process.env.TARGET_URL || 'file:///home/damienriehl/Coding Projects/sonsteng-magnum-opus/app/editor/test-harness.html';
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const WIDTHS = [1600, 1400, 1236, 1180, 1100, 1024, 900, 768, 480, 390];

(async () => {
  const browser = await puppeteer.launch({
    headless: process.env.HEADFUL !== '1' && process.env.HEADLESS !== '0',
    args: ['--no-sandbox'], executablePath: '/snap/bin/chromium',
  });
  const page = await browser.newPage();
  let bad = 0;
  for (const w of WIDTHS) {
    await page.setViewport({ width: w, height: 1000 });
    await page.goto(HARNESS, { waitUntil: 'load' });
    await page.waitForFunction(() => window.SonstengEditor && window.SonstengEditor.ready() >= 1, { timeout: 20000 });
    // Placement re-runs as late assets land (fonts swapping in change every
    // measurement), so settle before measuring — 500ms caught a mid-state and
    // reported a clean layout as broken.
    await sleep(1800);
    const res = await page.evaluate(() => {
      // Check each rail against EVERY piece of text on the page, not just its own
      // block. The first version of this test only compared a rail with the block
      // it belonged to, which is exactly the overlap that cannot happen — and it
      // passed clean while the icons were visibly sitting on the lede and on a
      // neighbouring heading. A control may not land on ANY text.
      const texts = [];
      document.querySelectorAll('main *').forEach((el) => {
        if (el.closest('.eb-tools')) return;                 // the rails themselves
        let own = '';
        for (const n of el.childNodes) if (n.nodeType === 3) own += n.nodeValue;
        if (!own.trim()) return;
        const cs = getComputedStyle(el);
        if (cs.display === 'none' || cs.visibility === 'hidden') return;
        const r = el.getBoundingClientRect();
        if (r.width > 0 && r.height > 0) texts.push({ r, tag: el.tagName, t: own.trim().slice(0, 28) });
      });
      const out = [];
      document.querySelectorAll('.eb-tools').forEach((t) => {
        const a = t.getBoundingClientRect();
        if (!a.width || !a.height) return;
        let hit = null;
        for (const x of texts) {
          const ox = Math.min(a.right, x.r.right) - Math.max(a.left, x.r.left);
          const oy = Math.min(a.bottom, x.r.bottom) - Math.max(a.top, x.r.top);
          if (ox > 1 && oy > 1) { hit = x; break; }
        }
        out.push({
          gutter: t.classList.contains('eb-tools--gutter'),
          overlaps: !!hit,
          hitText: hit ? (hit.tag + ' “' + hit.t + '”') : '',
          offRight: Math.round(a.right) > document.documentElement.clientWidth,
        });
      });
      return out;
    });
    const over = res.filter((r) => r.overlaps);
    const off = res.filter((r) => r.offRight);
    const g = res.filter((r) => r.gutter).length;
    if (over.length || off.length) bad++;
    if (over.length) console.log('        e.g. rail over ' + over[0].hitText);
    console.log(`${String(w).padStart(5)}px  rails=${res.length}  gutter=${g}  flow=${res.length - g}  ` +
      `OVERLAP=${over.length}  OFFSCREEN=${off.length}  ${over.length || off.length ? '<-- BAD' : 'ok'}`);
  }
  console.log(bad ? `\nFAIL: ${bad} width(s) place controls over the text` : '\nPASS: no width places controls over the text');
  await browser.close();
  process.exit(bad ? 1 : 0);
})();
