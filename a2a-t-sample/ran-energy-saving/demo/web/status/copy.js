/** Bilingual copy of the process status page.
 *
 * The process language is injected through the ``a2at-language`` meta tag; there is no runtime
 * switcher. Protocol tokens and raw process values stay verbatim.
 */

const ZH = {
  lang: "zh-CN",
  "page.title": "A2A / A2A-T · 运行状态",
  "page.kicker": "RAN 节能 · A2A / A2A-T",
  "page.heading": "运行状态",
  "nav.tester": "评测控制台",
  "nav.demo": "演示页面",
  "status.loading": "正在读取状态…",
  "status.paused": "页面不可见，已暂停刷新",
  "status.refreshed": "更新于 {time} · 每 {seconds} 秒刷新",
  "status.failed": "读取运行状态失败：{error}",
  "section.process": "进程与环境",
  "section.processHint": "重启 UI 进程会终止正在执行的批次；开始新批次前，先确认这里没有其他人正在跑。",
  "section.batches": "正在执行的批次（所有浏览器）",
  "section.batchesEmpty": "当前没有正在执行的批次。",
  "section.runs": "运行中的 run（所有来源）",
  "section.runsEmpty": "当前没有运行中的 run。",
  "section.recent": "最近结束（本进程）",
  "section.recentEmpty": "本进程还没有结束的批次。",
  "table.batch": "批次",
  "table.owner": "触发人",
  "table.protocol": "协议",
  "table.progress": "进度",
  "table.currentItem": "当前用例",
  "table.elapsed": "已运行",
  "table.concurrency": "并发",
  "table.mode": "模式",
  "table.endpoint": "端点",
  "table.run": "Run",
  "table.source": "来源",
  "table.protocolTransport": "协议 / 传输",
  "table.status": "状态",
  "table.remoteTask": "远端 Task",
  "table.started": "开始",
  "table.latestState": "最近状态",
  "table.result": "结果",
  "table.finished": "结束",
  "fact.separator": "：",
  "fact.process": "进程",
  "fact.started": "启动",
  "fact.model": "模型",
  "fact.nms": "NMS",
  "fact.ran": "RAN 服务",
  "fact.dataDir": "数据目录",
  "fact.sdkPool": "SDK 池",
  "fact.activeItems": "在跑用例",
  "fact.runningBatches": "执行中批次",
  "fact.keptTasks": "保留 Task",
  "fact.detachedRuns": "后台 run",
  "value.realNms": "真实网管",
  "value.localRan": "本地 RAN Agent",
  "value.unconfigured": "未配置",
  "value.unknownIp": "IP 未知",
  "value.noCookie": "无 tester cookie",
  "value.serialBusy": "串行锁忙",
  "value.serialIdle": "串行锁空闲",
  "value.unknownSource": "未知来源",
  "process.started": "{time}（已运行 {duration}）",
  "process.model": "{label}（{mode}）",
  "process.runningBatches": "{count}（草稿 {drafts}）",
  "process.keptTasks": "批次 {kept} · run {runs}",
  "process.detachedRuns": "detached {detached} · 全部 {live}",
  "process.uptime": "已 {duration}",
  "batch.progress": "{done}/{total} · {percent}%",
  "batch.queued": " · 排队重跑 {count}",
  "batch.keepTasks": "保留 Task",
  "batch.stopAfter": "执行到 {stage}",
  "batch.round": " · 第{round}轮",
  "batch.item": "{label}（{protocol}）",
  "batch.finished": "完成",
  "batch.cancelled": "取消",
  "batch.running": "执行中",
  "batch.draft": "草稿",
  "batch.count": "{count} 个",
  "run.count": "{count} 个",
  "recent.count": "{count} 个",
};

