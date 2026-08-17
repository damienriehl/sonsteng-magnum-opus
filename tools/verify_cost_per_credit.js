#!/usr/bin/env node
'use strict';

const fs = require('fs');
const path = require('path');

function loadPuppeteer() {
  const candidates = [
    process.env.PUP_DIR,
    'puppeteer',
    '/home/damienriehl/.npm/_npx/7d92d9a2d2ccc630/node_modules/puppeteer'
  ].filter(Boolean);
  for (const candidate of candidates) {
    try { return require(candidate); } catch (_) { /* try the next candidate */ }
  }
  throw new Error('Puppeteer unavailable (set PUP_DIR or install puppeteer)');
}

const ROOT = path.resolve(__dirname, '..');
const PAGE = path.join(ROOT, 'site', 'cost-per-credit.html');

async function run() {
  const puppeteer = loadPuppeteer();
  const browser = await puppeteer.launch({
    headless: process.env.HEADLESS !== '0',
    args: ['--no-sandbox', '--disable-setuid-sandbox']
  });
  const failures = [];
  let checks = 0;
  const requests = [];
  const consoleErrors = [];
  const assert = (name, condition, detail = '') => {
    checks += 1;
    const pass = Boolean(condition);
    console.log(`${pass ? 'PASS' : 'FAIL'}  ${name}${detail ? ` — ${detail}` : ''}`);
    if (!pass) failures.push(name);
  };

  try {
    const page = await browser.newPage();
    await page.setViewport({ width: 390, height: 844, deviceScaleFactor: 1 });
    page.on('request', request => requests.push(request.url()));
    page.on('console', message => {
      if (message.type() === 'error') consoleErrors.push(message.text());
    });
    await page.setContent(fs.readFileSync(PAGE, 'utf8'), { waitUntil: 'load' });

    const setInput = (selector, value) => page.$eval(selector, (input, next) => {
      input.value = next;
      input.dispatchEvent(new Event('input', { bubbles: true }));
    }, value);

    assert('stipend model is the default', await page.$eval(
      'input[name="pay-model"][value="stipend"]', input => input.checked
    ));
    assert('financial assumptions start blank', await page.$$eval(
      'input:not([type="radio"])', inputs => inputs.every(input => input.value === '')
    ));

    await setInput('#exercise-count', '20');
    const untouchedStipend = await page.evaluate(() => ({
      stipendInvalid: document.getElementById('stipend-per-exercise').getAttribute('aria-invalid'),
      creditsInvalid: document.getElementById('stipend-credits').getAttribute('aria-invalid'),
      stipendError: document.getElementById('stipend-per-exercise-error').textContent,
      creditsError: document.getElementById('stipend-credits-error').textContent
    }));
    assert('editing one field leaves untouched siblings quiet',
      untouchedStipend.stipendInvalid === null && untouchedStipend.creditsInvalid === null &&
      untouchedStipend.stipendError === '' && untouchedStipend.creditsError === '');
    await setInput('#stipend-per-exercise', '1500');
    await setInput('#stipend-credits', '5');
    const validStipend = await page.$eval('#stipend-result', output => output.textContent);
    assert('stipend inputs compute cost per credit', validStipend === '$6,000.00', validStipend);

    await setInput('#exercise-count', '101');
    const invalidState = await page.evaluate(() => ({
      result: document.getElementById('stipend-result').textContent,
      invalid: document.getElementById('exercise-count').getAttribute('aria-invalid'),
      error: document.getElementById('exercise-count-error').textContent
    }));
    assert('invalid input retains the last valid result', invalidState.result === validStipend,
      invalidState.result);
    assert('invalid input is announced accessibly', invalidState.invalid === 'true' &&
      invalidState.error.includes('last valid result'));

    await setInput('#standard-cost', '5000');
    await page.click('input[name="pay-model"][value="load"]');
    const switched = await page.evaluate(() => ({
      stipendHidden: document.getElementById('stipend-panel').hidden,
      loadHidden: document.getElementById('load-panel').hidden,
      comparator: document.getElementById('standard-cost').value,
      comparatorResult: document.getElementById('standard-result').textContent
    }));
    assert('load switch changes only the practicum panel', switched.stipendHidden &&
      !switched.loadHidden && switched.comparator === '5000' &&
      switched.comparatorResult === '$5,000.00');
    const untouchedLoad = await page.$$eval('#load-panel input', inputs => inputs.map(input => ({
      invalid: input.getAttribute('aria-invalid'),
      error: document.getElementById(input.id + '-error').textContent
    })));
    assert('switching models leaves untouched fields quiet',
      untouchedLoad.every(field => field.invalid === null && field.error === ''));

    await setInput('#annual-salary', '120000');
    await setInput('#annual-load-credits', '12');
    await setInput('#load-credits', '5');
    const loadResult = await page.$eval('#load-result', output => output.textContent);
    assert('load-credit inputs recompute without reload', loadResult === '$10,000.00', loadResult);

    const mobile = await page.evaluate(() => ({
      viewport: document.documentElement.clientWidth,
      page: document.documentElement.scrollWidth,
      tableScrollable: document.querySelector('.table-wrap').scrollWidth >
        document.querySelector('.table-wrap').clientWidth
    }));
    assert('390px layout contains wide tables locally', mobile.page <= mobile.viewport &&
      mobile.tableScrollable, `${mobile.page}px page / ${mobile.viewport}px viewport`);
    assert('calculator makes no network requests', requests.length === 0,
      requests.join(', '));
    assert('calculator emits no console errors', consoleErrors.length === 0,
      consoleErrors.join('; '));
  } finally {
    await browser.close();
  }

  console.log(`\n${failures.length ? 'FAIL' : 'PASS'}: ${checks - failures.length}/${checks} cost-page assertions`);
  process.exitCode = failures.length ? 1 : 0;
}

run().catch(error => {
  console.error('HARNESS ERROR', error);
  process.exitCode = 1;
});
