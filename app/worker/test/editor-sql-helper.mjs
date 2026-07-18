// editor-sql-helper.mjs — node:sqlite adapter matching the DO's ctx.storage.sql
// surface (exec(query, ...binds) -> { toArray() }), for EditorStoreCore tests.
// Not a *.test.js file, so the test runner ignores it as a suite.
import { DatabaseSync } from "node:sqlite";
import { EditorStoreCore } from "../src/editor-store-core.js";

class NodeSql {
  constructor() {
    this.db = new DatabaseSync(":memory:");
  }
  exec(query, ...binds) {
    if (binds.length === 0 && query.includes(";")) {
      this.db.exec(query); // multi-statement schema
      return { toArray: () => [] };
    }
    const st = this.db.prepare(query);
    if (/^\s*select/i.test(query)) {
      return { toArray: () => st.all(...binds) };
    }
    st.run(...binds);
    return { toArray: () => [] };
  }
}

// makeCore({ now }) -> a fresh, schema-initialized EditorStoreCore. `now` is a
// mutable clock: pass a { value } and mutate it, or a function.
export function makeCore(nowFn) {
  const core = new EditorStoreCore(new NodeSql(), nowFn || (() => Date.now()));
  core.initSchema();
  return core;
}

export { NodeSql };
