/*
Persona journey schema (tools/persona_journeys.json)
==================================================
Each journey has {id, story, persona, viewports, binding}. A binding is exactly
one of:
  steps   -> ordered steps in `steps`
  harness -> {command, story_checks}
  command -> {command, story_checks, local_target?, account_boundary?}

Step operations:
  goto {path}
  click|focus {selector|name}
  press {key}
  type {selector|name, text}
  waitFor {selector|text|url}
  expectDownload {pattern, selector?|name?, timeout_ms?}
  assert {kind, check, ...}

Assertion kinds: selector, text, attr, url, consoleClean, focusOn, a11yName,
a11yRole, a11yState, readingOrder, and liveRegion. Every assertion carries the
1-based acceptance-check index it proves. See docs/uat/journey-schema.md.
*/
'use strict';

const crypto = require('crypto');
const fs = require('fs');
const path = require('path');

const ROOT = path.resolve(__dirname, '..');
const DEFAULT_JOURNEYS = path.join(__dirname, 'persona_journeys.json');
const DEFAULT_RUN_DIR = path.join(ROOT, 'build', 'uat', 'runs');
const DEFAULT_SHOTS_DIR = path.join(ROOT, 'build', 'uat', 'shots');
const NAVIGATION_TIMEOUT = 30000;
const VIEWPORTS = {
  desktop: {width: 1280, height: 900, deviceScaleFactor: 1},
  phone: {width: 390, height: 844, deviceScaleFactor: 2, isMobile: true, hasTouch: true},
  zoom200: {width: 640, height: 450, deviceScaleFactor: 2},
};

class JourneyFailure extends Error {}
class InfrastructureFailure extends Error {}

function loadPuppeteer() {
  const candidates = [process.env.PUP_DIR, 'puppeteer', '/home/damienriehl/.npm/_npx/7d92d9a2d2ccc630/node_modules/puppeteer'].filter(Boolean);
  for (const candidate of candidates) { try { return require(candidate); } catch (_) {} }
  throw new Error('Puppeteer unavailable (set PUP_DIR or install puppeteer)');
}

function usage(message) {
  if (message) console.error(message);
  console.error('Usage: node tools/verify_persona_journeys.js --base <url> [--only id,id] --env-label <label> [--run-dir path] [--shots-dir path]');
  return 2;
}

function parseArgs(argv) {
  const result = {runDir: DEFAULT_RUN_DIR, shotsDir: DEFAULT_SHOTS_DIR, journeys: DEFAULT_JOURNEYS, only: null};
  for (let i = 0; i < argv.length; i++) {
    const flag = argv[i];
    if (!['--base', '--only', '--env-label', '--run-dir', '--shots-dir', '--journeys'].includes(flag)) throw new Error(`unknown argument: ${flag}`);
    if (!argv[i + 1]) throw new Error(`${flag} requires a value`);
    const value = argv[++i];
    if (flag === '--base') result.base = value;
    if (flag === '--only') result.only = new Set(value.split(',').map((item) => item.trim()).filter(Boolean));
    if (flag === '--env-label') result.env = value;
    if (flag === '--run-dir') result.runDir = path.resolve(value);
    if (flag === '--shots-dir') result.shotsDir = path.resolve(value);
    if (flag === '--journeys') result.journeys = path.resolve(value);
  }
  if (!result.base || !result.env) throw new Error('--base and --env-label are required');
  result.base = new URL(result.base).href;
  return result;
}

function loadCatalog(source) {
  const parsed = JSON.parse(fs.readFileSync(source, 'utf8'));
  const journeys = Array.isArray(parsed) ? parsed : parsed.journeys;
  if (!Array.isArray(journeys)) throw new Error(`${source} must contain a journeys array`);
  return journeys;
}

function utcStamp(date = new Date()) {
  return date.toISOString().replace(/[-:]/g, '').replace('.', '');
}

function safeName(value) {
  return String(value).replace(/[^A-Za-z0-9._-]+/g, '-').replace(/^-+|-+$/g, '') || 'journey';
}

