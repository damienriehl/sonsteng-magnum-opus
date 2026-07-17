// providers/registry.js — the provider table. One common interface:
//   complete({ system, messages, maxTokens, providerCfg }) -> { ok, text, usage }
// providerCfg = { apiKey, model, jsonMode? }. system = null | string |
// { prefix, tail } (chat; prefix is the cacheable Segment A — only the Anthropic
// adapter actually caches it).

import * as anthropic from "./anthropic.js";
import * as openai from "./openai.js";
import * as google from "./google.js";

const PROVIDERS = { anthropic, openai, google };

export const PROVIDER_NAMES = Object.keys(PROVIDERS);

export function getProvider(name) {
  return PROVIDERS[name] || null;
}
