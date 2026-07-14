"use strict";

const STORAGE_KEY = "a-share-catalyst-lens:v2";
const LEGACY_STORAGE_KEY = "a-share-catalyst-lens:v1";

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

const SOURCE_TYPE_LABELS = {
  exchange_announcement: "交易所公告",
  regulator_policy: "监管与政策",
  company_ir: "公司与投资者关系",
  financial_report: "财务报告",
  market_data: "市场数据",
  peer_context: "同行与板块",
  trusted_media: "权威媒体",
  social: "社交信息",
  counterevidence: "反证材料",
  other: "其他",
};

const EVIDENCE_STATUS_LABELS = {
  pending: "待审核",
  accepted: "已采纳",
  rejected: "已排除",
};

const REVIEW_ACTION_LABELS = {
  created: "创建",
  status_changed: "状态变更",
  note_updated: "补充备注",
};

const DIRECTION_LABELS = {
  positive: "正向",
  negative: "负向",
  mixed: "混合",
  neutral: "中性",
};

const COVERAGE_LABELS = {
  official_source: "一手官方来源",
  financial_impact: "经营或财务影响",
  market_data: "市场行为确认",
  peer_context: "同行与板块对照",
  counterevidence: "反证与失效条件",
};

const EVIDENCE_NUMBER_FIELDS = [
  "reliability",
  "relevance",
  "freshness",
  "materiality",
  "immediacy",
  "novelty",
  "market_alignment",
  "priced_in_risk",
  "counterevidence",
];

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
let isDiscovering = false;
let isSavingEvidence = false;
const reviewingEvidenceIds = new Set();
let backendState = { available: false, checking: true, version: "" };

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
    resultContext: document.getElementById("resultContext"),
    verdictLabel: document.getElementById("verdictLabel"),
    confidenceBadge: document.getElementById("confidenceBadge"),
    verdictSummary: document.getElementById("verdictSummary"),
    scoreBasis: document.getElementById("scoreBasis"),
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
    runtimeStatus: document.getElementById("runtimeStatus"),
    evidenceModeBadge: document.getElementById("evidenceModeBadge"),
    evidenceReviewSummary: document.getElementById("evidenceReviewSummary"),
    addEvidenceButton: document.getElementById("addEvidenceButton"),
    discoverEvidenceButton: document.getElementById("discoverEvidenceButton"),
    discoveryControls: document.getElementById("discoveryControls"),
    discoveryQuery: document.getElementById("discoveryQuery"),
    discoveryStart: document.getElementById("discoveryStart"),
    discoveryEnd: document.getElementById("discoveryEnd"),
    evidenceFilterRow: document.getElementById("evidenceFilterRow"),
    evidenceFilters: document.getElementById("evidenceFilters"),
    evidenceList: document.getElementById("evidenceList"),
    allEvidenceCount: document.getElementById("allEvidenceCount"),
    pendingEvidenceCount: document.getElementById("pendingEvidenceCount"),
    acceptedEvidenceCount: document.getElementById("acceptedEvidenceCount"),
    rejectedEvidenceCount: document.getElementById("rejectedEvidenceCount"),
    metricModeText: document.getElementById("metricModeText"),
    resetOverridesButton: document.getElementById("resetOverridesButton"),
    acceptedEvidenceSummary: document.getElementById("acceptedEvidenceSummary"),
    qualityEmptyState: document.getElementById("qualityEmptyState"),
    qualityLayout: document.getElementById("qualityLayout"),
    evidenceConfidenceValue: document.getElementById("evidenceConfidenceValue"),
    evidenceConfidenceBar: document.getElementById("evidenceConfidenceBar"),
    coverageValue: document.getElementById("coverageValue"),
    coverageBar: document.getElementById("coverageBar"),
    coverageChecklist: document.getElementById("coverageChecklist"),
    evidenceDialog: document.getElementById("evidenceDialog"),
    evidenceForm: document.getElementById("evidenceForm"),
    evidenceDialogTitle: document.getElementById("evidenceDialogTitle"),
    evidenceDialogSubtitle: document.getElementById("evidenceDialogSubtitle"),
    evidenceDialogStatus: document.getElementById("evidenceDialogStatus"),
    closeEvidenceDialogButton: document.getElementById("closeEvidenceDialogButton"),
    cancelEvidenceButton: document.getElementById("cancelEvidenceButton"),
    saveEvidenceButton: document.getElementById("saveEvidenceButton"),
  });

  buildMetricControls();
  bindEvents();
  normalizeState();
  renderEventSelector();
  hydrateForm();
  renderEvidenceWorkspace();
  renderResults();
  activateView("summary");
  setupPwa();
  detectBackend();
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
    caseId: "",
    evidence: [],
    metricOverrides: {},
    evidenceFilter: "all",
    discoveryQuery: "",
    discoveryStart: dateDaysAgo(90),
    discoveryEnd: localDateString(),
    serverAnalysis: null,
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

function dateDaysAgo(days) {
  const date = new Date();
  date.setDate(date.getDate() - days);
  const offset = date.getTimezoneOffset() * 60_000;
  return new Date(date.getTime() - offset).toISOString().slice(0, 10);
}

function loadState() {
  try {
    const current = localStorage.getItem(STORAGE_KEY);
    const saved = current || localStorage.getItem(LEGACY_STORAGE_KEY);
    if (!saved) return { events: [createEvent()], activeIndex: 0 };
    const parsed = JSON.parse(saved);
    return {
      events: Array.isArray(parsed.events)
        ? parsed.events.map((event) => migrateEvent(event, !current))
        : [createEvent()],
      activeIndex: Number.isInteger(parsed.activeIndex) ? parsed.activeIndex : 0,
    };
  } catch (_error) {
    return { events: [createEvent()], activeIndex: 0 };
  }
}

function migrateEvent(event, legacy = false) {
  const raw = event && typeof event === "object" ? event : {};
  const legacyOverrides = legacy
    ? Object.fromEntries(METRICS.map((metric) => [metric.key, true]))
    : {};
  const metricOverrides =
    raw.metricOverrides && typeof raw.metricOverrides === "object"
      ? Object.fromEntries(
          Object.entries(raw.metricOverrides).filter(
            ([key, value]) => METRICS.some((metric) => metric.key === key) && Boolean(value)
          )
        )
      : legacyOverrides;
  const evidence = Array.isArray(raw.evidence)
    ? raw.evidence.filter((item) => item && typeof item === "object").map(normalizeEvidence)
    : [];
  return createEvent({ ...raw, metricOverrides, evidence });
}

