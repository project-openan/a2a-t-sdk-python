/** The three human moments of the demo: intent review, negotiation decision and MML review. */

import { actionLabel, format, proposalLine } from "../copy.js";

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
    remaining: remainingSeconds(review.requestedAt, review.timeoutSeconds),
    options: [
      { action: "approve", label: copy.intentReview.confirm, primary: true },
      { action: "edit", label: copy.intentReview.edit },
      { action: "cancel", label: copy.intentReview.cancel, danger: true },
    ],
  };
}

/** Build the negotiation decision card, or ``null`` when no decision is pending. */
export function decisionCard(state, copy) {
  const decision = state.decision;
  if (decision === null || decision.resolved || state.finished) return null;
  const round = decision.round ?? decision.proposal?.round ?? 1;
  const maxRounds = decision.maxRounds ?? decision.proposal?.max_rounds ?? 1;
  const kind = decision.proposal?.kind || "";
  const recommendation = decision.recommendation;
  const recommendedAction = recommendation?.action || null;
  // In confirm mode the OSS Agent is still thinking: do not offer a decision before its
  // recommendation arrives, otherwise the engineer could answer without the agent's input.
  if (state.mode === "confirm" && !recommendation) return null;
  const regionOptions = areaOptions(decision.proposal?.options, copy);
  const proposalText =
    proposalItemText(decision.proposal?.content, "region") ||
    proposalLine(copy, state.scenario, round) ||
    firstLine(decision.proposal?.content) ||
    copy.negotiation.generic;

  const options = [];
  if (recommendedAction) {
    options.push({ action: "confirm", label: copy.decision.confirm, primary: true });
  } else {
    options.push({ action: "accept", label: copy.decision.acceptNms, primary: true });
  }
  if (recommendedAction && recommendedAction !== "accept") {
    options.push({ action: "accept", label: copy.decision.acceptNms });
    options.push({ action: "counter", label: copy.decision.editCounter });
  }
  options.push({ action: "reject", label: copy.decision.reject, danger: true });

  return {
    type: "decision",
    kicker: copy.decision.kicker,
    title: format(copy.decision.title, { round, maxRounds }),
    text: proposalText,
    detail: recommendation
      ? `${format(copy.decision.recommendation, {
          action: actionLabel(copy, recommendedAction),
        })}${recommendation.rationale ? ` — ${recommendation.rationale}` : ""}`
      : copy.decision.question,
    kind,
    recommendedAction,
    recommendation,
    counterText: recommendation?.counter_proposal || "",
    regionOptions,
    requestedRegion: decision.proposal?.options?.requested || "",
    options,
    remaining: remainingSeconds(decision.requestedAt, decision.timeoutSeconds),
  };
}

/** Build approval 2 (the MIE MML review), or ``null`` when no review is pending. */
export function approvalCard(state, copy) {
  if (
    state.protocol === "pure" &&
    state.mieWaiting &&
    state.mieApproval !== null &&
    state.mieResolved === null &&
    !state.finished &&
    state.status === "running"
  ) {
    const approval = isObject(state.mieApproval) ? state.mieApproval : {};
    const plan = isObject(approval.plan) ? approval.plan : {};
    const cell = plan.cellId || "—";
    const window = plan.window || "—";
    const rate = plan.rate || "—";
    return {
      type: "approval",
      pure: true,
      taskId: approval.taskId || null,
      kicker: copy.pure?.mieKicker || copy.approval.kicker,
      title: copy.pure?.mieTitle || copy.approval.title,
      text: format(copy.approval.detail, { cell, window, rate }),
      detail: copy.pure?.mieWait || copy.approval.waiting,
      mml: null,
      policy: copy.pure?.mieNote || copy.approval.note,
      remaining: remainingSeconds(approval.requestedAt, approval.timeoutSeconds),
      options: [
        { action: "approve", label: copy.approval.approve, primary: true },
        { action: "reject", label: copy.approval.reject, danger: true },
      ],
    };
  }
  // A gate that is still marked waiting when the run has already ended must not keep the page
  // stuck on the card: the ending card has to take over.
  if (state.approvalState !== "waiting" || state.finished || state.status !== "running") return null;
  const approval = state.approval || {};
  const cell = approval.cellId || "—";
  const window = approval.window || "—";
  const rate = approval.rate || "—";
  const detail = approval.generic
    ? copy.approval.waiting
    : format(copy.approval.detail, { cell, window, rate });
  return {
    type: "approval",
    kicker: copy.approval.kicker,
    title: copy.approval.title,
    text: detail,
    detail: null,
    // Review is one step: the command stays visible on one line; the policy note is already a
    // stage bubble, so the card stays compact enough to keep the whole stage on screen.
    mml: {
      label: copy.approval.sample,
      text: copy.approval.mml(cell, window, String(rate).replace("%", "")),
    },
    policy: null,
    remaining: remainingSeconds(approval.requestedAt, approval.timeoutSeconds),
    options: [
      { action: "approve", label: copy.approval.approve, primary: true },
      { action: "reject", label: copy.approval.reject, danger: true },
    ],
  };
}

