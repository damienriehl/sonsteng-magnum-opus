/* Real-browser contract for catalog enhancement, history, and scaled paging. */
'use strict';
const fs = require('fs');
const http = require('http');
const path = require('path');
const SITE = path.resolve(__dirname, '..', 'site', 'platform', 'matters');

function loadPuppeteer() {
  const candidates = [process.env.PUP_DIR, 'puppeteer', '/home/damienriehl/.npm/_npx/7d92d9a2d2ccc630/node_modules/puppeteer'].filter(Boolean);
  for (const candidate of candidates) { try { return require(candidate); } catch (_) {} }
  throw new Error('Puppeteer unavailable (set PUP_DIR or install puppeteer)');
}
const matters = Array.from({length: 60}, (_, i) => ({
  id: `m${String(i + 1).padStart(2, '0')}`, slug: `matter-${i + 1}`,
  caption: `Matter ${i + 1}`, history_summary: `History ${i + 1}`,
  shape: 'trial', shape_label: 'Trial', tier: 'real', jurisdiction: 'MN', fee_type: 'fixed',
}));

async function run() {
  const server = http.createServer((req, res) => {
    const pathname = new URL(req.url, 'http://127.0.0.1').pathname;
    if (pathname.endsWith('/catalog-index.json')) {
      return setTimeout(() => { res.setHeader('content-type', 'application/json'); res.end(JSON.stringify({page_size: 50, matters})); }, 250);
    }
    const name = pathname.endsWith('/catalog.js') ? 'catalog.js' : 'index.html';
    res.setHeader('content-type', name.endsWith('.js') ? 'text/javascript' : 'text/html');
    res.end(fs.readFileSync(path.join(SITE, name)));
  });
  await new Promise((resolve) => server.listen(0, '127.0.0.1', resolve));
  const browser = await loadPuppeteer().launch({
    executablePath: process.env.CHROME_BIN || process.env.CHROMIUM_PATH || '/snap/bin/chromium',
    headless: process.env.HEADLESS === '1', args: ['--no-sandbox', '--disable-dev-shm-usage'],
  });
  let failures = 0;
  try {
    const page = await browser.newPage();
    await page.goto(`http://127.0.0.1:${server.address().port}/page-2.html`, {waitUntil: 'domcontentloaded'});
    await page.type('#catalog-search', 'typed-before-index');
    await page.waitForFunction(() => document.querySelector('[data-catalog-status]').textContent.includes('page 2 of 2'));
    const initial = await page.evaluate(() => ({
      input: document.querySelector('#catalog-search').value,
      cards: document.querySelectorAll('[data-catalog-results] .catalog-card').length,
    }));
    if (initial.input !== 'typed-before-index' || initial.cards !== 10) failures++;

    await page.click('#catalog-search', {clickCount: 3}); await page.type('#catalog-search', 'Matter');
    await page.click('[data-catalog-form] button[type=submit]');
    await page.waitForFunction(() => location.search.includes('q=Matter'));
    const filtered = await page.evaluate(() => ({
      focus: document.activeElement && document.activeElement.id,
      next: document.querySelector('.pagination a[aria-current="false"]')?.href || '',
    }));
    const next = new URL(filtered.next);
    if (filtered.focus !== 'catalog-results' || !next.pathname.endsWith('/page-2.html') ||
        next.searchParams.get('q') !== 'Matter' || next.searchParams.get('page') !== '2') failures++;

    await page.click('#catalog-search', {clickCount: 3}); await page.type('#catalog-search', 'Matter 1');
    await page.click('[data-catalog-form] button[type=submit]'); await page.goBack();
    await page.waitForFunction(() => document.querySelector('#catalog-search').value === 'Matter');
    const restored = await page.$eval('[data-catalog-status]', (el) => el.textContent);
    if (!restored.includes('60 matters · page 1 of 2')) failures++;
    await page.close();
  } finally {
    await browser.close(); await new Promise((resolve) => server.close(resolve));
  }
  console.log(`CATALOG CLIENT ${failures ? 'FAIL' : 'PASS'}`);
  return failures ? 1 : 0;
}
run().then((code) => process.exit(code)).catch((error) => { console.error('CATALOG CLIENT ERROR:', error.message); process.exit(1); });
