"use strict";

const STORAGE_KEY = "a-share-catalyst-lens:v1";

const METRICS = [
  {
    key: "source_reliability",
    label: "来源可靠性",
    description: "官方公告、多源确认和一手材料得分更高。",
    risk: false,
  },
  {
    key: "materiality",
    label: "影响实质性",
    description: "是否直接影响收入、利润、现金流或监管环境。",
    risk: false,
  },
  {
    key: "immediacy",
    label: "时效性",
    description: "短期可落地、可验证的影响得分更高。",
    risk: false,
  },
  {
    key: "novelty",
    label: "新颖性",
    description: "新信息高于市场已充分知晓的重复信息。",
    risk: false,
  },
  {
    key: "confirmation",
    label: "确认度",
    description: "数字、公告细节和独立交叉验证是否充分。",
    risk: false,
  },
  {
    key: "market_alignment",
    label: "市场配合度",
    description: "个股、板块、成交量和同行是否同步确认。",
    risk: false,
  },
  {
    key: "priced_in_risk",
    label: "已反映风险",
    description: "涨幅、拥挤度和估值是否已透支利好。",
    risk: true,
  },
  {
    key: "counterevidence",
    label: "反证强度",
    description: "执行、财务、政策或相反数据风险有多强。",
    risk: true,
  },
];

const EVENT_TYPE_LABELS = {
  policy: "政策与监管",
  earnings: "业绩与指引",
  order: "订单与经营进展",
  capital: "回购、分红与资本动作",
  mna: "并购与重组",
  product: "产品获批与技术进展",
  flow: "资金流与市场结构",
  theme: "板块与题材",
  rumor: "传闻与社交情绪",
  risk: "负面风险",
};

const POSITIVE_INSIGHTS = {
  source_reliability: "证据来源可靠，可追溯性较强",
  materiality: "事件可能直接影响经营或估值逻辑",
  immediacy: "影响具备较清晰的短期落地窗口",
  novelty: "信息增量较高，尚有重新定价空间",
  confirmation: "存在公告、数字或多源材料确认",
  market_alignment: "价格、成交量或板块表现支持该逻辑",
};

const MONITORING_BY_TYPE = {
  policy: ["跟踪正式文件、实施细则和适用范围", "观察板块内受益公司是否出现持续分化"],
  earnings: ["核对收入、利润、现金流和非经常性损益", "关注后续指引是否延续本次改善"],
  order: ["跟踪合同执行、回款节奏和毛利率", "核对订单金额占年度营收的比例"],
  capital: ["跟踪实施进度、资金来源和股东实际动作", "检查是否伴随减持、再融资或稀释风险"],
  mna: ["跟踪交易方案、问询回复和监管审批", "检查估值、商誉、业绩承诺和整合风险"],
  product: ["跟踪量产、客户验证、订单和收入确认", "区分技术突破与商业化兑现"],
  flow: ["观察成交量、换手、资金持续性和板块广度", "警惕短线拥挤和高位分歧"],
  theme: ["确认题材与公司主营业务的真实关联度", "观察龙头、跟风股和板块指数是否同步"],
  rumor: ["等待公司、交易所或监管机构正式确认", "避免把单一社交来源当作事实"],
  risk: ["跟踪风险是否继续扩大或得到正式澄清", "检查流动性、偿债、监管和持续经营信号"],
};

const FORM_FIELDS = [
  "stockCode",
  "company",
  "eventType",
  "eventDate",
  "title",
  "sourceUrl",
  "notes",
  ...METRICS.map((metric) => metric.key),
];

const EXAMPLE_EVENTS = [
  {
    stockCode: "688519",
    company: "示例科技",
    eventType: "order",
    eventDate: "2026-07-11",
    title: "公司公告获得占上年营收约 57% 的长期订单",
    sourceUrl: "https://example.com/announcement",
    notes: "订单金额较大且有正式公告；板块当日上涨，但个股此前一个月已累计上涨约 38%。",
    source_reliability: 5,
    materiality: 4,
    immediacy: 4,
    novelty: 3,
    confirmation: 4,
    market_alignment: 3,
    priced_in_risk: 2,
    counterevidence: 1,
  },
  {
    stockCode: "BK-DEMO",
    company: "示例板块",
    eventType: "rumor",
    eventDate: "2026-07-11",
    title: "社交平台流传尚未确认的产业扶持传闻",
    sourceUrl: "",
    notes: "目前只有单一二级来源，没有正式政策文件或公司公告。",
    source_reliability: 1,
    materiality: 2,
    immediacy: 2,
    novelty: 3,
    confirmation: 0,
    market_alignment: 2,
    priced_in_risk: 3,
    counterevidence: 4,
  },
];

