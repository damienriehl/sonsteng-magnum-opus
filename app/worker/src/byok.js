// byok.js — PURE resolution of "which upstream do we call, on whose key?"
// (node-testable; no cloudflare imports, no I/O, and — critically — no logging:
// the resolved object carries the user's api_key and must never be logged).
//
// Rules (API-CONTRACTS.md §BYOK):
//  * A request MAY carry byok: { provider, api_key, model? }.
//  * provider ∈ {anthropic, openai, google}; model must be in that provider's
//    allowlist (env MODEL_ALLOW_*), defaulting to env MODEL_DEFAULT_* if absent.
//  * BYOK mode SKIPS the spend counter (their money) but still enforces turn
//    caps, turn_id dedupe, session validity, input caps, and the debrief guard —
//    the router passes skipBudget:true to the DO.
//  * No byok + no hosted ANTHROPIC_API_KEY -> the typed no_hosted_key error.

import { PROVIDER_NAMES } from "./providers/registry.js";

function csv(str) {
  return (str || "").split(",").map((s) => s.trim()).filter(Boolean);
}

// Per-provider defaults + allowlists, read from env vars (config, not code).
export function providerModelConfig(env) {
  return {
    anthropic: {
      default: env.MODEL_DEFAULT_ANTHROPIC || "claude-haiku-4-5",
      allow: csv(env.MODEL_ALLOW_ANTHROPIC || "claude-haiku-4-5"),
    },
    openai: {
      default: env.MODEL_DEFAULT_OPENAI || "gpt-4o-mini",
      allow: csv(env.MODEL_ALLOW_OPENAI || "gpt-4o-mini"),
    },
    google: {
      default: env.MODEL_DEFAULT_GOOGLE || "gemini-2.0-flash",
      allow: csv(env.MODEL_ALLOW_GOOGLE || "gemini-2.0-flash"),
    },
  };
}

// Resolve the upstream for one request. `byok` is the (untrusted) body field.
// Returns one of:
//   { ok:true, mode:"byok",   provider, apiKey, model, skipBudget:true  }
//   { ok:true, mode:"hosted", provider:"anthropic", apiKey, model, skipBudget:false }
//   { ok:false, code, message, status }
export function resolveUpstream(env, byok) {
  const models = providerModelConfig(env);

  if (byok != null) {
    if (typeof byok !== "object" || Array.isArray(byok)) {
      return { ok: false, code: "validation_error", message: "byok must be an object.", status: 400 };
    }
    const provider = byok.provider;
    if (!PROVIDER_NAMES.includes(provider)) {
      return {
        ok: false, code: "validation_error", status: 400,
        message: "byok.provider must be one of: " + PROVIDER_NAMES.join(", ") + ".",
      };
    }
    if (typeof byok.api_key !== "string" || byok.api_key.trim().length < 8) {
      return { ok: false, code: "validation_error", message: "byok.api_key is required.", status: 400 };
    }
    const cfg = models[provider];
    let model = cfg.default;
    if (byok.model != null) {
      if (typeof byok.model !== "string" || !cfg.allow.includes(byok.model)) {
        return {
          ok: false, code: "validation_error", status: 400,
          message: "byok.model must be one of: " + cfg.allow.join(", ") + " (or omit for " + cfg.default + ").",
        };
      }
      model = byok.model;
    }
    return { ok: true, mode: "byok", provider, apiKey: byok.api_key.trim(), model, skipBudget: true };
  }

  // Hosted demo path — only if this deployment actually has a key.
  if (!env.ANTHROPIC_API_KEY) {
    return {
      ok: false, code: "no_hosted_key", status: 503,
      message: "This deployment has no hosted demo key. Add your own API key to interview the client.",
    };
  }
  return {
    ok: true, mode: "hosted", provider: "anthropic",
    apiKey: env.ANTHROPIC_API_KEY, model: models.anthropic.default, skipBudget: false,
  };
}

// Resolve the explicitly supplied grader set for a formative memo assessment.
// One credential always means one grader: the hosted key and the legacy `byok`
// block must never be fanned out into a pretend panel. A multi-grader run
// therefore requires one explicit BYOK block for each of the three supported,
// distinct providers. This keeps the code-computed median integral.
//
// The returned graders contain live credentials and are request-lifetime only.
// Callers must pass only provider/model/mode provenance into result data.
export function resolvePanelUpstreams(env, { byok, byokPanel } = {}) {
  if (byok != null && byokPanel != null) {
    return {
      ok: false, code: "validation_error", status: 400,
      message: "Supply either byok or byok_panel, not both.",
    };
  }

  if (byokPanel == null) {
    const grader = resolveUpstream(env, byok);
    if (!grader.ok) return grader;
    return { ok: true, graders: [grader] };
  }

  if (!Array.isArray(byokPanel) || byokPanel.length !== PROVIDER_NAMES.length) {
    return {
      ok: false, code: "validation_error", status: 400,
      message: `byok_panel must contain exactly ${PROVIDER_NAMES.length} explicit provider credentials.`,
    };
  }

  const graders = [];
  for (const byokEntry of byokPanel) {
    if (byokEntry == null) {
      return {
        ok: false, code: "validation_error", status: 400,
        message: "Every byok_panel entry must supply its own provider credential.",
      };
    }
    const grader = resolveUpstream(env, byokEntry);
    if (!grader.ok) return grader;
    // Each entry is explicit BYOK input. This also prevents a malformed panel
    // entry from silently falling through to the hosted key.
    if (grader.mode !== "byok") {
      return {
        ok: false, code: "validation_error", status: 400,
        message: "Every byok_panel entry must supply its own provider credential.",
      };
    }
    graders.push(grader);
  }

  const providers = new Set(graders.map((grader) => grader.provider));
  if (providers.size !== graders.length) {
    return {
      ok: false, code: "validation_error", status: 400,
      message: "byok_panel graders must use distinct providers.",
    };
  }
  return { ok: true, graders };
}
