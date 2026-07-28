// editor-store.js — EditorStore, the second SQLite Durable Object (migration
// tag v2, appended — v1/BudgetCounter is NEVER altered). Thin DO shell: schema
// init once via blockConcurrencyWhile, then every RPC delegates synchronously to
// EditorStoreCore (the DO's single-threaded model is the atomicity guarantee — no
// await between a core method's reads and writes). One global instance, routed by
// constant name: env.EDITOR.getByName("global-v1").
//
// All map/allowlist validation and server-side resolution of `editor` +
// `original_text` happen in the ENDPOINT layer (editor-endpoints.js) before these
// methods are called; this store trusts only what the endpoint resolved.

import { DurableObject } from "cloudflare:workers";
import { EditorStoreCore } from "./editor-store-core.js";

export class EditorStore extends DurableObject {
  constructor(ctx, env) {
    super(ctx, env);
    this.core = new EditorStoreCore(ctx.storage.sql);
    ctx.blockConcurrencyWhile(async () => {
      this.core.initSchema();
      // Crash reconciliation runs before the DO serves any claim: no limbo.
      this.core.reconcile();
    });
  }

  suggest(input, ceilings, opts) { return this.core.suggest(input, ceilings, opts); }
  recordHeartbeat(beat) { return this.core.recordHeartbeat(beat); }
  getHeartbeat() { return this.core.getHeartbeat(); }
  heartbeatAgeS() { return this.core.heartbeatAgeS(); }
  listForEditor(editor, page) { return this.core.listForEditor(editor, page); }
  listForPage(page) { return this.core.listForPage(page); }
  listAll() { return this.core.listAll(); }
  decide(args) { return this.core.decide(args); }
  markDrift(id) { return this.core.markDrift(id); }
  reanchor(id, patch) { return this.core.reanchor(id, patch); }
  claimBatch(batchId, opts) { return this.core.claimBatch(batchId, opts); }
  finalize(batchId, outcome) { return this.core.finalize(batchId, outcome); }
  reconcile() { return this.core.reconcile(); }
  digest() { return this.core.digest(); }
  purge(days) { return this.core.purge(days); }
  fileRevertRequest(input) { return this.core.fileRevertRequest(input); }
  listRevertRequests(status) { return this.core.listRevertRequests(status); }
  resolveRevertRequest(id, status, note) { return this.core.resolveRevertRequest(id, status, note); }
  fileScopedRequest(input) { return this.core.fileScopedRequest(input); }
  listScopedRequests(status) { return this.core.listScopedRequests(status); }
  claimScopedRequest(id) { return this.core.claimScopedRequest(id); }
  resolveScopedRequest(id, patch) { return this.core.resolveScopedRequest(id, patch); }
  groupOutcome(groupId) { return this.core.groupOutcome(groupId); }
}