let state = loadState();
let isHydrating = false;
let saveTimer = null;
let statusTimer = null;
let installPrompt = null;

const elements = {};

document.addEventListener("DOMContentLoaded", initialize);

function initialize() {
  Object.assign(elements, {
    form: document.getElementById("eventForm"),
    installButton: document.getElementById("installButton"),
    eventSelector: document.getElementById("eventSelector"),
    addEventButton: document.getElementById("addEventButton"),
    removeEventButton: document.getElementById("removeEventButton"),
    loadExampleButton: document.getElementById("loadExampleButton"),
    importButton: document.getElementById("importButton"),
    importFile: document.getElementById("importFile"),
    exportButton: document.getElementById("exportButton"),
    resetButton: document.getElementById("resetButton"),
    copyReportButton: document.getElementById("copyReportButton"),
    downloadReportButton: document.getElementById("downloadReportButton"),
    scoreRing: document.getElementById("scoreRing"),
    scoreValue: document.getElementById("scoreValue"),
    verdictLabel: document.getElementById("verdictLabel"),
    confidenceBadge: document.getElementById("confidenceBadge"),
    verdictSummary: document.getElementById("verdictSummary"),
    eventCount: document.getElementById("eventCount"),
    averageScore: document.getElementById("averageScore"),
    highestScore: document.getElementById("highestScore"),
    componentBars: document.getElementById("componentBars"),
    reportContent: document.getElementById("reportContent"),
    eventComparison: document.getElementById("eventComparison"),
    jsonOutput: document.getElementById("jsonOutput"),
    resultsPane: document.getElementById("resultsPane"),
    saveState: document.getElementById("saveState"),
    appStatus: document.getElementById("appStatus"),
  });

  buildMetricControls();
  bindEvents();
  normalizeState();
  renderEventSelector();
  hydrateForm();
  renderResults();
  activateView("summary");
  setupPwa();
}

function createEvent(overrides = {}) {
  return {
    id: createId(),
    stockCode: "",
    company: "",
    eventType: "policy",
    eventDate: localDateString(),
    title: "",
    sourceUrl: "",
    notes: "",
    source_reliability: 0,
    materiality: 0,
    immediacy: 0,
    novelty: 0,
    confirmation: 0,
    market_alignment: 0,
    priced_in_risk: 0,
    counterevidence: 0,
    ...overrides,
  };
}

function createId() {
  if (globalThis.crypto && typeof globalThis.crypto.randomUUID === "function") {
    return globalThis.crypto.randomUUID();
  }
  return `event-${Date.now()}-${Math.random().toString(16).slice(2)}`;
}

function localDateString() {
  const date = new Date();
  const offset = date.getTimezoneOffset() * 60_000;
  return new Date(date.getTime() - offset).toISOString().slice(0, 10);
}

function loadState() {
  try {
    const saved = localStorage.getItem(STORAGE_KEY);
    if (!saved) return { events: [createEvent()], activeIndex: 0 };
    const parsed = JSON.parse(saved);
    return {
      events: Array.isArray(parsed.events) ? parsed.events.map((event) => createEvent(event)) : [createEvent()],
      activeIndex: Number.isInteger(parsed.activeIndex) ? parsed.activeIndex : 0,
    };
  } catch (_error) {
    return { events: [createEvent()], activeIndex: 0 };
  }
}

function normalizeState() {
  if (!Array.isArray(state.events) || !state.events.length) state.events = [createEvent()];
  state.events = state.events.map((event) => createEvent(event));
  state.activeIndex = Math.min(Math.max(Number(state.activeIndex) || 0, 0), state.events.length - 1);
}

