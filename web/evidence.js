(function (root, factory) {
  const api = factory();
  if (typeof module === "object" && module.exports) {
    module.exports = api;
  }
  root.EvidenceScoring = api;
})(typeof globalThis !== "undefined" ? globalThis : this, function () {
  "use strict";

  const METRIC_FIELDS = [
    "source_reliability",
    "materiality",
    "immediacy",
    "novelty",
    "confirmation",
    "market_alignment",
    "priced_in_risk",
    "counterevidence",
  ];
  const REVIEW_ACTIONS = new Set(["created", "status_changed", "note_updated"]);
  const EVIDENCE_STATUSES = new Set(["pending", "accepted", "rejected"]);

  function clamp(value, low = 0, high = 5) {
    const number = Number(value);
    if (!Number.isFinite(number)) return low;
    return Math.min(Math.max(number, low), high);
  }

  function acceptedEvidence(items) {
    return Array.isArray(items) ? items.filter((item) => item?.status === "accepted") : [];
  }

  function parseAuditTimestamp(value) {
    if (typeof value !== "string") return null;
    const timestamp = value.trim();
    const parts = timestamp.match(/^(\d{4})-(\d{2})-(\d{2})[Tt ](\d{2}):(\d{2})/);
    if (!parts || !/(?:Z|[+-]\d{2}:?\d{2})$/i.test(timestamp)) return null;

    const [, yearText, monthText, dayText, hourText, minuteText] = parts;
    const year = Number(yearText);
    const month = Number(monthText);
    const day = Number(dayText);
    const hour = Number(hourText);
    const minute = Number(minuteText);
    const leapYear = year % 4 === 0 && (year % 100 !== 0 || year % 400 === 0);
    const daysInMonth = [31, leapYear ? 29 : 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31];
    if (
      year < 1 ||
      month < 1 ||
      month > 12 ||
      day < 1 ||
      day > daysInMonth[month - 1] ||
      hour > 23 ||
      minute > 59
    ) {
      return null;
    }

    const parsed = Date.parse(timestamp);
    if (Number.isNaN(parsed)) return null;
    const fraction = timestamp.match(/\.(\d+)(?:Z|[+-]\d{2}:?\d{2})$/i)?.[1] || "";
    const microseconds = Number(`${fraction}000000`.slice(0, 6).slice(3));
    return [parsed, microseconds];
  }

  function validateReviewHistory(entries, finalStatus) {
    if (!EVIDENCE_STATUSES.has(finalStatus)) throw new Error("证据审核状态无效");
    if (entries === undefined) return;
    if (!Array.isArray(entries)) throw new Error("审核记录必须是数组");
    if (!entries.length) return;
    if (entries.length > 100) throw new Error("审核记录不能超过 100 条");

    let currentStatus = null;
    let previousTime = null;
    entries.forEach((entry, index) => {
      const position = `第 ${index + 1} 条审核记录`;
      if (!entry || typeof entry !== "object" || Array.isArray(entry)) {
        throw new Error(`${position}格式无效`);
      }
      if (!REVIEW_ACTIONS.has(entry.action)) throw new Error(`${position}的动作无效`);
      if (!EVIDENCE_STATUSES.has(entry.to_status)) {
        throw new Error(`${position}的目标状态无效`);
      }
      const fromStatus = entry.from_status ?? null;
      if (fromStatus !== null && !EVIDENCE_STATUSES.has(fromStatus)) {
        throw new Error(`${position}的原状态无效`);
      }
      if (entry.review_note !== undefined) {
        if (typeof entry.review_note !== "string") {
          throw new Error(`${position}的审核备注必须是文本`);
        }
        if (entry.review_note.length > 500) {
          throw new Error(`${position}的审核备注不能超过 500 个字符`);
        }
      }

      const timestampValue = parseAuditTimestamp(entry.created_at);
      if (timestampValue === null) {
        throw new Error(`${position}的时间必须有效且包含时区`);
      }

      if (index === 0) {
        if (entry.action !== "created" || fromStatus !== null) {
          throw new Error("审核记录必须以 created 开始，且首条 from_status 必须为空");
        }
      } else {
        if (entry.action === "created") {
          throw new Error("审核记录只能包含一条 created");
        }
        if (fromStatus !== currentStatus) throw new Error(`${position}与上一条状态不连续`);
        if (
          timestampValue[0] < previousTime[0] ||
          (timestampValue[0] === previousTime[0] && timestampValue[1] < previousTime[1])
        ) {
          throw new Error("审核记录时间必须非递减");
        }
        if (entry.action === "status_changed" && entry.to_status === currentStatus) {
          throw new Error(`${position}的 status_changed 必须改变状态`);
        }
        if (entry.action === "note_updated" && entry.to_status !== currentStatus) {
          throw new Error(`${position}的 note_updated 不得改变状态`);
        }
      }

      currentStatus = entry.to_status;
      previousTime = timestampValue;
    });

    if (currentStatus !== finalStatus) {
      throw new Error("审核记录最终状态必须与证据状态一致");
    }
  }

  function weightedAverage(items, field) {
    if (!items.length) return 0;
    const weights = items.map((item) => Math.max(Number(item.relevance) || 0, 0.5));
    const total = items.reduce(
      (sum, item, index) => sum + (Number(item[field]) || 0) * weights[index],
      0
    );
    return clamp(total / weights.reduce((sum, value) => sum + value, 0));
  }

  function recencyScore(value, now = new Date()) {
    if (!value) return 1;
    const published = new Date(value);
    if (Number.isNaN(published.getTime())) return 1;
    const ageDays = Math.max(Math.floor((now.getTime() - published.getTime()) / 86_400_000), 0);
    if (ageDays <= 7) return 5;
    if (ageDays <= 30) return 4;
    if (ageDays <= 90) return 3;
    if (ageDays <= 180) return 2;
    return 1;
  }

  function deriveMetrics(items) {
    if (!items.length) return Object.fromEntries(METRIC_FIELDS.map((field) => [field, 0]));

    const sourceNames = new Set(items.map((item) => item.source_name || item.source_type));
    const confirmation = Math.min(
      5,
      1.5 + Math.max(sourceNames.size - 1, 0) * 1.5 + Math.max(items.length - 1, 0) * 0.5
    );
    const negativeItems = items.filter((item) => ["negative", "mixed"].includes(item.direction));

    return {
      source_reliability: weightedAverage(items, "reliability"),
      materiality: Math.max(...items.map((item) => Number(item.materiality) || 0)),
      immediacy: weightedAverage(items, "immediacy"),
      novelty: weightedAverage(items, "novelty"),
      confirmation: clamp(confirmation),
      market_alignment: weightedAverage(items, "market_alignment"),
      priced_in_risk: Math.max(...items.map((item) => Number(item.priced_in_risk) || 0)),
      counterevidence: Math.max(
        0,
        ...negativeItems.map((item) => Number(item.counterevidence) || Number(item.materiality) || 0)
      ),
    };
  }

  function evidenceConfidence(items) {
    if (!items.length) return 0;
    const average = (field, fallback) =>
      items.reduce((sum, item) => sum + fallback(item[field], item), 0) / items.length;
    const reliability = average("reliability", (value) => Number(value) || 0) / 5;
    const relevance = average("relevance", (value) => Number(value) || 0) / 5;
    const freshness = average(
      "freshness",
      (value, item) => Number(value) || recencyScore(item.published_at)
    ) / 5;
    const sourceDiversity = Math.min(
      new Set(items.map((item) => item.source_name || item.source_type)).size / 3,
      1
    );
    const citationCompleteness = average(
      "url",
      (_value, item) => (item.url ? 0.5 : 0) + (item.quote ? 0.5 : 0)
    );
    const directions = new Set(items.map((item) => item.direction));
    const contradictionPenalty = directions.has("positive") && directions.has("negative") ? 10 : 0;
    const score =
      reliability * 35 +
      relevance * 25 +
      freshness * 15 +
      sourceDiversity * 15 +
      citationCompleteness * 10 -
      contradictionPenalty;
    return Number(Math.min(Math.max(score, 0), 100).toFixed(1));
  }

  function coverage(items) {
    const sourceTypes = new Set(items.map((item) => item.source_type));
    const details = {
      official_source: [
        "exchange_announcement",
        "regulator_policy",
        "company_ir",
        "financial_report",
      ].some((type) => sourceTypes.has(type)),
      financial_impact: items.some((item) => (Number(item.materiality) || 0) >= 3),
      market_data: sourceTypes.has("market_data"),
      peer_context: sourceTypes.has("peer_context"),
      counterevidence: items.some(
        (item) =>
          ["negative", "mixed"].includes(item.direction) ||
          item.source_type === "counterevidence" ||
          (Number(item.counterevidence) || 0) > 0
      ),
    };
    return {
      score: Object.values(details).filter(Boolean).length * 20,
      details,
    };
  }

  function analyze(items, overrides = {}) {
    const accepted = acceptedEvidence(items);
    const metrics = deriveMetrics(accepted);
    Object.entries(overrides || {}).forEach(([key, value]) => {
      if (METRIC_FIELDS.includes(key) && value !== null && value !== undefined) {
        metrics[key] = clamp(value);
      }
    });
    const coverageResult = coverage(accepted);
    return {
      metrics: Object.fromEntries(
        Object.entries(metrics).map(([key, value]) => [key, Number(value.toFixed(2))])
      ),
      evidence_confidence: evidenceConfidence(accepted),
      coverage_score: coverageResult.score,
      coverage: coverageResult.details,
      accepted_evidence_count: accepted.length,
      total_evidence_count: Array.isArray(items) ? items.length : 0,
    };
  }

  return {
    METRIC_FIELDS,
    acceptedEvidence,
    analyze,
    clamp,
    coverage,
    deriveMetrics,
    evidenceConfidence,
    recencyScore,
    validateReviewHistory,
    weightedAverage,
  };
});
