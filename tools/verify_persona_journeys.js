/*
Persona journey schema (tools/persona_journeys.json)
==================================================
Each journey has {id, story, persona, viewports, binding}. A binding is exactly
one of:
  steps   -> ordered steps in `steps`
  harness -> {command, story_checks, environments?, credential_gate?}
  command -> {command, story_checks, local_target?, account_boundary?,
              environments?, credential_gate?}

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

Name-based controls compare aria-label (or textContent when absent) after
collapsing whitespace, case-insensitively. Exact names rank above substring
matches; visibility ranks within each tier, and the shortest substring match is
preferred. Non-interactive tabindex=-1 containers are excluded. When only
hidden matches exist the step reports "control not visible".
*/
'use strict';

const crypto = require('crypto');
const {spawn} = require('child_process');
const fs = require('fs');
const path = require('path');

const ROOT = path.resolve(__dirname, '..');
const DEFAULT_JOURNEYS = path.join(__dirname, 'persona_journeys.json');
const DEFAULT_RUN_DIR = path.join(ROOT, 'build', 'uat', 'runs');
const DEFAULT_SHOTS_DIR = path.join(ROOT, 'build', 'uat', 'shots');
const NAVIGATION_TIMEOUT = 30000;
const ASSERTION_VISIBILITY_TIMEOUT = 2500;
const ASSERTION_POLL_INTERVAL = 50;
const DEFAULT_BINDING_TIMEOUT = 1800000;
const BINDING_EXECUTABLES = new Set(['node', 'python3', 'python', 'npx', 'bash', 'sh', 'cd', 'git', 'pytest', 'curl']);
const NATIVE_INTERACTIVE_SELECTOR = 'a[href],button,input,textarea,select,summary,[role="button"]';
const UAT_WORKSPACE_KINDS = new Set(['downloads', 'profiles']);
const WORKER_URLS = {
  dev: 'https://sonsteng-chat.damienriehl.workers.dev',
  prod: 'https://sonsteng-chat-production.damienriehl.workers.dev',
};
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
  console.error('Usage: node tools/verify_persona_journeys.js (--base <url> | --bindings) [--only id,id] --env-label <label> [--binding-timeout ms] [--run-dir path] [--shots-dir path]');
  return 2;
}

