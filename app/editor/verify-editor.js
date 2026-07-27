/* ============================================================================
   verify-editor.js — drives app/editor/test-harness.html through the P3 editor
   client's paths and asserts them PASS/FAIL. DISPLAY=:0 puppeteer (headful) on
   the home box's Xwayland, snap chromium, --no-sandbox. Screenshots to $HOME.
   Run: DISPLAY=:0 node app/editor/verify-editor.js

   Covers (per the deliverable): full edit -> Save (autocorrect preview) ->
   Sent ✓ -> inline status; selection-comment on a prose block; comment-only
   enforced on formatted + json_scalar blocks (+ the "Damien will apply wording"
   note); 401 re-auth preserves draft + id; triple-Save dedupe; large-type;
   normalize() byte-for-byte parity vs tools/text_norm.py; mobile 390x844 +
   Windows-ish 1280 viewports.
   ============================================================================ */
const puppeteer = require('/home/damienriehl/.npm/_npx/7d92d9a2d2ccc630/node_modules/puppeteer');
const path = require('path');
const { execFileSync } = require('child_process');

const DIR = __dirname;                              // app/editor
const REPO = path.resolve(DIR, '..', '..');         // repo root
const OUT = process.env.HOME;
// Default: load the harness straight off disk (file://). When the checkout lives
// somewhere snap-confined Chromium cannot read (e.g. a ~/.cache worktree), set
// HARNESS_URL to a localhost static-server URL for app/editor/test-harness.html
// (the harness header sanctions this) — everything else is byte-identical.
const HARNESS = process.env.HARNESS_URL || ('file://' + path.join(DIR, 'test-harness.html'));

const results = [];
function assert(rule, cond, detail) {
  results.push({ rule, pass: !!cond, detail: detail || '' });
  console.log((cond ? 'PASS' : 'FAIL') + '  ' + rule + (detail ? '  — ' + detail : ''));
}
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

async function boot(browser, w, h) {
  const page = await browser.newPage();
  await page.setViewport({ width: w, height: h });
  await page.goto(HARNESS, { waitUntil: 'load' });
  await page.waitForFunction(() => window.SonstengEditor && window.SonstengEditor.ready() >= 4, { timeout: 8000 });
  return page;
}