function sha256File(source) {
  return crypto.createHash('sha256').update(fs.readFileSync(source)).digest('hex');
}

function relativeArtifact(source) {
  const relative = path.relative(ROOT, source);
  return relative.startsWith('..') ? source : relative.replaceAll(path.sep, '/');
}

function launchBrowser(puppeteer) {
  return puppeteer.launch({
    executablePath: process.env.CHROME_BIN || process.env.CHROMIUM_PATH || '/snap/bin/chromium',
    headless: process.env.HEADFUL !== '1' && process.env.HEADLESS !== '0',
    userDataDir: path.join('/tmp', `sonsteng-persona-uat-${process.pid}-${Date.now()}`),
    args: ['--no-sandbox', '--disable-dev-shm-usage', '--disable-crash-reporter', '--disable-breakpad'],
  });
}

function targetUrl(base, targetPath) {
  if (new URL(base).protocol === 'file:') return new URL(String(targetPath).replace(/^\//, ''), base).href;
  return new URL(targetPath, base).href;
}

function errorText(error) {
  return error && error.message ? error.message : String(error);
}

function isInfrastructureError(error) {
  if (error instanceof InfrastructureFailure) return true;
  const message = errorText(error);
  return /net::ERR_(?:NAME_NOT_RESOLVED|CONNECTION_REFUSED|CONNECTION_TIMED_OUT|ADDRESS_UNREACHABLE|INTERNET_DISCONNECTED|CERT_|SSL_)|Navigation timeout|Target closed|Session closed|Protocol error|browser.*disconnected|socket hang up/i.test(message);
}

function stepFailure(step, detail) {
  const check = step.check === undefined ? '' : ` (check ${step.check})`;
  throw new JourneyFailure(`${step.op}${step.kind ? ` ${step.kind}` : ''}${check}: ${detail}`);
}

async function elementByName(page, name) {
  const handles = await page.$$('a[href],button,input,textarea,select,summary,[role="button"],[tabindex]');
  for (const handle of handles) {
    const matched = await handle.evaluate((element, expected) => {
      const labelled = element.getAttribute('aria-labelledby');
      const labelledText = labelled ? labelled.split(/\s+/).map((id) => document.getElementById(id)?.textContent || '').join(' ') : '';
      const label = element.getAttribute('aria-label') || labelledText || element.getAttribute('title') || element.getAttribute('alt') || element.innerText || element.value || '';
      return label.trim() === expected || label.trim().includes(expected);
    }, name);
    if (matched) return handle;
    await handle.dispose();
  }
  return null;
}

async function resolveElement(page, step) {
  if (step.selector) return page.$(step.selector);
  if (step.name) return elementByName(page, step.name);
  return null;
}

async function requireElement(page, step) {
  const handle = await resolveElement(page, step);
  if (!handle) stepFailure(step, `control not found (${step.selector || step.name || 'no selector or name'})`);
  return handle;
}

async function waitForText(page, text, timeout) {
  await page.waitForFunction(
    (expected) => [...document.querySelectorAll('body *')].some((element) => {
      const style = getComputedStyle(element);
      const box = element.getBoundingClientRect();
      return style.display !== 'none' && style.visibility !== 'hidden' && box.width > 0 && box.height > 0 && (element.innerText || '').includes(expected);
    }),
    {timeout},
    text,
  );
}

function globRegex(pattern) {
  const escaped = String(pattern).replace(/[.+^${}()|[\]\\]/g, '\\$&').replaceAll('*', '.*').replaceAll('?', '.');
  return new RegExp(`^${escaped}$`);
}

async function waitForDownload(downloadDir, pattern, timeout) {
  const matcher = globRegex(pattern);
  const deadline = Date.now() + timeout;
  while (Date.now() < deadline) {
    const found = fs.readdirSync(downloadDir).find((name) => matcher.test(name) && !name.endsWith('.crdownload'));
    if (found) {
      const source = path.join(downloadDir, found);
      return {filename: found, size: fs.statSync(source).size};
    }
    await new Promise((resolve) => setTimeout(resolve, 100));
  }
  throw new JourneyFailure(`expectDownload: no file matching ${pattern} within ${timeout}ms`);
}

async function accessibilityNode(page, step) {
  const handle = await requireElement(page, step);
  try {
    const node = await page.accessibility.snapshot({root: handle, interestingOnly: false});
    if (!node) stepFailure(step, 'control is absent from the accessibility tree');
    return node;
  } finally {
    await handle.dispose();
  }
}

async function runAssertion(page, step, state) {
  if (!Number.isInteger(step.check) || step.check < 1) stepFailure(step, 'assertion has no positive integer check index');
  if (step.kind === 'selector') {
    const handle = await page.$(step.selector);
    if (!handle) stepFailure(step, `selector absent: ${step.selector}`);
    if (step.count !== undefined) {
      const count = await page.$$eval(step.selector, (elements) => elements.length);
      if (count !== step.count) stepFailure(step, `selector count was ${count}, expected ${step.count}: ${step.selector}`);
    }
    if (step.visible) {
      const visible = await handle.evaluate((element) => { const style = getComputedStyle(element); const box = element.getBoundingClientRect(); return style.display !== 'none' && style.visibility !== 'hidden' && box.width > 0 && box.height > 0; });
      if (!visible) stepFailure(step, `selector is not visible: ${step.selector}`);
    }
    if (step.withinViewport) {
      const inside = await handle.evaluate((element) => { const box = element.getBoundingClientRect(); return box.left >= 0 && box.top >= 0 && box.right <= innerWidth && box.bottom <= innerHeight; });
      if (!inside) stepFailure(step, `selector is outside the viewport: ${step.selector}`);
    }
    if (step.noHorizontalOverflow) {
      const overflow = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
      if (overflow > 1) stepFailure(step, `horizontal overflow is ${overflow}px`);
    }
    await handle.dispose();
    return;
  }
  if (step.kind === 'text') {
    const text = step.text;
    const found = await page.evaluate(({selector, expected}) => {
      const elements = selector ? [...document.querySelectorAll(selector)] : [document.body];
      return elements.some((element) => {
        const style = getComputedStyle(element); const box = element.getBoundingClientRect();
        return style.display !== 'none' && style.visibility !== 'hidden' && box.width > 0 && box.height > 0 && (element.innerText || '').includes(expected);
      });
    }, {selector: step.selector, expected: text});
    if (!found) stepFailure(step, `visible text absent${step.selector ? ` in ${step.selector}` : ''}: ${text}`);
    return;
  }
  if (step.kind === 'attr') {
    const handle = await requireElement(page, step);
    const actual = await handle.evaluate((element, attribute) => element.getAttribute(attribute), step.attr);
    await handle.dispose();
    const expected = step.value === null ? null : String(step.value);
    const ok = step.includes === undefined ? actual === expected : String(actual || '').includes(String(step.includes));
    if (!ok) stepFailure(step, `${step.selector || step.name} ${step.attr} was ${JSON.stringify(actual)}`);
    return;
  }
  if (step.kind === 'url') {
    const actual = page.url();
    const expected = step.url || step.value;
    const ok = step.exact ? actual === targetUrl(state.base, expected) : actual.includes(expected);
    if (!ok) stepFailure(step, `URL ${actual} did not match ${expected}`);
    return;
  }
  if (step.kind === 'consoleClean') {
    if (state.consoleErrors.length) stepFailure(step, state.consoleErrors[0]);
    return;
  }
  if (step.kind === 'focusOn') {
    const handle = await requireElement(page, step);
    const focused = await page.evaluate((element) => document.activeElement === element, handle);
    await handle.dispose();
    if (!focused) stepFailure(step, `focus is not on ${step.selector || step.name}`);
    return;
  }
  if (step.kind === 'a11yName' || step.kind === 'a11yRole' || step.kind === 'a11yState') {
    const node = await accessibilityNode(page, step);
    if (step.kind === 'a11yName' && node.name !== step.expected) stepFailure(step, `accessible name was ${JSON.stringify(node.name)}, expected ${JSON.stringify(step.expected)}`);
    if (step.kind === 'a11yRole' && node.role !== step.expected) stepFailure(step, `accessible role was ${JSON.stringify(node.role)}, expected ${JSON.stringify(step.expected)}`);
    if (step.kind === 'a11yState') {
      for (const [name, expected] of Object.entries(step.state || {})) if (node[name] !== expected) stepFailure(step, `accessibility state ${name} was ${JSON.stringify(node[name])}, expected ${JSON.stringify(expected)}`);
    }
    return;
  }
  if (step.kind === 'readingOrder') {
    const selectors = step.selectors || [];
    const ordered = await page.evaluate((items) => items.map((selector) => document.querySelector(selector)).every((element, index, all) => element && (index === 0 || (all[index - 1].compareDocumentPosition(element) & Node.DOCUMENT_POSITION_FOLLOWING))), selectors);
    if (!ordered) stepFailure(step, `selectors are not in reading order: ${selectors.join(', ')}`);
    return;
  }
  if (step.kind === 'liveRegion') {
    const selector = step.selector || '[aria-live]';
    const actual = await page.$$eval(selector, (elements) => elements.map((element) => element.innerText || '').join(' '));
    if (!actual.includes(step.text)) stepFailure(step, `live region lacks text: ${step.text}`);
    return;
  }
  stepFailure(step, `unknown assertion kind: ${step.kind}`);
}

async function performStep(page, step, state) {
  const timeout = step.timeout_ms || NAVIGATION_TIMEOUT;
  if (step.op === 'goto') {
    let response;
    try { response = await page.goto(targetUrl(state.base, step.path), {waitUntil: 'networkidle2', timeout}); }
    catch (error) { if (isInfrastructureError(error)) throw new InfrastructureFailure(errorText(error)); throw error; }
    if (!response) throw new InfrastructureFailure(`navigation returned no response for ${step.path}`);
    if (response.status() >= 400) stepFailure(step, `${response.status()} ${response.statusText()} for ${step.path}`);
    return;
  }
  if (step.op === 'click' || step.op === 'focus') {
    const handle = await requireElement(page, step);
    if (step.op === 'click') await handle.click(); else await handle.focus();
    await handle.dispose();
    return;
  }
  if (step.op === 'press') { await page.keyboard.press(step.key); return; }
  if (step.op === 'type') {
    const handle = await requireElement(page, step);
    await handle.type(step.text || '');
    await handle.dispose();
    return;
  }
  if (step.op === 'waitFor') {
    if (step.selector) await page.waitForSelector(step.selector, {visible: step.visible !== false, timeout});
    else if (step.text) await waitForText(page, step.text, timeout);
    else if (step.url) await page.waitForFunction((expected) => location.href.includes(expected), {timeout}, step.url);
    else stepFailure(step, 'waitFor needs selector, text, or url');
    return;
  }
  if (step.op === 'expectDownload') {
    if (step.selector || step.name) {
      const handle = await requireElement(page, step);
      await handle.click(); await handle.dispose();
    }
    state.downloads.push(await waitForDownload(state.downloadDir, step.pattern, timeout));
    return;
  }
  if (step.op === 'assert') { await runAssertion(page, step, state); return; }
  stepFailure(step, `unknown operation: ${step.op}`);
}

async function capture(page, shotPath) {
  fs.mkdirSync(path.dirname(shotPath), {recursive: true});
  await page.screenshot({path: shotPath, fullPage: true});
  return sha256File(shotPath);
}

async function runSteps(browser, journey, viewportName, context) {
  const started = Date.now();
  const browserContext = await browser.createBrowserContext();
  const page = await browserContext.newPage();
  const downloadDir = path.join('/tmp', `sonsteng-uat-download-${process.pid}-${safeName(journey.id)}-${safeName(viewportName)}-${context.retry}`);
  fs.mkdirSync(downloadDir, {recursive: true});
  const state = {base: context.base, consoleErrors: [], downloads: [], downloadDir};
  page.on('console', (message) => { if (message.type() === 'error') state.consoleErrors.push(`console: ${message.text()}`); });
  page.on('pageerror', (error) => state.consoleErrors.push(`pageerror: ${errorText(error)}`));
  await page.setViewport(VIEWPORTS[viewportName]);
  const client = await page.target().createCDPSession();
  await client.send('Page.setDownloadBehavior', {behavior: 'allow', downloadPath: downloadDir});
  const shotPath = path.join(context.shotRoot, `${safeName(journey.id)}-${safeName(viewportName)}-${context.retry}.png`);
  let verdict = 'PASS'; let firstFailure = null; let digest = null;
  try {
    for (const step of journey.steps) await performStep(page, step, state);
  } catch (error) {
    verdict = isInfrastructureError(error) ? 'ERROR' : 'FAIL';
    firstFailure = errorText(error);
  }
  try {
    digest = await capture(page, shotPath);
  } catch (error) {
    verdict = 'ERROR';
    firstFailure = firstFailure || `screenshot evidence failed: ${errorText(error)}`;
  }
  const retained = verdict === 'PASS' || !fs.existsSync(shotPath) ? null : relativeArtifact(shotPath);
  if (verdict === 'PASS' && fs.existsSync(shotPath)) fs.unlinkSync(shotPath);
  await page.close().catch(() => {});
  await browserContext.close().catch(() => {});
  fs.rmSync(downloadDir, {recursive: true, force: true});
  return {
    journey: journey.id, story: journey.story, persona: journey.persona, viewport: viewportName,
    verdict, first_failure: firstFailure, digest, ...(retained ? {shot_path: retained} : {}),
    duration_ms: Date.now() - started, canary: Boolean(journey.canary), retry: context.retry,
    ...(state.downloads.length ? {downloads: state.downloads} : {}),
  };
}

async function fetchBuild(browser, base) {
  const result = {spine_build_id: null, git_base_sha: null, release_sha: null};
  const page = await browser.newPage();
  try {
    try {
      const response = await page.goto(targetUrl(base, '/platform/data/.build-stamp.json'), {waitUntil: 'domcontentloaded', timeout: 15000});
      if (response && response.ok()) {
        const parsed = JSON.parse(await page.evaluate(() => document.body.innerText));
        result.spine_build_id = parsed.spine_build_id || null;
        result.git_base_sha = parsed.git_base_sha || null;
      }
    } catch (_) {}
    try {
      const response = await page.goto(targetUrl(base, '/platform/'), {waitUntil: 'domcontentloaded', timeout: 15000});
      if (response) result.release_sha = response.headers()['x-release-sha'] || null;
    } catch (_) {}
  } finally { await page.close().catch(() => {}); }
  return result;
}

function bindingAttempt(journey) {
  const binding = journey[journey.binding] || {};
  return {
    journey: journey.id, story: journey.story, persona: journey.persona, viewport: 'n/a', verdict: 'NOT RUN',
    first_failure: `${journey.binding} binding: ${binding.command || 'no command recorded'}`,
    digest: null, duration_ms: 0, canary: Boolean(journey.canary), retry: 0,
    artifact: binding.command || null,
  };
}

async function main(argv) {
  let options;
  try { options = parseArgs(argv); } catch (error) { return usage(errorText(error)); }
  const journeys = loadCatalog(options.journeys);
  const selected = options.only ? journeys.filter((item) => options.only.has(item.id)) : journeys;
  if (options.only) {
    const found = new Set(selected.map((item) => item.id));
    const missing = [...options.only].filter((id) => !found.has(id));
    if (missing.length) return usage(`unknown journey id(s): ${missing.join(', ')}`);
  }
  fs.mkdirSync(options.runDir, {recursive: true});
  fs.mkdirSync(options.shotsDir, {recursive: true});
  const started = new Date(); const stamp = utcStamp(started); const runId = `${stamp}-${safeName(options.env)}`;
  const shotRoot = path.join(options.shotsDir, runId); fs.mkdirSync(shotRoot, {recursive: true});
  const puppeteer = loadPuppeteer(); let browser = null; const launchErrors = [];
  for (let retry = 0; retry < 2 && !browser; retry++) {
    try { browser = await launchBrowser(puppeteer); }
    catch (error) { launchErrors.push(`browser launch: ${errorText(error)}`); }
  }
  const build = browser ? await fetchBuild(browser, options.base) : {spine_build_id: null, git_base_sha: null, release_sha: null};
  const attempts = []; const finalByKey = new Map();
  try {
    for (const journey of selected) {
      if (journey.binding !== 'steps') {
        const attempt = bindingAttempt(journey); attempts.push(attempt); finalByKey.set(`${journey.id}|n/a`, attempt);
        console.log(`NOT RUN ${journey.id} n/a — ${attempt.first_failure}`); continue;
      }
      for (const viewport of journey.viewports) {
        if (!browser) {
          for (let retry = 0; retry < launchErrors.length; retry++) {
            const attempt = {
              journey: journey.id, story: journey.story, persona: journey.persona, viewport,
              verdict: 'ERROR', first_failure: launchErrors[retry], digest: null,
              duration_ms: 0, canary: Boolean(journey.canary), retry,
            };
            attempts.push(attempt); finalByKey.set(`${journey.id}|${viewport}`, attempt);
            console.log(`ERROR ${journey.id} ${viewport} — ${attempt.first_failure}`);
          }
          continue;
        }
        let final;
        for (let retry = 0; retry < 2; retry++) {
          let attempt;
          try {
            if (!browser || !browser.isConnected()) browser = await launchBrowser(puppeteer);
            attempt = await runSteps(browser, journey, viewport, {base: options.base, shotRoot, retry});
          } catch (error) {
            attempt = {
              journey: journey.id, story: journey.story, persona: journey.persona, viewport,
              verdict: 'ERROR', first_failure: errorText(error), digest: null,
              duration_ms: 0, canary: Boolean(journey.canary), retry,
            };
            if (browser && !browser.isConnected()) browser = null;
          }
          attempts.push(attempt); final = attempt;
          console.log(`${attempt.verdict} ${journey.id} ${viewport}${attempt.first_failure ? ` — ${attempt.first_failure}` : ''}`);
          if (attempt.verdict !== 'ERROR') break;
        }
        finalByKey.set(`${journey.id}|${viewport}`, final);
      }
    }
  } finally { if (browser) await browser.close().catch(() => {}); }

  if (fs.existsSync(shotRoot) && fs.readdirSync(shotRoot).length === 0) fs.rmdirSync(shotRoot);
  const run = {run_id: runId, env: options.env, base: options.base, started: started.toISOString(), build, attempts};
  const runPath = path.join(options.runDir, `${runId}.json`);
  fs.writeFileSync(runPath, JSON.stringify(run, null, 2) + '\n');
  let failures = 0;
  for (const attempt of finalByKey.values()) {
    if (attempt.canary) { if (attempt.verdict !== 'FAIL') failures++; }
    else if (attempt.verdict === 'FAIL' || attempt.verdict === 'ERROR') failures++;
  }
  console.log(`RUN FILE ${relativeArtifact(runPath)}`);
  console.log(`JOURNEY SUMMARY ${finalByKey.size - failures}/${finalByKey.size} acceptable (${attempts.length} attempt record${attempts.length === 1 ? '' : 's'})`);
  return failures ? 1 : 0;
}

main(process.argv.slice(2)).then((code) => process.exit(code)).catch((error) => { console.error('PERSONA JOURNEY ERROR:', errorText(error)); process.exit(2); });