function buildMetricControls() {
  const positiveContainer = document.getElementById("positiveMetrics");
  const riskContainer = document.getElementById("riskMetrics");

  METRICS.forEach((metric) => {
    const wrapper = document.createElement("div");
    wrapper.className = "metric-control";

    const top = document.createElement("div");
    top.className = "metric-control-top";

    const label = document.createElement("label");
    label.htmlFor = metric.key;
    label.textContent = metric.label;

    const output = document.createElement("output");
    output.className = "metric-value";
    output.id = `${metric.key}Value`;
    output.htmlFor = metric.key;
    output.textContent = "0";

    const input = document.createElement("input");
    input.type = "range";
    input.id = metric.key;
    input.name = metric.key;
    input.min = "0";
    input.max = "5";
    input.step = "1";
    input.value = "0";
    input.setAttribute("aria-describedby", `${metric.key}Help`);
    input.setAttribute("aria-valuetext", metricAriaText(0, metric.risk));

    const anchors = document.createElement("div");
    anchors.className = "range-anchors";
    anchors.innerHTML = metric.risk ? "<span>0 低</span><span>5 高</span>" : "<span>0 无</span><span>5 强</span>";

    const help = document.createElement("p");
    help.id = `${metric.key}Help`;
    help.textContent = metric.description;

    top.append(label, output);
    wrapper.append(top, input, anchors, help);
    (metric.risk ? riskContainer : positiveContainer).append(wrapper);
  });
}

function bindEvents() {
  elements.form.addEventListener("input", handleFormInput);
  elements.form.addEventListener("change", handleFormInput);
  elements.form.addEventListener("submit", handleSubmit);
  elements.installButton.addEventListener("click", installApp);
  elements.eventSelector.addEventListener("change", handleEventSelection);
  elements.addEventButton.addEventListener("click", addEvent);
  elements.removeEventButton.addEventListener("click", removeEvent);
  elements.loadExampleButton.addEventListener("click", loadExample);
  elements.importButton.addEventListener("click", () => elements.importFile.click());
  elements.importFile.addEventListener("change", importJson);
  elements.exportButton.addEventListener("click", exportJson);
  elements.resetButton.addEventListener("click", resetAll);
  elements.copyReportButton.addEventListener("click", copyReport);
  elements.downloadReportButton.addEventListener("click", downloadMarkdownReport);

  document.querySelectorAll("[role='tab']").forEach((tab) => {
    tab.addEventListener("click", () => activateView(tab.dataset.view));
    tab.addEventListener("keydown", handleTabKeydown);
  });
}

function handleFormInput(event) {
  if (isHydrating || !event.target.name || !FORM_FIELDS.includes(event.target.name)) return;
  const current = state.events[state.activeIndex];
  current[event.target.name] = event.target.type === "range" ? Number(event.target.value) : event.target.value;

  if (event.target.type === "range") {
    document.getElementById(`${event.target.name}Value`).textContent = event.target.value;
    const metric = METRICS.find((item) => item.key === event.target.name);
    event.target.setAttribute("aria-valuetext", metricAriaText(event.target.value, metric?.risk));
  }

  renderEventSelector();
  renderResults();
  scheduleSave();
}

function handleSubmit(event) {
  event.preventDefault();
  renderResults();
  persistState();
  showStatus("分析摘要已更新");
  if (window.matchMedia("(max-width: 820px)").matches) {
    elements.resultsPane.scrollIntoView({ behavior: "smooth", block: "start" });
  }
}

function handleEventSelection() {
  state.activeIndex = Number(elements.eventSelector.value);
  hydrateForm();
  renderResults();
  scheduleSave();
}

function addEvent() {
  state.events.push(createEvent());
  state.activeIndex = state.events.length - 1;
  renderEventSelector();
  hydrateForm();
  renderResults();
  persistState();
  document.getElementById("title").focus();
  showStatus("已添加关联事件");
}

function removeEvent() {
  if (state.events.length <= 1) return;
  state.events.splice(state.activeIndex, 1);
  state.activeIndex = Math.min(state.activeIndex, state.events.length - 1);
  renderEventSelector();
  hydrateForm();
  renderResults();
  persistState();
  showStatus("当前事件已删除");
}

function loadExample() {
  state = {
    events: EXAMPLE_EVENTS.map((event) => createEvent(event)),
    activeIndex: 0,
  };
  renderEventSelector();
  hydrateForm();
  renderResults();
  persistState();
  showStatus("已载入两条示例事件");
}

