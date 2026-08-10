// Deterministic, dependency-free prose redlines.
//
// The edit-script shape follows this repository's first-party Python
// SequenceMatcher renderer (tools/render_diff_lib.py): tokenize meaningful
// prose boundaries, retain equal runs, and group adjacent delete/insert spans
// as one replacement. The JS implementation is deliberately bounded and uses
// structured segments so callers never need to trust diff text as HTML.

import { escapeHtml } from "./editor-map.js";
import { sha256Hex } from "./text-norm.js";

const MAX_TEXT_LENGTH = 32_768;
const MAX_TOKENS = 1_024;
const MAX_MATRIX_CELLS = 250_000;
const DEFAULT_CONTEXT_TOKENS = 12;
const MIN_MOVE_WORDS = 4;
const MIN_MOVE_CHARACTERS = 24;

const TOKEN_RE = /[\p{L}\p{N}][\p{L}\p{M}\p{N}'’_-]*|\p{M}+|\s+|[^\p{L}\p{M}\p{N}\s]/gu;

function tokenize(text) {
  const tokens = [];
  for (const match of text.matchAll(TOKEN_RE)) {
    const value = match[0];
    tokens.push({
      text: value,
      start: match.index,
      end: match.index + value.length,
      type: /^\s+$/u.test(value) ? "space" : (/^[\p{L}\p{M}\p{N}]/u.test(value) ? "word" : "punctuation"),
    });
  }
  return tokens;
}

function lcsOpcodes(a, b) {
  if (a.length * b.length > MAX_MATRIX_CELLS) return null;
  const width = b.length + 1;
  const table = new Uint16Array((a.length + 1) * width);
  for (let i = a.length - 1; i >= 0; i -= 1) {
    for (let j = b.length - 1; j >= 0; j -= 1) {
      table[i * width + j] = a[i] === b[j]
        ? table[(i + 1) * width + j + 1] + 1
        : Math.max(table[(i + 1) * width + j], table[i * width + j + 1]);
    }
  }

  const steps = [];
  let i = 0;
  let j = 0;
  while (i < a.length || j < b.length) {
    if (i < a.length && j < b.length && a[i] === b[j]) {
      steps.push({ type: "equal", a0: i, a1: ++i, b0: j, b1: ++j });
    } else if (j < b.length && (i === a.length || table[i * width + j + 1] > table[(i + 1) * width + j])) {
      steps.push({ type: "insert", a0: i, a1: i, b0: j, b1: ++j });
    } else {
      steps.push({ type: "delete", a0: i, a1: ++i, b0: j, b1: j });
    }
  }

  const runs = [];
  for (const step of steps) {
    const last = runs.at(-1);
    if (last?.type === step.type && last.a1 === step.a0 && last.b1 === step.b0) {
      last.a1 = step.a1;
      last.b1 = step.b1;
    } else {
      runs.push({ ...step });
    }
  }
  return runs;
}

function sliceText(tokens, start, end) {
  return tokens.slice(start, end).map((token) => token.text).join("");
}

function charRefinement(oldText, newText) {
  const oldChars = Array.from(oldText);
  const newChars = Array.from(newText);
  const runs = lcsOpcodes(oldChars, newChars);
  if (!runs) return [{ type: "delete", text: oldText }, { type: "insert", text: newText }].filter((x) => x.text);
  return runs.map((run) => ({
    type: run.type,
    text: run.type === "insert"
      ? newChars.slice(run.b0, run.b1).join("")
      : oldChars.slice(run.a0, run.a1).join(""),
  })).filter((part) => part.text);
}

function canonicalJson(value) {
  if (Array.isArray(value)) return `[${value.map(canonicalJson).join(",")}]`;
  if (value && typeof value === "object") {
    return `{${Object.keys(value).sort().map((key) => `${JSON.stringify(key)}:${canonicalJson(value[key])}`).join(",")}}`;
  }
  return JSON.stringify(value);
}

async function sha256Id(prefix, value) {
  return `${prefix}_${await sha256Hex(canonicalJson(value))}`;
}

function validateInputs(oldText, newText, metadata) {
  if (typeof oldText !== "string" || typeof newText !== "string") throw new TypeError("old and new prose must be strings");
  if (oldText.length > MAX_TEXT_LENGTH || newText.length > MAX_TEXT_LENGTH) {
    throw new RangeError(`prose exceeds maximum supported length of ${MAX_TEXT_LENGTH} UTF-16 code units`);
  }
  for (const field of ["source_ref", "source_revision", "prod_base"]) {
    if (typeof metadata?.[field] !== "string" || !metadata[field]) throw new TypeError(`${field} is required for stable operation identity`);
  }
}

function countLiteral(haystack, needle) {
  let count = 0;
  let at = 0;
  while (needle && (at = haystack.indexOf(needle, at)) !== -1) {
    count += 1;
    at += needle.length;
  }
  return count;
}

async function pairMoves(operations, oldText, newText, metadata) {
  const deletions = operations.filter((op) => op.kind === "delete");
  const insertions = operations.filter((op) => op.kind === "insert");
  for (const deletion of deletions) {
    const exact = deletion.old_text.trim();
    const words = tokenize(exact).filter((token) => token.type === "word").map((token) => token.text);
    if (words.length < MIN_MOVE_WORDS || exact.length < MIN_MOVE_CHARACTERS) continue;
    const distinctiveProbe = words.slice(0, 3).join(" ");
    if (countLiteral(oldText, distinctiveProbe) !== 1 || countLiteral(newText, distinctiveProbe) !== 1) continue;
    const candidates = insertions.filter((op) => op.new_text.trim() === exact && !op.move_pair_id);
    if (candidates.length !== 1) continue;
    const insertion = candidates[0];
    const pairId = await sha256Id("move", {
      ...metadata,
      text: exact,
      from: [deletion.base_start, deletion.base_end],
      to: [insertion.proposed_start, insertion.proposed_end],
    });
    deletion.move_pair_id = pairId;
    deletion.move_role = "from";
    deletion.decision_id = pairId;
    insertion.move_pair_id = pairId;
    insertion.move_role = "to";
    insertion.decision_id = pairId;
  }
}

export async function atomicProseDiff(oldText, newText, metadata, options = {}) {
  validateInputs(oldText, newText, metadata);
  const oldTokens = tokenize(oldText);
  const newTokens = tokenize(newText);
  if (oldTokens.length > MAX_TOKENS || newTokens.length > MAX_TOKENS) {
    throw new RangeError(`prose exceeds maximum supported token count of ${MAX_TOKENS}`);
  }
  const contextTokens = Math.max(0, Math.min(32, Number.isInteger(options.context_tokens)
    ? options.context_tokens : DEFAULT_CONTEXT_TOKENS));
  let runs = lcsOpcodes(oldTokens.map((token) => token.text), newTokens.map((token) => token.text));
  if (!runs) runs = [{ type: "delete", a0: 0, a1: oldTokens.length, b0: 0, b1: 0 },
    { type: "insert", a0: oldTokens.length, a1: oldTokens.length, b0: 0, b1: newTokens.length }];

  let groups = [];
  for (let index = 0; index < runs.length;) {
    if (runs[index].type === "equal") {
      groups.push({ ...runs[index] });
      index += 1;
      continue;
    }
    const change = { type: "change", a0: runs[index].a0, a1: runs[index].a1, b0: runs[index].b0, b1: runs[index].b1 };
    index += 1;
    while (index < runs.length && runs[index].type !== "equal") {
      change.a1 = runs[index].a1;
      change.b1 = runs[index].b1;
      index += 1;
    }
    groups.push(change);
  }
  // Whitespace is reviewable when it is the whole change, but it is a poor
  // alignment anchor inside a phrase replacement ("quick" -> "very swift").
  // Fold only whitespace-only equal islands between changes; never bridge an
  // unchanged word or punctuation span.
  const folded = [];
  for (let index = 0; index < groups.length; index += 1) {
    const current = groups[index];
    if (current.type === "change" && groups[index + 1]?.type === "equal" &&
        /^\s+$/u.test(sliceText(oldTokens, groups[index + 1].a0, groups[index + 1].a1)) &&
        groups[index + 2]?.type === "change") {
      folded.push({ type: "change", a0: current.a0, a1: groups[index + 2].a1,
        b0: current.b0, b1: groups[index + 2].b1 });
      index += 2;
    } else {
      folded.push(current);
    }
  }
  const tightened = [];
  const appendGroup = (group) => {
    const prior = tightened.at(-1);
    if (prior?.type === group.type && prior.a1 === group.a0 && prior.b1 === group.b0) {
      prior.a1 = group.a1;
      prior.b1 = group.b1;
    } else tightened.push(group);
  };
  for (const item of folded) {
    if (item.type === "equal") {
      appendGroup(item);
      continue;
    }
    let { a0, a1, b0, b1 } = item;
    const prefixA = a0;
    const prefixB = b0;
    while (a0 < a1 && b0 < b1 && oldTokens[a0].text === newTokens[b0].text) { a0 += 1; b0 += 1; }
    if (a0 > prefixA) appendGroup({ type: "equal", a0: prefixA, a1: a0, b0: prefixB, b1: b0 });
    const suffixA = a1;
    const suffixB = b1;
    while (a0 < a1 && b0 < b1 && oldTokens[a1 - 1].text === newTokens[b1 - 1].text) { a1 -= 1; b1 -= 1; }
    if (a0 < a1 || b0 < b1) appendGroup({ type: "change", a0, a1, b0, b1 });
    if (a1 < suffixA) appendGroup({ type: "equal", a0: a1, a1: suffixA, b0: b1, b1: suffixB });
  }
  groups = tightened;

  const operations = [];
  for (const group of groups.filter((item) => item.type === "change")) {
    const oldValue = sliceText(oldTokens, group.a0, group.a1);
    const newValue = sliceText(newTokens, group.b0, group.b1);
    const kind = oldValue && newValue ? "replace" : (oldValue ? "delete" : "insert");
    const baseStart = oldTokens[group.a0]?.start ?? oldText.length;
    const baseEnd = group.a1 > group.a0 ? oldTokens[group.a1 - 1].end : baseStart;
    const proposedStart = newTokens[group.b0]?.start ?? newText.length;
    const proposedEnd = group.b1 > group.b0 ? newTokens[group.b1 - 1].end : proposedStart;
    const identity = {
      source_ref: metadata.source_ref,
      source_revision: metadata.source_revision,
      prod_base: metadata.prod_base,
      base_range: [baseStart, baseEnd],
      proposed_range: [proposedStart, proposedEnd],
      old_text: oldValue,
      new_text: newValue,
    };
    const id = await sha256Id("op", identity);
    operations.push({
      id,
      decision_id: id,
      kind,
      ...identity,
      context_before: oldTokens.slice(Math.max(0, group.a0 - contextTokens), group.a0).map((token) => token.text),
      context_after: oldTokens.slice(group.a1, group.a1 + contextTokens).map((token) => token.text),
      refinement: kind === "replace" ? charRefinement(oldValue, newValue) : [],
    });
  }

  await pairMoves(operations, oldText, newText, metadata);
  const byRange = new Map(operations.map((operation) => [
    `${operation.base_range[0]}:${operation.base_range[1]}:${operation.proposed_range[0]}:${operation.proposed_range[1]}`,
    operation,
  ]));
  const segments = groups.map((group) => {
    if (group.type === "equal") return { type: "equal", text: sliceText(oldTokens, group.a0, group.a1) };
    const baseStart = oldTokens[group.a0]?.start ?? oldText.length;
    const baseEnd = group.a1 > group.a0 ? oldTokens[group.a1 - 1].end : baseStart;
    const proposedStart = newTokens[group.b0]?.start ?? newText.length;
    const proposedEnd = group.b1 > group.b0 ? newTokens[group.b1 - 1].end : proposedStart;
    const operation = byRange.get(`${baseStart}:${baseEnd}:${proposedStart}:${proposedEnd}`);
    return { type: "change", operation_id: operation.id, decision_id: operation.decision_id,
      old_text: operation.old_text, new_text: operation.new_text, kind: operation.kind,
      move_pair_id: operation.move_pair_id || null, refinement: operation.refinement };
  });
  return { version: 1, source_ref: metadata.source_ref, source_revision: metadata.source_revision,
    prod_base: metadata.prod_base, operations, segments };
}

// This helper returns only engine-owned semantic markup. Every prose byte is
// escaped here; DOM clients may instead render the same segments with textContent.
export function renderAtomicSegments(segments) {
  return segments.map((segment) => {
    if (segment.type === "equal") return escapeHtml(segment.text);
    const deleteLabel = segment.move_pair_id ? "Moved from" : "Deleted text";
    const insertLabel = segment.move_pair_id ? "Moved to" : "Added text";
    const deleted = segment.old_text ? `<del aria-label="${deleteLabel}">${escapeHtml(segment.old_text)}</del>` : "";
    const inserted = segment.new_text ? `<ins aria-label="${insertLabel}">${escapeHtml(segment.new_text)}</ins>` : "";
    return deleted + inserted;
  }).join("");
}

export const ATOMIC_DIFF_LIMITS = Object.freeze({
  max_text_length: MAX_TEXT_LENGTH,
  max_tokens: MAX_TOKENS,
  max_matrix_cells: MAX_MATRIX_CELLS,
});
