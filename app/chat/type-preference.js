/* Shared Large Type preference: CSP-safe, pre-paint, and cross-tab aware. */
(function () {
  'use strict';

  var CANONICAL_KEY = 'sonsteng-type-lg';
  var LEGACY_KEY = 'sonsteng_type_lg';
  var listeners = [];

  function readAndMigrate() {
    var canonical = null;
    var legacy = null;
    try {
      canonical = localStorage.getItem(CANONICAL_KEY);
      legacy = localStorage.getItem(LEGACY_KEY);
      /* An enabled value wins when old and new deployments disagree. */
      var enabled = canonical === '1' || legacy === '1';
      localStorage.setItem(CANONICAL_KEY, enabled ? '1' : '0');
      localStorage.removeItem(LEGACY_KEY);
      return enabled;
    } catch (e) {
      return document.documentElement.classList.contains('type-lg');
    }
  }

  function apply(enabled, announce) {
    document.documentElement.classList.toggle('type-lg', enabled);
    if (announce) listeners.slice().forEach(function (listener) { listener(enabled); });
    return enabled;
  }

  function set(enabled) {
    enabled = !!enabled;
    try {
      localStorage.setItem(CANONICAL_KEY, enabled ? '1' : '0');
      localStorage.removeItem(LEGACY_KEY);
    } catch (e) {}
    return apply(enabled, true);
  }

  var initial = apply(readAndMigrate(), false);
  window.addEventListener('storage', function (event) {
    if (event.key === CANONICAL_KEY || event.key === LEGACY_KEY || event.key === null) {
      apply(readAndMigrate(), true);
    }
  });

  window.SonstengTypePreference = {
    get: function () { return document.documentElement.classList.contains('type-lg'); },
    set: set,
    subscribe: function (listener) {
      listeners.push(listener);
      listener(document.documentElement.classList.contains('type-lg'));
      return function () { listeners = listeners.filter(function (item) { return item !== listener; }); };
    },
    initial: initial
  };
})();