function normalizeState() {
  if (!Array.isArray(state.events) || !state.events.length) state.events = [createEvent()];
  state.events = state.events.map((event) => migrateEvent(event));
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
    input.step = "0.1";
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
  elements.addEvidenceButton.addEventListener("click", () => openEvidenceDialog());
  elements.discoverEvidenceButton.addEventListener("click", discoverEvidence);
  elements.evidenceFilters.addEventListener("click", handleEvidenceFilter);
  elements.evidenceList.addEventListener("click", handleEvidenceAction);
  elements.evidenceForm.addEventListener("submit", saveEvidence);
  elements.closeEvidenceDialogButton.addEventListener("click", closeEvidenceDialog);
  elements.cancelEvidenceButton.addEventListener("click", closeEvidenceDialog);
  elements.resetOverridesButton.addEventListener("click", resetMetricOverrides);
  [elements.discoveryQuery, elements.discoveryStart, elements.discoveryEnd].forEach((input) => {
    input.addEventListener("change", handleDiscoveryInput);
    input.addEventListener("input", handleDiscoveryInput);
  });

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
    current.metricOverrides[event.target.name] = true;
    current.serverAnalysis = null;
    document.getElementById(`${event.target.name}Value`).textContent = event.target.value;
    const metric = METRICS.find((item) => item.key === event.target.name);
    event.target.setAttribute("aria-valuetext", metricAriaText(event.target.value, metric?.risk));
  }

  renderEventSelector();
  renderMetricMode();
  renderResults();
  scheduleSave();
}

async function handleSubmit(event) {
  event.preventDefault();
  if (backendState.available && state.events[state.activeIndex].caseId) {
    await refreshServerAnalysis({ quiet: true, targetEvent: state.events[state.activeIndex] });
  }
  renderResults();
  persistState();
  showStatus("分析摘要已更新");
  if (window.matchMedia("(max-width: 820px)").matches) {
    elements.resultsPane.scrollIntoView({ behavior: preferredScrollBehavior(), block: "start" });
  }
}

function handleEventSelection() {
  state.activeIndex = Number(elements.eventSelector.value);
  hydrateForm();
  renderEvidenceWorkspace();
  renderResults();
  loadRemoteEvidence({ quiet: true });
  scheduleSave();
}

function addEvent() {
  state.events.push(createEvent());
  state.activeIndex = state.events.length - 1;
  renderEventSelector();
  hydrateForm();
  renderEvidenceWorkspace();
  renderResults();
  persistState();
  document.getElementById("title").focus();
  showStatus("已添加关联事件");
}

function removeEvent() {
  if (state.events.length <= 1) return;
  const removed = state.events[state.activeIndex];
  const eventName =
    removed.title.trim() || removed.company.trim() || removed.stockCode.trim() || `事件 ${state.activeIndex + 1}`;
  if (!window.confirm(`确定删除“${eventName}”吗？相关证据和本地草稿也会一并移除。`)) return;
  state.events.splice(state.activeIndex, 1);
  state.activeIndex = Math.min(state.activeIndex, state.events.length - 1);
  renderEventSelector();
  hydrateForm();
  renderEvidenceWorkspace();
  renderResults();
  persistState();
  if (backendState.available && removed.caseId) {
    apiFetch(`/api/cases/${encodeURIComponent(removed.caseId)}`, { method: "DELETE" }).catch(() => {});
  }
  showStatus("当前事件已删除");
}

function loadExample() {
  state = {
    events: EXAMPLE_EVENTS.map((event) => createEvent(event)),
    activeIndex: 0,
  };
  renderEventSelector();
  hydrateForm();
  renderEvidenceWorkspace();
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
      events: importedEvents.map(prepareImportedEvent),
      activeIndex: 0,
    };
    renderEventSelector();
    hydrateForm();
    renderEvidenceWorkspace();
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
  const remoteCaseIds = state.events.map((event) => event.caseId).filter(Boolean);
  localStorage.removeItem(STORAGE_KEY);
  localStorage.removeItem(LEGACY_STORAGE_KEY);
  state = { events: [createEvent()], activeIndex: 0 };
  renderEventSelector();
  hydrateForm();
  renderEvidenceWorkspace();
  renderResults();
  if (backendState.available) {
    remoteCaseIds.forEach((caseId) => {
      apiFetch(`/api/cases/${encodeURIComponent(caseId)}`, { method: "DELETE" }).catch(() => {});
    });
  }
  showStatus("已重置全部数据");
}

function normalizeReviewHistory(entries, status, createdAt, reviewNote = "") {
  const validActions = new Set(Object.keys(REVIEW_ACTION_LABELS));
  const validStatuses = new Set(Object.keys(EVIDENCE_STATUS_LABELS));
  const history = Array.isArray(entries)
    ? entries
        .filter(
          (entry) =>
            entry &&
            typeof entry === "object" &&
            validActions.has(entry.action) &&
            validStatuses.has(entry.to_status)
        )
        .map((entry) => ({
          id: entry.id || createId(),
          action: entry.action,
          from_status: validStatuses.has(entry.from_status) ? entry.from_status : null,
          to_status: entry.to_status,
          review_note: String(entry.review_note || ""),
          created_at: entry.created_at || createdAt,
        }))
    : [];
  if (history.length) return history;
  return [
    {
      id: createId(),
      action: "created",
      from_status: null,
      to_status: status,
      review_note: String(reviewNote || ""),
      created_at: createdAt,
    },
  ];
}

function appendLocalReviewHistory(item, action, fromStatus, toStatus, reviewNote, createdAt) {
  item.review_history = [
    ...(item.review_history || []),
    {
      id: createId(),
      action,
      from_status: fromStatus,
      to_status: toStatus,
      review_note: reviewNote,
      created_at: createdAt,
    },
  ];
  item.reviewed_at = createdAt;
  item.review_note = reviewNote;
}