const EN = {
  lang: "en-US",
  "page.title": "A2A / A2A-T · Process status",
  "page.kicker": "RAN energy saving · A2A / A2A-T",
  "page.heading": "Process status",
  "nav.tester": "Test console",
  "nav.demo": "Demo page",
  "status.loading": "Reading status…",
  "status.paused": "Page hidden; refresh paused",
  "status.refreshed": "Updated {time} · refreshes every {seconds} s",
  "status.failed": "Failed to read the status: {error}",
  "section.process": "Process and environment",
  "section.processHint":
    "Restarting the UI process stops the batches it is running; before starting a new batch, check that nobody else is running one.",
  "section.batches": "Running batches (all browsers)",
  "section.batchesEmpty": "No batch is running.",
  "section.runs": "Live runs (all sources)",
  "section.runsEmpty": "No run is live.",
  "section.recent": "Recently finished (this process)",
  "section.recentEmpty": "No batch has finished in this process yet.",
  "table.batch": "Batch",
  "table.owner": "Owner",
  "table.protocol": "Protocol",
  "table.progress": "Progress",
  "table.currentItem": "Current case",
  "table.elapsed": "Elapsed",
  "table.concurrency": "Concurrency",
  "table.mode": "Mode",
  "table.endpoint": "Endpoint",
  "table.run": "Run",
  "table.source": "Source",
  "table.protocolTransport": "Protocol / transport",
  "table.status": "Status",
  "table.remoteTask": "Remote Task",
  "table.started": "Started",
  "table.latestState": "Latest state",
  "table.result": "Result",
  "table.finished": "Finished",
  "fact.separator": ": ",
  "fact.process": "Process",
  "fact.started": "Started",
  "fact.model": "Model",
  "fact.nms": "NMS",
  "fact.ran": "RAN server",
  "fact.dataDir": "Data directory",
  "fact.sdkPool": "SDK pool",
  "fact.activeItems": "Active cases",
  "fact.runningBatches": "Running batches",
  "fact.keptTasks": "Kept Tasks",
  "fact.detachedRuns": "Background runs",
  "value.realNms": "Vendor NMS",
  "value.localRan": "Local RAN Agent",
  "value.unconfigured": "not configured",
  "value.unknownIp": "unknown IP",
  "value.noCookie": "no tester cookie",
  "value.serialBusy": "serial lock busy",
  "value.serialIdle": "serial lock idle",
  "value.unknownSource": "unknown source",
  "process.started": "{time} (up {duration})",
  "process.model": "{label} ({mode})",
  "process.runningBatches": "{count} (drafts {drafts})",
  "process.keptTasks": "batches {kept} · run {runs}",
  "process.detachedRuns": "detached {detached} · all {live}",
  "process.uptime": "up {duration}",
  "batch.progress": "{done}/{total} · {percent}%",
  "batch.queued": " · reruns queued {count}",
  "batch.keepTasks": "Keep Tasks",
  "batch.stopAfter": "Run to {stage}",
  "batch.round": " · round {round}",
  "batch.item": "{label} ({protocol})",
  "batch.finished": "Finished",
  "batch.cancelled": "Cancelled",
  "batch.running": "Running",
  "batch.draft": "Draft",
  "batch.count": "{count}",
  "run.count": "{count}",
  "recent.count": "{count}",
};

export const COPY = { "zh-CN": ZH, "en-US": EN };

/** Return the copy table of one language, falling back to the template language. */
export function copyFor(language) {
  return COPY[language] || COPY["zh-CN"];
}

/** Render ``key`` with ``{name}`` interpolation; an unknown key stays visible. */
export function t(copy, key, params = {}) {
  const template = copy[key];
  if (template === undefined) return key;
  return template.replace(/\{(\w+)\}/g, (match, name) =>
    Object.prototype.hasOwnProperty.call(params, name) ? String(params[name]) : match,
  );
}

/** Apply ``data-i18n`` / ``data-i18n-title`` bindings under ``root``. */
export function applyDataI18n(root, copy) {
  for (const node of root.querySelectorAll("[data-i18n]")) {
    const value = copy[node.dataset.i18n];
    if (value !== undefined) node.textContent = value;
  }
  for (const node of root.querySelectorAll("[data-i18n-title]")) {
    const value = copy[node.dataset.i18nTitle];
    if (value !== undefined) node.title = value;
  }
}

/** Status labels of one language, keyed by the stable batch status. */
export function batchStatusLabels(copy) {
  return {
    finished: [t(copy, "batch.finished"), "ok"],
    cancelled: [t(copy, "batch.cancelled"), ""],
    running: [t(copy, "batch.running"), "action"],
    draft: [t(copy, "batch.draft"), ""],
  };
}
