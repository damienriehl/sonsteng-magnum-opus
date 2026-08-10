// Human Publisher surface. Preparation remains a release-service operation;
// this page can authorize only the exact immutable `prepared` record returned
// by the store. With JavaScript absent it is deliberately read-only.
import { escapeHtml, escapeJsonIsland } from "./editor-map.js";
import { attributionLabel } from "./editor-auth.js";

const STATE = Object.freeze({
  draft: ["Draft", "Choose the end of the next contiguous DEV apply frontier. Preparation freezes the evidence before release is possible."],
  prepared: ["Prepared", "An immutable production preview is ready for Publisher review."],
  authorized: ["Authorized", "A Publisher authorized this exact batch. Release automation may now claim it."],
  executing: ["Publishing", "Automation is releasing the authorized manifest. No further Publisher action is available."],
  delayed: ["Delayed", "The authorized release is waiting. Its membership remains frozen."],
  failed_fenced: ["Failed and fenced", "Publishing stopped and later releases are fenced. An operator must reconcile the recorded manifest."],
  restoring: ["Restoring", "Automation is restoring the recorded production manifest."],
  restored: ["Restored", "Production was restored. Publishing a different target requires a new Publisher authorization."],
  verified: ["Verified", "Pages and the production Worker agree on the authorized release identity."],
  complete: ["Published", "The authorized batch is verified and complete in production."],
});

function short(value) {
  const s = String(value || "");
  return s.length > 16 ? `${s.slice(0, 12)}…${s.slice(-4)}` : s;
}

export function publisherViewModel(context = {}) {
  const batches = Array.isArray(context.batches) ? context.batches : [];
  let enclosedChanges = 0;
  const targets = batches.map((batch) => {
    enclosedChanges += Array.isArray(batch.changes) ? batch.changes.length : 0;
    return { ...batch, enclosedChanges };
  });
  const release = context.release && typeof context.release === "object" ? context.release : null;
  const state = release?.state || "draft";
  return {
    batches, targets, release, state,
    eligibleChanges: enclosedChanges,
    productionStatus: enclosedChanges && (!release || state === "prepared")
      ? "Available on DEV — waiting for Publisher"
      : (STATE[state]?.[0] || String(state).replace(/_/g, " ")),
  };
}

function renderTargets(vm) {
  if (!vm.targets.length) return "<p class=\"pub-empty\">No complete DEV apply batch is eligible for production.</p>";
  return "<fieldset class=\"pub-targets\"><legend>Next contiguous frontier</legend>" +
    vm.targets.map((b, i) => "<label><input type=\"radio\" name=\"target_batch_id\" value=\"" +
      escapeHtml(b.batch_id) + "\"" + (i === vm.targets.length - 1 ? " checked" : "") + "> " +
      "Through <strong>" + escapeHtml(b.batch_id) + "</strong> — " + b.enclosedChanges +
      " enclosed change" + (b.enclosedChanges === 1 ? "" : "s") + "</label>").join("") +
    "<button type=\"button\" disabled aria-describedby=\"pub-prepare-help\">Prepare immutable preview</button>" +
    "<p class=\"pub-help\" id=\"pub-prepare-help\">Choosing a target does not publish it. The trusted release service will enable preparation when it can freeze the exact evidence; authorization is unavailable until then.</p></fieldset>";
}

function renderChanges(vm) {
  const releaseIds = new Set(vm.release?.suggestion_ids || []);
  const batches = vm.release
    ? vm.batches.filter((b) => (vm.release.batches || []).some((rb) => rb.batch_id === b.batch_id))
    : vm.batches;
  const rows = [];
  for (const batch of batches) for (const change of (batch.changes || [])) {
    if (vm.release && !releaseIds.has(change.id)) continue;
    const action = change.kind === "history_revert" ? "Approved History revert" : "Suggested change";
    rows.push("<article class=\"pub-change\"><header><strong>" + escapeHtml(action) + ": " +
      escapeHtml(change.source_ref || change.id) +
      "</strong><span>" + (change.kind === "history_revert" ? "Requested by " : "By ") +
      escapeHtml(attributionLabel(change.editor) || "Unknown") +
      (change.group_id ? " · group " + escapeHtml(change.group_id) : "") + "</span></header>" +
      "<div class=\"pub-redline\"><section><h4>Before</h4><p><del>" + escapeHtml(change.original_text || "") +
      "</del></p></section><section><h4>After</h4><p><ins>" + escapeHtml(change.new_text || "") +
      "</ins></p></section></div></article>");
  }
  return rows.length ? rows.join("") : "<p class=\"pub-empty\">No enclosed change detail is available.</p>";
}

