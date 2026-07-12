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

  function clamp(value, low = 0, high = 5) {
    const number = Number(value);
    if (!Number.isFinite(number)) return low;
    return Math.min(Math.max(number, low), high);
  }

  function acceptedEvidence(items) {
    return Array.isArray(items) ? items.filter((item) => item?.status === "accepted") : [];
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
    weightedAverage,
  };
});
