/* Headless contract check for the local-only weekly-hours editor.
   Run: EDITOR_HEADLESS=1 node app/hours/verify-hours.js */
'use strict';
const path = require('path');
const puppeteer = require('/home/damienriehl/.npm/_npx/7d92d9a2d2ccc630/node_modules/puppeteer');

const PAGE = 'file://' + path.join(__dirname, 'index.html');
const results = [];
function check(name, value) {
  results.push(!!value);
  console.log((value ? 'PASS' : 'FAIL') + '  ' + name);
}

(async function () {
  const browser = await puppeteer.launch({
    executablePath: '/snap/bin/chromium',
    headless: process.env.EDITOR_HEADLESS !== '0',
    args: ['--no-sandbox', '--disable-dev-shm-usage', '--allow-file-access-from-files']
  });
  const page = await browser.newPage();
  await page.setViewport({width: 390, height: 844});
  const external = [];
  page.on('request', req => { if (!req.url().startsWith('file:') && !req.url().startsWith('blob:')) external.push(req.url()); });
  await page.evaluateOnNewDocument(() => {
    localStorage.setItem('sonsteng.weekly-hours.v1', '{"storage_version":99,"opaque":"future bytes"}');
  });
  await page.goto(PAGE, {waitUntil: 'load'});
  await page.click('[data-mode="persistent"]');
  const quarantined = await page.$eval('#storage-status', n => n.textContent);
  await page.type('#learner-id', 'synthetic-change');
  const preserved = await page.evaluate(() => localStorage.getItem('sonsteng.weekly-hours.v1'));
  check('future storage envelope is quarantined', /byte-preserved|preserved/.test(quarantined));
  check('older client does not overwrite future bytes', preserved === '{"storage_version":99,"opaque":"future bytes"}');
  check('import remains disabled while future bytes are quarantined',
    /disabled|preserve/i.test(await page.$eval('#import-preview', n => {
      document.querySelector('#preview-import').click(); return n.textContent;
    })));
  check('no network request occurs', external.length === 0);
  check('mobile viewport has no horizontal overflow', await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth));
  check('live region is present', await page.$eval('#announcer', n => n.getAttribute('aria-live') === 'polite'));
  await page.close();

  const weeks = await browser.newPage();
  await weeks.evaluateOnNewDocument(() => localStorage.clear());
  await weeks.goto(PAGE, {waitUntil: 'load'});
  await weeks.click('[data-mode="persistent"]');
  await weeks.type('#learner-id', 'learner-synthetic-nav');
  await weeks.type('#offering-id', 'offering-synthetic-nav');
  await weeks.click('#add-entry');
  await weeks.type('.entry input[type="text"]', 'Synthetic retained project');
  await weeks.click('#next-week');
  await weeks.click('#previous-week');
  check('previous and next week navigation preserves each weekly draft',
    await weeks.$eval('.entry input[type="text"]', n => n.value) === 'Synthetic retained project');
  await weeks.evaluate(() => { window.confirm = () => true; });
  await weeks.click('#clear');
  check('clear removes the storage key instead of recreating an empty envelope',
    await weeks.evaluate(() => localStorage.getItem('sonsteng.weekly-hours.v1')) === null);
  await weeks.close();

  const unavailable = await browser.newPage();
  await unavailable.evaluateOnNewDocument(() => {
    Storage.prototype.setItem = function () { throw new Error('synthetic storage failure'); };
  });
  await unavailable.goto(PAGE, {waitUntil: 'load'});
  await unavailable.click('[data-mode="persistent"]');
  check('storage probe failure degrades to visible export-only mode',
    /unavailable|export-only/i.test(await unavailable.$eval('#storage-status', n => n.textContent)));
  await unavailable.close();
  await browser.close();
  if (results.some(x => !x)) process.exitCode = 1;
}()).catch(err => { console.error(err); process.exitCode = 1; });
