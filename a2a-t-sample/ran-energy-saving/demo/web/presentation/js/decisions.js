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
  // A failed recommendation is published as unavailable; the engineer must still be able to
  // decide, so that frame opens the same card without a recommended action.
  const recommendationUnavailable = decision.recommendationUnavailable === true;
  if (state.mode === "confirm" && !recommendation && !recommendationUnavailable) return null;
  const regionOptions = areaOptions(decision.proposal?.options, copy);
  const missingItems = Array.isArray(decision.missingItems)
    ? decision.missingItems.filter((item) => typeof item === "string")
    : [];
  const proposalText =
    proposalItemText(decision.proposal?.content, "region") ||
    proposalLine(copy, state.scenario, round) ||
    firstLine(decision.proposal?.content) ||
    copy.negotiation.generic;
  // An information proposal is a vendor question: show what it actually asked for instead of the
  // generic scenario line. Without parsed items the proposal text is still the fallback.
  const text =
    kind === "information" && missingItems.length > 0
      ? format(copy.decision.askedItems, { items: missingItems.join(", ") })
      : proposalText;

  // A deterministic recommendation is the only case where the card offers a recommended action.
  // Without it the RAN Agent is asking for information (or the OSS Agent could not decide), so the
  // card drops to its escape actions and lets the manual area carry the answer.
  const hasRecommendation = Boolean(recommendation);
  const options = [];
  if (hasRecommendation) {
    options.push({
      action: "confirm",
      label:
        kind === "information" && copy.decision.confirmInformation
          ? copy.decision.confirmInformation
          : confirmLabel(copy, recommendedAction),
      primary: true,
    });
    if (kind === "information") {
      // The NMS asked a question, not for an accept: the only shortcuts are the recommendation
      // (confirm) and the escape. The operator's value still goes through the manual box.
      if (recommendedAction !== "reject") {
        options.push({ action: "reject", label: copy.decision.reject, danger: true });
      }
    } else {
      if (recommendedAction !== "accept") {
        options.push({ action: "accept", label: copy.decision.acceptNms });
      }
      // Keep a counter path in every target/feasibility case: it lets the engineer disagree with an
      // accept recommendation and edit a recommended counter before sending it.
      options.push({ action: "counter", label: copy.decision.editCounter });
      if (recommendedAction !== "reject") {
        options.push({ action: "reject", label: copy.decision.reject, danger: true });
      }
    }
  } else if (kind === "information") {
    // There is no RAN proposal to accept: the engineer must supply the value, so only the reject
    // escape action remains beside the manual editor.
    options.push({ action: "reject", label: copy.decision.reject, danger: true });
  } else {
    options.push({ action: "accept", label: copy.decision.acceptNms, primary: true });
    options.push({ action: "reject", label: copy.decision.reject, danger: true });
  }

  const detail = recommendation
    ? `${format(copy.decision.recommendation, {
        action: actionLabel(copy, recommendedAction),
      })}${recommendation.rationale ? ` — ${recommendation.rationale}` : ""}`
    : recommendationUnavailable
      ? `${copy.decision.recommendationUnavailable}${
          decision.recommendationError ? ` — ${decision.recommendationError}` : ""
        }`
      : copy.decision.question;

  return {
    type: "decision",
    kicker: copy.decision.kicker,
    title: format(copy.decision.title, { round, maxRounds }),
    text,
    detail,
    kind,
    hasRecommendation,
    recommendedAction,
    recommendation,
    // The confirm button sends these values verbatim; show them instead of hiding the concrete
    // answer behind an action label.
    recommendationFields: recommendationFields(recommendation),
    missingItems,
    manualExample: manualExampleFor(copy, missingItems),
    // Without a deterministic recommendation the manual area is the primary answer surface, so it
    // opens by default; with one, the action row leads and the manual area stays collapsed.
    manualOpen: !hasRecommendation,
    counterText: recommendation?.counter_proposal || "",
    regionOptions,
    requestedRegion: decision.proposal?.options?.requested || "",
    options,
  };
}

/** List the concrete values a recommendation would send, for the confirmation card. */
function recommendationFields(recommendation) {
  const filled = isObject(recommendation?.filled_params) ? recommendation.filled_params : null;
  if (filled === null) return [];
  return Object.entries(filled)
    .map(([name, value]) => ({ name, value: fieldText(value) }))
    .filter((field) => field.value !== "");
}

/** Render one recommended field value without dropping list/object values. */
function fieldText(value) {
  if (Array.isArray(value)) return value.map((item) => fieldText(item)).filter(Boolean).join(", ");
  if (isObject(value)) return JSON.stringify(value);
  if (value === null || value === undefined) return "";
  return String(value);
}

/** Label the primary button with the action it actually sends. */
function confirmLabel(copy, action) {
  if (action === "counter") return copy.decision.confirmCounter;
  if (action === "reject") return copy.decision.confirmReject;
  if (action === "abort") return copy.decision.confirmAbort;
  return copy.decision.confirmAccept;
}

/** Pick the manual example that matches the first asked item, or the generic sentence. */
function manualExampleFor(copy, missingItems) {
  const examples = copy.decision.manualExamples || {};
  const fallback = examples.default || copy.decision.manualExample || "";
  for (const item of missingItems) {
    const key = manualExampleKey(item);
    if (key && examples[key]) return examples[key];
  }
  return fallback;
}

/** Map one asked-item label to a bounded example key. */
function manualExampleKey(item) {
  const text = String(item).toLowerCase();
  if (/region|区域/.test(text)) return "region";
  if (/throughput|吞吐/.test(text)) return "throughput";
  if (/rate|速率|带宽/.test(text)) return "rate";
  return "";
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
      options: [
        { action: "approve", label: copy.approval.approve, primary: true },
        { action: "reject", label: copy.approval.reject, danger: true },
      ],
    };
  }
  // No local approval gate means no card: a --nms run has no /approvals route, and a bare
  // INPUT_REQUIRED status is not an approval. The server approval span sets the gate when it is
  // real, so a split deployment still shows the card.
  if (state.approvalGate !== true) return null;
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
      // The trace records where the concrete values came from: the AI suggestion the operator
      // confirmed (even after picking a served area) is not the same fact as an operator answer.
      answerSource: areaParams === null ? "ai_recommendation" : "operator_edited",
    });
  }
  if (action === "counter") {
    return clean({
      action: "counter",
      rationale: copy.decision.counterRationale,
      counter_proposal: (counterText || "").trim() || card.counterText,
      answerSource: "operator_edited",
    });
  }
  if (action === "reject") {
    return { action: "reject", rationale: copy.decision.rejectRationale, answerSource: "operator" };
  }
  if (action === "abort") {
    return { action: "abort", rationale: copy.decision.abortRationale, answerSource: "operator" };
  }
  return clean({
    action: "accept",
    rationale: copy.decision.acceptRationale,
    filled_params: areaParams,
    answerSource: "operator",
  });
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
