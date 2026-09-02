#!/usr/bin/env node
'use strict';

const ORIGIN = 'https://legalpracticum.org';
const REQUEST_TIMEOUT_MS = 15000;
const PROBES = [
  ['missing-token', '/v1/session'],
  ['invalid-token', '/v1/session?cf_ts=invalid-token-value'],
  ['invalid-bypass', '/v1/session?bypass=not-a-real-bypass'],
];

function usage(stream = process.stderr) {
  stream.write('Usage: node tools/verify_bot_gate.js --worker <url>\n');
  stream.write('       --base is accepted as an alias for --worker.\n');
}

function parseArgs(argv) {
  if (argv.length === 1 && (argv[0] === '--help' || argv[0] === '-h')) return {help: true};
  if (argv.length !== 2 || !['--worker', '--base'].includes(argv[0])) {
    throw new Error('exactly one --worker <url> argument is required');
  }
  let worker;
  try { worker = new URL(argv[1]); }
  catch (_) { throw new Error('--worker must be an absolute HTTP or HTTPS URL'); }
  if (!['http:', 'https:'].includes(worker.protocol)) throw new Error('--worker must be an absolute HTTP or HTTPS URL');
  return {worker};
}

async function runProbe(worker, name, pathname) {
  const url = buildProbeUrl(worker, pathname);
  let response;
  try {
    response = await fetch(url, {
      method: 'GET',
      headers: {Origin: ORIGIN, Accept: 'application/json'},
      redirect: 'manual',
      signal: AbortSignal.timeout(REQUEST_TIMEOUT_MS),
    });
  } catch (error) {
    return {name, ok: false, detail: `request failed: ${error && error.message ? error.message : String(error)}`};
  }
  let payload = null;
  try { payload = await response.json(); } catch (_) {}
  const code = payload && payload.error && payload.error.code;
  const ok = response.status === 403 && code === 'turnstile_failed';
  return {name, ok, detail: `HTTP ${response.status} ${code || 'missing-error-code'}`};
}

function buildProbeUrl(worker, pathname) {
  const base = new URL(worker);
  base.pathname = `${base.pathname.replace(/\/+$/, '')}/`;
  base.search = '';
  base.hash = '';
  return new URL(String(pathname).replace(/^\/+/, ''), base);
}

async function main(argv) {
  let options;
  try { options = parseArgs(argv); }
  catch (error) {
    console.error(error.message);
    usage();
    return 2;
  }
  if (options.help) {
    usage(process.stdout);
    return 0;
  }
  const results = await Promise.all(PROBES.map(([name, pathname]) => runProbe(options.worker, name, pathname)));
  for (const result of results) console.log(`${result.ok ? 'PASS' : 'FAIL'} ${result.name} — ${result.detail}`);
  const passed = results.filter((result) => result.ok).length;
  console.log(`BOT GATE SUMMARY ${passed}/${results.length}`);
  return passed === results.length ? 0 : 1;
}

if (require.main === module) {
  main(process.argv.slice(2)).then((code) => process.exit(code)).catch((error) => {
    console.error(`BOT GATE ERROR: ${error && error.message ? error.message : String(error)}`);
    process.exit(2);
  });
}

module.exports = {buildProbeUrl};
