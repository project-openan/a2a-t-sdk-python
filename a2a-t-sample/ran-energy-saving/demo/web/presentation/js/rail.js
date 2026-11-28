/** Negotiation panel header: the round dots and the status line. */

import { format } from "../copy.js";
import { el } from "./dom.js";
import { negotiationExpected, resolutionOf, roundInfo, stageOf } from "./state.js";

export function createRail(els, getCopy) {
  // The page renders on every frame; only touch the DOM when the header actually changes.
  let dotsKey = null;
  let statusTextKey = null;

  function render(state) {
    const copy = getCopy();
    const { round, maxRounds } = roundInfo(state);
    const nextDotsKey = `${round}/${maxRounds}`;
    if (nextDotsKey !== dotsKey) {
      els.roundDots.replaceChildren();
      for (let index = 1; index <= maxRounds; index += 1) {
        const dot = el("span", "round-dot");
        dot.dataset.filled = String(index <= round);
        els.roundDots.append(dot);
      }
      dotsKey = nextDotsKey;
    }

    const status = statusText(state, copy);
    if (status !== statusTextKey) {
      els.status.textContent = status;
      statusTextKey = status;
    }
  }

  return { render };
}

function statusText(state, copy) {
  const stage = stageOf(state);
  const resolution = resolutionOf(state);
  const { round, maxRounds } = roundInfo(state);
  if (state.negotiation.length === 0 && !negotiationExpected(state) && stage !== "idle") {
    return copy.negotiation.notNeededShort;
  }
  if (state.negotiation.length === 0 && (stage === "executing" || stage === "done")) {
    return copy.negotiation.statusNotNeeded;
  }
  if (resolution === "rejected" || resolution === "aborted" || stage === "ended") {
    return copy.negotiation.statusEnded;
  }
  if (stage === "done" || stage === "executing" || resolution === "accepted") {
    return copy.negotiation.statusAgreed;
  }
  if (stage === "negotiating") {
    return maxRounds > 0
      ? format(copy.negotiation.statusNegotiating, {
          round: copy.round(Math.max(round, 1), maxRounds),
        })
      : copy.negotiation.statusWaiting;
  }
  if (stage === "canceled" || stage === "failed" || stage === "notApplied") {
    return copy.negotiation.statusEnded;
  }
  return copy.negotiation.statusIdle;
}
