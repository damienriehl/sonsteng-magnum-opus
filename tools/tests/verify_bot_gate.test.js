'use strict';

const assert = require('node:assert/strict');
const test = require('node:test');

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