/** Build the ``NegotiationDecision`` payload of one click. */
export function decisionPayload(card, action, copy, counterText, selectedArea) {
  const areaParams = selectedArea ? { region: selectedArea } : null;
  if (action === "confirm" && card.recommendation) {
    const mergeArea =
      areaParams !== null && card.recommendation.action === "accept"
        ? { ...(card.recommendation.filled_params || {}), ...areaParams }
        : card.recommendation.filled_params ?? null;
    return clean({
      action: card.recommendation.action,
      rationale: card.recommendation.rationale || copy.decision.acceptRationale,
      filled_params: mergeArea,
      counter_proposal: card.recommendation.counter_proposal ?? null,
      counter_params: card.recommendation.counter_params ?? null,
      confirmed_intent: card.recommendation.confirmed_intent ?? null,
      feasibility_summary: card.recommendation.feasibility_summary ?? null,
    });
  }
  if (action === "counter") {
    return clean({
      action: "counter",
      rationale: copy.decision.counterRationale,
      counter_proposal: (counterText || "").trim() || card.counterText,
    });
  }
  if (action === "reject") {
    return { action: "reject", rationale: copy.decision.rejectRationale };
  }
  if (action === "abort") {
    return { action: "abort", rationale: copy.decision.abortRationale };
  }
  return clean({
    action: "accept",
    rationale: copy.decision.acceptRationale,
    filled_params: areaParams,
  });
}

/** Seconds left in a human window, or ``null`` when the backend did not send a budget. */
export function remainingSeconds(requestedAt, timeoutSeconds) {
  const timeout = Number(timeoutSeconds);
  if (!Number.isFinite(timeout) || timeout <= 0) return null;
  const start = Date.parse(requestedAt || "");
  if (!Number.isFinite(start)) return Math.round(timeout);
  return Math.max(0, Math.round(timeout - (Date.now() - start) / 1000));
}

/** Format seconds as ``m:ss``. */
export function clock(seconds) {
  if (seconds === null || seconds === undefined) return "";
  const value = Math.max(0, Math.round(seconds));
  return `${Math.floor(value / 60)}:${String(value % 60).padStart(2, "0")}`;
}

function firstLine(content) {
  if (typeof content !== "string") return "";
  const line = content
    .split(/\r?\n/)
    .map((item) => item.replace(/^#+\s*/, "").replace(/\*\*/g, "").trim())
    .find((item) => item !== "");
  return line ? line.slice(0, 180) : "";
}

/** Return the value of one numbered item of a rendered information proposal. */
function proposalItemText(content, field) {
  if (typeof content !== "string") return "";
  const pattern = new RegExp(`^\\s*(?:\\d+\\.\\s*)?${field}\\s*:\\s*(.+)$`, "im");
  const match = content.match(pattern);
  return match ? match[1].trim() : "";
}

/** Build the localized selectable areas of one information proposal. */
function areaOptions(options, copy) {
  const entries = Array.isArray(options?.options) ? options.options : [];
  return entries
    .map((option) => {
      const labels = isObject(option?.labels) ? option.labels : {};
      const value = labels[copy.lang] || option?.value || "";
      return {
        value,
        label: value,
        score: Number(option?.score) || 0,
        recommended: option?.recommended === true,
      };
    })
    .filter((option) => option.value !== "");
}

function clean(payload) {
  const result = {};
  for (const [key, value] of Object.entries(payload)) {
    if (value !== null && value !== undefined && value !== "") result[key] = value;
  }
  return result;
}

function isObject(value) {
  return value !== null && typeof value === "object" && !Array.isArray(value);
}
