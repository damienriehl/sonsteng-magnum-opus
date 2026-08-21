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
    this.storage = ctx.storage;
    this.core = new EditorStoreCore(ctx.storage.sql, undefined,
      ctx.storage.transactionSync.bind(ctx.storage));
    ctx.blockConcurrencyWhile(async () => {
      this.core.initSchema();
      // Crash reconciliation runs before the DO serves any claim: no limbo.
      this.core.reconcile();
      this.core.expireAssessmentAudits();
      await this._scheduleAssessmentExpiry();
    });
  }

  async _scheduleAssessmentExpiry() {
    const expiresAt = this.core.nextAssessmentAuditExpiry();
    if (expiresAt !== null) await this.storage.setAlarm(expiresAt);
  }

  async alarm() {
    this.core.expireAssessmentAudits();
    await this._scheduleAssessmentExpiry();
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
  recordCanonicalMutation(input) { return this.core.recordCanonicalMutation(input); }
  completeCanonicalMutation(input) { return this.core.completeCanonicalMutation(input); }
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
  async recordAssessmentAudit(input) {
    const result = this.core.recordAssessmentAudit(input);
    if (result.ok) await this._scheduleAssessmentExpiry();
    return result;
  }
  readAssessmentAudit(input) { return this.core.readAssessmentAudit(input); }
  recordAssessmentOverride(input) { return this.core.recordAssessmentOverride(input); }
  expireAssessmentAudits() { return this.core.expireAssessmentAudits(); }
  prepareProductionRelease(input) { return this.core.prepareProductionRelease(input); }
  authorizeProductionRelease(input) { return this.core.authorizeProductionRelease(input); }
  getProductionRelease(id) { return this.core.getProductionRelease(id); }
  claimAuthorizedProductionRelease(input) { return this.core.claimAuthorizedProductionRelease(input); }
  claimProductionRestore(input) { return this.core.claimProductionRestore(input); }
  renewProductionReleaseLease(input) { return this.core.renewProductionReleaseLease(input); }
  transitionProductionRelease(input) { return this.core.transitionProductionRelease(input); }
  recordReviewRevision(input) { return this.core.recordReviewRevision(input); }
  backfillReviewRevisions(input) { return this.core.backfillReviewRevisions(input); }
  reconcileLegacyReview(input) { return this.core.reconcileLegacyReview(input); }
  getLegacyBackfillEvidence(throughBatchId) { return this.core.getLegacyBackfillEvidence(throughBatchId); }
  getPublisherReview(actor) { return this.core.getPublisherReview(actor); }
  getDevReviewAnnotations(sourceRefs) { return this.core.getDevReviewAnnotations(sourceRefs); }
  savePublisherReviewDraft(input) { return this.core.savePublisherReviewDraft(input); }
  submitPublisherReview(submission) { return this.core.submitPublisherReview(submission); }
  publisherContext() { return this.core.publisherContext(); }
  publisherSummary() { return this.core.publisherSummary(); }
  productionReleaseAudit() { return this.core.productionReleaseAudit(); }
  productionPreparationContext() { return this.core.productionPreparationContext(); }
}
