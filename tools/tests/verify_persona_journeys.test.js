'use strict';

const assert = require('node:assert/strict');
const test = require('node:test');

const {
  collapseWhitespace,
  controlNameMatches,
  filenameMatches,
  normalizeControlName,
  selectControlCandidate,
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