async function importJson(event) {
  const [file] = event.target.files || [];
  event.target.value = "";
  if (!file) return;

  try {
    const parsed = JSON.parse(await file.text());
    const importedEvents = Array.isArray(parsed) ? parsed : parsed.events;
    if (!Array.isArray(importedEvents) || !importedEvents.length) {
      throw new Error("JSON 需要包含非空 events 数组");
    }
    state = {
      events: importedEvents.map((item) => createEvent(item)),
      activeIndex: 0,
    };
    renderEventSelector();
    hydrateForm();
    renderResults();
    persistState();
    showStatus(`已导入 ${state.events.length} 条事件`);
  } catch (error) {
    showStatus(`导入失败：${error.message}`, true);
  }
}

function exportJson() {
  const payload = buildExportPayload();
  const filename = `a-share-catalyst-${localDateString()}.json`;
  downloadBlob(filename, JSON.stringify(payload, null, 2), "application/json;charset=utf-8");
  showStatus("分析数据已导出");
}

function resetAll() {
  if (!window.confirm("确定清空全部事件和本地草稿吗？")) return;
  localStorage.removeItem(STORAGE_KEY);
  state = { events: [createEvent()], activeIndex: 0 };
  renderEventSelector();
  hydrateForm();
  renderResults();
  showStatus("已重置全部数据");
}

async function copyReport() {
  const text = generateMarkdownReport();
  try {
    await navigator.clipboard.writeText(text);
  } catch (_error) {
    const textarea = document.createElement("textarea");
    textarea.value = text;
    textarea.style.position = "fixed";
    textarea.style.opacity = "0";
    document.body.append(textarea);
    textarea.select();
    document.execCommand("copy");
    textarea.remove();
  }
  showStatus("Markdown 摘要已复制");
}

function downloadMarkdownReport() {
  const event = state.events[state.activeIndex];
  const identifier = safeFilename(event.stockCode || event.company || "catalyst-report");
  downloadBlob(`${identifier}-${localDateString()}.md`, generateMarkdownReport(), "text/markdown;charset=utf-8");
  showStatus("Markdown 报告已下载");
}

function renderEventSelector() {
  const previous = state.activeIndex;
  elements.eventSelector.replaceChildren();
  state.events.forEach((event, index) => {
    const option = document.createElement("option");
    option.value = String(index);
    option.textContent = `${index + 1}. ${event.company || event.title || "未命名事件"}`;
    elements.eventSelector.append(option);
  });
  elements.eventSelector.value = String(previous);
  elements.removeEventButton.disabled = state.events.length <= 1;
}

function hydrateForm() {
  isHydrating = true;
  const current = state.events[state.activeIndex];
  FORM_FIELDS.forEach((field) => {
    const input = document.getElementById(field);
    if (!input) return;
    input.value = current[field] ?? "";
    if (input.type === "range") {
      document.getElementById(`${field}Value`).textContent = String(current[field] ?? 0);
      const metric = METRICS.find((item) => item.key === field);
      input.setAttribute("aria-valuetext", metricAriaText(current[field] ?? 0, metric?.risk));
    }
  });
  isHydrating = false;
}

function renderResults() {
  const analysis = CatalystScoring.scorePayload({ events: state.events });
  const currentResult = analysis.events[state.activeIndex];
  const currentEvent = state.events[state.activeIndex];
  const hasInput = Boolean(currentEvent.title.trim() || currentEvent.company.trim() || currentEvent.stockCode.trim());
  const score = hasInput && currentResult ? currentResult.score : 0;

  elements.scoreValue.textContent = formatScore(score);
  elements.scoreRing.style.setProperty("--score-angle", `${score * 3.6}deg`);
  elements.scoreRing.style.setProperty("--score-color", scoreColor(score));
  elements.verdictLabel.textContent = hasInput
    ? CatalystScoring.gradeLabel(currentResult?.grade || "no_events")
    : "等待分析";
  renderConfidence(hasInput ? currentResult?.confidence || "Low" : "Pending");
  elements.verdictSummary.textContent = verdictSummary(currentResult, currentEvent);
  elements.eventCount.textContent = analysis.summary.event_count;
  elements.averageScore.textContent = formatScore(analysis.summary.average_score);
  elements.highestScore.textContent = formatScore(analysis.summary.highest_score || 0);

  renderComponentBars(currentResult);
  renderReport(currentResult, currentEvent);
  renderEventComparison(analysis);
  elements.jsonOutput.textContent = JSON.stringify(buildExportPayload(analysis), null, 2);
}

