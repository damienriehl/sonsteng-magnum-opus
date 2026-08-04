/* Mock-only browser gate for interview and critique behavior/layout. */
'use strict';

const fs = require('fs');
const path = require('path');

const REPO = path.resolve(__dirname, '..');
const MATRIX = JSON.parse(
  fs.readFileSync(path.join(__dirname, 'platform_browser_matrix.json'), 'utf8')
);
const HARNESS = 'file://' + path.join(REPO, 'app', 'chat', 'test.html');
const QUERY = {
  interview: '?view=chat&matter=m05&persona=m05.per.halvard&title=State%20v.%20Halvard&client=Devon%20Halvard&scenario=normal',
  critique: '?view=critique&matter=m05&title=Suppression%20Memo%20Critique'
};

function loadPuppeteer() {
  const candidates = [
    process.env.PUP_DIR,
    'puppeteer',
    '/home/damienriehl/.npm/_npx/7d92d9a2d2ccc630/node_modules/puppeteer'
  ].filter(Boolean);
  for (const candidate of candidates) {
    try {
      return require(candidate);
    } catch (_) {}
  }
  throw new Error('Puppeteer unavailable (set PUP_DIR or install puppeteer)');
}

async function setTypeMode(page, large) {
  await page.evaluateOnNewDocument(
    (on) => localStorage.setItem('sonsteng-type-lg', on ? '1' : '0'),
    large
  );
}

async function inspectLayout(page) {
  return page.evaluate(() => {
    const visible = (element) => {
      const style = getComputedStyle(element);
      const rect = element.getBoundingClientRect();
      return style.display !== 'none' && style.visibility !== 'hidden' && rect.width > 0 && rect.height > 0;
    };
    const inProduct = (element) => !element.closest('#harness');
    const headings = [...document.querySelectorAll('h1,h2,h3,h4,h5,h6')]
      .filter((element) => visible(element) && inProduct(element));
    let previousLevel = 0;
    const jumps = [];
    headings.forEach((heading) => {
      const level = +heading.tagName[1];
      if (previousLevel && level > previousLevel + 1) jumps.push(`h${previousLevel}->h${level}`);
      previousLevel = level;
    });
    const controls = [...document.querySelectorAll('button,input,textarea,select')]
      .filter((element) => visible(element) && inProduct(element))
      .map((element) => ({
        text: (element.textContent || element.getAttribute('aria-label') || '').trim(),
        w: element.getBoundingClientRect().width,
        h: element.getBoundingClientRect().height
      }))
      .filter((control) => control.w < 24 || control.h < 24);
    return {
      h1: headings.filter((heading) => heading.tagName === 'H1').length,
      jumps,
      overflow: document.documentElement.scrollWidth - document.documentElement.clientWidth,
      large: document.documentElement.classList.contains('type-lg'),
      controls
    };
  });
}

async function exerciseInterview(page) {
  await page.waitForFunction(
    () => window.SonstengChat && window.SonstengChat.getState() === 'IDLE' && sessionStorage.getItem('sonsteng_sess'),
    {timeout: 10000}
  );
  await page.evaluate(() => window.SonstengChat.send('Please tell me what happened from the beginning.'));
  await page.waitForFunction(
    () => window.SonstengChat && window.SonstengChat.getTurns() === 1,
    {timeout: 15000}
  );
  const reply = await page.$eval('#stream', (element) => element.textContent);
  if (!reply.includes('It was late')) throw new Error('mock interview outcome changed');
}

async function exerciseCritique(page) {
  await page.type(
    '#deliverable',
    'The stop should be suppressed because the officer lacked reasonable articulable suspicion. The icy road explains the observed movement.'
  );
  await page.click('.paste button[type=submit]');
  await page.waitForSelector('.crit-card', {timeout: 10000});
  const text = await page.$eval('#result-mount', (element) => element.textContent);
  if (!text.includes('Issue framing') || !text.includes('26 / 40')) {
    throw new Error('mock critique outcome changed');
  }
}

async function verifyLargeType(page) {
  const found = await page.evaluate(() => {
    const button = [...document.querySelectorAll('button')]
      .find((candidate) => candidate.textContent.trim() === 'LARGE TYPE');
    if (!button) return false;
    button.click();
    return button.getAttribute('aria-pressed') === 'true';
  });
  if (!found) throw new Error('Large Type control missing or aria-pressed did not update');
}

function assertLayout(layout) {
  const errors = [];
  if (layout.h1 !== 1) errors.push(`H1=${layout.h1}`);
  if (layout.jumps.length) errors.push(`heading jumps ${layout.jumps}`);
  if (layout.overflow > 1) errors.push(`overflow ${layout.overflow}px`);
  if (layout.controls.length) {
    errors.push(`undersize controls ${JSON.stringify(layout.controls.slice(0, 4))}`);
  }
  if (!layout.large) errors.push('Large Type class absent');
  if (errors.length) throw new Error(errors.join(' | '));
}

async function runCase(browser, viewport, large, surface) {
  const page = await browser.newPage();
  await page.setViewport(viewport);
  await setTypeMode(page, large);
  try {
    await page.goto(HARNESS + QUERY[surface], {waitUntil: 'networkidle0', timeout: 30000});
    await page.waitForSelector(surface === 'interview' ? '#composer-input' : '#deliverable', {timeout: 10000});
    if (surface === 'interview') await exerciseInterview(page);
    else await exerciseCritique(page);
    await verifyLargeType(page);
    assertLayout(await inspectLayout(page));
    console.log(`PASS ${surface} ${viewport.name} ${large ? 'large-start' : 'baseline-start'}`);
    return true;
  } catch (error) {
    console.error(`FAIL ${surface} ${viewport.name} ${large ? 'large' : 'baseline'} — ${error.message}`);
    return false;
  } finally {
    await page.close();
  }
}

async function run() {
  const puppeteer = loadPuppeteer();
  const browser = await puppeteer.launch({
    executablePath: process.env.CHROME_BIN || process.env.CHROMIUM_PATH || '/snap/bin/chromium',
    headless: process.env.HEADLESS === '1',
    userDataDir: path.join('/tmp', `sonsteng-chat-${process.pid}`),
    args: ['--no-sandbox', '--disable-dev-shm-usage', '--disable-crash-reporter', '--disable-breakpad']
  });
  let failures = 0;
  let total = 0;
  for (const viewport of MATRIX.viewports) {
    for (const large of [false, true]) {
      for (const surface of ['interview', 'critique']) {
        total++;
        if (!await runCase(browser, viewport, large, surface)) failures++;
      }
    }
  }
  await browser.close();
  console.log(`CHAT/CRITIQUE SUMMARY ${total - failures}/${total} PASS`);
  process.exit(failures ? 1 : 0);
}

run().catch((error) => {
  console.error('BROWSER GATE ERROR:', error.message);
  process.exit(1);
});
