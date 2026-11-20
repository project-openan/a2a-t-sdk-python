/** Protocol comparison: the same request run over A2A-T and over plain A2A.
 *
 * Nothing here fabricates a run: the card renders the recorded outcome of the two real runs kept
 * in the browser. Only the two protocol rows that describe a capability (structured intent and
 * template validation) are fixed text, because they describe the protocol, not a run.
 */

/** Build the compact record of one finished run. */
export function buildRecord(state) {
  const protocol = state.protocol === "plain" ? "plain" : "a2at";
  const rounds =
    protocol === "plain"
      ? 0
      : state.negotiation.reduce((max, message) => Math.max(max, message.round || 0), 0);
  return {
    protocol,
    scenario: state.scenario || "",
    finalState: state.finalState || "",
    rounds,
    resolution: resolutionOf(state),
    reportRate: reportRate(state.report),
    savedKwh: savedKwh(state.report),
    detail: state.lastStatusText || "",
    at: new Date().toISOString(),
  };
}

/** Return the comparison rows of one scenario; either record may be ``null``. */
export function compareRows(copy, a2at, plain) {
  return [
    {
      label: copy.compare.intent,
      a2at: a2at ? copy.compare.intentA2at : copy.compare.notRun,
      plain: plain ? copy.compare.intentPlain : copy.compare.notRun,
    },
    {
      label: copy.compare.negotiation,
      a2at: negotiationText(copy, a2at),
      plain: negotiationText(copy, plain),
    },
    {
      label: copy.compare.validation,
      a2at: a2at ? copy.compare.validationA2at : copy.compare.notRun,
      plain: plain ? copy.compare.validationPlain : copy.compare.notRun,
    },
    {
      label: copy.compare.outcome,
      a2at: outcomeText(copy, a2at),
      plain: outcomeText(copy, plain),
    },
  ];
}

/** Return the outcome line of one recorded run. */
export function outcomeText(copy, record) {
  if (!record) return copy.compare.notRun;
  if (record.finalState === "task_completed") {
    return record.reportRate !== null
      ? format(copy.compare.outcomeApplied, { rate: Math.round(record.reportRate) })
      : copy.compare.outcomeCompleted;
  }
  if (record.finalState === "task_rejected") return copy.compare.outcomeRejected;
  if (record.finalState === "intent_not_confirmed") return copy.compare.outcomeIntentNotConfirmed;
  if (record.finalState === "input_invalid") return copy.compare.outcomeInputInvalid;
  if (record.finalState === "decision_ungrounded") return copy.compare.outcomeUngrounded;
  if (record.finalState === "no_task_negotiation_ended") return copy.compare.outcomeNegotiationEnded;
  if (record.finalState === "no_task_negotiation_aborted") return copy.compare.outcomeNegotiationAborted;
  if (record.finalState === "task_failed") return copy.compare.outcomeFailed;
  return copy.compare.notRun;
}

function negotiationText(copy, record) {
  if (!record) return copy.compare.notRun;
  if (record.protocol === "plain") return copy.compare.notApplicable;
  if (record.rounds === 0) return copy.compare.notTriggered;
  return format(copy.compare.negotiationRounds, {
    rounds: record.rounds,
    resolution: resolutionLabel(copy, record.resolution),
  });
}

function resolutionLabel(copy, resolution) {
  return copy.compare.resolutions[resolution] || copy.compare.resolutions.ended;
}

function resolutionOf(state) {
  for (let index = state.negotiation.length - 1; index >= 0; index -= 1) {
    const message = state.negotiation[index];
    if (message.action === "abort") return "aborted";
    if (message.side === "oss" && message.action === "accept") return "accepted";
    if (message.side === "oss" && message.action === "reject") return "rejected";
  }
  return "ended";
}

function reportRate(report) {
  if (!report || typeof report !== "object") return null;
  const value = report.target_rate ?? report.energy?.saving_percent;
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}

function savedKwh(report) {
  if (!report || typeof report !== "object") return null;
  const value = report.energy?.saved_kwh ?? report.estimated_saving_kwh;
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}

function format(template, values) {
  return String(template).replace(/\{(\w+)\}/g, (match, key) =>
    values[key] === undefined ? match : String(values[key]),
  );
}
