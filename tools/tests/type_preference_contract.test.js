'use strict';

const assert = require('assert');
const fs = require('fs');
const vm = require('vm');
const path = require('path');

const source = fs.readFileSync(path.join(__dirname, '../../app/chat/type-preference.js'), 'utf8');

function boot(initial) {
  const values = new Map(Object.entries(initial || {}));
  const handlers = {};
  const classes = new Set();
  const context = {
    localStorage: {
      getItem(key) { return values.has(key) ? values.get(key) : null; },
      setItem(key, value) { values.set(key, value); },
      removeItem(key) { values.delete(key); }
    },
    document: { documentElement: { classList: {
      contains(name) { return classes.has(name); },
      toggle(name, enabled) { if (enabled) classes.add(name); else classes.delete(name); }
    } } },
    window: {
      addEventListener(name, handler) { handlers[name] = handler; }
    }
  };
  context.window.window = context.window;
  context.window.document = context.document;
  context.window.localStorage = context.localStorage;
  Object.assign(context, context.window);
  vm.runInNewContext(source, context);
  return { values, classes, handlers, api: context.window.SonstengTypePreference };
}

let state = boot({ 'sonsteng-type-lg': '0', sonsteng_type_lg: '1' });
assert(state.classes.has('type-lg'), 'enabled legacy preference wins a conflict');
assert.strictEqual(state.values.get('sonsteng-type-lg'), '1');
assert(!state.values.has('sonsteng_type_lg'), 'legacy key is removed after migration');

let observed = null;
state.api.subscribe(value => { observed = value; });
state.api.set(false);
assert.strictEqual(observed, false);
assert(!state.classes.has('type-lg'));
assert.strictEqual(state.values.get('sonsteng-type-lg'), '0');

state.values.set('sonsteng-type-lg', '1');
state.handlers.storage({ key: 'sonsteng-type-lg' });
assert.strictEqual(observed, true, 'storage events synchronize open tabs');
assert(state.classes.has('type-lg'));

console.log('type preference contract: PASS');