function decisionMap(item) {
  const source = item.submitted_review?.decisions || item.draft?.decisions || [];
  return new Map(source.map((decision) => [decision.operation_id, decision]));
}

function pageFor(sourceRef) {
  const path = String(sourceRef || "Unknown source").split("#", 1)[0];
  return path.replace(/^site\/platform\//, "/platform/");
}

function contextText(parts) {
  const text = Array.isArray(parts) ? parts.join("") : "";
  return text.length > 180 ? text.slice(-180) : text;
}

function renderMarkedOperation(operation) {
  const before = escapeHtml(contextText(operation.context_before));
  const afterRaw = Array.isArray(operation.context_after) ? operation.context_after.join("") : "";
  const after = escapeHtml(afterRaw.length > 180 ? afterRaw.slice(0, 180) : afterRaw);
  const moved = !!operation.move_pair_id;
  const deleteLabel = moved ? "Moved from" : "Deleted text";
  const insertLabel = moved ? "Moved to" : "Added text";
  const deleted = operation.old_text ? "<span class=\"pub-sr-label\">" + deleteLabel +
    "</span><del aria-label=\"" + deleteLabel + "\">" + escapeHtml(operation.old_text) + "</del>" : "";
  const inserted = operation.new_text ? "<span class=\"pub-sr-label\">" + insertLabel +
    "</span><ins aria-label=\"" + insertLabel + "\">" + escapeHtml(operation.new_text) + "</ins>" : "";
  return "<p class=\"pub-excerpt\">" + before + deleted + inserted + after + "</p>";
}

function renderDecisionCard(item, operations, position, total, decision) {
  const operation = operations[0];
  const decisionId = operation.decision_id || operation.id;
  const safeId = escapeHtml(decisionId);
  const checked = (value) => decision?.decision === value ? " checked" : "";
  const submitted = item.submitted_review ? " disabled" : "";
  const stale = item.stale ? " disabled" : "";
  const excerpts = operations.map((op) => renderMarkedOperation(op)).join("");
  const status = item.stale ? "Stale" : decision ? String(decision.decision || "Unreviewed").replace(/^./, (c) => c.toUpperCase()) : "Unreviewed";
  return "<article class=\"pub-operation\" id=\"change-" + safeId + "\" data-operation-id=\"" + safeId +
    "\" data-review-status=\"" + escapeHtml(status.toLowerCase()) + "\"><header><h4>Change " + position +
    " of " + total + "</h4><span class=\"pub-change-status\">" + escapeHtml(status) + "</span></header>" + excerpts +
    "<fieldset class=\"pub-decision\"" + (item.stale ? " disabled" : "") +
    "><legend>Decision for change " + position + " in " + escapeHtml(item.revision.source_ref) + "</legend>" +
    [
      ["accepted", "Accept"], ["rejected", "Reject"], ["questioned", "Ask question"],
    ].map(([value, label]) => "<label><input type=\"radio\" name=\"decision-" + safeId +
      "\" value=\"" + value + "\"" + checked(value) + submitted + stale + "> " + label + "</label>").join("") +
    "<div class=\"pub-note pub-reject-note\"><label for=\"reject-" + safeId + "\">Rejection note (optional)</label>" +
    "<textarea id=\"reject-" + safeId + "\" data-note-for=\"rejected\" rows=\"2\"" + submitted + stale + ">" +
    escapeHtml(decision?.decision === "rejected" ? decision.note || "" : "") + "</textarea></div>" +
    "<div class=\"pub-note pub-question-note\"><label for=\"question-" + safeId + "\">Question (required when asking)</label>" +
    "<textarea id=\"question-" + safeId + "\" data-note-for=\"questioned\" rows=\"2\"" +
    (decision?.decision === "questioned" ? " required" : "") + submitted + stale + ">" +
    escapeHtml(decision?.decision === "questioned" ? decision.note || "" : "") + "</textarea></div>" +
    "<p class=\"pub-inline-error\" id=\"error-" + safeId + "\" hidden></p></fieldset></article>";
}

function renderGranularReview(review) {
  const revisions = Array.isArray(review?.revisions) ? review.revisions : [];
  if (!revisions.length) return "<section class=\"pub-review\" aria-labelledby=\"pub-review-title\"><h2 id=\"pub-review-title\">Review changes</h2><p class=\"pub-empty\">No atomic review evidence is available yet.</p></section>";
  const counts = review.counts || {};
  const filters = [["all", counts.total], ["reviewed", counts.reviewed], ["unreviewed", counts.unreviewed], ["accepted", counts.accepted],
    ["rejected", counts.rejected], ["questioned", counts.questioned]];
  let ordinal = 0;
  const sources = revisions.map((item) => {
    const decisions = decisionMap(item);
    const units = new Map();
    for (const operation of (item.revision?.operations || [])) {
      const id = operation.decision_id || operation.id;
      if (!units.has(id)) units.set(id, []);
      units.get(id).push(operation);
    }
    const cards = [...units.entries()].map(([id, operations]) => {
      ordinal += 1;
      return renderDecisionCard(item, operations, ordinal, Number(counts.total || units.size), decisions.get(id));
    }).join("");
    const submitted = item.submitted_review ? "<p class=\"pub-submitted\">Submitted review: " +
      escapeHtml(item.submitted_review.id) + "</p>" : "";
    return "<section class=\"pub-source\" data-review-revision=\"" + escapeHtml(item.revision.id) +
      "\" data-source-revision=\"" + escapeHtml(item.revision.source_revision) + "\" data-prod-base=\"" +
      escapeHtml(item.revision.prod_base) + "\"><header><p class=\"pub-page\">" + escapeHtml(pageFor(item.revision.source_ref)) +
      "</p><h3>" + escapeHtml(item.revision.source_ref) + "</h3></header>" + submitted + cards +
      "<p class=\"pub-save-state\" role=\"status\" aria-live=\"polite\">" +
      (item.submitted_review ? "Submitted" : "Saved") + "</p><button class=\"pub-retry\" type=\"button\" hidden>Retry save</button>" +
      "<details class=\"pub-context\"><summary>Show more context</summary><h4>Original field</h4><p>" +
      escapeHtml(item.revision.original_text || "") + "</p><h4>Proposed DEV field</h4><p>" +
      escapeHtml(item.revision.proposed_text || "") + "</p></details></section>";
  }).join("");
  return "<section class=\"pub-review\" aria-labelledby=\"pub-review-title\"><h2 id=\"pub-review-title\">Review changes</h2>" +
    "<p>Decide each atomic change. Draft choices remain private until you submit the review.</p>" +
    "<div class=\"pub-counts\" aria-label=\"Filter changes by review status\">" + filters.map(([name, count]) =>
      "<button type=\"button\" data-filter=\"" + name + "\" aria-pressed=\"" + (name === "all" ? "true" : "false") +
      "\">" + name.replace(/^./, (c) => c.toUpperCase()) + " <span>" + Number(count || 0) + "</span></button>").join("") + "</div>" +
    "<div class=\"pub-jump\"><button type=\"button\" id=\"pub-next-unreviewed\">Next unreviewed</button>" +
    "<button type=\"button\" id=\"pub-next-problem\">Next problem</button></div>" +
    "<div id=\"error-summary\" class=\"pub-error-summary\" role=\"alert\" tabindex=\"-1\" hidden><h3>Review needs attention</h3><ul></ul></div>" +
    "<form id=\"publisher-review-form\" novalidate>" + sources +
    "<section class=\"pub-submit\"><h3>Submit review</h3><p>Submitting records these decisions and makes accepted changes eligible for a later preview. <strong>Submitting this review does not authorize production.</strong></p>" +
    "<button type=\"submit\" id=\"pub-submit-review\">Submit review</button></section></form></section>";
}

function renderEvidence(release) {
  if (!release) return "";
  const fields = [["Release", release.id], ["Target", release.target_environment],
    ["Last apply batch", release.target_batch_id], ["Production base", release.base_sha],
    ["Candidate", release.candidate_sha], ["Generator", release.generator_id],
    ["Membership", release.membership_hash], ["Evidence", release.evidence_hash],
    ["Manifest", release.manifest_hash]];
  const batches = (release.batches || []).map((b) => "<li><strong>" + escapeHtml(b.batch_id) +
    "</strong> — commit " + escapeHtml(short(b.commit_sha)) + "</li>").join("");
  const events = (release.events || []).map((event) => {
    const iso = Number.isFinite(Number(event.created_at)) ? new Date(Number(event.created_at)).toISOString() : "";
    return "<li><strong>" + escapeHtml(String(event.type || "event").replace(/_/g, " ")) + "</strong> by " +
      escapeHtml(attributionLabel(event.actor) || event.actor || "system") +
      (iso ? " at <time datetime=\"" + escapeHtml(iso) + "\">" + escapeHtml(iso) + "</time>" : "") + "</li>";
  }).join("");
  return "<dl class=\"pub-binding\">" + fields.map(([k, v]) => "<div><dt>" + k +
    "</dt><dd title=\"" + escapeHtml(v || "") + "\">" + escapeHtml(short(v)) + "</dd></div>").join("") + "</dl>" +
    (batches ? "<h3>Complete enclosed apply batches</h3><ol class=\"pub-batches\">" + batches + "</ol>" : "") +
    (events ? "<h3>Release events</h3><ol class=\"pub-events\">" + events + "</ol>" : "");
}

export function renderPublisherPage(context = {}, viewerLabel = "") {
  const vm = publisherViewModel(context);
  const release = vm.release;
  const hasGranularReview = Array.isArray(context.review?.revisions) && context.review.revisions.length > 0;
  const [label, explanation] = STATE[vm.state] || [vm.state, "No release action is available for this state."];
  const binding = release ? escapeJsonIsland({ id: release.id, target_batch_id: release.target_batch_id,
    base_sha: release.base_sha, candidate_sha: release.candidate_sha, generator_id: release.generator_id,
    evidence_hash: release.evidence_hash, manifest_hash: release.manifest_hash,
    membership_hash: release.membership_hash }) : "{}";
  const authorize = vm.state === "prepared" ?
    "<section class=\"pub-authority\"><h2>Authorize this exact production release</h2>" +
    "<p><strong>Consequence:</strong> automation may publish only release <strong>" + escapeHtml(release.id) +
    "</strong>, through batch <strong>" + escapeHtml(release.target_batch_id) + "</strong>, containing <strong>" +
    (release.suggestion_ids || []).length + " changes</strong>, from <strong>" + escapeHtml(short(release.base_sha)) +
    "</strong> to <strong>" + escapeHtml(short(release.candidate_sha)) + "</strong>.</p>" +
    "<label class=\"pub-confirm\"><input id=\"pub-confirm\" type=\"checkbox\"> I reviewed every enclosed change and authorize this exact production batch.</label>" +
    "<button type=\"button\" id=\"pub-authorize\" disabled>Authorize release to production</button>" +
    "<p class=\"pub-nojs\">JavaScript is required for the signed, same-origin authorization request. Without it this page remains review-only.</p></section>" : "";
  const html = "<!doctype html><html lang=\"en\"><head><meta charset=\"utf-8\">" +
    "<meta name=\"viewport\" content=\"width=device-width, initial-scale=1\"><title>Production Publisher — Sonsteng</title>" +
    "<link rel=\"stylesheet\" href=\"/edit/assets/editor.css\"><link rel=\"stylesheet\" href=\"/edit/assets/publisher.css\"></head>" +
    "<body><main class=\"pub\" data-release-state=\"" + escapeHtml(vm.state) + "\"><nav aria-label=\"Editor\"><a href=\"/edit/review\">Suggestion review</a> · <a href=\"/edit/history/\">History</a></nav>" +
    "<header><p class=\"pub-eyebrow\">Human publication gate" + (viewerLabel ? " · " + escapeHtml(viewerLabel) : "") +
    "</p><h1>Production Publisher</h1><p>Approval and DEV availability do not publish production.</p></header>" +
    "<section class=\"pub-state\" aria-labelledby=\"pub-state-title\"><h2 id=\"pub-state-title\">" + escapeHtml(label) +
    "</h2><p>" + escapeHtml(explanation) + "</p><p class=\"pub-status\">" + escapeHtml(vm.productionStatus) + "</p></section>" +
    renderGranularReview(context.review) +
    (!release ? renderTargets(vm) : "") +
    "<section><h2>" + (release ? "Immutable prepared preview" : "Eligible DEV changes") + "</h2>" +
    (hasGranularReview && !release ? "<p>Atomic review decisions above replace the legacy whole-field preview.</p>" : renderChanges(vm)) +
    "<details><summary>Evidence, manifest, and event record</summary>" + renderEvidence(release) +
    "<p>Secondary evidence is disclosed here; the exact identity and consequence remain above the release control.</p></details></section>" +
    authorize + "<p id=\"pub-live\" class=\"pub-live\" aria-live=\"polite\" tabindex=\"-1\"></p></main>" +
    "<script type=\"application/json\" id=\"publisher-binding\">" + binding + "</script>" +
    "<script src=\"/edit/assets/publisher.js\" defer></script></body></html>";
  return new Response(html, { status: 200, headers: { "content-type": "text/html; charset=utf-8" } });
}