function normalizeEvidence(item = {}) {
  const now = new Date().toISOString();
  const status = item.status || (item.origin === "automatic" ? "pending" : "accepted");
  const createdAt = item.created_at || now;
  const reviewHistory = normalizeReviewHistory(
    item.review_history,
    status,
    createdAt,
    item.review_note
  );
  const latestReview = [...reviewHistory]
    .reverse()
    .find((entry) => entry.action !== "created");
  const normalized = {
    id: item.id || createId(),
    origin: item.origin || "manual",
    source_type: item.source_type || "other",
    source_name: item.source_name || "手动输入",
    title: item.title || "未命名证据",
    url: item.url || "",
    published_at: item.published_at || null,
    fetched_at: item.fetched_at || now,
    quote: item.quote || "",
    claim: item.claim || "",
    direction: item.direction || "neutral",
    status,
    reviewed_at:
      item.reviewed_at ||
      latestReview?.created_at ||
      (item.origin !== "automatic" && status !== "pending" ? createdAt : null),
    review_note: String(item.review_note ?? latestReview?.review_note ?? ""),
    review_history: reviewHistory,
    metadata: item.metadata && typeof item.metadata === "object" ? item.metadata : {},
    created_at: createdAt,
    updated_at: item.updated_at || now,
    isLocal: item.isLocal ?? !item.case_id,
  };
  EVIDENCE_NUMBER_FIELDS.forEach((field) => {
    const fallback = ["market_alignment", "priced_in_risk", "counterevidence"].includes(field) ? 0 : 3;
    normalized[field] = EvidenceScoring.clamp(item[field] ?? fallback);
  });
  return normalized;
}

function prepareImportedEvent(item) {
  const raw = item && typeof item === "object" ? item : {};
  const hasOverrides = raw.metricOverrides && typeof raw.metricOverrides === "object";
  const metricOverrides = hasOverrides
    ? raw.metricOverrides
    : Object.fromEntries(METRICS.map((metric) => [metric.key, true]));
  const evidence = Array.isArray(raw.evidence)
    ? raw.evidence.map((entry) =>
        normalizeEvidence({ ...entry, id: createId(), case_id: undefined, isLocal: true })
      )
    : [];
  return migrateEvent({
    ...raw,
    id: createId(),
    caseId: "",
    serverAnalysis: null,
    metricOverrides,
    evidence,
  });
}

function handleDiscoveryInput() {
  if (isHydrating) return;
  const event = state.events[state.activeIndex];
  event.discoveryQuery = elements.discoveryQuery.value;
  event.discoveryStart = elements.discoveryStart.value;
  event.discoveryEnd = elements.discoveryEnd.value;
  scheduleSave();
}

function handleEvidenceFilter(event) {
  const button = event.target.closest("[data-evidence-filter]");
  if (!button) return;
  state.events[state.activeIndex].evidenceFilter = button.dataset.evidenceFilter;
  renderEvidenceWorkspace();
  scheduleSave();
}

function handleEvidenceAction(event) {
  const button = event.target.closest("[data-evidence-action]");
  if (!button) return;
  const item = state.events[state.activeIndex].evidence.find(
    (evidence) => evidence.id === button.dataset.evidenceId
  );
  if (!item) return;
  if (button.dataset.evidenceAction === "edit") openEvidenceDialog(item);
  if (button.dataset.evidenceAction === "accept") reviewEvidence(item.id, "accepted");
  if (button.dataset.evidenceAction === "reject") reviewEvidence(item.id, "rejected");
}

function renderEvidenceWorkspace() {
  const event = state.events[state.activeIndex];
  const items = event.evidence;
  const counts = {
    all: items.length,
    pending: items.filter((item) => item.status === "pending").length,
    accepted: items.filter((item) => item.status === "accepted").length,
    rejected: items.filter((item) => item.status === "rejected").length,
  };
  elements.allEvidenceCount.textContent = counts.all;
  elements.pendingEvidenceCount.textContent = counts.pending;
  elements.acceptedEvidenceCount.textContent = counts.accepted;
  elements.rejectedEvidenceCount.textContent = counts.rejected;
  elements.evidenceReviewSummary.textContent = counts.all
    ? [
        `${counts.accepted} 条已采纳参与评分`,
        counts.pending ? `${counts.pending} 条待审核不计分` : "",
        counts.rejected ? `${counts.rejected} 条已排除` : "",
      ]
        .filter(Boolean)
        .join(" · ")
    : "0 条证据";
  elements.evidenceFilterRow.hidden = counts.all === 0;
  elements.evidenceFilters.querySelectorAll("[data-evidence-filter]").forEach((button) => {
    button.setAttribute("aria-pressed", String(button.dataset.evidenceFilter === event.evidenceFilter));
  });

  renderBackendState();
  elements.evidenceList.replaceChildren();
  if (isDiscovering) {
    for (let index = 0; index < 3; index += 1) {
      const skeleton = document.createElement("div");
      skeleton.className = "evidence-skeleton";
      skeleton.setAttribute("aria-hidden", "true");
      elements.evidenceList.append(skeleton);
    }
    return;
  }

  const visibleItems =
    event.evidenceFilter === "all"
      ? items
      : items.filter((item) => item.status === event.evidenceFilter);
  if (!visibleItems.length) {
    const empty = document.createElement("p");
    empty.className = "evidence-empty";
    empty.textContent =
      event.evidenceFilter === "all"
        ? "尚无证据。可手动添加，或使用自动发现检索公告。"
        : "当前状态下暂无证据";
    elements.evidenceList.append(empty);
    return;
  }

  visibleItems.forEach((item) => {
    const article = document.createElement("article");
    article.className = "evidence-item";
    article.dataset.status = item.status;
    const isReviewing = reviewingEvidenceIds.has(item.id);
    if (isReviewing) article.setAttribute("aria-busy", "true");
    const url = safeHttpUrl(item.url);
    const title = url
      ? `<a href="${escapeHtml(url)}" target="_blank" rel="noreferrer">${escapeHtml(item.title)}</a>`
      : escapeHtml(item.title);
    const copy = item.claim || item.quote;
    article.innerHTML = `
      <div class="evidence-item-heading">
        <div class="evidence-item-main">
          <div class="evidence-badges">
            <span class="evidence-badge status-${escapeHtml(item.status)}">${escapeHtml(EVIDENCE_STATUS_LABELS[item.status] || item.status)}</span>
            <span class="evidence-badge">${item.origin === "automatic" ? "自动发现" : "手动输入"}</span>
            <span class="evidence-badge direction-${escapeHtml(item.direction)}">${escapeHtml(DIRECTION_LABELS[item.direction] || "中性")}</span>
          </div>
          <h4>${title}</h4>
          ${copy ? `<p class="evidence-item-copy">${escapeHtml(copy)}</p>` : ""}
          <p class="evidence-meta">
            <span>${escapeHtml(item.source_name || SOURCE_TYPE_LABELS[item.source_type] || "未知来源")}</span>
            <span>${escapeHtml(formatEvidenceDate(item.published_at))}</span>
            <span>可靠性 ${formatScore(item.reliability)}/5</span>
            <span>相关性 ${formatScore(item.relevance)}/5</span>
            ${item.reviewed_at ? `<span>最近审核 ${escapeHtml(formatReviewTimestamp(item.reviewed_at))}</span>` : ""}
            ${item.isLocal ? "<span>仅本地</span>" : ""}
          </p>
          ${renderEvidenceAudit(item)}
        </div>
        <div class="evidence-actions">
          ${
            item.status !== "accepted"
              ? `<button class="button button-accept" type="button" data-evidence-action="accept" data-evidence-id="${escapeHtml(item.id)}" ${isReviewing ? "disabled" : ""}>采纳</button>`
              : ""
          }
          ${
            item.status !== "rejected"
              ? `<button class="button button-reject" type="button" data-evidence-action="reject" data-evidence-id="${escapeHtml(item.id)}" ${isReviewing ? "disabled" : ""}>排除</button>`
              : ""
          }
          <button class="icon-button" type="button" data-evidence-action="edit" data-evidence-id="${escapeHtml(item.id)}" aria-label="编辑证据" title="编辑证据" ${isReviewing ? "disabled" : ""}>
            <svg class="icon" viewBox="0 0 24 24" aria-hidden="true"><path d="m4 16-1 5 5-1L19 9l-4-4L4 16Z"/><path d="m13 7 4 4"/></svg>
          </button>
        </div>
      </div>`;
    elements.evidenceList.append(article);
  });
}

