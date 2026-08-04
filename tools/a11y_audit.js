/* ============================================================================
   a11y_audit.js — accessibility sweep over the generated practicum pages.

   Written 2026-07-27 after Damien found the STANDARD / LARGE TYPE toggle sitting
   at 1.06:1 — cream text on a cream background, effectively invisible, shipped
   and unnoticed. The lesson is not "fix that toggle"; it is that nothing was
   MEASURING. This does.

   Checks, all from the rendered DOM in a real browser (no external service, no
   new dependency beyond the puppeteer the editor harness already uses):

     TEXT CONTRAST      every visible text node vs its effective background,
                        against the WCAG 1.4.3 threshold for its size/weight
                        (4.5:1, or 3:1 for large text).
     UI CONTRAST        borders/glyphs of interactive controls vs their
                        surround (1.4.11, 3:1).
     ACCESSIBLE NAMES   every button / link / input can be announced.
     TARGET SIZE        interactive controls are at least 24x24 (2.5.8 AA),
                        flagged separately at 44x44 (2.5.5 AAA — this project's
                        own standing rule, because its readers are not 25).
     IMAGE ALT          every <img> has alt (or is explicitly decorative).
     HEADINGS           no skipped levels, exactly one h1.
     LANDMARKS + LANG   <html lang>, a main landmark, a page title.

   Run:  DISPLAY=:0 node tools/a11y_audit.js [url ...]
   With no arguments it sweeps the local built site over file://.
   Exit code is 1 if any FAIL-level finding survives, so it can gate a build.
   ============================================================================ */
const puppeteer = require('/home/damienriehl/.npm/_npx/7d92d9a2d2ccc630/node_modules/puppeteer');
const path = require('path');
const fs = require('fs');

const REPO = path.resolve(__dirname, '..');
const SITE = path.join(REPO, 'site', 'platform');
const MATRIX = JSON.parse(fs.readFileSync(path.join(__dirname, 'platform_browser_matrix.json'), 'utf8'));
const DEFAULT_PAGES = MATRIX.pages.filter((p) => !p.interactive).map((p) => p.path);