function parseArgs(argv) {
  const result = {
    runDir: DEFAULT_RUN_DIR,
    shotsDir: DEFAULT_SHOTS_DIR,
    journeys: DEFAULT_JOURNEYS,
    only: null,
    bindings: false,
    bindingTimeout: DEFAULT_BINDING_TIMEOUT,
    bindingTimeoutProvided: false,
  };
  for (let i = 0; i < argv.length; i++) {
    const flag = argv[i];
    if (flag === '--bindings') { result.bindings = true; continue; }
    if (!['--base', '--only', '--env-label', '--run-dir', '--shots-dir', '--journeys', '--binding-timeout'].includes(flag)) throw new Error(`unknown argument: ${flag}`);
    if (!argv[i + 1]) throw new Error(`${flag} requires a value`);
    const value = argv[++i];
    if (flag === '--base') result.base = value;
    if (flag === '--only') result.only = new Set(value.split(',').map((item) => item.trim()).filter(Boolean));
    if (flag === '--env-label') result.env = value;
    if (flag === '--run-dir') result.runDir = path.resolve(value);
    if (flag === '--shots-dir') result.shotsDir = path.resolve(value);
    if (flag === '--journeys') result.journeys = path.resolve(value);
    if (flag === '--binding-timeout') {
      result.bindingTimeout = Number(value);
      result.bindingTimeoutProvided = true;
      if (!Number.isSafeInteger(result.bindingTimeout) || result.bindingTimeout <= 0) throw new Error('--binding-timeout must be a positive integer');
    }
  }
  if (!result.env) throw new Error('--env-label is required');
  if (result.bindings && result.base) throw new Error('--bindings and --base are mutually exclusive');
  if (!result.bindings && !result.base) throw new Error('--base is required unless --bindings is used');
  if (!result.bindings && result.bindingTimeoutProvided) throw new Error('--binding-timeout requires --bindings');
  if (result.base) result.base = new URL(result.base).href;
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

function uatWorkspacePath(kind, ...components) {
  if (!UAT_WORKSPACE_KINDS.has(kind)) throw new Error(`unsupported UAT workspace kind: ${kind}`);
  const safeComponents = components.map(safeName);
  if (safeComponents.some((component) => component === '.' || component === '..')) {
    throw new Error('unsafe UAT workspace component');
  }
  return path.join(ROOT, 'build', 'uat', kind, ...safeComponents);
}

function sha256File(source) {
  return crypto.createHash('sha256').update(fs.readFileSync(source)).digest('hex');
}

function relativeArtifact(source) {
  const relative = path.relative(ROOT, source);
  return relative.startsWith('..') ? source : relative.replaceAll(path.sep, '/');
}

async function launchBrowser(puppeteer, userDataDir) {
  fs.mkdirSync(userDataDir, {recursive: true});
  try {
    return await puppeteer.launch({
      executablePath: process.env.CHROME_BIN || process.env.CHROMIUM_PATH || '/snap/bin/chromium',
      headless: process.env.HEADFUL !== '1' && process.env.HEADLESS !== '0',
      userDataDir,
      args: ['--no-sandbox', '--disable-dev-shm-usage', '--disable-crash-reporter', '--disable-breakpad'],
    });
  } catch (error) {
    fs.rmSync(userDataDir, {recursive: true, force: true});
    throw error;
  }
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

function collapseWhitespace(value) {
  return String(value || '').replace(/\s+/g, ' ').trim();
}

function normalizeControlName(value) {
  return collapseWhitespace(value).toLowerCase();
}

function controlNameMatches(actual, expected) {
  const candidate = normalizeControlName(actual);
  const requested = normalizeControlName(expected);
  return Boolean(requested) && (candidate === requested || candidate.includes(requested));
}

function navigationIsReady(expected, actualUrl, readyState) {
  const href = actualUrl === undefined ? globalThis.location.href : actualUrl;
  const documentState = readyState === undefined ? globalThis.document.readyState : readyState;
  return href.includes(expected) && documentState === 'complete';
}

function attributeMatches(actual, expectedValue, expectedIncludes) {
  if (expectedIncludes !== undefined) return String(actual || '').includes(String(expectedIncludes));
  const expected = expectedValue === null ? null : String(expectedValue);
  return actual === expected;
}

function compareControlRanks(left, right) {
  if (left.exact !== right.exact) return Number(left.exact) - Number(right.exact);
  if (left.visible !== right.visible) return Number(left.visible) - Number(right.visible);
  return right.nameLength - left.nameLength;
}

function selectControlCandidate(candidates, expected) {
  const requested = normalizeControlName(expected);
  if (!requested) return null;
  let selected = null;
  let selectedRank = null;
  for (const candidate of candidates) {
    if (candidate.tabIndex === -1 && candidate.interactive === false) continue;
    const normalizedName = normalizeControlName(candidate.name);
    const exact = normalizedName === requested;
    if (!exact && !normalizedName.includes(requested)) continue;
    const rank = {exact, visible: Boolean(candidate.visible), nameLength: normalizedName.length};
    if (!selectedRank || compareControlRanks(rank, selectedRank) > 0) {
      selected = candidate;
      selectedRank = rank;
    }
  }
  return selected;
}

function elementIsVisible(element, expected = true) {
  if (!element || !element.isConnected) return false;
  const visible = typeof element.checkVisibility === 'function'
    ? element.checkVisibility({checkOpacity: true, checkVisibilityCSS: true})
    : (() => { const style = getComputedStyle(element); const box = element.getBoundingClientRect(); return style.display !== 'none' && style.visibility !== 'hidden' && style.opacity !== '0' && box.width > 0 && box.height > 0; })();
  return visible === expected;
}

async function elementByName(page, name) {
  const handles = await page.$$(`${NATIVE_INTERACTIVE_SELECTOR},[tabindex]`);
  const candidates = [];
  for (let index = 0; index < handles.length; index++) {
    const handle = handles[index];
    const candidate = await handle.evaluate((element, candidateIndex, nativeSelector) => ({
      index: candidateIndex,
      name: element.getAttribute('aria-label') || element.textContent || '',
      interactive: element.matches(nativeSelector),
      tabIndex: element.tabIndex,
    }), index, NATIVE_INTERACTIVE_SELECTOR);
    candidate.visible = await handle.evaluate(elementIsVisible);
    candidates.push(candidate);
  }
  const selected = selectControlCandidate(candidates, name);
  for (let index = 0; index < handles.length; index++) {
    if (!selected || index !== selected.index) await handles[index].dispose();
  }
  return selected ? {handle: handles[selected.index], visible: selected.visible} : {handle: null, visible: false};
}

async function resolveElement(page, step) {
  if (step.selector) return {handle: await page.$(step.selector), visible: null};
  if (step.name) return elementByName(page, step.name);
  return {handle: null, visible: false};
}

async function requireElement(page, step, {allowHidden = false, timeout = 0} = {}) {
  const deadline = Date.now() + timeout;
  let sawHidden = false;
  while (true) {
    const resolved = await resolveElement(page, step);
    if (resolved.handle) {
      if (!step.name || resolved.visible || allowHidden) return resolved.handle;
      sawHidden = true;
      await resolved.handle.dispose();
    }
    const remaining = deadline - Date.now();
    if (remaining <= 0) break;
    await new Promise((resolve) => setTimeout(resolve, Math.min(ASSERTION_POLL_INTERVAL, remaining)));
  }
  if (sawHidden) stepFailure(step, `control not visible (${step.name})`);
  stepFailure(step, `control not found (${step.selector || step.name || 'no selector or name'})`);
}

async function waitForElementVisibility(page, handle, expected = true, timeout = ASSERTION_VISIBILITY_TIMEOUT) {
  await handle.evaluate((element) => {
    if (element.isConnected) element.scrollIntoView({block: 'center'});
  });
  try {
    await page.waitForFunction(
      elementIsVisible,
      {timeout, polling: 50},
      handle,
      expected,
    );
    return true;
  } catch (_) {
    return false;
  }
}

async function waitForVisibleText(page, selector, text, timeout) {
  const expected = collapseWhitespace(text);
  const search = {selector: selector || null, expected};
  await page.evaluate(({selector: targetSelector, expected: targetText}) => {
    const collapse = (value) => String(value || '').replace(/\s+/g, ' ').trim();
    const elements = targetSelector
      ? [...document.querySelectorAll(targetSelector)]
      : [...document.querySelectorAll('body *')];
    const matching = elements.filter((element) => collapse(element.textContent).includes(targetText));
    const target = matching.find((element) => ![...element.children].some((child) => collapse(child.textContent).includes(targetText))) || matching[0] || elements[0];
    if (target && target.isConnected) target.scrollIntoView({block: 'center'});
  }, search);
  try {
    await page.waitForFunction(
      ({selector: targetSelector, expected: targetText}) => {
        const collapse = (value) => String(value || '').replace(/\s+/g, ' ').trim();
        const elements = targetSelector
          ? [...document.querySelectorAll(targetSelector)]
          : [...document.querySelectorAll('body *')];
        return elements.some((element) => {
          if (!element.isConnected) return false;
          if (!collapse(element.textContent).includes(targetText)) return false;
          const box = element.getBoundingClientRect();
          if (box.width <= 0 || box.height <= 0) return false;
          const visible = typeof element.checkVisibility === 'function'
            ? element.checkVisibility({checkOpacity: true, checkVisibilityCSS: true})
            : (() => { const style = getComputedStyle(element); return style.display !== 'none' && style.visibility !== 'hidden' && style.opacity !== '0'; })();
          return visible;
        });
      },
      {timeout, polling: 50},
      search,
    );
    return true;
  } catch (_) {
    return false;
  }
}

async function waitForText(page, text, timeout) {
  return waitForVisibleText(page, null, text, timeout);
}

async function waitForAttribute(handle, attribute, expectedValue, expectedIncludes, timeout) {
  const deadline = Date.now() + timeout;
  let actual = null;
  while (true) {
    actual = await handle.evaluate((element, name) => element.getAttribute(name), attribute);
    if (attributeMatches(actual, expectedValue, expectedIncludes)) return {matched: true, actual};
    const remaining = deadline - Date.now();
    if (remaining <= 0) return {matched: false, actual};
    await new Promise((resolve) => setTimeout(resolve, Math.min(ASSERTION_POLL_INTERVAL, remaining)));
  }
}

function liveRegionTextMatches(values, expected) {
  const actual = collapseWhitespace(Array.isArray(values) ? values.join(' ') : values);
  const requested = collapseWhitespace(expected);
  return Boolean(requested) && actual.includes(requested);
}

function globRegex(pattern) {
  const escaped = String(pattern).replace(/[.+^${}()|[\]\\]/g, '\\$&').replaceAll('*', '.*').replaceAll('?', '.');
  return new RegExp(`^${escaped}$`);
}

function filenameMatches(pattern, filename) {
  return globRegex(pattern).test(String(filename));
}

async function waitForDownloadDirectory(downloadDir, pattern, timeout) {
  const deadline = Date.now() + timeout;
  while (Date.now() < deadline) {
    const found = fs.readdirSync(downloadDir).find((name) => filenameMatches(pattern, name) && !name.endsWith('.crdownload'));
    if (found) {
      const source = path.join(downloadDir, found);
      return {filename: found, size: fs.statSync(source).size};
    }
    await new Promise((resolve) => setTimeout(resolve, 100));
  }
  throw new JourneyFailure(`expectDownload: no file matching ${pattern} within ${timeout}ms`);
}

async function configureDownloads(page, downloadDir) {
  const client = typeof page.createCDPSession === 'function'
    ? await page.createCDPSession()
    : await page.target().createCDPSession();
  try {
    const target = await client.send('Target.getTargetInfo').catch(() => null);
    await client.send('Browser.setDownloadBehavior', {
      behavior: 'allowAndName',
      downloadPath: downloadDir,
      eventsEnabled: true,
      ...(target && target.targetInfo.browserContextId ? {browserContextId: target.targetInfo.browserContextId} : {}),
    });
    return {client, eventsEnabled: true};
  } catch (browserError) {
    try {
      await client.send('Page.setDownloadBehavior', {behavior: 'allow', downloadPath: downloadDir});
      return {client, eventsEnabled: false};
    } catch (pageError) {
      throw new InfrastructureFailure(`download setup failed: ${errorText(browserError)}; fallback failed: ${errorText(pageError)}`);
    }
  }
}

function createDownloadEventWaiter(client, downloadDir, pattern, timeout) {
  const matching = new Map();
  const completed = new Set();
  let settled = false;
  let timer;
  let diskPoll;
  let resolvePromise;
  let rejectPromise;

  const cleanup = () => {
    clearTimeout(timer);
    clearInterval(diskPoll);
    client.off('Browser.downloadWillBegin', onBegin);
    client.off('Browser.downloadProgress', onProgress);
  };
  const complete = (value) => {
    if (settled) return;
    settled = true;
    cleanup();
    resolvePromise(value);
  };
  const fail = (error) => {
    if (settled) return;
    settled = true;
    cleanup();
    rejectPromise(error);
  };
  const resultOnDisk = (guid, filename) => {
    for (const storedName of [guid, filename]) {
      const source = path.join(downloadDir, storedName);
      try {
        if (storedName.endsWith('.crdownload')) continue;
        return {filename, size: fs.statSync(source).size};
      } catch (error) {
        if (error.code !== 'ENOENT') throw error;
      }
    }
    return null;
  };
  function onBegin(event) {
    if (filenameMatches(pattern, event.suggestedFilename)) matching.set(event.guid, event.suggestedFilename);
  }
  function onProgress(event) {
    if (event.state === 'completed') completed.add(event.guid);
    const filename = matching.get(event.guid);
    if (!filename) return;
    if (event.state === 'canceled') {
      fail(new JourneyFailure(`expectDownload: download canceled for ${filename}`));
      return;
    }
    if (event.state === 'completed') {
      const result = resultOnDisk(event.guid, filename);
      if (result) complete(result);
    }
  }

  const promise = new Promise((resolve, reject) => {
    resolvePromise = resolve;
    rejectPromise = reject;
    client.on('Browser.downloadWillBegin', onBegin);
    client.on('Browser.downloadProgress', onProgress);
    diskPoll = setInterval(() => {
      for (const [guid, filename] of matching) {
        if (!completed.has(guid)) continue;
        const result = resultOnDisk(guid, filename);
        if (result) { complete(result); break; }
      }
    }, 100);
    timer = setTimeout(() => fail(new JourneyFailure(`expectDownload: no completed download matching ${pattern} within ${timeout}ms`)), timeout);
  });
  return {promise, cancel: () => complete(null)};
}

async function accessibilityNode(page, step) {
  const handle = await requireElement(page, step, {allowHidden: true});
  try {
    if (!await waitForElementVisibility(page, handle)) stepFailure(step, `control not visible (${step.selector || step.name})`);
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
    if (step.visible !== undefined) {
      await waitForElementVisibility(page, handle, step.visible);
      const visible = await handle.evaluate(elementIsVisible);
      if (visible !== step.visible) stepFailure(step, `selector is ${visible ? 'visible' : 'not visible'}, expected ${step.visible ? 'visible' : 'not visible'}: ${step.selector}`);
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
    const found = await waitForVisibleText(page, step.selector, text, ASSERTION_VISIBILITY_TIMEOUT);
    if (!found) stepFailure(step, `visible text absent${step.selector ? ` in ${step.selector}` : ''}: ${text}`);
    return;
  }
  if (step.kind === 'attr') {
    const handle = await requireElement(page, step);
    let result;
    try {
      result = await waitForAttribute(
        handle,
        step.attr,
        step.value,
        step.includes,
        ASSERTION_VISIBILITY_TIMEOUT,
      );
    } finally {
      await handle.dispose();
    }
    if (!result.matched) stepFailure(step, `${step.selector || step.name} ${step.attr} was ${JSON.stringify(result.actual)}`);
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
    const handle = await requireElement(page, step, {allowHidden: true});
    let focused;
    try {
      if (!await waitForElementVisibility(page, handle)) stepFailure(step, `control not visible (${step.selector || step.name})`);
      focused = await page.evaluate((element) => document.activeElement === element, handle);
    } finally {
      await handle.dispose();
    }
    if (!focused) stepFailure(step, `focus is not on ${step.selector || step.name}`);
    return;
  }
  if (step.kind === 'a11yName' || step.kind === 'a11yRole' || step.kind === 'a11yState') {
    const node = await accessibilityNode(page, step);
    if (step.kind === 'a11yName') {
      const actual = collapseWhitespace(node.name);
      const expected = collapseWhitespace(step.expected);
      const matches = step.contains ? actual.includes(expected) : actual === expected;
      if (!matches) stepFailure(step, `accessible name was ${JSON.stringify(node.name)}, expected ${step.contains ? 'to contain ' : ''}${JSON.stringify(step.expected)}`);
    }
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
    const actual = await page.$$eval(selector, (elements) => elements.map((element) => element.textContent || ''));
    if (!liveRegionTextMatches(actual, step.text)) stepFailure(step, `live region lacks text: ${step.text}`);
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
    const handle = await requireElement(page, step, {timeout: ASSERTION_VISIBILITY_TIMEOUT});
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
    else if (step.text) {
      const found = await waitForText(page, step.text, timeout);
      if (!found) stepFailure(step, `visible text absent: ${step.text}`);
    }
    else if (step.url) await page.waitForFunction(navigationIsReady, {timeout, polling: ASSERTION_POLL_INTERVAL}, step.url);
    else stepFailure(step, 'waitFor needs selector, text, or url');
    return;
  }
  if (step.op === 'expectDownload') {
    if (!state.downloadClient) {
      const download = await configureDownloads(page, state.downloadDir);
      state.downloadClient = download.client;
      state.downloadEventsEnabled = download.eventsEnabled;
    }
    const waiter = state.downloadEventsEnabled
      ? createDownloadEventWaiter(state.downloadClient, state.downloadDir, step.pattern, timeout)
      : null;
    try {
      if (step.selector || step.name) {
        const handle = await requireElement(page, step);
        await handle.click(); await handle.dispose();
      }
    } catch (error) {
      if (waiter) waiter.cancel();
      throw error;
    }
    state.downloads.push(waiter
      ? await waiter.promise
      : await waitForDownloadDirectory(state.downloadDir, step.pattern, timeout));
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
  const downloadDir = uatWorkspacePath(
    'downloads',
    context.runId,
    `${journey.id}-${viewportName}-${context.retry}`,
  );
  fs.mkdirSync(downloadDir, {recursive: true});
  const state = {base: context.base, consoleErrors: [], downloads: [], downloadDir};
  page.on('console', (message) => { if (message.type() === 'error') state.consoleErrors.push(`console: ${message.text()}`); });
  page.on('pageerror', (error) => state.consoleErrors.push(`pageerror: ${errorText(error)}`));
  try {
    await page.setViewport(VIEWPORTS[viewportName]);
    await page.emulateMediaFeatures([{name: 'prefers-reduced-motion', value: 'reduce'}]);
  } catch (error) {
    await page.close().catch(() => {});
    await browserContext.close().catch(() => {});
    fs.rmSync(downloadDir, {recursive: true, force: true});
    throw error;
  }
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
  if (state.downloadClient) await state.downloadClient.detach().catch(() => {});
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

async function fetchBuild(browser, base, envLabel = null, bindings = false) {
  const result = {spine_build_id: null, git_base_sha: null, release_sha: null};
  if (bindings) {
    if (envLabel === 'local') {
      const stampPath = path.join(ROOT, 'site', 'platform', 'data', '.build-stamp.json');
      try {
        const parsed = JSON.parse(fs.readFileSync(stampPath, 'utf8'));
        result.spine_build_id = parsed.spine_build_id || null;
        result.git_base_sha = parsed.git_base_sha || null;
      } catch (error) {
        console.warn(`Binding build provenance unavailable for local: ${errorText(error)}; recording nulls`);
      }
      return result;
    }
    const workerUrl = WORKER_URLS[envLabel];
    if (!workerUrl) {
      console.warn(`Binding release provenance unavailable for ${envLabel}: no Worker URL; recording nulls`);
      return result;
    }
    try {
      const response = await fetch(targetUrl(workerUrl, '/edit/release-provenance'), {
        method: 'GET',
        redirect: 'manual',
        signal: AbortSignal.timeout(15000),
      });
      result.release_sha = response.headers.get('x-release-sha') || null;
      if (!result.release_sha) {
        console.warn(`Binding release provenance unavailable for ${envLabel}: HTTP ${response.status} had no x-release-sha; recording null`);
      }
    } catch (error) {
      console.warn(`Binding release provenance unavailable for ${envLabel}: ${errorText(error)}; recording null`);
    }
    return result;
  }
  if (!browser) return result;
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

function bindingAttempt(journey, verdict, firstFailure, overrides = {}) {
  const binding = journey[journey.binding] || {};
  return {
    journey: journey.id, story: journey.story, persona: journey.persona, viewport: 'n/a', verdict,
    first_failure: firstFailure,
    digest: null, duration_ms: 0, canary: Boolean(journey.canary), retry: 0,
    artifact: binding.command || null,
    ...overrides,
  };
}

function executableBinding(command, envLabel) {
  if (typeof command !== 'string' || !command.trim()) return {reason: 'command is empty'};
  const placeholder = command.match(/<[^>]+>/);
  if (placeholder) return {reason: `unresolved placeholder ${placeholder[0]}`};
  const executable = command.trim().split(/\s+/, 1)[0];
  if (!BINDING_EXECUTABLES.has(executable)) return {reason: `unrecognized executable ${executable}`};
  if (command.includes('{{WORKER_URL}}')) {
    const workerUrl = WORKER_URLS[envLabel];
    if (!workerUrl) return {reason: `{{WORKER_URL}} is unavailable for ${envLabel}`};
    return {command: command.replaceAll('{{WORKER_URL}}', workerUrl)};
  }
  return {command};
}

function lastOutputLines(output, limit = 40) {
  const normalized = String(output).replace(/\r\n/g, '\n').replace(/\n$/, '');
  if (!normalized) return '';
  return normalized.split('\n').slice(-limit).join('\n');
}

function terminateChild(child) {
  if (!child.pid) return;
  try {
    if (process.platform === 'win32') child.kill('SIGKILL');
    else process.kill(-child.pid, 'SIGKILL');
  } catch (_) {
    try { child.kill('SIGKILL'); } catch (_) {}
  }
}

function rollingOutputTail(previous, chunk) {
  const lines = (previous + chunk).replace(/\r\n/g, '\n').split('\n');
  return lines.length > 41 ? lines.slice(-41).join('\n') : lines.join('\n');
}

function executeBindingCommand(command, timeoutMs, logFd, digest) {
  return new Promise((resolve) => {
    const started = Date.now();
    let tail = '';
    let settled = false;
    let timedOut = false;
    let logError = null;
    const child = spawn('sh', ['-c', command], {
      cwd: ROOT,
      env: {...process.env, HEADLESS: '1', EDITOR_HEADLESS: '1'},
      detached: process.platform !== 'win32',
      stdio: ['ignore', 'pipe', 'pipe'],
    });
    const capture = (chunk) => {
      if (logError) return;
      try {
        fs.writeSync(logFd, chunk);
        digest.update(chunk);
        tail = rollingOutputTail(tail, chunk.toString());
      } catch (error) {
        logError = error;
        terminateChild(child);
      }
    };
    child.stdout.on('data', capture);
    child.stderr.on('data', capture);
    const timer = setTimeout(() => {
      timedOut = true;
      terminateChild(child);
    }, timeoutMs);
    child.on('error', (error) => {
      if (settled) return;
      settled = true;
      clearTimeout(timer);
      resolve({verdict: 'ERROR', reason: `binding spawn failed: ${errorText(error)}`, tail, duration: Date.now() - started});
    });
    child.on('close', (code, signal) => {
      if (settled) return;
      settled = true;
      clearTimeout(timer);
      if (logError) {
        resolve({verdict: 'ERROR', reason: `binding log failed: ${errorText(logError)}`, tail, duration: Date.now() - started});
      } else if (timedOut) {
        resolve({verdict: 'ERROR', reason: `binding timed out after ${timeoutMs}ms`, tail, duration: Date.now() - started});
      } else if (code === 0) {
        resolve({verdict: 'PASS', reason: null, tail, duration: Date.now() - started});
      } else {
        resolve({verdict: 'FAIL', reason: tail ? null : `binding exited ${code === null ? `on signal ${signal}` : `with code ${code}`}`, tail, duration: Date.now() - started});
      }
    });
  });
}

async function runBinding(journey, command, timeout, retry, logFd, digest) {
  const binding = journey[journey.binding] || {};
  const result = await executeBindingCommand(command, timeout, logFd, digest);
  const tail = lastOutputLines(result.tail);
  const firstFailure = result.verdict === 'PASS' ? null : (result.reason && tail ? `${result.reason}: ${tail}` : result.reason || tail);
  const attempt = bindingAttempt(journey, result.verdict, firstFailure, {
    duration_ms: result.duration,
    retry,
    binding_command: binding.command,
  });
  return attempt;
}

function bindingPrecondition(journey, envLabel) {
  const binding = journey[journey.binding] || {};
  if (Array.isArray(binding.environments) && !binding.environments.includes(envLabel)) {
    return bindingAttempt(journey, 'NOT RUN', `binding restricted to ${binding.environments.join(', ')}`);
  }
  if (binding.credential_gate && !process.env[binding.credential_gate]) {
    return bindingAttempt(journey, 'BLOCKED', `credential ${binding.credential_gate} unavailable`);
  }
  const executable = executableBinding(binding.command, envLabel);
  if (executable.reason) return bindingAttempt(journey, 'NOT RUN', `binding is not executable: ${executable.reason}`);
  return executable.command;
}

async function main(argv) {
  let options;
  try { options = parseArgs(argv); } catch (error) { return usage(errorText(error)); }
  const journeys = loadCatalog(options.journeys);
  const available = journeys.filter((item) => options.bindings ? item.binding !== 'steps' : item.binding === 'steps');
  const selected = options.only ? available.filter((item) => options.only.has(item.id)) : available;
  if (options.only) {
    const found = new Set(selected.map((item) => item.id));
    const missing = [...options.only].filter((id) => !found.has(id));
    if (missing.length) return usage(`journey id(s) unavailable in ${options.bindings ? 'bindings' : 'steps'} mode: ${missing.join(', ')}`);
  }
  fs.mkdirSync(options.runDir, {recursive: true});
  fs.mkdirSync(options.shotsDir, {recursive: true});
  const started = new Date(); const stamp = utcStamp(started); const runId = `${stamp}-${safeName(options.env)}`;
  const shotRoot = path.join(options.shotsDir, runId); fs.mkdirSync(shotRoot, {recursive: true});
  let browser = null; let puppeteer = null; let browserLaunch = 0; const launchErrors = [];
  const profileRoot = uatWorkspacePath('profiles', runId);
  const downloadRoot = uatWorkspacePath('downloads', runId);
  const openBrowser = async () => {
    if (!puppeteer) puppeteer = loadPuppeteer();
    const profileDir = uatWorkspacePath('profiles', runId, `browser-${process.pid}-${browserLaunch++}`);
    return launchBrowser(puppeteer, profileDir);
  };
  if (!options.bindings && selected.length) {
    for (let retry = 0; retry < 2 && !browser; retry++) {
      try { browser = await openBrowser(); }
      catch (error) { launchErrors.push(`browser launch: ${errorText(error)}`); }
    }
  }
  let build = {spine_build_id: null, git_base_sha: null, release_sha: null};
  const attempts = []; const finalByKey = new Map();
  try {
    build = await fetchBuild(browser, options.base, options.env, options.bindings);
    for (const journey of selected) {
      if (options.bindings) {
        const precondition = bindingPrecondition(journey, options.env);
        if (typeof precondition !== 'string') {
          attempts.push(precondition); finalByKey.set(`${journey.id}|n/a`, precondition);
          console.log(`${precondition.verdict} ${journey.id} n/a — ${precondition.first_failure}`);
          continue;
        }
        let final;
        const bindingAttempts = [];
        const logPath = path.join(shotRoot, `${safeName(journey.id)}-binding.log`);
        const logFd = fs.openSync(logPath, 'w'); const digest = crypto.createHash('sha256');
        try {
          for (let retry = 0; retry < 2; retry++) {
            if (retry) {
              const marker = Buffer.from(`\n--- retry ${retry} ---\n`);
              fs.writeSync(logFd, marker); digest.update(marker);
            }
            const attempt = await runBinding(journey, precondition, options.bindingTimeout, retry, logFd, digest);
            bindingAttempts.push(attempt); attempts.push(attempt); final = attempt;
            console.log(`${attempt.verdict} ${journey.id} n/a${attempt.first_failure ? ` — ${attempt.first_failure}` : ''}`);
            if (attempt.verdict !== 'ERROR') break;
          }
        } finally {
          fs.closeSync(logFd);
        }
        const logDigest = digest.digest('hex'); const artifact = relativeArtifact(logPath);
        for (const attempt of bindingAttempts) { attempt.digest = logDigest; attempt.artifact = artifact; }
        finalByKey.set(`${journey.id}|n/a`, final);
        continue;
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
            if (!browser || !browser.isConnected()) browser = await openBrowser();
            attempt = await runSteps(browser, journey, viewport, {base: options.base, shotRoot, runId, retry});
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
  } finally {
    if (browser) await browser.close().catch(() => {});
    fs.rmSync(profileRoot, {recursive: true, force: true});
    fs.rmSync(downloadRoot, {recursive: true, force: true});
  }

  if (fs.existsSync(shotRoot) && fs.readdirSync(shotRoot).length === 0) fs.rmdirSync(shotRoot);
  const run = {run_id: runId, env: options.env, base: options.base || null, started: started.toISOString(), build, attempts};
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

if (require.main === module) {
  main(process.argv.slice(2)).then((code) => process.exit(code)).catch((error) => { console.error('PERSONA JOURNEY ERROR:', errorText(error)); process.exit(2); });
}

module.exports = {
  attributeMatches,
  collapseWhitespace,
  controlNameMatches,
  fetchBuild,
  filenameMatches,
  liveRegionTextMatches,
  navigationIsReady,
  normalizeControlName,
  requireElement,
  selectControlCandidate,
  uatWorkspacePath,
  waitForAttribute,
};