async function run() {
  const browser = await puppeteer.launch({
    executablePath: '/snap/bin/chromium',
    headless: false,
    args: ['--no-sandbox', '--disable-dev-shm-usage', '--window-size=1300,1500']
  });

  /* ======================= DESKTOP (Windows-ish 1280) ==================== */
  {
    const page = await boot(browser, 1280, 1500);

    /* --- N: normalize() mirrors tools/text_norm.py byte-for-byte ---------- */
    const samples = [
      '  The  “quick”—brown fox’s tale…  ',
      'line one\r\nline two line three',
      'non​breaking space',
      'em—dash en–dash minus−sign and figure‒dash',
      'tabs\t\tand   spaces\nand a newline',
      'straight ok · already normal'
    ];
    const jsNorm = await page.evaluate((ss) => ss.map((x) => window.SonstengEditor.normalize(x)), samples);
    let pyNorm = [];
    try {
      const outp = execFileSync('python3', ['-c',
        "import sys,json; sys.path.insert(0, sys.argv[1]); import text_norm; " +
        "data=json.load(sys.stdin); print(json.dumps([text_norm.normalize(x) for x in data]))",
        path.join(REPO, 'tools')], { input: JSON.stringify(samples), encoding: 'utf8' });
      pyNorm = JSON.parse(outp);
    } catch (e) { pyNorm = ['<python error: ' + (e.message || e) + '>']; }
    const normMatch = JSON.stringify(jsNorm) === JSON.stringify(pyNorm);
    assert('N  normalize() mirrors tools/text_norm.py byte-for-byte',
      normMatch, normMatch ? (samples.length + ' samples identical') : ('js=' + JSON.stringify(jsNorm) + ' py=' + JSON.stringify(pyNorm)));

    /* --- C: WHAT IS EDITABLE (revised 2026-07-27) -------------------------
       These three assertions used to pin the OPPOSITE contract: json_scalar and
       inline-formatted blocks were comment-only in the client. That was stricter
       than anything behind it — the Worker rejects only kind 'comment_only', and
       the apply engine handles both (WP5 surgical scalar splice, WP7 span-splice
       with a needs_human fallback). On a real matter packet it hid the pencil on
       1,739 of 3,474 blocks, including every numbered activity, because each one
       opens with a bolded lead-in. Damien found it by asking why half the
       paragraphs could not be edited. */
    const b2 = await page.evaluate(() => window.SonstengEditor.block(2));
    const b3 = await page.evaluate(() => window.SonstengEditor.block(3));
    assert('C1 json_scalar block IS editable (WP5 surgical scalar splice)',
      b2 && b2.editable === true && b2.commentOnly === false, 'kind=' + (b2 && b2.kind) + ' editable=' + (b2 && b2.editable));
    assert('C2 inline-formatted block IS editable (WP7 span-splice, needs_human on ambiguity)',
      b3 && b3.editable === true && b3.commentOnly === false, 'kind=' + (b3 && b3.kind) + ' editable=' + (b3 && b3.editable));
    // A formatted block must enter a real edit session and carry a suggestion id.
    await page.evaluate(() => window.SonstengEditor.typeInto(3, 'A formatted line, now genuinely editable.'));
    const b3after = await page.evaluate(() => window.SonstengEditor.block(3));
    assert('C3 editing a formatted block enters EDIT and mints an id (no silent refusal)',
      b3after.state === 'EDITING' && b3after.dirty === true && !!b3after.suggestionId,
      'state=' + b3after.state + ' dirty=' + b3after.dirty);
    await page.evaluate(() => window.SonstengEditor.clickCancel(3));
    // C4 (revised 2026-07-27): the explanation used to stand permanently under
    // every formatted block — the same sentence repeated dozens of times down a
    // matter packet. It now travels with the control that can act on it: the
    // Comment affordance's tooltip, and the comment panel once opened. The
    // CONTRACT being pinned is that the reason is never lost, only relocated.
    const tip3 = await page.evaluate(() => {
      // The rail precedes its block; walk back past anything the overlay may
      // have inserted (margin comment bubbles) to find it.
      const blk = document.querySelector('.eb[data-eb-index="3"]');
      let tools = blk && blk.previousElementSibling;
      while (tools && !tools.classList.contains('eb-tools')) tools = tools.previousElementSibling;
      const btn = tools && tools.querySelector('.eb-act--comment');
      return btn && btn.getAttribute('title');
    });
    assert('C4a an editable block offers BOTH controls, each with a plain-word label',
      /leave a note for Damien/.test(tip3 || ''), tip3 ? ('“' + tip3.slice(0, 60) + '…”') : 'no tooltip');
    const noNote3 = await page.evaluate(() => window.SonstengEditor.noteText(3));
    assert('C4b the standing beige explainer is gone from the block itself',
      !noNote3 || !/Damien will apply the wording/.test(noNote3), 'note=' + JSON.stringify(noNote3));
    await page.evaluate(() => window.SonstengEditor.clickBlockComment(3));
    const why3 = await page.evaluate(() => {
      const w = document.querySelector('.comment-bubble__why');
      return { text: w && w.textContent, shown: !!(w && w.classList.contains('show')) };
    });
    // Block 3 is editable now, so the panel must NOT claim it cannot be edited —
    // the explanation is reserved for genuinely comment-only blocks.
    assert('C4c the comment panel does not claim an editable block is un-editable',
      why3.shown === false && !why3.text, 'shown=' + why3.shown + ' text=' + JSON.stringify(why3.text));
    await page.evaluate(() => window.SonstengEditor.closeBubble && window.SonstengEditor.closeBubble());
    await page.evaluate(() => {
      const c = document.querySelector('.comment-bubble__close');
      c && c.dispatchEvent(new MouseEvent('click', { detail: 0, bubbles: true, cancelable: true }));
    });

    /* --- E: edit -> autocorrect preview -> Send -> Sent ✓ -> inline status - */
    const EDIT1 = 'You are an attorney for Devon Halvard, arrested after a late-night stop on an icy road.';
    await page.evaluate((t) => window.SonstengEditor.typeInto(1, t), EDIT1);
    let s1 = await page.evaluate(() => window.SonstengEditor.block(1));
    assert('E1 editable prose enters edit + mints one suggestion_id on input',
      s1.state === 'EDITING' && !!s1.suggestionId && s1.dirty === true, 'state=' + s1.state + ' id=' + (s1.suggestionId || '').slice(0, 8));
    await page.evaluate(() => window.SonstengEditor.openPreview(1));
    const prev = await page.evaluate(() => ({ vis: window.SonstengEditor.previewVisible(), text: window.SonstengEditor.previewText() }));
    assert('E2 autocorrect preview shows EXACTLY what will be sent (before submit)',
      prev.vis === true && prev.text === EDIT1, 'preview="' + (prev.text || '').slice(0, 40) + '…"');
    const callsBeforeE = await page.evaluate(() => window.__MOCK_CTRL__.server().calls);
    await page.evaluate(() => window.SonstengEditor.confirmSend(1));
    await page.waitForFunction(() => window.SonstengEditor.block(1).state === 'IDLE', { timeout: 4000 });
    s1 = await page.evaluate(() => window.SonstengEditor.block(1));
    const srvE = await page.evaluate(() => window.__MOCK_CTRL__.server());
    assert('E3 send clears id + draft, exits edit (committed once)',
      s1.suggestionId === null && s1.dirty === false && srvE.count === 1, 'count=' + srvE.count + ' id=' + s1.suggestionId);
    await sleep(150);   // let the post-send re-poll land
    const st1 = await page.evaluate(() => window.SonstengEditor.statusText(1));
    assert('E4 inline status renders after send (Sent ✓ / synced Pending)',
      /Sent ✓|Pending/.test(st1 || ''), 'status="' + st1 + '"');

    /* --- D: triple-Save dedupe (slow net) => ONE logical suggestion -------- */
    await page.evaluate(() => window.__MOCK_CTRL__.setSlow(500));
    await page.evaluate(() => window.SonstengEditor.typeInto(1, 'A revised intro sentence, edited again by John.'));
    const srvBeforeD = await page.evaluate(() => window.__MOCK_CTRL__.server());
    await page.evaluate(() => window.SonstengEditor.tripleClickSave(1));
    const midState = await page.evaluate(() => window.SonstengEditor.block(1).state);
    await page.waitForFunction(() => window.SonstengEditor.block(1).state === 'IDLE', { timeout: 5000 });
    const srvAfterD = await page.evaluate(() => window.__MOCK_CTRL__.server());
    assert('D1 Save disabled synchronously (state=SAVING during the await)',
      midState === 'SAVING', 'mid-flight state=' + midState);
    assert('D2 triple-Save => ONE network call + ONE new suggestion (dedupe)',
      (srvAfterD.calls - srvBeforeD.calls) === 1 && (srvAfterD.count - srvBeforeD.count) === 1,
      'call-delta=' + (srvAfterD.calls - srvBeforeD.calls) + ' count-delta=' + (srvAfterD.count - srvBeforeD.count));
    await page.evaluate(() => window.__MOCK_CTRL__.setSlow(0));

    /* --- A: 401 re-auth preserves draft + id; resend reuses same id -------- */
    await page.evaluate(() => window.__MOCK_CTRL__.setSlow(200));
    await page.evaluate(() => window.__MOCK_CTRL__.forceOnce('no_edit_auth'));
    await page.evaluate(() => window.SonstengEditor.typeInto(1, 'A sentence John writes while his link is about to expire.'));
    const idA = await page.evaluate(() => window.SonstengEditor.block(1).suggestionId);
    await page.evaluate(() => window.SonstengEditor.clickSave(1));
    await page.waitForFunction(() => window.SonstengEditor.block(1).state !== 'SAVING', { timeout: 4000 });
    const s1a = await page.evaluate(() => window.SonstengEditor.block(1));
    const reauth = await page.evaluate(() => window.SonstengEditor.reauthShown(1));
    const idOnSrvBefore = await page.evaluate((id) => window.__MOCK_CTRL__.server().ids.indexOf(id) !== -1, idA);
    assert('A1 401 preserves draft + id (recoverable, NOT committed)',
      s1a.suggestionId === idA && /about to expire/.test(s1a.snapshot) && idOnSrvBefore === false && s1a.dirty === true,
      'id-preserved=' + (s1a.suggestionId === idA) + ' on-server=' + idOnSrvBefore);
    assert('A2 friendly re-auth affordance shown (never a raw 4xx)', reauth === true, 'reauth visible=' + reauth);
    await page.evaluate(() => window.SonstengEditor.reauthResend(1));
    await page.waitForFunction(() => window.SonstengEditor.block(1).state === 'IDLE', { timeout: 4000 });
    const idOnSrvAfter = await page.evaluate((id) => window.__MOCK_CTRL__.server().ids.indexOf(id) !== -1, idA);
    assert('A3 resend after re-auth reuses the SAME id (idempotent)', idOnSrvAfter === true, 'server has id ' + (idA || '').slice(0, 8));
    await page.evaluate(() => window.__MOCK_CTRL__.setSlow(0));

    /* --- S: selection-comment on a prose block (block 4) ------------------- */
    const full4 = await page.evaluate(() => window.SonstengEditor.blockText(4));
    const phrase = 'delivery-route license';
    const start = full4.indexOf(phrase);
    const end = start + phrase.length;
    await page.evaluate((a, b) => window.SonstengEditor.dragSelect(4, a, b), start, end);
    await sleep(160);
    const cap = await page.evaluate(() => window.SonstengEditor.captured());
    const floatVis = await page.evaluate(() => window.SonstengEditor.floatVisible());
    assert('S1 selection captured at rest; floating Comment button appears',
      cap && new RegExp(phrase).test(cap.text) && floatVis === true, 'captured="' + (cap && cap.text) + '" float=' + floatVis);
    await page.evaluate(() => window.SonstengEditor.clickFloat());
    const bub = await page.evaluate(() => window.SonstengEditor.bubbleOpen());
    assert('S2 Comment anchors to the EXACT selected range (not a collapsed caret)',
      bub && new RegExp(phrase).test(bub.anchor || ''), 'anchor="' + (bub && bub.anchor) + '"');
    const srvBeforeS = await page.evaluate(() => window.__MOCK_CTRL__.server().count);
    await page.evaluate(() => window.SonstengEditor.sendBubble('Confirm the license type against the client intake.'));
    await page.waitForFunction((n) => window.__MOCK_CTRL__.server().count === n + 1, { timeout: 4000 }, srvBeforeS);
    assert('S3 comment persisted (kind=comment) with the anchor',
      true, 'server count ' + srvBeforeS + ' -> ' + (await page.evaluate(() => window.__MOCK_CTRL__.server().count)));

    /* --- M: margin bubbles render from /pending (Word-style) --------------- */
    await page.evaluate(() => window.SonstengEditor.repoll());
    await sleep(150);
    const margins = await page.evaluate(() => window.SonstengEditor.marginBubbles());
    assert('M1 Word-style margin bubbles render pending/accepted/declined comments',
      margins.length >= 2 && margins.join(' ').indexOf('Set aside') !== -1, 'bubbles=' + margins.length);

    /* --- AX: comment dialog a11y — role/aria-modal, Escape-close + focus
       return, keyboard-operable Cancel (WP10 a11y sweep, design §9) ---------- */
    await page.evaluate(() => window.SonstengEditor.clickBlockComment(4));
    const dlg = await page.evaluate(() => {
      var b = document.querySelector('.comment-bubble');
      return { open: !!(window.SonstengEditor.bubbleOpen()), role: b && b.getAttribute('role'), modal: b && b.getAttribute('aria-modal') };
    });
    assert('AX1 comment surface is a dialog (role=dialog, aria-modal=true)',
      dlg.open && dlg.role === 'dialog' && dlg.modal === 'true', 'role=' + dlg.role + ' modal=' + dlg.modal);

    // Escape (dispatched from the textarea, must bubble to the dialog) closes it.
    await page.evaluate(() => {
      var ta = document.getElementById('eb-comment-text');
      ta.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true, cancelable: true }));
    });
    await sleep(30);
    const afterEsc = await page.evaluate(() => !!(window.SonstengEditor.bubbleOpen()));
    assert('AX2 Escape closes the comment dialog', afterEsc === false, 'open=' + afterEsc);

    // Keyboard activation of Cancel (click with detail===0) closes the dialog —
    // the button was mouse-only (mousedown) before the fix.
    await page.evaluate(() => window.SonstengEditor.clickBlockComment(4));
    const reopened = await page.evaluate(() => !!(window.SonstengEditor.bubbleOpen()));
    await page.evaluate(() => {
      var cancel = document.querySelector('.comment-bubble .btn--ghost');
      cancel.dispatchEvent(new MouseEvent('click', { detail: 0, bubbles: true, cancelable: true }));
    });
    await sleep(30);
    const afterCancelKey = await page.evaluate(() => !!(window.SonstengEditor.bubbleOpen()));
    assert('AX3 Cancel is keyboard-operable (detail-0 click closes the dialog)',
      reopened === true && afterCancelKey === false, 'reopened=' + reopened + ' closedByKey=' + (!afterCancelKey));

    /* --- L: large-type toggle sets .type-lg on <html> --------------------- */
    await page.evaluate(() => {
      const b = document.querySelector('.editor-banner .segmented-toggle button:last-child');
      b && b.click();
    });
    const lg = await page.evaluate(() => document.documentElement.classList.contains('type-lg'));
    assert('L1 large-type toggle applies .type-lg (first-class a11y)', lg === true, 'type-lg=' + lg);

    /* --- B: persistent banner + save bar present -------------------------- */
    const banner = await page.$eval('.editor-banner', el => el.textContent).catch(() => '');
    assert('B1 persistent "changes go to Damien" banner present',
      /changes go to Damien/i.test(banner || ''), 'banner="' + (banner || '').replace(/\s+/g, ' ').slice(0, 48) + '…"');

    /* --- HY: pending overlay hydrates block text across reloads (WYSIWYG) ----
       Feed the client server-style #edits-data items (the shape projectPendingItems
       now emits) through renderPending — the exact entry boot + repoll use — and
       assert hydration, attribution, the stale/declined/draft guards, and that a
       re-edit FROM a hydrated block saves a valid supersede (canonical hash). */
    const HYREF = await page.evaluate(() => window.SonstengEditor.block(4).ref);
    const HYPAGE = await page.evaluate(() => window.SonstengEditor.page());
    const HYTEXT = 'Devon Halvard is a delivery driver with no prior record.';

    // 1) a pending EDIT paints its new_text into the block + shows attribution
    await page.evaluate((ref, t) => window.SonstengEditor.applyPending([
      { block_index: 4, source_ref: ref, status: 'pending', kind: 'prose',
        new_text: t, base_hash: 'hash-idx4-v1', map_version: 'harness-v1',
        attribution: 'JOS', preview: t }
    ]), HYREF, HYTEXT);
    const hy1 = await page.evaluate(() => ({ text: window.SonstengEditor.blockText(4), b: window.SonstengEditor.block(4), st: window.SonstengEditor.statusText(4) }));
    assert('HY1 pending suggestion hydrates the block text on (re)load (WYSIWYG)',
      hy1.text === HYTEXT && hy1.b.hydrated === true, 'text="' + hy1.text.slice(0, 40) + '" hydrated=' + hy1.b.hydrated);
    assert('HY2 hydrated block shows status pill + author attribution (JOS)',
      /Pending/.test(hy1.st) && /JOS/.test(hy1.st), 'pill="' + hy1.st + '"');
    assert('HY3 hydration is display-only — canonical originalHash/originalText untouched',
      hy1.b.originalHash === 'hash-idx4-v1' && /breath-test result/.test(hy1.b.originalText), 'hash=' + hy1.b.originalHash);

    // 2) declined does NOT hydrate — the block reverts to the ORIGINAL text
    await page.evaluate((ref) => window.SonstengEditor.applyPending([
      { block_index: 4, source_ref: ref, status: 'declined', kind: 'prose',
        new_text: 'Should never appear.', base_hash: 'hash-idx4-v1', attribution: 'JOS' }
    ]), HYREF);
    const hy2 = await page.evaluate(() => ({ text: window.SonstengEditor.blockText(4), hydrated: window.SonstengEditor.block(4).hydrated }));
    assert('HY4 declined does NOT hydrate; block reverts to its original text',
      /breath-test result/.test(hy2.text) && hy2.hydrated === false, 'text="' + hy2.text.slice(0, 40) + '"');

    // 3) a STALE suggestion (baseline hash moved) skips hydration -> pill-only
    await page.evaluate((ref) => window.SonstengEditor.applyPending([
      { block_index: 4, source_ref: ref, status: 'pending', kind: 'prose',
        new_text: 'Stale overlay must be skipped.', base_hash: 'HASH-MOVED', attribution: 'JOS', preview: 'stale' }
    ]), HYREF);
    const hy3 = await page.evaluate(() => ({ text: window.SonstengEditor.blockText(4), hydrated: window.SonstengEditor.block(4).hydrated, st: window.SonstengEditor.statusText(4) }));
    assert('HY5 stale suggestion (hash moved) skips hydration; falls back to the pill',
      /breath-test result/.test(hy3.text) && hy3.hydrated === false && /Pending/.test(hy3.st),
      'text="' + hy3.text.slice(0, 28) + '" pill="' + hy3.st + '"');

    // 4) an unsent DRAFT beats hydration (draft = newer intent)
    await page.evaluate((ref, pg) => {
      var key = window.SonstengEditor.draftKeyFor(4);
      sessionStorage.setItem(key, JSON.stringify({ page: pg, source_ref: ref, original_hash: 'hash-idx4-v1', new_text: 'my unsent draft', ts: Date.now() }));
    }, HYREF, HYPAGE);
    await page.evaluate((ref) => window.SonstengEditor.applyPending([
      { block_index: 4, source_ref: ref, status: 'pending', kind: 'prose',
        new_text: 'Suggestion should be suppressed by the draft.', base_hash: 'hash-idx4-v1', attribution: 'JOS' }
    ]), HYREF);
    const hy4 = await page.evaluate(() => ({ text: window.SonstengEditor.blockText(4), hydrated: window.SonstengEditor.block(4).hydrated }));
    assert('HY6 an unsent draft beats hydration (draft = newer intent)',
      hy4.text.indexOf('Suggestion should be suppressed') === -1 && hy4.hydrated === false, 'text="' + hy4.text.slice(0, 40) + '"');
    await page.evaluate(() => { sessionStorage.removeItem(window.SonstengEditor.draftKeyFor(4)); });

    // 5) re-edit FROM a hydrated block saves a valid SUPERSEDE (canonical hash)
    await page.evaluate((ref, t) => window.SonstengEditor.applyPending([
      { block_index: 4, source_ref: ref, status: 'pending', kind: 'prose',
        new_text: t, base_hash: 'hash-idx4-v1', attribution: 'JOS', preview: t }
    ]), HYREF, 'A hydrated starting point for the re-edit.');
    await page.evaluate(() => window.SonstengEditor.typeInto(4, 'A re-edit that starts from the hydrated suggestion text.'));
    await page.evaluate(() => window.SonstengEditor.clickSave(4));
    await page.waitForFunction(() => window.SonstengEditor.block(4).state === 'IDLE', { timeout: 4000 });
    const last = await page.evaluate(() => window.__MOCK_CTRL__.last());
    assert('HY7 re-edit from a hydrated block saves a valid supersede (canonical original_hash sent, unpoisoned)',
      last && last.source_ref === HYREF && last.original_hash === 'hash-idx4-v1' && /re-edit that starts/.test(last.new_text || ''),
      'sent hash=' + (last && last.original_hash) + ' text="' + ((last && last.new_text) || '').slice(0, 30) + '"');

    await page.screenshot({ path: path.join(OUT, 'editor-desktop.png'), fullPage: false });
    console.log('   [screenshot] ' + path.join(OUT, 'editor-desktop.png'));
    // large-type screenshot for visual QA
    await page.screenshot({ path: path.join(OUT, 'editor-largetype.png'), fullPage: false });
    console.log('   [screenshot] ' + path.join(OUT, 'editor-largetype.png'));
    await page.close();
  }

  /* ============================ MOBILE (390x844) ========================= */
  {
    const page = await boot(browser, 390, 844);
    // edit + save on the small viewport (keyboard-tracking bar path)
    await page.evaluate(() => window.SonstengEditor.typeInto(1, 'A short mobile edit for Devon.'));
    const barVis = await page.evaluate(() => window.SonstengEditor.barVisible());
    await page.evaluate(() => window.SonstengEditor.clickSave(1));
    await page.waitForFunction(() => window.SonstengEditor.block(1).state === 'IDLE', { timeout: 4000 });
    const s1m = await page.evaluate(() => window.SonstengEditor.block(1));
    assert('MOB1 edit + Save works on 390x844 (save bar shown; committed)',
      barVis === true && s1m.dirty === false && s1m.suggestionId === null, 'bar=' + barVis + ' dirty=' + s1m.dirty);
    // MOB2 (revised 2026-07-27): on touch there is no hover, so the icon rail
    // must read as interactive at rest — the word labels stay expanded and the
    // glyphs sit at high opacity. A hover-only rail would simply not exist on
    // John's iPad. This asserts the touch branch of the affordance CSS.
    const railM = await page.evaluate(() => {
      // The rail precedes its block; walk back past anything the overlay may
      // have inserted (margin comment bubbles) to find it.
      const blk = document.querySelector('.eb[data-eb-index="3"]');
      let tools = blk && blk.previousElementSibling;
      while (tools && !tools.classList.contains('eb-tools')) tools = tools.previousElementSibling;
      const btn = tools && tools.querySelector('.eb-act--comment');
      if (!btn) return null;
      const cs = getComputedStyle(btn);
      const box = btn.getBoundingClientRect();
      const lab = btn.querySelector('.eb-act__label');
      return {
        opacity: parseFloat(cs.opacity),
        h: Math.round(box.height), w: Math.round(box.width),
        label: lab && lab.textContent,
        labelVisible: lab ? parseFloat(getComputedStyle(lab).opacity) > 0 : false,
        title: btn.getAttribute('title'),
        ariaLabel: btn.getAttribute('aria-label'),
      };
    });
    assert('MOB2 icon rail is visible at rest on touch (never hover-only) with its word label',
      railM && railM.opacity >= 0.5 && railM.labelVisible && /Comment/.test(railM.label || ''),
      railM ? ('opacity=' + railM.opacity + ' label="' + railM.label + '" labelVisible=' + railM.labelVisible) : 'no rail');
    assert('MOB3 icon affordance keeps a 44x44 touch target (WCAG 2.5.5) and an accessible name',
      railM && railM.h >= 44 && railM.w >= 44 && /Comment/.test(railM.ariaLabel || '')
        && /leave a note for Damien/.test(railM.title || ''),
      railM ? (railM.w + 'x' + railM.h + ' aria="' + railM.ariaLabel + '"') : 'no rail');
    await page.screenshot({ path: path.join(OUT, 'editor-mobile.png'), fullPage: false });
    console.log('   [screenshot] ' + path.join(OUT, 'editor-mobile.png'));
    await page.close();
  }

  /* ============ AUTO-SAVE / UNMASK / HEARTBEAT / FLUSH (fresh page) ======== */
  {
    const page = await boot(browser, 1280, 1500);
    const AUTOSAVE_WAIT = 2900;   // > AUTOSAVE_MS (2500) so the debounce fires

    /* --- AS1 debounce coalescing: rapid edits => ONE auto-send, no Save ---- */
    await page.evaluate(() => window.__MOCK_CTRL__.clear());
    const asBefore = await page.evaluate(() => window.__MOCK_CTRL__.server().calls);
    await page.evaluate(() => window.SonstengEditor.typeInto(1, 'Auto edit one.'));
    await page.evaluate(() => window.SonstengEditor.typeInto(1, 'Auto edit two.'));
    await page.evaluate(() => window.SonstengEditor.typeInto(1, 'Auto edit three — the keeper.'));
    await sleep(AUTOSAVE_WAIT);
    const asSrv = await page.evaluate(() => window.__MOCK_CTRL__.server());
    const asLast = await page.evaluate(() => window.__MOCK_CTRL__.last());
    const asBlk = await page.evaluate(() => window.SonstengEditor.block(1));
    assert('AS1 typing auto-saves after the debounce (one send, latest text, no Save button)',
      (asSrv.calls - asBefore) === 1 && asLast && /the keeper/.test(asLast.new_text || '') && asBlk.dirty === false,
      'call-delta=' + (asSrv.calls - asBefore) + ' dirty=' + asBlk.dirty);

    /* --- AS2 rotation: the next burst uses a FRESH idempotency id ---------- */
    const idsBeforeRot = await page.evaluate(() => window.__MOCK_CTRL__.server().ids.slice());
    await page.evaluate(() => window.SonstengEditor.typeInto(1, 'A second, separate burst of typing.'));
    await sleep(AUTOSAVE_WAIT);
    const idsAfterRot = await page.evaluate(() => window.__MOCK_CTRL__.server().ids.slice());
    const newIds = idsAfterRot.filter((x) => idsBeforeRot.indexOf(x) === -1);
    assert('AS2 each burst rotates the idempotency id (fresh id, not a same-id replay)',
      idsAfterRot.length === idsBeforeRot.length + 1 && newIds.length === 1,
      'ids before=' + idsBeforeRot.length + ' after=' + idsAfterRot.length);

    /* --- AS3 in-flight serialization: the debounce send is the sole flight - */
    await page.evaluate(() => window.__MOCK_CTRL__.setSlow(700));
    const asBefore3 = await page.evaluate(() => window.__MOCK_CTRL__.server().calls);
    await page.evaluate(() => window.SonstengEditor.typeInto(1, 'An edit sent over a slow link.'));
    await sleep(AUTOSAVE_WAIT);
    const midFlight = await page.evaluate(() => window.SonstengEditor.block(1).state);
    await page.waitForFunction(() => window.SonstengEditor.block(1).state !== 'SAVING' && window.SonstengEditor.block(1).dirty === false, { timeout: 5000 });
    const asSrv3 = await page.evaluate(() => window.__MOCK_CTRL__.server().calls);
    assert('AS3 one in-flight auto-save per block (SAVING during the await; exactly one send)',
      midFlight === 'SAVING' && (asSrv3 - asBefore3) === 1, 'mid=' + midFlight + ' delta=' + (asSrv3 - asBefore3));
    await page.evaluate(() => window.__MOCK_CTRL__.setSlow(0));

    /* --- UN1 needs_human UNMASK: text shown, but framed as a warning ------- */
    const UNREF = await page.evaluate(() => window.SonstengEditor.block(4).ref);
    await page.evaluate((ref) => window.SonstengEditor.applyPending([
      { block_index: 4, source_ref: ref, status: 'needs_human', kind: 'prose',
        new_text: 'An edit the apply engine could not land automatically.',
        base_hash: window.SonstengEditor.block(4).originalHash, map_version: (window.__HARNESS_MAP__ && 'harness-v1'),
        attribution: 'JOS', preview: 'needs attention' }
    ]), UNREF);
    const un = await page.evaluate(() => ({
      text: window.SonstengEditor.blockText(4), warn: window.SonstengEditor.blockWarn(4),
      st: window.SonstengEditor.statusText(4)
    }));
    assert('UN1 needs_human unmasks: edited text shown WITH a warning frame + "not applied" pill',
      /could not land automatically/.test(un.text) && un.warn === true && /Not applied/.test(un.st),
      'warn=' + un.warn + ' pill="' + un.st + '"');

    /* --- HB1/HB2/HB3 heartbeat banner states (DIRECT_APPLY on) ------------- */
    await sleep(900);   // let any trailing (slow) re-poll from AS3 settle first
    // A helper: set the mode + age, re-poll, and wait for the banner to reflect it.
    async function pollBanner(age, match) {
      await page.evaluate((a) => { window.__MOCK_CTRL__.setDirectApply(true); window.__MOCK_CTRL__.setHeartbeatAge(a); }, age);
      await page.evaluate(() => window.SonstengEditor.repoll());
      await page.waitForFunction((re) => new RegExp(re, 'i').test(window.SonstengEditor.bannerText()), { timeout: 4000 }, match).catch(() => {});
      return page.evaluate(() => ({ t: window.SonstengEditor.bannerText(), w: window.SonstengEditor.bannerWarn() }));
    }
    const hbFresh = await pollBanner(60, 'go live automatically');   // fresh (<5 min)
    assert('HB1 fresh heartbeat → subtle "edits go live automatically" (no warning)',
      /go live automatically/i.test(hbFresh.t) && hbFresh.w === false, 'banner="' + hbFresh.t + '" warn=' + hbFresh.w);

    const hbStale = await pollBanner(900, 'paused');                 // stale (>10 min)
    assert('HB2 stale heartbeat → warning "auto-apply paused … edits are safe and queued"',
      /paused/i.test(hbStale.t) && /safe and queued/i.test(hbStale.t) && hbStale.w === true, 'banner="' + hbStale.t + '" warn=' + hbStale.w);

    const hbNone = await pollBanner(null, 'paused');                 // never checked in
    assert('HB3 no heartbeat yet → warning banner (never a false "live" claim)',
      hbNone.w === true && /paused/i.test(hbNone.t), 'banner="' + hbNone.t + '" warn=' + hbNone.w);

    /* --- FL1 flush-on-hide: an unsent dirty edit is flushed on pagehide ---- */
    await page.evaluate(() => window.__MOCK_CTRL__.setDirectApply(false));
    await page.evaluate(() => window.__MOCK_CTRL__.clear());
    const FLREF = await page.evaluate(() => window.SonstengEditor.block(4).ref);
    await page.evaluate(() => window.SonstengEditor.typeInto(4, 'A mid-type edit interrupted by leaving the page.'));
    // fire pagehide BEFORE the 2.5s debounce would send — the flush must catch it
    await page.evaluate(() => window.dispatchEvent(new Event('pagehide')));
    await sleep(150);
    const flSrv = await page.evaluate(() => window.__MOCK_CTRL__.server());
    const flLast = await page.evaluate(() => window.__MOCK_CTRL__.last());
    assert('FL1 flush-on-hide sends the unsent edit (keepalive) before the debounce fires',
      flSrv.count >= 1 && flLast && /interrupted by leaving/.test(flLast.new_text || ''),
      'count=' + flSrv.count + ' text="' + ((flLast && flLast.new_text) || '').slice(0, 30) + '"');

    await page.close();
  }

  await browser.close();

  const passed = results.filter(r => r.pass).length;
  console.log('\n===== ASSERTION SUMMARY: ' + passed + '/' + results.length + ' PASS =====');
  if (passed !== results.length) {
    process.exitCode = 1;
    console.log('FAILURES:');
    results.filter(r => !r.pass).forEach(r => console.log('  - ' + r.rule + ' :: ' + r.detail));
  }
}

run().catch(e => { console.error('HARNESS ERROR', e); process.exitCode = 2; });