function formatEvidenceDate(value) {
  if (!value) return "日期未记录";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return String(value).slice(0, 10);
  return new Intl.DateTimeFormat("zh-CN", {
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
  }).format(date);
}

function formatReviewTimestamp(value) {
  if (!value) return "时间未记录";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return String(value);
  return new Intl.DateTimeFormat("zh-CN", {
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  }).format(date);
}

function reviewHistoryDescription(entry) {
  const toLabel = EVIDENCE_STATUS_LABELS[entry.to_status] || entry.to_status;
  if (entry.action === "created") return `创建时：${toLabel}`;
  if (entry.action === "status_changed") {
    const fromLabel = EVIDENCE_STATUS_LABELS[entry.from_status] || entry.from_status || "未设置";
    return `${fromLabel} → ${toLabel}`;
  }
  return `${REVIEW_ACTION_LABELS[entry.action] || "审核记录"}（${toLabel}）`;
}

function renderEvidenceAudit(item) {
  const history = Array.isArray(item.review_history) ? item.review_history : [];
  if (!history.length) return "";
  const entries = history
    .map(
      (entry) => `
        <li>
          <div>
            <strong>${escapeHtml(reviewHistoryDescription(entry))}</strong>
            <time datetime="${escapeHtml(entry.created_at || "")}">${escapeHtml(formatReviewTimestamp(entry.created_at))}</time>
          </div>
          ${entry.review_note ? `<p>${escapeHtml(entry.review_note)}</p>` : ""}
        </li>`
    )
    .join("");
  return `
    <details class="evidence-audit">
      <summary>审核记录 ${history.length}</summary>
      <ol>${entries}</ol>
    </details>`;
}

function openEvidenceDialog(item = null) {
  elements.evidenceForm.reset();
  elements.evidenceDialogStatus.textContent = "";
  elements.evidenceDialogTitle.textContent = item ? "编辑证据" : "添加证据";
  elements.evidenceDialogSubtitle.textContent = item?.origin === "automatic"
    ? "核对自动发现结果后再决定是否采纳。"
    : "记录可核验的来源、事实和判断。";
  const values = item || {
    ...normalizeEvidence({ isLocal: true }),
    source_name: "",
    title: "",
  };
  elements.evidenceForm.elements.evidenceId.value = item?.id || "";
  [
    "source_type",
    "source_name",
    "title",
    "url",
    "claim",
    "quote",
    "direction",
    "status",
    "review_note",
    ...EVIDENCE_NUMBER_FIELDS,
  ].forEach((field) => {
    const input = elements.evidenceForm.elements[field];
    if (input) input.value = values[field] ?? "";
  });
  elements.evidenceForm.elements.published_at.value = values.published_at
    ? String(values.published_at).slice(0, 10)
    : "";
  elements.evidenceDialog.showModal();
  window.setTimeout(() => elements.evidenceForm.elements.title.focus(), 0);
}

function closeEvidenceDialog() {
  if (elements.evidenceDialog.open) elements.evidenceDialog.close();
}

async function saveEvidence(event) {
  event.preventDefault();
  if (isSavingEvidence) return;
  isSavingEvidence = true;
  elements.saveEvidenceButton.disabled = true;
  elements.saveEvidenceButton.textContent = "保存中...";
  const current = state.events[state.activeIndex];
  const formData = new FormData(elements.evidenceForm);
  const evidenceId = formData.get("evidenceId");
  const existingIndex = current.evidence.findIndex((item) => item.id === evidenceId);
  const existing = existingIndex >= 0 ? current.evidence[existingIndex] : null;
  const payload = evidencePayloadFromForm(formData);
  const updatedAt = new Date().toISOString();
  const localItem = normalizeEvidence({
    ...existing,
    ...payload,
    id: existing?.id || createId(),
    origin: existing?.origin || "manual",
    isLocal: existing?.isLocal ?? true,
    updated_at: updatedAt,
  });
  if (existing) {
    const statusChanged = existing.status !== localItem.status;
    const noteChanged = existing.review_note !== localItem.review_note;
    if (statusChanged || noteChanged) {
      appendLocalReviewHistory(
        localItem,
        statusChanged ? "status_changed" : "note_updated",
        existing.status,
        localItem.status,
        localItem.review_note,
        updatedAt
      );
    }
  }

  if (existingIndex >= 0) current.evidence[existingIndex] = localItem;
  else current.evidence.unshift(localItem);
  current.serverAnalysis = null;
  closeEvidenceDialog();
  renderEvidenceWorkspace();
  hydrateForm();
  renderResults();
  persistState();

  try {
    if (backendState.available && current.stockCode.trim()) {
      await ensureCase(current);
      if (existing && !existing.isLocal) {
        const updated = await apiFetch(`/api/evidence/${encodeURIComponent(existing.id)}`, {
          method: "PATCH",
          body: evidenceApiPayload(localItem),
        });
        current.evidence[existingIndex] = normalizeEvidence({ ...updated, isLocal: false });
      } else {
        await syncLocalEvidence(current);
      }
      await refreshServerAnalysis({ quiet: true, targetEvent: current });
      showStatus("证据已保存并同步");
    } else {
      showStatus("证据已保存在当前浏览器");
    }
  } catch (error) {
    showStatus(`证据已本地保存；同步失败：${error.message}`, true);
  } finally {
    isSavingEvidence = false;
    elements.saveEvidenceButton.disabled = false;
    elements.saveEvidenceButton.textContent = "保存证据";
    renderEvidenceWorkspace();
    renderResults();
    persistState();
  }
}

