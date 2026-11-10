/** The correction chain shared by one operator's blocked attempt and its retries.
 *
 * Every attempt keeps its own run and conversation. The chain id groups the attempts in one
 * Langfuse session, and the trace id is the blocked attempt's vendor-facing id: the corrected
 * retry reuses it so both attempts land in one workflow trace with a single neutral root.
 */

/** Session key of the pending chain; matches the presentation's other session-scoped state. */
export const CHAIN_KEY = "a2at-ran-correction-chain";

const HEX32 = /^[0-9a-f]{32}$/;

/** Return a valid lowercase 32-hex trace id, preferring the server value over the run id. */
function traceIdOf(value, fallback) {
  const raw = typeof value === "string" ? value.trim().toLowerCase() : "";
  if (HEX32.test(raw) && raw !== "0".repeat(32)) return raw;
  const alt = typeof fallback === "string" ? fallback.trim().toLowerCase() : "";
  return HEX32.test(alt) && alt !== "0".repeat(32) ? alt : "";
}

/** Build the chain identity of one attempt from the ``POST /api/runs`` response. */
export function identity(runId, chainId, scenario, protocol, traceId, runMode) {
  if (typeof runId !== "string" || !runId) return null;
  return {
    runId,
    // A server that predates the chain id falls back to the conversation, which it already
    // returns; the retry then still names a parent the operator can follow.
    chainId: typeof chainId === "string" && chainId ? chainId : runId,
    // The server defaults the first attempt's trace to its run id; persisting that default lets
    // a reload still pin the retry to the blocked attempt's trace.
    traceId: traceIdOf(traceId, runId),
    scenario: typeof scenario === "string" ? scenario : null,
    protocol: typeof protocol === "string" ? protocol : null,
    // A Modify is a different workflow; it must not inherit a blocked create attempt's trace.
    runMode: runMode === "modify" ? "modify" : "create",
  };
}

/** Return the pending chain when it still belongs to the example and run mode on the stage. */
export function pending(chain, scenario, protocol, runMode) {
  if (!chain) return null;
  if (chain.scenario !== scenario || chain.protocol !== protocol) return null;
  const wanted = runMode === "modify" ? "modify" : "create";
  if ((chain.runMode || "create") !== wanted) return null;
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
      // Storage written before the trace id existed falls back to the run id, which the server
      // used as the first attempt's trace.
      traceId: traceIdOf(parsed.traceId, parsed.runId),
      scenario: typeof parsed.scenario === "string" ? parsed.scenario : null,
      protocol: typeof parsed.protocol === "string" ? parsed.protocol : null,
      runMode: parsed.runMode === "modify" ? "modify" : "create",
    };
  } catch {
    return null;
  }
}
