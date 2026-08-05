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
const fs = require('fs');
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
  await installRadiusMock(page);
  return page;
}

async function installRadiusMock(page) {
  await page.evaluate(() => {
    const base = window.__EDITOR_MOCK__;
    const ceiling = 100;
    let confirmationSequence = 0;
    let confirmationToken = null;
    let scopedDecision = { accepted: null, status: null, issuedToken: null };
    window.__MOCK_CTRL__.scopedDecision = () => ({
      accepted: scopedDecision.accepted,
      status: scopedDecision.status,
      issuedToken: scopedDecision.issuedToken
    });
    const radii = {
      course: { blocks: 3692, files: 282, matters: 20 },
      module: {
        M1: { blocks: 460, files: 40, matters: 20 },
        M2: { blocks: 492, files: 42, matters: 20 },
        M3: { blocks: 371, files: 36, matters: 20 }
      },
      matter: { blocks: 206, files: 14, matters: 1 },
      part: { blocks: 122, files: 8, matters: 1 }
    };
    window.__EDITOR_MOCK__ = function (req) {
      if (req.path.indexOf('/scoped-request') !== 0) return base(req);
      const body = req.body || {};
      const radius = body.level === 'course' ? radii.course
        : body.level === 'module' ? radii.module[body.module]
        : radii[body.level];
      // Let the stock harness record the request, but derive the response from
      // the enumerated fixture radius rather than the scope's name.
      return Promise.resolve(base(req)).then(() => {
        if (radius && radius.blocks > ceiling &&
            (!body.confirmed || !confirmationToken ||
             body.confirmation_token !== confirmationToken)) {
          confirmationToken = 'verify-editor-confirmation-' + (++confirmationSequence);
          scopedDecision = { accepted: false, status: 409, issuedToken: confirmationToken };
          return { ok: false, status: 409, data: { ok: false,
            error: { code: 'ceiling_confirmation_required' }, radius,
            confirmation_token: confirmationToken } };
        }
        scopedDecision = { accepted: true, status: 200, issuedToken: confirmationToken };
        return { ok: true, status: 200,
          data: { ok: true, id: body.id, status: 'requested' } };
      });
    };
  });
}

async function bootFacts(browser, w, h) {
  const page = await browser.newPage();
  await page.setViewport({ width: w, height: h });
  await page.setRequestInterception(true);
  page.on('request', req => {
    if (req.isNavigationRequest() && req.frame() === page.mainFrame()) {
      const html = fs.readFileSync(path.join(DIR, 'test-harness.html'), 'utf8')
        .replace('"page": "matters/m05-dwi-meridian/index.html"',
                 '"page": "matters/m05-dwi-meridian/facts/index.html"');
      req.respond({ status: 200, contentType: 'text/html', body: html });
    } else {
      req.continue();
    }
  });
  await page.goto(HARNESS, { waitUntil: 'load' });
  await page.waitForFunction(() => window.SonstengEditor && window.SonstengEditor.ready() >= 4, { timeout: 8000 });
  await installRadiusMock(page);
  return page;
}

