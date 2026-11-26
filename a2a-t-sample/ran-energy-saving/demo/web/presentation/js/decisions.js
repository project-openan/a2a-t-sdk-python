/** The human moments of the demo: intent review, validation and the negotiation decision. */

import { format } from "../copy.js";

/** Build approval 1: the engineer confirms the structured intent before the Task-T is sent. */
export function intentCard(state, copy) {
  const review = state.intentReview;
  if (review === null || review.resolved || state.finished) return null;
  return {
    type: "intent",
    kicker: copy.intentReview.kicker,
    title: copy.intentReview.title,
    text: copy.intentReview.text,
    detail: copy.intentReview.detail,
    validation: state.validation,
    options: [
      { action: "approve", label: copy.intentReview.confirm, primary: true },
      { action: "edit", label: copy.intentReview.edit },
      { action: "cancel", label: copy.intentReview.cancel, danger: true },
    ],
  };
}

/** Build the pre-send validation failure card, or ``null`` when the run was not blocked. */
export function validationCard(state, copy) {
  if (state.finalState !== "input_invalid" || state.validationDismissed) return null;
  const validation = state.validation;
  if (!isObject(validation) || validation.blocking !== true) return null;
  return {
    type: "validation",
    kicker: copy.validation.kicker,
    title: copy.validation.title,
    text: copy.validation.text,
    detail: copy.validation.retryHint,
    validation,
    rawRequest: state.rawRequest || state.userRequest || state.intentText || "",
    options: [
      { action: "retry", label: copy.validation.retry, primary: true },
      { action: "dismiss", label: copy.validation.dismiss },
    ],
  };
}

/** Build the one negotiation card: server prompt, one answer box, send and More actions. */
export function decisionCard(state, copy) {
  const decision = activeDecision(state);
  if (decision === null) return null;
  const round = decision.round ?? decision.proposal?.round ?? 1;
  const maxRounds = decision.maxRounds ?? decision.proposal?.max_rounds ?? 1;
  const kind = decision.proposal?.kind || "";
  const protocol = state.protocol || "a2a-t";
  const content =
    typeof decision.proposal?.content === "string" ? decision.proposal.content.trim() : "";
  const pure = protocol === "pure";
  const prefilled = pure && (kind === "target" || kind === "feasibility");
  return {
    type: "decision",
    negotiationId: decision.proposal?.negotiation_id || decision.proposal?.context_id || "",
    round,
    maxRounds,
    kind,
    protocol,
    kicker: copy.decision.kicker,
    title: format(copy.decision.title, { round, maxRounds }),
    text: content || copy.negotiation.generic,
    detail: null,
    // An information question asks for a value; a pure target/feasibility proposal is prefilled,
    // so clearing it is an explicit edit that must still be sent.
    inputRequired: kind === "information" || prefilled,
    prefill: prefilled ? prefillFromProposal(content) : "",
    options: [
      { action: "reject", label: copy.decision.reject, danger: true },
      { action: "abort", label: copy.decision.abort, danger: true },
    ],
  };
}

/** Return the pending decision of either the early or the answered frame. */
function activeDecision(state) {
  if (state.finished) return null;
  if (state.decision !== null && !state.decision.resolved) return state.decision;
  if (state.decisionPending !== null && !state.decisionPending.resolved) return state.decisionPending;
  return null;
}

/** Prefill a pure target/feasibility answer with the proposal's own segments, else its text. */
function prefillFromProposal(content) {
  const segments = String(content || "").match(/\{[^{}]*\d+\s*Mbps[^{}]*\}/gi) || [];
  return segments.length > 0 ? segments.join(", ") : String(content || "").trim();
}

/** Build the decision payload of one Send / Reject / Abort click. */
export function decisionPayload(card, action, copy, text = "") {
  const answer = String(text || "").trim();
  if (action === "reject") {
    return pureTerminal(
      card,
      {
        action: "reject",
        rationale: copy.decision.rejectRationale,
        answerSource: "operator",
      },
      copy.decision.pureRejectText,
    );
  }
  if (action === "abort") {
    return pureTerminal(
      card,
      {
        action: "abort",
        rationale: copy.decision.abortRationale,
        answerSource: "operator",
      },
      copy.decision.pureAbortText,
    );
  }
  const payload = {
    action: "accept",
    rationale: answer || copy.decision.acceptRationale,
    answerSource: "operator",
  };
  if (answer) payload.answerText = answer;
  if (answer && card.kind === "target") payload.confirmed_intent = answer;
  if (answer && card.kind === "feasibility") payload.feasibility_summary = answer;
  return payload;
}

/** A pure run sends one plain text; a fixed refusal is that text, an A2A-T run uses the action. */
function pureTerminal(card, payload, text) {
  if (card.protocol === "pure" && text) payload.answerText = text;
  return payload;
}

function isObject(value) {
  return value !== null && typeof value === "object" && !Array.isArray(value);
}
