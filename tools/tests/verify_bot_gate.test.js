'use strict';

const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const {setTimeout: delay} = require('node:timers/promises');
const vm = require('node:vm');

const probeScript = path.resolve(__dirname, '../verify_bot_gate.js');
const expectedPaths = [
  '/v1/session',
  '/v1/session?cf_ts=invalid-token-value',
  '/v1/session?bypass=not-a-real-bypass',
];

const {buildProbeUrl} = require('../verify_bot_gate.js');

test('probe URLs stay on a base origin without a path', () => {
  assert.equal(
    buildProbeUrl(new URL('https://sonsteng-chat.example'), '/v1/session').href,
    'https://sonsteng-chat.example/v1/session',
  );
});

test('probe URLs avoid a protocol-relative double slash for a trailing-slash base', () => {
  assert.equal(
    buildProbeUrl(new URL('https://sonsteng-chat.example/'), '/v1/session?cf_ts=invalid').href,
    'https://sonsteng-chat.example/v1/session?cf_ts=invalid',
  );
});

test('probe URLs retain a base sub-path', () => {
  assert.equal(
    buildProbeUrl(new URL('https://sonsteng-chat.example/worker/api'), '/v1/session').href,
    'https://sonsteng-chat.example/worker/api/v1/session',
  );
});

test('probes wait for each prior request before starting the next', async () => {
  const seen = [];
  let activeRequest = false;
  const fakeFetch = async (url) => {
    if (activeRequest) throw new Error('probe requests overlapped');
    const parsed = new URL(url);
    const actualPath = `${parsed.pathname}${parsed.search}`;
    seen.push(actualPath);
    activeRequest = true;
    await delay(actualPath === expectedPaths[0] ? 25 : 0);
    activeRequest = false;
    return {
      status: 403,
      json: async () => ({error: {code: 'turnstile_failed'}}),
    };
  };
  const output = [];
  let resolveExit;
  const exitCode = new Promise((resolve) => { resolveExit = resolve; });
  const scriptModule = {exports: {}};
  const scriptRequire = (id) => require(id);
  scriptRequire.main = scriptModule;

  vm.runInNewContext(fs.readFileSync(probeScript, 'utf8'), {
    AbortSignal,
    URL,
    __dirname: path.dirname(probeScript),
    __filename: probeScript,
    console: {
      error: (...args) => output.push(args.join(' ')),
      log: (...args) => output.push(args.join(' ')),
    },
    fetch: fakeFetch,
    module: scriptModule,
    process: {
      argv: ['node', probeScript, '--worker', 'https://sonsteng-chat.example'],
      exit: resolveExit,
    },
    require: scriptRequire,
  });

  assert.equal(await exitCode, 0);
  assert.deepEqual(seen, expectedPaths);
  assert.match(output.join('\n'), /BOT GATE SUMMARY 3\/3/);
});