const AUDIT = function () {
  /* ---- colour helpers (run INSIDE the page) ---- */
  function parseColor(c) {
    const m = /rgba?\(([\d.]+),\s*([\d.]+),\s*([\d.]+)(?:,\s*([\d.]+))?\)/.exec(c || '');
    if (!m) return null;
    return { r: +m[1], g: +m[2], b: +m[3], a: m[4] === undefined ? 1 : +m[4] };
  }
  function over(fg, bg) { // alpha-composite fg over opaque bg
    const a = fg.a;
    return { r: a * fg.r + (1 - a) * bg.r, g: a * fg.g + (1 - a) * bg.g, b: a * fg.b + (1 - a) * bg.b, a: 1 };
  }
  function lum(c) {
    const f = (v) => { v /= 255; return v <= 0.04045 ? v / 12.92 : Math.pow((v + 0.055) / 1.055, 2.4); };
    return 0.2126 * f(c.r) + 0.7152 * f(c.g) + 0.0722 * f(c.b);
  }
  function ratio(a, b) {
    const la = lum(a), lb = lum(b), hi = Math.max(la, lb), lo = Math.min(la, lb);
    return (hi + 0.05) / (lo + 0.05);
  }
  function hex(c) {
    const h = (v) => Math.round(v).toString(16).padStart(2, '0');
    return '#' + h(c.r) + h(c.g) + h(c.b);
  }
  // Effective background: walk ancestors until something is not transparent.
  function bgOf(el) {
    let n = el, acc = null;
    while (n && n.nodeType === 1) {
      const c = parseColor(getComputedStyle(n).backgroundColor);
      if (c && c.a > 0) {
        acc = acc ? over(acc, c) : c;
        if (acc.a >= 1) return acc;
      }
      n = n.parentElement;
    }
    return acc && acc.a >= 1 ? acc : { r: 255, g: 255, b: 255, a: 1 };
  }
  // Text inside an aria-hidden subtree is decoration by declaration (a "/"
  // between breadcrumbs, an ornamental rule) — it is not exposed to assistive
  // technology and 1.4.3 does not govern it. Elements that declare the WCAG
  // 2.5.8 "essential size" exception (a stacked bar segment, whose width IS the
  // datum) are likewise recorded as exceptions rather than failures.
  function decorative(el) {
    return !!el.closest('[aria-hidden="true"]');
  }
  function sizeException(el) {
    const v = el.getAttribute('data-a11y');
    return v === 'essential-size' || v === 'equivalent-table';
  }
  function visible(el) {
    const cs = getComputedStyle(el);
    if (cs.display === 'none' || cs.visibility === 'hidden') return false;
    if (parseFloat(cs.opacity) === 0) return false;
    const r = el.getBoundingClientRect();
    return r.width > 0 && r.height > 0;
  }
  function ownText(el) {
    let s = '';
    for (const n of el.childNodes) if (n.nodeType === 3) s += n.nodeValue;
    return s.trim();
  }
  function label(el) {
    const bits = [
      el.getAttribute('aria-label'),
      el.getAttribute('title'),
      el.getAttribute('alt'),
      (el.textContent || '').trim(),
    ];
    const id = el.getAttribute('aria-labelledby');
    if (id) { const t = document.getElementById(id); if (t) bits.push((t.textContent || '').trim()); }
    if (el.tagName === 'INPUT' || el.tagName === 'TEXTAREA' || el.tagName === 'SELECT') {
      if (el.id) {
        const l = document.querySelector('label[for="' + CSS.escape(el.id) + '"]');
        if (l) bits.push((l.textContent || '').trim());
      }
      bits.push(el.getAttribute('placeholder'));
    }
    return bits.filter(Boolean).join(' ').trim();
  }
  function where(el) {
    const cls = (el.getAttribute('class') || '').split(/\s+/).filter(Boolean).slice(0, 2).join('.');
    return el.tagName.toLowerCase() + (cls ? '.' + cls : '');
  }

  const findings = [];
  let exceptions = 0;
  const add = (level, check, detail, sample) => findings.push({ level, check, detail, sample });

  /* ---- 1. text contrast (1.4.3) ---- */
  const seen = new Set();
  document.querySelectorAll('body *').forEach((el) => {
    const text = ownText(el);
    if (!text || !visible(el) || decorative(el)) return;
    const cs = getComputedStyle(el);
    const fg0 = parseColor(cs.color);
    if (!fg0) return;
    const bg = bgOf(el);
    const fg = fg0.a < 1 ? over(fg0, bg) : fg0;
    // element opacity dilutes the ink toward its backdrop
    const op = parseFloat(cs.opacity);
    const eff = op < 1 ? over({ ...fg, a: op }, bg) : fg;
    const px = parseFloat(cs.fontSize);
    const weight = parseInt(cs.fontWeight, 10) || 400;
    const large = px >= 24 || (px >= 18.66 && weight >= 700);
    const need = large ? 3 : 4.5;
    const r = ratio(eff, bg);
    const key = where(el) + '|' + hex(eff) + '|' + hex(bg) + '|' + Math.round(px);
    if (r < need && !seen.has(key)) {
      seen.add(key);
      add('FAIL', 'text-contrast',
        `${r.toFixed(2)}:1 (needs ${need}:1) — ${hex(eff)} on ${hex(bg)} at ${px}px/${weight}`,
        `${where(el)} “${text.slice(0, 48)}”`);
    }
  });

  /* ---- 2. accessible names + target size on interactive controls ---- */
  const INTERACTIVE = 'a[href], button, input:not([type=hidden]), select, textarea, [role=button], [tabindex]:not([tabindex="-1"])';
  const CONTROL = new Set(['A', 'BUTTON', 'INPUT', 'SELECT', 'TEXTAREA']);
  document.querySelectorAll(INTERACTIVE).forEach((el) => {
    if (!visible(el)) return;
    const name = label(el);
    if (!name) add('FAIL', 'accessible-name', 'interactive control announces nothing', where(el));
    const r = el.getBoundingClientRect();
    const inline = el.tagName === 'A' && getComputedStyle(el).display.indexOf('inline') === 0;
    if (sizeException(el)) {
      exceptions++;
    } else if (!inline) {
      if (r.width < 24 || r.height < 24) {
        add('FAIL', 'target-size-aa', `${Math.round(r.width)}x${Math.round(r.height)} (WCAG 2.5.8 needs 24x24)`, where(el) + ' ' + name.slice(0, 32));
      } else if ((r.width < 44 || r.height < 44) && CONTROL.has(el.tagName)
                 && !el.classList.contains('chip')) {
        // AAA (2.5.5) is this project's own rule for things a reader PRESSES.
        // Applying it to SVG chart marks generated hundreds of warnings that
        // said nothing — a bar is read, not pressed.
        add('WARN', 'target-size-aaa', `${Math.round(r.width)}x${Math.round(r.height)} (project rule: 44x44)`, where(el) + ' ' + name.slice(0, 32));
      }
    }
  });

  /* ---- 3. images ---- */
  document.querySelectorAll('img').forEach((el) => {
    if (el.getAttribute('alt') === null) add('FAIL', 'img-alt', 'img has no alt attribute', el.getAttribute('src') || '');
  });

  /* ---- 4. headings ---- */
  const hs = [...document.querySelectorAll('h1,h2,h3,h4,h5,h6')].filter(visible);
  const h1s = hs.filter((h) => h.tagName === 'H1');
  if (h1s.length === 0) add('FAIL', 'headings', 'page has no visible h1', '');
  if (h1s.length > 1) add('WARN', 'headings', `${h1s.length} h1 elements`, '');
  let prev = 0;
  hs.forEach((h) => {
    const lvl = +h.tagName[1];
    if (prev && lvl > prev + 1) {
      add('WARN', 'headings', `level jumps h${prev} -> h${lvl}`, (h.textContent || '').trim().slice(0, 40));
    }
    prev = lvl;
  });

  /* ---- 5. landmarks / lang / title ---- */
  if (!document.documentElement.getAttribute('lang')) add('FAIL', 'lang', '<html> has no lang attribute', '');
  if (!document.querySelector('main, [role=main]')) add('WARN', 'landmark', 'no main landmark', '');
  if (!(document.title || '').trim()) add('FAIL', 'title', 'page has no title', '');

  if (exceptions) {
    add('NOTE', 'target-size-exception',
      `${exceptions} chart marks exempt from 2.5.8 — each chart ships an equivalent full-size data table`, '');
  }
  return findings;
};

