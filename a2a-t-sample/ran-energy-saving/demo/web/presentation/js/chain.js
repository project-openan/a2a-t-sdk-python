/** The correction chain shared by one operator's blocked attempt and its retries.
 *
 * Every attempt keeps its own run, conversation and trace. The chain id is the one identity that
 * survives the retry, so Langfuse groups the attempts under one session and the evidence can be
 * followed from the blocked request to the one that was actually sent.
 */

/** Session key of the pending chain; matches the presentation's other session-scoped state. */
export const CHAIN_KEY = "a2at-ran-correction-chain";

/** Build the chain identity of one attempt from the ``POST /api/runs`` response. */
export function identity(runId, chainId, scenario, protocol) {
  if (typeof runId !== "string" || !runId) return null;
  return {
    runId,
    // A server that predates the chain id falls back to the conversation, which it already
    // returns; the retry then still names a parent the operator can follow.
    chainId: typeof chainId === "string" && chainId ? chainId : runId,
    scenario: typeof scenario === "string" ? scenario : null,
    protocol: typeof protocol === "string" ? protocol : null,
  };
}

/** Return the pending chain when it still belongs to the example on the stage. */
export function pending(chain, scenario, protocol) {
  if (!chain) return null;
  if (chain.scenario !== scenario || chain.protocol !== protocol) return null;
  return chain;
}

/** Persist one chain so a page reload still retries the same blocked attempt. */
export function save(storage, chain) {
  try {
    storage.setItem(CHAIN_KEY, JSON.stringify(chain));
  } catch {
    // A browser without storage still retries within this page.
  }
}

/** Drop the persisted chain; a new example, a reset or a run that reached the server. */
export function clear(storage) {
  try {
    storage.removeItem(CHAIN_KEY);
  } catch {
    // Ignore storage failures; the caller clears its in-memory value.
  }
}

/** Restore the chain a reload left behind, or ``null`` when it is missing or unusable. */
export function restore(storage) {
  try {
    const raw = storage.getItem(CHAIN_KEY);
    if (!raw) return null;
    const parsed = JSON.parse(raw);
    if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) return null;
    if (typeof parsed.runId !== "string" || !parsed.runId) return null;
    if (typeof parsed.chainId !== "string" || !parsed.chainId) return null;
    return {
      runId: parsed.runId,
      chainId: parsed.chainId,
      scenario: typeof parsed.scenario === "string" ? parsed.scenario : null,
      protocol: typeof parsed.protocol === "string" ? parsed.protocol : null,
    };
  } catch {
    return null;
  }
}