function evidencePayloadFromForm(formData) {
  const date = String(formData.get("published_at") || "");
  const payload = {
    source_type: String(formData.get("source_type") || "other"),
    source_name: String(formData.get("source_name") || "手动输入").trim(),
    title: String(formData.get("title") || "").trim(),
    url: String(formData.get("url") || "").trim(),
    published_at: date ? `${date}T00:00:00+08:00` : null,
    claim: String(formData.get("claim") || "").trim(),
    quote: String(formData.get("quote") || "").trim(),
    direction: String(formData.get("direction") || "neutral"),
    status: String(formData.get("status") || "accepted"),
    review_note: String(formData.get("review_note") || "").trim(),
  };
  EVIDENCE_NUMBER_FIELDS.forEach((field) => {
    payload[field] = EvidenceScoring.clamp(formData.get(field));
  });
  return payload;
}

function evidenceApiPayload(item) {
  return {
    source_type: item.source_type,
    source_name: item.source_name,
    title: item.title,
    url: item.url,
    published_at: item.published_at,
    quote: item.quote,
    claim: item.claim,
    direction: item.direction,
    reliability: item.reliability,
    relevance: item.relevance,
    freshness: item.freshness,
    materiality: item.materiality,
    immediacy: item.immediacy,
    novelty: item.novelty,
    market_alignment: item.market_alignment,
    priced_in_risk: item.priced_in_risk,
    counterevidence: item.counterevidence,
    status: item.status,
    reviewed_at: item.reviewed_at,
    review_note: item.review_note,
    review_history: item.review_history,
  };
}

async function reviewEvidence(evidenceId, status) {
  if (reviewingEvidenceIds.has(evidenceId)) return;
  const current = state.events[state.activeIndex];
  const index = current.evidence.findIndex((item) => item.id === evidenceId);
  if (index < 0) return;
  const item = current.evidence[index];
  const previousStatus = item.status;
  const reviewedAt = new Date().toISOString();
  reviewingEvidenceIds.add(evidenceId);
  item.status = status;
  item.updated_at = reviewedAt;
  appendLocalReviewHistory(item, "status_changed", previousStatus, status, "", reviewedAt);
  current.serverAnalysis = null;
  renderEvidenceWorkspace();
  hydrateForm();
  renderResults();
  persistState();

  try {
    if (backendState.available && current.stockCode.trim()) {
      await ensureCase(current);
      if (item.isLocal) {
        await syncLocalEvidence(current);
      } else {
        const updated = await apiFetch(
          `/api/evidence/${encodeURIComponent(item.id)}`,
          { method: "PATCH", body: { status, review_note: "" } }
        );
        const updatedIndex = current.evidence.findIndex((entry) => entry.id === item.id);
        if (updatedIndex >= 0) {
          current.evidence[updatedIndex] = normalizeEvidence({ ...updated, isLocal: false });
        }
      }
      await refreshServerAnalysis({ quiet: true, targetEvent: current });
      showStatus(status === "accepted" ? "证据已采纳" : "证据已排除");
    } else {
      showStatus(status === "accepted" ? "证据已在本地采纳" : "证据已在本地排除");
    }
  } catch (error) {
    showStatus(`审核结果仅保存在本地：${error.message}`, true);
  } finally {
    reviewingEvidenceIds.delete(evidenceId);
    renderEvidenceWorkspace();
    renderResults();
    persistState();
  }
}

async function resetMetricOverrides() {
  const current = state.events[state.activeIndex];
  current.metricOverrides = {};
  current.serverAnalysis = null;
  hydrateForm();
  renderResults();
  persistState();
  if (backendState.available && current.caseId) {
    await refreshServerAnalysis({ quiet: true, targetEvent: current });
  }
  showStatus("已恢复证据推导评分");
}

async function discoverEvidence() {
  const current = state.events[state.activeIndex];
  if (!backendState.available || isDiscovering) return;
  if (!/^\d{6}$/.test(current.stockCode.trim())) {
    showStatus("自动发现需要填写 6 位 A 股股票代码", true);
    document.getElementById("stockCode").focus();
    return;
  }
  if (current.discoveryStart && current.discoveryEnd && current.discoveryStart > current.discoveryEnd) {
    showStatus("检索开始日期不能晚于结束日期", true);
    elements.discoveryStart.focus();
    return;
  }

  isDiscovering = true;
  const buttonMarkup = elements.discoverEvidenceButton.innerHTML;
  elements.discoverEvidenceButton.disabled = true;
  elements.discoverEvidenceButton.textContent = "检索中...";
  renderEvidenceWorkspace();
  try {
    await ensureCase(current);
    await syncLocalEvidence(current);
    const result = await apiFetch(`/api/cases/${encodeURIComponent(current.caseId)}/discover`, {
      method: "POST",
      body: {
        start_date: current.discoveryStart || null,
        end_date: current.discoveryEnd || null,
        query: current.discoveryQuery || "",
        limit: 20,
      },
    });
    await loadRemoteEvidence({ quiet: true });
    if (result.created_count > 0) current.evidenceFilter = "pending";
    const duplicateText = result.duplicate_count ? `，跳过 ${result.duplicate_count} 条重复项` : "";
    showStatus(`发现 ${result.created_count} 条待审核证据${duplicateText}；采纳后才参与评分`);
  } catch (error) {
    showStatus(`自动发现失败：${error.message}`, true);
  } finally {
    isDiscovering = false;
    elements.discoverEvidenceButton.innerHTML = buttonMarkup;
    elements.discoverEvidenceButton.disabled = !backendState.available;
    renderEvidenceWorkspace();
    renderResults();
    persistState();
  }
}

async function detectBackend() {
  const controller = new AbortController();
  const timeout = window.setTimeout(() => controller.abort(), 1800);
  try {
    const health = await apiFetch("./api/health", { signal: controller.signal });
    backendState = {
      available: health?.status === "ok",
      checking: false,
      version: health?.version || "",
    };
  } catch (_error) {
    backendState = { available: false, checking: false, version: "" };
  } finally {
    window.clearTimeout(timeout);
    renderBackendState();
  }
  if (backendState.available) await loadRemoteEvidence({ quiet: true });
}