function renderConfidence(value) {
  elements.confidenceBadge.className = "confidence-badge";
  if (value === "High") elements.confidenceBadge.classList.add("high");
  if (value === "Low") elements.confidenceBadge.classList.add("low");
  if (value === "Pending") elements.confidenceBadge.classList.add("pending");
  elements.confidenceBadge.textContent = value === "Pending" ? "待评估" : `${value} confidence`;
}

function verdictSummary(result, event) {
  if (!event.title) return "填写事件标题、来源和评分维度后，判断会随输入实时更新。";
  if (result.score >= 80) return "证据、实质影响和确认度较强，但仍需持续检查执行进度与价格反应。";
  if (result.score >= 65) return "整体偏正向，事件具备一定重估基础，但仍存在需要跟踪的反证。";
  if (result.score >= 50) return "存在正向信息，但证据强度、市场确认或已反映风险限制了结论。";
  if (result.score >= 35) return "当前证据不足以形成高置信度判断，建议先补充一手来源和市场确认。";
  return "现有信息不支持把它视为明确利好，或风险扣分已经抵消正向证据。";
}

function renderComponentBars(result) {
  elements.componentBars.replaceChildren();
  METRICS.forEach((metric) => {
    const value = result?.components?.[metric.key] || 0;
    const max = metric.risk ? CatalystScoring.PENALTIES[metric.key] : CatalystScoring.WEIGHTS[metric.key];
    const width = Math.min((Math.abs(value) / max) * 100, 100);

    const row = document.createElement("div");
    row.className = `component-row${metric.risk ? " penalty" : ""}`;
    row.innerHTML = `
      <span class="component-row-label" title="${escapeHtml(metric.label)}">${escapeHtml(metric.label)}</span>
      <span class="component-track"><span class="component-fill" style="width:${width}%"></span></span>
      <span class="component-value">${value > 0 ? "+" : ""}${formatScore(value)}</span>
    `;
    elements.componentBars.append(row);
  });
}

function buildReportModel(result, event) {
  const positive = Object.keys(CatalystScoring.WEIGHTS)
    .map((key) => ({ key, points: result.components[key], value: Number(event[key] || 0) }))
    .filter((item) => item.value > 0)
    .sort((a, b) => b.points - a.points)
    .slice(0, 4)
    .map((item) => `${POSITIVE_INSIGHTS[item.key]}（${item.value}/5，贡献 +${formatScore(item.points)}）`);

  const risks = [];
  if (Number(event.priced_in_risk) > 0) {
    risks.push(`已反映风险为 ${event.priced_in_risk}/5，需检查前期涨幅、估值和交易拥挤度。`);
  }
  if (Number(event.counterevidence) > 0) {
    risks.push(`反证强度为 ${event.counterevidence}/5，应核对执行、财务、政策或相反数据。`);
  }
  if (Number(event.source_reliability) < 3) risks.push("来源可靠性偏低，当前材料不足以支撑高置信度结论。");
  if (Number(event.confirmation) < 3) risks.push("确认度不足，建议补充正式公告、数字或独立交叉验证。");
  if (Number(event.market_alignment) < 3) risks.push("市场配合度较弱，个股、板块或成交量尚未形成一致确认。");
  if (event.eventType === "rumor") risks.push("事件属于传闻或社交情绪，正式确认前不应当作事实使用。");

  const monitoring = [...(MONITORING_BY_TYPE[event.eventType] || MONITORING_BY_TYPE.policy)];
  if (!safeHttpUrl(event.sourceUrl)) monitoring.unshift("补充一条可核验的一手来源链接。");
  if (Number(event.market_alignment) < 4) monitoring.push("继续观察相对板块、指数、成交量和同行表现。");
  monitoring.push("预先记录会让结论失效的价格、公告或经营信号。");

  return {
    positive: positive.length ? positive : ["当前尚未录入足够的正向证据。"],
    risks: risks.length ? risks : ["当前扣分项较低，但仍需检查事件执行和市场环境变化。"],
    monitoring: [...new Set(monitoring)],
  };
}