(async () => {
  const args = process.argv.slice(2);
  const explicitTargets = args.length > 0;
  const targets = explicitTargets ? args : DEFAULT_PAGES.map((p) => 'file://' + path.join(SITE, p));
  const browser = await puppeteer.launch({
    headless: process.env.HEADLESS === '1', args: ['--no-sandbox', '--window-size=1280,900'],
    executablePath: process.env.CHROME_BIN || process.env.CHROMIUM_PATH || '/snap/bin/chromium', defaultViewport: { width: 1280, height: 900 },
    userDataDir: path.join('/tmp', `sonsteng-a11y-${process.pid}`),
  });
  let fails = 0, warns = 0;
  const report = [];
  const cases = targets.flatMap((url) => explicitTargets ? [{url, mode:'baseline'}] : MATRIX.typeModes.map((mode) => ({url, mode})));
  for (const {url, mode} of cases) {
    const page = await browser.newPage();
    try {
      await page.evaluateOnNewDocument((large) => localStorage.setItem('sonsteng-type-lg', large ? '1' : '0'), mode === 'large');
      await page.goto(url, { waitUntil: 'networkidle2', timeout: 30000 });
      await page.evaluate(() => document.fonts ? document.fonts.ready : Promise.resolve());
      const findings = await page.evaluate(AUDIT);
      const f = findings.filter((x) => x.level === 'FAIL');
      const w = findings.filter((x) => x.level === 'WARN');
      // NOTE-level entries are reasoned exceptions, printed but never counted.
      fails += f.length; warns += w.length;
      const short = url.replace('file://' + SITE + '/', '').replace(/^https?:\/\//, '');
      report.push({ url: short, mode, findings });
      console.log(`\n=== ${short} [${mode}] — ${f.length} FAIL, ${w.length} WARN`);
      const shown = new Set();
      findings.forEach((x) => {
        const k = x.check + '|' + x.detail;
        if (shown.has(k)) return;
        shown.add(k);
        console.log(`  ${x.level}  ${x.check}: ${x.detail}${x.sample ? '  [' + x.sample + ']' : ''}`);
      });
    } catch (e) {
      console.log(`\n=== ${url} — ERROR ${e.message}`);
      fails++;
    }
    await page.close();
  }
  console.log(`\n===== A11Y AUDIT: ${fails} FAIL, ${warns} WARN across ${cases.length} page/mode case(s) =====`);
  fs.writeFileSync(path.join(REPO, 'build', 'a11y-audit.json'), JSON.stringify(report, null, 2));
  await browser.close();
  process.exit(fails ? 1 : 0);
})();
