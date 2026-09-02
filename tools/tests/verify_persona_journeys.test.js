'use strict';

const assert = require('node:assert/strict');
const path = require('node:path');
const test = require('node:test');

const {
  collapseWhitespace,
  controlNameMatches,
  fetchBuild,
  filenameMatches,
  liveRegionTextMatches,
  normalizeControlName,
  selectControlCandidate,
  uatWorkspacePath,
} = require('../verify_persona_journeys.js');

test('whitespace collapse normalizes text assertion content', () => {
  assert.equal(collapseWhitespace('  19,077\n\tMinnesota   attorneys  '), '19,077 Minnesota attorneys');
  assert.equal(collapseWhitespace(null), '');
});

test('download filename patterns support glob wildcards literally', () => {
  assert.equal(filenameMatches('m05-*.zip', 'm05-dwi-meridian-student-materials.zip'), true);
  assert.equal(filenameMatches('packet-?.zip', 'packet-1.zip'), true);
  assert.equal(filenameMatches('packet-?.zip', 'packet-10.zip'), false);
  assert.equal(filenameMatches('matter[1].zip', 'matter[1].zip'), true);
  assert.equal(filenameMatches('matter[1].zip', 'matter1.zip'), false);
});

test('control names collapse whitespace and compare case-insensitively', () => {
  assert.equal(normalizeControlName('  The\n  Evidence  '), 'the evidence');
  assert.equal(controlNameMatches('THE EVIDENCE', 'The Evidence'), true);
  assert.equal(controlNameMatches('Open the library →', 'open the library'), true);
  assert.equal(controlNameMatches('The Demonstration', 'The Evidence'), false);
});

test('name lookup prefers a visible match over an earlier hidden match', () => {
  const candidates = [
    {index: 0, name: 'The Evidence', visible: false},
    {index: 1, name: 'THE EVIDENCE', visible: true},
  ];

  assert.deepEqual(selectControlCandidate(candidates, 'The Evidence'), candidates[1]);
});

test('name lookup distinguishes a hidden match from no match', () => {
  const hidden = {index: 0, name: 'The Evidence', visible: false};

  assert.deepEqual(selectControlCandidate([hidden], 'the evidence'), hidden);
  assert.equal(selectControlCandidate([hidden], 'Open'), null);
});

test('name lookup ranks an exact link above an earlier visible containing container', () => {
  const candidates = [
    {index: 0, name: 'Module 1 — Foundational Fact gathering Client counseling', visible: true, interactive: false, tabIndex: 0},
    {index: 1, name: 'Fact gathering', visible: true, interactive: true, tabIndex: 0},
  ];

  assert.deepEqual(selectControlCandidate(candidates, 'Fact gathering'), candidates[1]);
});

test('name lookup excludes a non-interactive tabindex minus-one main', () => {
  const main = {index: 0, name: 'Module 1 — Foundational Fact gathering', visible: true, interactive: false, tabIndex: -1};
  const link = {index: 1, name: 'Fact gathering', visible: true, interactive: true, tabIndex: 0};

  assert.deepEqual(selectControlCandidate([main, link], 'Fact gathering'), link);
  assert.equal(selectControlCandidate([main], 'Fact gathering'), null);
});

test('name lookup prefers the shortest visible name among substring-only matches', () => {
  const candidates = [
    {index: 0, name: 'Taxonomy Skills browser 26 surveyed skills across the curriculum', visible: true},
    {index: 1, name: 'Skills browser 26 surveyed', visible: true},
  ];

  assert.deepEqual(selectControlCandidate(candidates, 'Skills browser'), candidates[1]);
});

test('live-region text comparison uses collapsed case-sensitive DOM text', () => {
  assert.equal(liveRegionTextMatches(['20 matters', 'page 1 of 1'], '20 matters'), true);
  assert.equal(liveRegionTextMatches(['20 MATTERS', 'PAGE 1 OF 1'], '20 matters'), false);
});

test('UAT workspace paths stay under the repository build tree and sanitize components', () => {
  const expected = path.resolve(__dirname, '..', '..', 'build', 'uat', 'downloads', 'run-01', 'journey-phone-0');

  assert.equal(uatWorkspacePath('downloads', 'run 01', 'journey/phone/0'), expected);
  assert.throws(() => uatWorkspacePath('screenshots', 'run-01'), /unsupported UAT workspace kind/);
  assert.throws(() => uatWorkspacePath('downloads', '..', '..', 'escaped'), /unsafe UAT workspace component/);
  assert.throws(() => uatWorkspacePath('profiles', '.'), /unsafe UAT workspace component/);
});

test('binding provenance records a Worker release header', async (t) => {
  const originalFetch = global.fetch;
  t.after(() => { global.fetch = originalFetch; });
  global.fetch = async () => new Response('', {headers: {'x-release-sha': 'release-123'}});

  assert.deepEqual(await fetchBuild(null, null, 'dev', true), {
    spine_build_id: null,
    git_base_sha: null,
    release_sha: 'release-123',
  });
});

test('unreachable binding provenance records nulls and reports the reason', async (t) => {
  const originalFetch = global.fetch;
  const originalWarn = console.warn;
  const warnings = [];
  t.after(() => { global.fetch = originalFetch; console.warn = originalWarn; });
  global.fetch = async () => { throw new Error('offline fixture'); };
  console.warn = (message) => warnings.push(message);

  assert.deepEqual(await fetchBuild(null, null, 'prod', true), {
    spine_build_id: null,
    git_base_sha: null,
    release_sha: null,
  });
  assert.match(warnings.join('\n'), /release provenance unavailable.*offline fixture/i);
});