function renderReport(result, event) {
  const model = buildReportModel(result, event);
  const source = safeHttpUrl(event.sourceUrl);
  const sourceCell = source
    ? `<a href="${escapeHtml(source)}" target="_blank" rel="noreferrer">查看来源</a>`
    : "未提供";

  elements.reportContent.innerHTML = `
    <section class="report-section wide">
      <h3>证据台账</h3>
      <div class="evidence-table-wrap">
        <table class="evidence-table">
          <thead><tr><th>日期</th><th>标的</th><th>类型</th><th>事件事实</th><th>来源</th></tr></thead>
          <tbody><tr>
            <td>${escapeHtml(event.eventDate || "未填写")}</td>
            <td>${escapeHtml([event.stockCode, event.company].filter(Boolean).join(" · ") || "未填写")}</td>
            <td>${escapeHtml(EVENT_TYPE_LABELS[event.eventType] || "其他")}</td>
            <td>${escapeHtml(event.title || "未填写事件标题")}</td>
            <td>${sourceCell}</td>
          </tr></tbody>
        </table>
      </div>
    </section>
    <section class="report-section">
      <h3>正向证据</h3>
      <ul>${model.positive.map((item) => `<li>${escapeHtml(item)}</li>`).join("")}</ul>
    </section>
    <section class="report-section risk">
      <h3>反证与已反映风险</h3>
      <ul>${model.risks.map((item) => `<li>${escapeHtml(item)}</li>`).join("")}</ul>
    </section>
    <section class="report-section wide">
      <h3>后续观察</h3>
      <ul>${model.monitoring.map((item) => `<li>${escapeHtml(item)}</li>`).join("")}</ul>
    </section>
  `;
}

function renderEventComparison(analysis) {
  elements.eventComparison.replaceChildren();
  analysis.events.forEach((result, index) => {
    const event = state.events[index];
    const row = document.createElement("div");
    row.className = `event-row${index === state.activeIndex ? " active" : ""}`;
    row.innerHTML = `
      <div class="event-row-title">
        <strong>${escapeHtml(event.title || "未命名事件")}</strong>
        <span>${escapeHtml([event.stockCode, event.company, EVENT_TYPE_LABELS[event.eventType]].filter(Boolean).join(" · "))}</span>
      </div>
      <span class="event-row-score">${formatScore(result.score)}</span>
      <span class="event-row-grade">${escapeHtml(CatalystScoring.gradeLabel(result.grade))}</span>
      <button type="button" title="编辑此事件" aria-label="编辑此事件">
        <svg class="icon" viewBox="0 0 24 24" aria-hidden="true"><path d="M5 12h14M13 6l6 6-6 6"/></svg>
      </button>
    `;
    row.querySelector("button").addEventListener("click", () => {
      state.activeIndex = index;
      renderEventSelector();
      hydrateForm();
      renderResults();
      document.querySelector(".editor-pane").scrollIntoView({ behavior: "smooth", block: "start" });
    });
    elements.eventComparison.append(row);
  });
}

function generateMarkdownReport() {
  const analysis = CatalystScoring.scorePayload({ events: state.events });
  const result = analysis.events[state.activeIndex];
  const event = state.events[state.activeIndex];
  const model = buildReportModel(result, event);
  const reportSource = safeHttpUrl(event.sourceUrl);

  return [
    "# A-Share Catalyst Lens 分析报告",
    "",
    `- **标的**：${markdownText([event.stockCode, event.company].filter(Boolean).join(" · ") || "未填写")}`,
    `- **事件**：${markdownText(event.title || "未填写")}`,
    `- **类型**：${markdownText(EVENT_TYPE_LABELS[event.eventType] || "其他")}`,
    `- **结论**：${markdownText(CatalystScoring.gradeLabel(result.grade))}`,
    `- **催化分数**：${formatScore(result.score)}/100`,
    `- **置信度**：${result.confidence}`,
    "",
    "## 正向证据",
    "",
    ...model.positive.map((item, index) => `${index + 1}. ${item}`),
    "",
    "## 反证与已反映风险",
    "",
    ...model.risks.map((item, index) => `${index + 1}. ${item}`),
    "",
    "## 后续观察",
    "",
    ...model.monitoring.map((item, index) => `${index + 1}. ${item}`),
    "",
    "## 证据记录",
    "",
    `- **日期**：${markdownText(event.eventDate || "未填写")}`,
    `- **来源**：${reportSource ? `[查看来源](${reportSource})` : "未提供"}`,
    `- **备注**：${markdownText(event.notes || "无")}`,
    "",
    "> 免责声明：规则评分仅用于研究辅助，不预测价格，也不构成投资建议。",
  ].join("\n");
}

