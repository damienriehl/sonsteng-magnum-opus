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
  transactionSync(callback) {
    this.db.exec("BEGIN IMMEDIATE");
    try { const result = callback(); this.db.exec("COMMIT"); return result; }
    catch (error) { this.db.exec("ROLLBACK"); throw error; }
  }
}

// makeCore({ now }) -> a fresh, schema-initialized EditorStoreCore. `now` is a
// mutable clock: pass a { value } and mutate it, or a function.
export function makeCore(nowFn) {
  const sql = new NodeSql();
  const core = new EditorStoreCore(sql, nowFn || (() => Date.now()),
    sql.transactionSync.bind(sql));
  core.initSchema();
  return core;
}

export { NodeSql };