function renderBackendState() {
  if (!elements.runtimeStatus) return;
  elements.runtimeStatus.className = "privacy-status";
  if (backendState.checking) {
    elements.runtimeStatus.classList.add("local");
    elements.runtimeStatus.innerHTML = '<span class="status-dot" aria-hidden="true"></span><span>正在检测运行模式</span>';
    elements.evidenceModeBadge.textContent = "检测中";
    elements.evidenceModeBadge.className = "runtime-badge";
  } else if (backendState.available) {
    elements.runtimeStatus.innerHTML = '<span class="status-dot" aria-hidden="true"></span><span>本地服务已连接</span>';
    elements.evidenceModeBadge.textContent = `混合模式${backendState.version ? ` v${backendState.version}` : ""}`;
    elements.evidenceModeBadge.className = "runtime-badge connected";
  } else {
    elements.runtimeStatus.classList.add("local");
    elements.runtimeStatus.innerHTML = '<span class="status-dot" aria-hidden="true"></span><span>仅在浏览器本地处理</span>';
    elements.evidenceModeBadge.textContent = "本地手动模式";
    elements.evidenceModeBadge.className = "runtime-badge";
  }
  elements.discoveryControls.hidden = !backendState.available;
  elements.discoverEvidenceButton.disabled = !backendState.available || isDiscovering;
  elements.discoverEvidenceButton.title = backendState.available
    ? "从巨潮资讯检索公司公告"
    : "运行 python -m server 后可用";
}

async function ensureCase(event) {
  const stockCode = event.stockCode.trim();
  if (!stockCode) throw new Error("请先填写股票代码");
  const payload = {
    stock_code: stockCode,
    company: event.company.trim(),
    event_title: event.title.trim(),
    event_type: event.eventType,
    event_date: event.eventDate || null,
    query: event.discoveryQuery || "",
  };
  if (event.caseId) {
    try {
      await apiFetch(`/api/cases/${encodeURIComponent(event.caseId)}`, {
        method: "PATCH",
        body: payload,
      });
      return event.caseId;
    } catch (error) {
      if (error.status !== 404) throw error;
      event.caseId = "";
    }
  }
  const created = await apiFetch("/api/cases", { method: "POST", body: payload });
  event.caseId = created.id;
  persistState();
  return event.caseId;
}

async function syncLocalEvidence(event) {
  if (!event.caseId) return;
  for (let index = 0; index < event.evidence.length; index += 1) {
    const item = event.evidence[index];
    if (!item.isLocal) continue;
    const response = await apiFetch(`/api/cases/${encodeURIComponent(event.caseId)}/evidence`, {
      method: "POST",
      body: evidenceApiPayload(item),
    });
    event.evidence[index] = normalizeEvidence({ ...response.item, isLocal: false });
  }
  persistState();
}

async function loadRemoteEvidence({ quiet = false } = {}) {
  const event = state.events[state.activeIndex];
  if (!backendState.available || !event.caseId) return;
  try {
    await syncLocalEvidence(event);
    const response = await apiFetch(`/api/cases/${encodeURIComponent(event.caseId)}/evidence`);
    event.evidence = response.items.map((item) => normalizeEvidence({ ...item, isLocal: false }));
    event.serverAnalysis = null;
    await refreshServerAnalysis({ quiet: true, targetEvent: event });
    if (state.events[state.activeIndex].id === event.id) {
      renderEvidenceWorkspace();
      hydrateForm();
      renderResults();
    }
    persistState();
  } catch (error) {
    if (error.status === 404) {
      event.caseId = "";
      event.serverAnalysis = null;
      event.evidence = event.evidence.map((item) => ({ ...item, isLocal: true }));
      persistState();
    } else if (!quiet) {
      showStatus(`读取证据失败：${error.message}`, true);
    }
  }
}

async function refreshServerAnalysis({ quiet = false, targetEvent = null } = {}) {
  const event = targetEvent || state.events[state.activeIndex];
  if (!backendState.available || !event.caseId) return null;
  try {
    const result = await apiFetch(`/api/cases/${encodeURIComponent(event.caseId)}/score`, {
      method: "POST",
      body: { metrics: metricOverrideValues(event) },
    });
    event.serverAnalysis = result;
    if (state.events[state.activeIndex].id === event.id) {
      hydrateForm();
      renderResults();
    }
    persistState();
    return result;
  } catch (error) {
    if (!quiet) showStatus(`服务端评分失败：${error.message}`, true);
    return null;
  }
}

async function apiFetch(path, options = {}) {
  const request = {
    method: options.method || "GET",
    signal: options.signal,
    headers: { Accept: "application/json" },
  };
  if (options.body !== undefined) {
    request.headers["Content-Type"] = "application/json";
    request.body = JSON.stringify(options.body);
  }
  let response;
  try {
    response = await fetch(path, request);
  } catch (error) {
    if (error.name === "AbortError") throw new Error("连接超时");
    throw new Error("无法连接本地服务");
  }
  if (response.status === 204) return null;
  const contentType = response.headers.get("content-type") || "";
  const data = contentType.includes("application/json") ? await response.json() : null;
  if (!response.ok) {
    const error = new Error(data?.detail || `请求失败（HTTP ${response.status}）`);
    error.status = response.status;
    throw error;
  }
  return data;
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
  const effective = effectiveEvent(current);
  FORM_FIELDS.forEach((field) => {
    const input = document.getElementById(field);
    if (!input) return;
    input.value = METRICS.some((metric) => metric.key === field)
      ? effective[field] ?? 0
      : current[field] ?? "";
    if (input.type === "range") {
      document.getElementById(`${field}Value`).textContent = formatScore(effective[field] ?? 0);
      const metric = METRICS.find((item) => item.key === field);
      input.setAttribute("aria-valuetext", metricAriaText(effective[field] ?? 0, metric?.risk));
    }
  });
  elements.discoveryQuery.value = current.discoveryQuery || "";
  elements.discoveryStart.value = current.discoveryStart || dateDaysAgo(90);
  elements.discoveryEnd.value = current.discoveryEnd || localDateString();
  renderMetricMode();
  isHydrating = false;
}