function buildExportPayload(existingAnalysis) {
  const analysis = existingAnalysis || CatalystScoring.scorePayload({ events: state.events });
  return {
    schema_version: 1,
    exported_at: new Date().toISOString(),
    active_event_index: state.activeIndex,
    events: state.events,
    analysis,
  };
}

function activateView(view) {
  document.querySelectorAll("[role='tab']").forEach((tab) => {
    const active = tab.dataset.view === view;
    tab.setAttribute("aria-selected", String(active));
    tab.tabIndex = active ? 0 : -1;
    document.getElementById(`${tab.dataset.view}View`).hidden = !active;
  });
}

function handleTabKeydown(event) {
  if (!["ArrowLeft", "ArrowRight", "Home", "End"].includes(event.key)) return;
  const tabs = [...document.querySelectorAll("[role='tab']")];
  const currentIndex = tabs.indexOf(event.currentTarget);
  let nextIndex = currentIndex;
  if (event.key === "ArrowLeft") nextIndex = (currentIndex - 1 + tabs.length) % tabs.length;
  if (event.key === "ArrowRight") nextIndex = (currentIndex + 1) % tabs.length;
  if (event.key === "Home") nextIndex = 0;
  if (event.key === "End") nextIndex = tabs.length - 1;
  event.preventDefault();
  tabs[nextIndex].focus();
  activateView(tabs[nextIndex].dataset.view);
}

function setupPwa() {
  if ("serviceWorker" in navigator && ["http:", "https:"].includes(location.protocol)) {
    navigator.serviceWorker.register("./sw.js").catch(() => {
      showStatus("离线缓存暂不可用", true);
    });
  }

  window.addEventListener("beforeinstallprompt", (event) => {
    event.preventDefault();
    installPrompt = event;
    elements.installButton.hidden = false;
  });

  window.addEventListener("appinstalled", () => {
    installPrompt = null;
    elements.installButton.hidden = true;
    showStatus("应用已安装");
  });
}

async function installApp() {
  if (!installPrompt) return;
  await installPrompt.prompt();
  const choice = await installPrompt.userChoice;
  if (choice.outcome === "accepted") showStatus("正在安装应用");
  installPrompt = null;
  elements.installButton.hidden = true;
}

function scheduleSave() {
  elements.saveState.textContent = "保存中...";
  window.clearTimeout(saveTimer);
  saveTimer = window.setTimeout(persistState, 240);
}

function persistState() {
  try {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(state));
    elements.saveState.textContent = "已自动保存";
  } catch (_error) {
    elements.saveState.textContent = "本地保存不可用";
  }
}

function showStatus(message, isError = false) {
  window.clearTimeout(statusTimer);
  elements.appStatus.textContent = message;
  elements.appStatus.style.color = isError ? "var(--negative)" : "var(--brand-accent)";
  statusTimer = window.setTimeout(() => {
    elements.appStatus.textContent = "";
  }, 3600);
}

function downloadBlob(filename, content, type) {
  const blob = new Blob([content], { type });
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = filename;
  document.body.append(link);
  link.click();
  link.remove();
  URL.revokeObjectURL(url);
}

function safeHttpUrl(value) {
  if (!value) return "";
  try {
    const url = new URL(value);
    return ["http:", "https:"].includes(url.protocol) ? url.href : "";
  } catch (_error) {
    return "";
  }
}

function safeFilename(value) {
  return String(value || "catalyst-report")
    .trim()
    .replace(/[^a-zA-Z0-9_-]+/g, "-")
    .replace(/^-+|-+$/g, "")
    .slice(0, 60) || "catalyst-report";
}

function markdownText(value) {
  return String(value ?? "")
    .replace(/\r?\n/g, " ")
    .replace(/([\\`*_{}\[\]<>|])/g, "\\$1");
}

function metricAriaText(value, risk) {
  const labels = risk ? ["低", "很低", "较低", "中等", "较高", "高"] : ["无", "很弱", "较弱", "中等", "较强", "强"];
  const number = CatalystScoring.clamp(value);
  return `${number}，${labels[Math.round(number)]}`;
}

function scoreColor(score) {
  if (score >= 65) return "var(--positive)";
  if (score >= 35) return "var(--warning)";
  return "var(--negative)";
}

function formatScore(value) {
  const number = Number(value || 0);
  return Number.isInteger(number) ? String(number) : number.toFixed(1);
}

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}
