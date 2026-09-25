// Runtime boundary only: keep the production router, DO wrappers, core logic,
// SQL transactions and Web Crypto intact when running them under Node.
import { registerHooks } from 'node:module';
import { NodeSql } from './editor-sql-helper.mjs';

const runtime = 'data:text/javascript,' + encodeURIComponent(
  'export class DurableObject { constructor(ctx, env) { this.ctx = ctx; this.env = env; } }');
const hooks = registerHooks({
  resolve(specifier, context, nextResolve) {
    return specifier === 'cloudflare:workers'
      ? { url: runtime, shortCircuit: true }
      : nextResolve(specifier, context);
  },
});
let entry;
try {
  entry = await import('../src/index.js');
} finally {
  hooks.deregister();
}
export const worker = entry.default;
export const { BudgetCounter, EditorStore } = entry;

export async function durableObject(Class, env = {}) {
  const sql = new NodeSql();
  const initializers = [];
  const alarms = [];
  const storage = {
    sql, transactionSync: sql.transactionSync.bind(sql),
    async setAlarm(at) { alarms.push(at); },
  };
  const ctx = { storage, blockConcurrencyWhile(fn) { initializers.push(fn()); } };
  const object = new Class(ctx, env);
  await Promise.all(initializers);
  return { object, sql, alarms, close: () => sql.db.close() };
}