function renderResults() {
  const scoringEvents = state.events.map(effectiveEvent);
  const analysis = CatalystScoring.scorePayload({ events: scoringEvents });
  const currentResult = analysis.events[state.activeIndex];
  const currentEvent = state.events[state.activeIndex];
  const hasInput = Boolean(currentEvent.title.trim() || currentEvent.company.trim() || currentEvent.stockCode.trim());
  const score = hasInput && currentResult ? currentResult.score : 0;
  const eventIdentity = [currentEvent.stockCode.trim(), currentEvent.company.trim()].filter(Boolean).join(" · ");

  elements.resultContext.textContent = eventIdentity || "分析结果";
  elements.resultContext.title = eventIdentity;
  elements.scoreValue.textContent = hasInput ? formatScore(score) : "—";
  elements.scoreRing.style.setProperty("--score-angle", `${score * 3.6}deg`);
  elements.scoreRing.style.setProperty("--score-color", hasInput ? scoreColor(score) : "var(--subtle)");
  elements.scoreRing.setAttribute("aria-valuenow", String(score));
  elements.scoreRing.setAttribute(
    "aria-valuetext",
    hasInput ? `催化强度 ${formatScore(score)}，满分 100` : "待评估"
  );
  elements.verdictLabel.textContent = hasInput
    ? CatalystScoring.gradeLabel(currentResult?.grade || "no_events")
    : "等待分析";
  renderConfidence(hasInput ? currentResult?.confidence || "Low" : "Pending");
  elements.verdictSummary.textContent = verdictSummary(currentResult, currentEvent);
  elements.scoreBasis.textContent = scoreBasisText(currentEvent, hasInput);
  elements.copyReportButton.disabled = !hasInput;
  elements.downloadReportButton.disabled = !hasInput;
  elements.eventCount.textContent = analysis.summary.event_count;
  elements.averageScore.textContent = formatScore(analysis.summary.average_score);
  elements.highestScore.textContent = formatScore(analysis.summary.highest_score || 0);

  renderEvidenceQuality(currentEvent);
  renderComponentBars(currentResult);
  renderReport(currentResult, currentEvent);
  renderEventComparison(analysis);
  elements.jsonOutput.textContent = JSON.stringify(buildExportPayload(analysis), null, 2);
}

function getEvidenceAnalysis(event) {
  const local = EvidenceScoring.analyze(event.evidence, metricOverrideValues(event));
  const remote = event.serverAnalysis;
  if (
    remote &&
    remote.total_evidence_count === local.total_evidence_count &&
    remote.accepted_evidence_count === local.accepted_evidence_count
  ) {
    return remote;
  }
  return local;
}

function effectiveEvent(event) {
  const evidenceAnalysis = getEvidenceAnalysis(event);
  if (!evidenceAnalysis.accepted_evidence_count) return { ...event };
  return { ...event, ...evidenceAnalysis.metrics };
}

function metricOverrideValues(event) {
  return Object.fromEntries(
    METRICS.filter((metric) => event.metricOverrides?.[metric.key]).map((metric) => [
      metric.key,
      Number(event[metric.key]) || 0,
    ])
  );
}

function renderMetricMode() {
  const event = state.events[state.activeIndex];
  const acceptedCount = EvidenceScoring.acceptedEvidence(event.evidence).length;
  const overrideCount = Object.keys(metricOverrideValues(event)).length;
  if (!acceptedCount) {
    elements.metricModeText.textContent = "每项 0-5 分 · 手动";
  } else if (!overrideCount) {
    elements.metricModeText.textContent = "由已采纳证据推导";
  } else {
    elements.metricModeText.textContent = `证据推导 · ${overrideCount} 项人工覆写`;
  }
  elements.resetOverridesButton.hidden = !acceptedCount || !overrideCount;
}

function scoreBasisText(event, hasInput) {
  if (!hasInput) return "填写事件信息后显示评分依据。";
  const analysis = getEvidenceAnalysis(event);
  const acceptedCount = Number(analysis.accepted_evidence_count || 0);
  const overrideCount = Object.keys(metricOverrideValues(event)).length;
  if (!acceptedCount) return "当前按手动维度计算；尚无已采纳证据参与评分。";
  if (!overrideCount) return `由 ${acceptedCount} 条已采纳证据推导；待审核证据不计分。`;
  return `由 ${acceptedCount} 条已采纳证据推导，含 ${overrideCount} 项人工覆写；待审核证据不计分。`;
}

function renderEvidenceQuality(event) {
  const analysis = getEvidenceAnalysis(event);
  const hasAcceptedEvidence = Number(analysis.accepted_evidence_count || 0) > 0;
  const confidence = Number(analysis.evidence_confidence || 0);
  const coverage = Number(analysis.coverage_score || 0);
  elements.evidenceConfidenceValue.textContent = `${formatScore(confidence)}%`;
  elements.coverageValue.textContent = `${formatScore(coverage)}%`;
  setProgress(elements.evidenceConfidenceBar, confidence);
  setProgress(elements.coverageBar, coverage);
  elements.acceptedEvidenceSummary.textContent = analysis.accepted_evidence_count
    ? `${analysis.accepted_evidence_count} 条已采纳 / ${analysis.total_evidence_count} 条总计`
    : "尚无已采纳证据";
  elements.qualityEmptyState.hidden = hasAcceptedEvidence;
  elements.qualityLayout.hidden = !hasAcceptedEvidence;
  elements.coverageChecklist.replaceChildren();
  Object.entries(COVERAGE_LABELS).forEach(([key, label]) => {
    const covered = Boolean(analysis.coverage?.[key]);
    const item = document.createElement("li");
    item.className = covered ? "covered" : "";
    item.innerHTML = `<span aria-hidden="true">${covered ? "✓" : "·"}</span><span>${escapeHtml(label)}</span>`;
    elements.coverageChecklist.append(item);
  });
}

function setProgress(element, value) {
  const safeValue = Math.min(Math.max(Number(value) || 0, 0), 100);
  element.setAttribute("aria-valuenow", String(safeValue));
  element.querySelector("span").style.width = `${safeValue}%`;
}

function renderConfidence(value) {
  elements.confidenceBadge.className = "confidence-badge";
  if (value === "High") elements.confidenceBadge.classList.add("high");
  if (value === "Low") elements.confidenceBadge.classList.add("low");
  if (value === "Pending") elements.confidenceBadge.classList.add("pending");
  elements.confidenceBadge.textContent = {
    High: "规则置信度：高",
    Medium: "规则置信度：中",
    Low: "规则置信度：低",
    Pending: "待评估",
  }[value] || "规则置信度：低";
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
  const hasVerifiableSource =
    safeHttpUrl(event.sourceUrl) ||
    EvidenceScoring.acceptedEvidence(event.evidence).some((item) => safeHttpUrl(item.url));
  if (!hasVerifiableSource) monitoring.unshift("补充一条可核验的一手来源链接。");
  if (Number(event.market_alignment) < 4) monitoring.push("继续观察相对板块、指数、成交量和同行表现。");
  monitoring.push("预先记录会让结论失效的价格、公告或经营信号。");

  return {
    positive: positive.length ? positive : ["当前尚未录入足够的正向驱动项。"],
    risks: risks.length ? risks : ["当前扣分项较低，但仍需检查事件执行和市场环境变化。"],
    monitoring: [...new Set(monitoring)],
  };
}