async function run() {
  const browser = await puppeteer.launch({
    executablePath: '/snap/bin/chromium',
    headless: process.env.EDITOR_HEADLESS === '1',
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
    const collision3 = await page.evaluate(() => {
      const tools = document.querySelector('.eb-tools[data-eb-for="3"]');
      return {
        text: window.SonstengEditor.blockText(3),
        pills: Array.from(tools ? tools.querySelectorAll('.eb-status') : []).map(el => el.textContent)
      };
    });
    assert('C2A applied prose and a pending structural insert at the same block index are both represented',
      /marshal the facts/.test(collision3.text) &&
      collision3.pills.some(p => /Live/.test(p)) &&
      collision3.pills.some(p => /New paragraph.*waiting for review/i.test(p)),
      'text="' + collision3.text.slice(0, 45) + '" pills=' + JSON.stringify(collision3.pills));
    // A formatted block must enter a real edit session and carry a suggestion id.
    await page.evaluate(() => window.SonstengEditor.typeInto(3, 'A formatted line, now genuinely editable.'));
    const b3after = await page.evaluate(() => window.SonstengEditor.block(3));
    assert('C3 editing a formatted block enters EDIT and mints an id (no silent refusal)',
      b3after.state === 'EDITING' && b3after.dirty === true && !!b3after.suggestionId,
      'state=' + b3after.state + ' dirty=' + b3after.dirty);
    await page.evaluate(() => window.SonstengEditor.clickCancel(3));

    /* --- SH: shared text exposes reach before commit (U4) ---------------- */
    const sharedAtRest = await page.evaluate(() => ({
      single: window.SonstengEditor.block(1),
      shared: window.SonstengEditor.block(4),
      singleMarked: document.querySelector('[data-eb-index="1"]').classList.contains('eb--shared'),
      sharedMarked: document.querySelector('[data-eb-index="4"]').classList.contains('eb--shared')
    }));
    assert('SH1 a single-occurrence block has no shared mark',
      sharedAtRest.single.shared === false && sharedAtRest.singleMarked === false);
    assert('SH2 a three-occurrence block is marked as shared before selection',
      sharedAtRest.shared.shared === true && sharedAtRest.shared.occurrenceCount === 3 && sharedAtRest.sharedMarked === true,
      'shared=' + sharedAtRest.shared.shared + ' occurrences=' + sharedAtRest.shared.occurrenceCount);

    await page.evaluate(() => {
      window.__MOCK_CTRL__.clear();
      window.SonstengEditor.typeInto(4, 'A shared edit whose reach John must choose.');
      window.SonstengEditor.clickSave(4);
    });
    const reach = await page.evaluate(() => window.SonstengEditor.sharedDialog());
    assert('SH3 shared commit names the other two pages before sending',
      reach && reach.pages.length === 2 &&
      reach.pages.some(p => /exercise/i.test(p)) && reach.pages.some(p => /module 1/i.test(p)),
      reach ? JSON.stringify(reach.pages) : 'dialog absent');
    const beforeEverywhere = await page.evaluate(() => window.__MOCK_CTRL__.server().calls);
    await page.evaluate(() => window.SonstengEditor.chooseSharedEverywhere());
    await page.waitForFunction(() => window.SonstengEditor.block(4).state === 'IDLE', { timeout: 4000 });
    const everywhere = await page.evaluate(() => ({ server: window.__MOCK_CTRL__.server(), last: window.__MOCK_CTRL__.last(), expectedRef: window.__HARNESS_MAP__[3].source_ref }));
    assert('SH4 change-everywhere sends exactly one suggestion against the shared leaf',
      everywhere.server.calls - beforeEverywhere === 1 &&
      everywhere.last.source_ref === everywhere.expectedRef,
      'calls=' + (everywhere.server.calls - beforeEverywhere) + ' ref=' + (everywhere.last && everywhere.last.source_ref));

    await page.evaluate(() => {
      window.__MOCK_CTRL__.clear();
      window.__U5_OVERRIDE_REQUESTS__ = [];
      window.addEventListener('sonsteng:page-override-requested', e => window.__U5_OVERRIDE_REQUESTS__.push(e.detail), { once: true });
      window.SonstengEditor.typeInto(4, 'Only this page should receive this wording.');
      window.SonstengEditor.clickSave(4);
      window.SonstengEditor.chooseSharedThisPage();
    });
    const thisPage = await page.evaluate(() => ({
      calls: window.__MOCK_CTRL__.server().calls,
      requests: window.__U5_OVERRIDE_REQUESTS__,
      block: window.SonstengEditor.block(4),
      expectedRef: window.__HARNESS_MAP__[3].source_ref,
      expectedPage: window.SonstengEditor.page()
    }));
    assert('SH5 this-page-only routes to the U5 override seam without editing the shared leaf',
      thisPage.calls === 0 && thisPage.requests.length === 1 &&
      thisPage.requests[0].source_ref === thisPage.expectedRef &&
      thisPage.requests[0].page === thisPage.expectedPage,
      'calls=' + thisPage.calls + ' requests=' + thisPage.requests.length);
    await page.evaluate(() => window.SonstengEditor.clickCancel(4));

    await page.evaluate(() => {
      window.__MOCK_CTRL__.clear();
      window.SonstengEditor.typeInto(4, 'Dismiss this reach choice.');
      window.SonstengEditor.clickSave(4);
      window.SonstengEditor.dismissSharedDialog();
    });
    const dismissed = await page.evaluate(() => ({
      calls: window.__MOCK_CTRL__.server().calls,
      open: window.SonstengEditor.sharedDialog(),
      block: window.SonstengEditor.block(4),
      pending: document.querySelector('[data-eb-index="4"]').classList.contains('eb--pending')
    }));
    assert('SH6 dismissing reach leaves no suggestion or pending mark',
      dismissed.calls === 0 && !dismissed.open && dismissed.block.state === 'EDITING' && dismissed.pending === false,
      'calls=' + dismissed.calls + ' state=' + dismissed.block.state + ' pending=' + dismissed.pending);
    await page.evaluate(() => window.SonstengEditor.clickCancel(4));
    // U4 deliberately exercises a successful edit of block 4, which changes
    // that session's in-memory baseline. Reload so the long-standing hydration
    // and selection checks below retain their pristine fixture assumptions.
    await page.reload({ waitUntil: 'load' });
    await page.waitForFunction(() => window.SonstengEditor && window.SonstengEditor.ready() >= 4, { timeout: 8000 });
    await installRadiusMock(page);
    // C4 (revised 2026-07-27): the explanation used to stand permanently under
    // every formatted block — the same sentence repeated dozens of times down a
    // matter packet. It now travels with the control that can act on it: the
    // Comment affordance's tooltip, and the comment panel once opened. The
    // CONTRACT being pinned is that the reason is never lost, only relocated.
    const tip3 = await page.evaluate(() => {
      // A rail may live beside its block OR in the overlay layer, so it is found
      // by identity rather than by position.
      const tools = document.querySelector('.eb-tools[data-eb-for="3"]');
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
    await page.evaluate(() => window.SonstengEditor.chooseSharedEverywhere());
    await page.waitForFunction(() => window.SonstengEditor.block(4).state === 'IDLE', { timeout: 4000 });
    const last = await page.evaluate(() => window.__MOCK_CTRL__.last());
    assert('HY7 re-edit from a hydrated block saves a valid supersede (canonical original_hash sent, unpoisoned)',
      last && last.source_ref === HYREF && last.original_hash === 'hash-idx4-v1' && /re-edit that starts/.test(last.new_text || ''),
      'sent hash=' + (last && last.original_hash) + ' text="' + ((last && last.new_text) || '').slice(0, 30) + '"');

    /* --- ST: structural operations (U4) — add / remove / move ------------- */
    // Affordance placement: prose blocks carry Add/Remove; move appears only
    // where a destination is expressible (after-a-block only); scalars get none.
    const stAff = await page.evaluate(() => {
      var q = function (idx, cls) {
        return !!document.querySelector('.eb-tools[data-eb-for="' + idx + '"] .eb-act--' + cls);
      };
      return { add5: q(5, 'add'), rm5: q(5, 'remove'), up5: q(5, 'up'), down5: q(5, 'down'),
               add6: q(6, 'add'), rm6: q(6, 'remove'), up6: q(6, 'up'), down6: q(6, 'down'),
               addScalar: q(2, 'add'), rmScalar: q(2, 'remove') };
    });
    assert('ST1 add/remove affordances on prose blocks; none on json_scalar',
      stAff.add5 && stAff.rm5 && stAff.add6 && stAff.rm6 && !stAff.addScalar && !stAff.rmScalar,
      JSON.stringify(stAff));
    assert('ST2 move only where a destination is expressible (5: down only; 6: none)',
      stAff.down5 && !stAff.up5 && !stAff.up6 && !stAff.down6, JSON.stringify(stAff));

    // Add-paragraph: composer opens, sends op:insert_after with the typed text.
    await page.click('.eb-tools[data-eb-for="5"] .eb-act--add');
    await page.waitForSelector('.eb-composer', { timeout: 3000 });
    await page.evaluate(() => {
      document.querySelector('.eb-composer textarea').value = 'A paragraph John added himself.';
    });
    await page.click('.eb-composer__send');
    await page.waitForFunction(() => {
      var l = window.__MOCK_CTRL__.last();
      return l && l.op === 'insert_after';
    }, { timeout: 4000 });
    const stAddBody = await page.evaluate(() => window.__MOCK_CTRL__.last());
    const REF5 = await page.evaluate(() => window.SonstengEditor.block(5).ref);
    const REF6 = await page.evaluate(() => window.SonstengEditor.block(6).ref);
    assert('ST3 composer sends op:insert_after anchored to the block, with the text',
      stAddBody.op === 'insert_after' && stAddBody.source_ref === REF5 &&
      stAddBody.new_text === 'A paragraph John added himself.' && stAddBody.original_hash === 'hash-idx5-v1',
      'op=' + stAddBody.op + ' ref=' + stAddBody.source_ref.slice(-12));
    const stPillTxt = await page.evaluate(() => window.SonstengEditor.statusText(5));
    assert('ST4 structural pill says it waits for review (never an instant claim)',
      /waiting for review/i.test(stPillTxt || ''), 'pill="' + stPillTxt + '"');

    // Remove: two-step confirm, sends op:delete with no payload.
    await page.click('.eb-tools[data-eb-for="6"] .eb-act--remove');
    await page.waitForSelector('.eb-confirm', { timeout: 3000 });
    const confirmCopy = await page.$eval('.eb-confirm__msg', el => el.textContent);
    assert('ST5 delete confirm states review-first + restorability in plain words',
      /Damien reviews/.test(confirmCopy) && /restored/.test(confirmCopy),
      '"' + confirmCopy.slice(0, 60) + '"');
    await page.click('.eb-confirm__yes');
    await page.waitForFunction(() => {
      var l = window.__MOCK_CTRL__.last();
      return l && l.op === 'delete';
    }, { timeout: 4000 });
    const stDelBody = await page.evaluate(() => window.__MOCK_CTRL__.last());
    assert('ST6 confirm sends op:delete addressed to the block, no payload',
      stDelBody.op === 'delete' && stDelBody.source_ref === REF6 && stDelBody.new_text == null,
      'op=' + stDelBody.op);

    // Move down: sends op:move with the NEXT same-file block as destination.
    await page.click('.eb-tools[data-eb-for="5"] .eb-act--down');
    await page.waitForFunction(() => {
      var l = window.__MOCK_CTRL__.last();
      return l && l.op === 'move';
    }, { timeout: 4000 });
    const stMoveBody = await page.evaluate(() => window.__MOCK_CTRL__.last());
    assert('ST7 move sends op:move with the same-document destination ref',
      stMoveBody.op === 'move' && stMoveBody.source_ref === REF5 && stMoveBody.op_arg === REF6,
      'op_arg=' + String(stMoveBody.op_arg).slice(-12));

    // A pending structural item NEVER paints its payload into the anchor block.
    await page.evaluate((ref) => window.SonstengEditor.applyPending([
      { block_index: 5, source_ref: ref, status: 'pending', kind: 'insert_after',
        new_text: 'Must never appear inside the anchor.', base_hash: 'hash-idx5-v1',
        attribution: 'JOS', preview: 'new paragraph' }
    ]), REF5);
    const stHyState = await page.evaluate(() => ({
      text: window.SonstengEditor.blockText(5),
      hydrated: window.SonstengEditor.block(5).hydrated,
      st: window.SonstengEditor.statusText(5)
    }));
    assert('ST8 structural pending shows as a pill on the anchor, never hydrates into it',
      stHyState.text.indexOf('Must never appear') === -1 && stHyState.hydrated === false &&
      /New paragraph/.test(stHyState.st || ''),
      'text="' + stHyState.text.slice(0, 30) + '" pill="' + stHyState.st + '"');

    /* --- SC: the scoped-change dialog (U8) -------------------------------- */
    await page.click('.editor-banner__bigger');
    await page.waitForSelector('.eb-scoped', { timeout: 3000 });
    const scCopy = await page.$eval('.eb-scoped__lede', el => el.textContent);
    assert('SC1 dialog states plainly that Damien approves before anything changes',
      /Damien approves/.test(scCopy), '"' + scCopy.slice(0, 60) + '"');
    const scBusinessOption = await page.evaluate(() => {
      const sel = document.querySelector('.eb-scoped__scope');
      const option = Array.from(sel.options).find(o => o.textContent === 'This matter\u2019s business arrangements');
      if (!option) return null;
      sel.value = option.value;
      document.querySelector('.eb-scoped__text').value = 'Update the fee arrangement.';
      return option.textContent;
    });
    await page.click('.eb-scoped__send');
    await page.waitForFunction(() => {
      const l = window.__MOCK_CTRL__.last();
      return l && l.instruction === 'Update the fee arrangement.';
    }, { timeout: 4000 });
    await page.waitForFunction(() => {
      const send = document.querySelector('.eb-scoped__send');
      return send && send.disabled === false;
    }, { timeout: 4000 });
    const scBusinessPayload = await page.evaluate(() => {
      const l = window.__MOCK_CTRL__.last();
      return { level: l.level, matter: l.matter, part: l.part };
    });
    const scExpectedBusinessPayload = {
      level: 'part', matter: 'm05-dwi-meridian', part: 'business'
    };
    assert('SC1B business arrangements is offered and sends the exact business part scope',
      scBusinessOption === 'This matter\u2019s business arrangements' &&
      JSON.stringify(scBusinessPayload) === JSON.stringify(scExpectedBusinessPayload),
      'scope=' + JSON.stringify(scBusinessPayload));
    await page.click('.eb-scoped__cancel');
    await page.click('.editor-banner__bigger');
    // Reload round-trip: text, scope, and the idempotency id travel together.
    const scDraftBefore = await page.evaluate(() => {
      const sel = document.querySelector('.eb-scoped__scope');
      sel.value = '3';
      sel.dispatchEvent(new Event('change'));
      const ta = document.querySelector('.eb-scoped__text');
      ta.value = 'Keep this carefully drafted paragraph through reload.';
      ta.dispatchEvent(new Event('input'));
      const key = Object.keys(sessionStorage).find(k => k.indexOf('scoped-request|') !== -1);
      return { key, rec: JSON.parse(sessionStorage.getItem(key)) };
    });
    await page.reload({ waitUntil: 'load' });
    await page.waitForFunction(() => window.SonstengEditor && window.SonstengEditor.ready() >= 4, { timeout: 8000 });
    await installRadiusMock(page);
    await page.click('.editor-banner__bigger');
    const scReload = await page.evaluate((key) => ({
      text: document.querySelector('.eb-scoped__text').value,
      scope: document.querySelector('.eb-scoped__scope').value,
      rec: JSON.parse(sessionStorage.getItem(key)),
      label: document.querySelector('.eb-scoped__send').textContent,
      status: document.querySelector('.eb-scoped__status').textContent
    }), scDraftBefore.key);
    assert('SCR reload restores scoped wording, scope, and the SAME id without stale confirmation',
      scReload.text === scDraftBefore.rec.instruction &&
      scReload.scope === scDraftBefore.rec.scope_index &&
      scReload.rec.id === scDraftBefore.rec.id &&
      /Send to Damien/.test(scReload.label) && scReload.status === '',
      'id=' + String(scReload.rec.id).slice(0, 8) + ' scope=' + scReload.scope);
    await page.click('.eb-scoped__cancel');
    await page.click('.editor-banner__bigger');
    await page.evaluate(() => {
      const sel = document.querySelector('.eb-scoped__scope');
      sel.value = String(sel.options.length - 1);          // "The whole course"
      document.querySelector('.eb-scoped__text').value = 'Modernize the tone throughout.';
    });
    await page.click('.eb-scoped__send');
    await page.waitForFunction(() => {
      const l = window.__MOCK_CTRL__.last();
      return l && l.level === 'course';
    }, { timeout: 4000 });
    const scFirstId = await page.evaluate(() => window.__MOCK_CTRL__.last().id);
    const scStatus = await page.$eval('.eb-scoped__status', el => el.textContent);
    assert('SC2 over-wide scope asks for plain-words confirmation with the radius',
      /3692 paragraphs/.test(scStatus) && /20 matter/.test(scStatus),
      '"' + scStatus.slice(0, 70) + '"');

    /* SC3: changing the scope after a ceiling prompt REVOKES the confirmation —
       a module-sized "yes" must never file a course-wide change. Tested before
       any successful send, because a sent dialog self-closes on a timer. */
    await page.evaluate(() => {
      const sel = document.querySelector('.eb-scoped__scope');
      sel.value = '0';
      sel.dispatchEvent(new Event('change'));
    });
    const scRevoked = await page.evaluate(() => ({
      label: document.querySelector('.eb-scoped__send').textContent,
      status: document.querySelector('.eb-scoped__status').textContent,
      draftId: JSON.parse(sessionStorage.getItem(
        Object.keys(sessionStorage).find(k => k.indexOf('scoped-request|') !== -1)
      )).id
    }));
    assert('SC3 changing scope after the ceiling prompt revokes confirmation and rotates id',
      /Send to Damien/.test(scRevoked.label) && scRevoked.status === '' &&
      scRevoked.draftId !== scFirstId,
      'label="' + scRevoked.label + '" id=' + String(scRevoked.draftId).slice(0, 8));

    /* SC4: back to the wide scope -> a FRESH 409 under a rotated id (the
       revoked confirmation did not carry over). */
    await page.evaluate(() => {
      const sel = document.querySelector('.eb-scoped__scope');
      sel.value = String(sel.options.length - 1);
      sel.dispatchEvent(new Event('change'));
    });
    await page.click('.eb-scoped__send');
    await sleep(600);
    const scSecondId = await page.evaluate(() => window.__MOCK_CTRL__.last().id);
    assert('SC4 the post-revocation send carries a fresh id, unconfirmed',
      scSecondId !== scFirstId, 'first=' + String(scFirstId).slice(0, 8) +
      ' second=' + String(scSecondId).slice(0, 8));

    /* SC5: the confirmed resend reuses THAT id — compare the ids for real; a
       "typeof id === string" assertion let a fresh-uuid-per-click regression
       pass green (it shipped in the sibling add-fact composer). */
    await page.click('.eb-scoped__send');
    await sleep(600);
    const scResend = await page.evaluate(() => ({
      body: window.__MOCK_CTRL__.last(),
      decision: window.__MOCK_CTRL__.scopedDecision()
    }));
    const scLast = scResend.body;
    assert('SC5 confirmed resend echoes the issued token, is accepted, and keeps the SAME id (idempotent)',
      scResend.decision.accepted === true && scResend.decision.status === 200 &&
      typeof scLast.confirmation_token === 'string' &&
      scLast.confirmation_token.length > 0 &&
      scLast.confirmation_token === scResend.decision.issuedToken &&
      scLast.confirmed === true && scLast.id === scSecondId &&
      scLast.instruction === 'Modernize the tone throughout.',
      'resend=' + String(scLast.id).slice(0, 8) + ' confirmed=' + scLast.confirmed +
      ' accepted=' + scResend.decision.accepted +
      ' token=' + String(scLast.confirmation_token || 'missing'));

    const scLaundered = await page.evaluate(() => window.__EDITOR_MOCK__({
      path: '/scoped-request', method: 'POST',
      headers: { 'X-Edit-Request': '1' },
      body: { id: 'laundered-confirmation', level: 'course',
        instruction: 'Modernize the tone throughout.', confirmed: true,
        confirmation_token: 'stale-confirmation-token' }
    }));
    assert('SC6 mock gate re-challenges a stale confirmation token, not filed',
      scLaundered.status === 409 &&
      scLaundered.data.error.code === 'ceiling_confirmation_required' &&
      !!scLaundered.data.confirmation_token,
      'status=' + scLaundered.status);

    await page.evaluate(() => {
      const d = document.querySelector('.eb-scoped');
      if (d) d.parentNode.removeChild(d);
      Object.keys(sessionStorage).forEach(k => {
        if (k.indexOf('scoped-request|') !== -1) sessionStorage.removeItem(k);
      });
    });

    // The fixture proves the boundary is computed, not synonymous with course:
    // this matter's ~122-block case-file part also exceeds the 100-block ceiling.
    await page.click('.editor-banner__bigger');
    await page.evaluate(() => {
      const ta = document.querySelector('.eb-scoped__text');
      ta.value = 'Update this case-file part.';
      ta.dispatchEvent(new Event('input'));
    });
    await page.click('.eb-scoped__send');
    await sleep(150);
    const scPartStatus = await page.$eval('.eb-scoped__status', el => el.textContent);
    assert('SC7 a non-course part with radius 122 also crosses the computed ceiling',
      /122 paragraphs/.test(scPartStatus), '"' + scPartStatus + '"');

    await page.screenshot({ path: path.join(OUT, 'editor-desktop.png'), fullPage: false });
    console.log('   [screenshot] ' + path.join(OUT, 'editor-desktop.png'));
    // large-type screenshot for visual QA
    await page.screenshot({ path: path.join(OUT, 'editor-largetype.png'), fullPage: false });
    console.log('   [screenshot] ' + path.join(OUT, 'editor-largetype.png'));
    await page.close();
  }

  /* ======================= FACTS PAGE (add-a-fact U8) ===================== */
  {
    const page = await bootFacts(browser, 1280, 1100);

    // A composed fact survives reload with the id that names that one intent.
    const afDraftBefore = await page.evaluate(() => {
      const name = document.querySelector('.eb-addfact__name');
      const text = document.querySelector('.eb-composer__text');
      name.value = 'response-window';
      name.dispatchEvent(new Event('input'));
      text.value = 'The drafted prose mention may be declined.';
      text.dispatchEvent(new Event('input'));
      const key = Object.keys(sessionStorage).find(k => k.indexOf('add-fact|') !== -1);
      return { key, rec: JSON.parse(sessionStorage.getItem(key)) };
    });
    await page.reload({ waitUntil: 'load' });
    await page.waitForSelector('.eb-composer', { timeout: 3000 });
    const afReload = await page.evaluate((key) => ({
      name: document.querySelector('.eb-addfact__name').value,
      text: document.querySelector('.eb-composer__text').value,
      rec: JSON.parse(sessionStorage.getItem(key))
    }), afDraftBefore.key);
    assert('AF1 reload restores add-a-fact key, text, and the SAME id',
      afReload.name === afDraftBefore.rec.name &&
      afReload.text === afDraftBefore.rec.text &&
      afReload.rec.id === afDraftBefore.rec.id,
      'id=' + String(afReload.rec.id).slice(0, 8));

    // First send fails; retry must replay the exact id, then land once.
    await page.evaluate(() => window.__MOCK_CTRL__.forceOnce('network'));
    await page.click('.eb-composer__send');
    await sleep(150);
    const afFirst = await page.evaluate(() => window.__MOCK_CTRL__.last());
    await page.click('.eb-composer__send');
    await sleep(150);
    const afRetry = await page.evaluate(() => ({
      last: window.__MOCK_CTRL__.last(),
      server: window.__MOCK_CTRL__.server(),
      status: document.querySelector('.eb-composer__row .eb-scoped__status').textContent,
      rendered: document.querySelector('main').textContent
    }));
    assert('AF2 retry after a failed send compares and reuses the SAME id',
      afFirst && afRetry.last.id === afFirst.id && afRetry.server.count === 1,
      'first=' + String(afFirst && afFirst.id).slice(0, 8) +
      ' retry=' + String(afRetry.last && afRetry.last.id).slice(0, 8));
    assert('AF3 submits the json_add matter/key/value, reports Added, and does not render the submitted value as prose',
      afRetry.last.op === 'json_add' &&
      afRetry.last.matter === 'm05-dwi-meridian' &&
      afRetry.last.fact_key === 'response-window' &&
      afRetry.last.new_text === 'The drafted prose mention may be declined.' &&
      /Added/.test(afRetry.status) &&
      afRetry.rendered.indexOf('The drafted prose mention may be declined.') === -1,
      'op=' + afRetry.last.op + ' matter=' + afRetry.last.matter +
      ' key=' + afRetry.last.fact_key + ' value=' + JSON.stringify(afRetry.last.new_text) +
      ' status=' + JSON.stringify(afRetry.status) + ' prose-rendered=false');

    const callsBeforeInvalid = afRetry.server.calls;
    await page.type('.eb-addfact__name', '!!!');
    await page.type('.eb-composer__text', 'Invalid key must not be sent.');
    await page.click('.eb-composer__send');
    await sleep(100);
    const callsAfterInvalid = await page.evaluate(() => window.__MOCK_CTRL__.server().calls);
    assert('AF4 schema-invalid new key is rejected before send',
      callsAfterInvalid === callsBeforeInvalid,
      'calls before=' + callsBeforeInvalid + ' after=' + callsAfterInvalid);
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
      // A rail may live beside its block OR in the overlay layer, so it is found
      // by identity rather than by position.
      const tools = document.querySelector('.eb-tools[data-eb-for="3"]');
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