function renderReport(result, event) {
  const scoredEvent = effectiveEvent(event);
  const model = buildReportModel(result, scoredEvent);
  const source = safeHttpUrl(event.sourceUrl);
  const acceptedEvidence = EvidenceScoring.acceptedEvidence(event.evidence);
  const evidenceRows = acceptedEvidence.length
    ? acceptedEvidence
        .map((item) => {
          const evidenceUrl = safeHttpUrl(item.url);
          const sourceCell = evidenceUrl
            ? `<a href="${escapeHtml(evidenceUrl)}" target="_blank" rel="noreferrer">${escapeHtml(item.source_name || "查看原文")}</a>`
            : escapeHtml(item.source_name || "未提供");
          return `<tr>
            <td>${escapeHtml(formatEvidenceDate(item.published_at))}</td>
            <td>${escapeHtml(SOURCE_TYPE_LABELS[item.source_type] || "其他")}</td>
            <td>${escapeHtml(DIRECTION_LABELS[item.direction] || "中性")}</td>
            <td>${escapeHtml(item.claim || item.title)}</td>
            <td>${sourceCell}</td>
          </tr>`;
        })
        .join("")
    : `<tr>
        <td>${escapeHtml(event.eventDate || "未填写")}</td>
        <td>${escapeHtml(EVENT_TYPE_LABELS[event.eventType] || "其他")}</td>
        <td>未评估</td>
        <td>${escapeHtml(event.title || "未填写事件标题")}</td>
        <td>${
          source
            ? `<a href="${escapeHtml(source)}" target="_blank" rel="noreferrer">查看来源</a>`
            : "未提供"
        }</td>
      </tr>`;

  elements.reportContent.innerHTML = `
    <section class="report-section wide">
      <h3>证据台账</h3>
      <div class="evidence-table-wrap" tabindex="0" role="region" aria-label="证据台账，可横向滚动">
        <table class="evidence-table">
          <thead><tr><th>日期</th><th>来源类型</th><th>方向</th><th>事实主张</th><th>出处</th></tr></thead>
          <tbody>${evidenceRows}</tbody>
        </table>
      </div>
    </section>
    <section class="report-section">
      <h3>正向驱动项</h3>
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
      renderEvidenceWorkspace();
      renderResults();
      loadRemoteEvidence({ quiet: true });
      document.querySelector(".editor-pane").scrollIntoView({ behavior: preferredScrollBehavior(), block: "start" });
    });
    elements.eventComparison.append(row);
  });
}

function preferredScrollBehavior() {
  return window.matchMedia("(prefers-reduced-motion: reduce)").matches ? "auto" : "smooth";
}

function generateMarkdownReport() {
  const analysis = CatalystScoring.scorePayload({ events: state.events.map(effectiveEvent) });
  const result = analysis.events[state.activeIndex];
  const event = state.events[state.activeIndex];
  const scoredEvent = effectiveEvent(event);
  const model = buildReportModel(result, scoredEvent);
  const reportSource = safeHttpUrl(event.sourceUrl);
  const evidenceAnalysis = getEvidenceAnalysis(event);
  const acceptedEvidence = EvidenceScoring.acceptedEvidence(event.evidence);
  const evidenceLines = acceptedEvidence.length
    ? acceptedEvidence.map((item, index) => {
        const itemUrl = safeHttpUrl(item.url);
        const title = markdownText(item.title || item.claim || "未命名证据");
        const source = markdownText(item.source_name || SOURCE_TYPE_LABELS[item.source_type] || "未知来源");
        return `${index + 1}. ${itemUrl ? `[${title}](${itemUrl})` : title}（${source}，${markdownText(DIRECTION_LABELS[item.direction] || "中性")}）`;
      })
    : [
        `1. ${reportSource ? `[${markdownText(event.title || "查看来源")}](${reportSource})` : markdownText(event.title || "未提供已采纳证据")}`,
      ];
  const reviewLines = event.evidence.flatMap((item) =>
    (item.review_history || []).map((entry) => {
      const note = entry.review_note ? `；${markdownText(entry.review_note)}` : "";
      return `${formatReviewTimestamp(entry.created_at)} · ${markdownText(item.title || "未命名证据")} · ${markdownText(reviewHistoryDescription(entry))}${note}`;
    })
  );

  return [
    "# A-Share Catalyst Lens 分析报告",
    "",
    `- **标的**：${markdownText([event.stockCode, event.company].filter(Boolean).join(" · ") || "未填写")}`,
    `- **事件**：${markdownText(event.title || "未填写")}`,
    `- **类型**：${markdownText(EVENT_TYPE_LABELS[event.eventType] || "其他")}`,
    `- **结论**：${markdownText(CatalystScoring.gradeLabel(result.grade))}`,
    `- **催化分数**：${formatScore(result.score)}/100`,
    `- **规则置信度**：${result.confidence}`,
    `- **证据置信度**：${formatScore(evidenceAnalysis.evidence_confidence)}/100`,
    `- **资料覆盖率**：${formatScore(evidenceAnalysis.coverage_score)}/100`,
    `- **已采纳证据**：${evidenceAnalysis.accepted_evidence_count} 条`,
    "",
    "## 正向驱动项",
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
    ...evidenceLines,
    "",
    "## 审核记录",
    "",
    ...(reviewLines.length
      ? reviewLines.map((line, index) => `${index + 1}. ${line}`)
      : ["1. 尚无审核记录。"]),
    "",
    `- **事件日期**：${markdownText(event.eventDate || "未填写")}`,
    `- **事件备注**：${markdownText(event.notes || "无")}`,
    "",
    "> 免责声明：规则评分仅用于研究辅助，不预测价格，也不构成投资建议。",
  ].join("\n");
}

function buildExportPayload(existingAnalysis) {
  const analysis = existingAnalysis || CatalystScoring.scorePayload({ events: state.events.map(effectiveEvent) });
  return {
    schema_version: 3,
    exported_at: new Date().toISOString(),
    active_event_index: state.activeIndex,
    events: state.events,
    evidence_analysis: state.events.map(getEvidenceAnalysis),
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
  elements.appStatus.classList.toggle("is-error", isError);
  statusTimer = window.setTimeout(() => {
    elements.appStatus.textContent = "";
    elements.appStatus.classList.remove("is-error");
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
