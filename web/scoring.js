(function (root, factory) {
  const api = factory();
  if (typeof module === "object" && module.exports) {
    module.exports = api;
  }
  root.CatalystScoring = api;
})(typeof globalThis !== "undefined" ? globalThis : this, function () {
  "use strict";

  const WEIGHTS = {
    source_reliability: 20,
    materiality: 20,
    immediacy: 10,
    novelty: 10,
    confirmation: 10,
    market_alignment: 10,
  };

  const PENALTIES = {
    priced_in_risk: 10,
    counterevidence: 10,
  };

  const METRIC_FIELDS = [...Object.keys(WEIGHTS), ...Object.keys(PENALTIES)];

  function clamp(value, low = 0, high = 5) {
    const number = Number(value);
    if (!Number.isFinite(number)) return low;
    return Math.min(Math.max(number, low), high);
  }

  function metricValue(event, key, index, warnings) {
    const raw = event[key];
    if (raw === undefined || raw === null || raw === "") {
      warnings.push(`events[${index}].${key} is missing; using 0`);
      return 0;
    }
    const number = Number(raw);
    if (!Number.isFinite(number)) {
      warnings.push(`events[${index}].${key} is not numeric; using 0`);
      return 0;
    }
    if (number < 0 || number > 5) {
      const clamped = clamp(number);
      warnings.push(`events[${index}].${key}=${number} is outside 0-5; clamped to ${clamped}`);
      return clamped;
    }
    return number;
  }

  function grade(score) {
    if (score >= 80) return "strong_positive";
    if (score >= 65) return "constructive";
    if (score >= 50) return "mixed_or_weak_positive";
    if (score >= 35) return "low_confidence";
    return "not_bullish_or_negative";
  }

  function gradeLabel(value) {
    return {
      strong_positive: "强正向催化",
      constructive: "偏正向催化",
      mixed_or_weak_positive: "混合或弱正向",
      low_confidence: "低置信度",
      not_bullish_or_negative: "暂不构成利好",
      no_events: "暂无事件",
    }[value] || "待判断";
  }

  function confidence(metrics, score) {
    const penalty = metrics.counterevidence + metrics.priced_in_risk;
    if (score >= 75 && metrics.source_reliability >= 4 && metrics.confirmation >= 4 && penalty <= 4) {
      return "High";
    }
    if (score >= 55 && metrics.source_reliability >= 3 && metrics.confirmation >= 3 && penalty <= 6) {
      return "Medium";
    }
    return "Low";
  }

  function scoreEvent(event, index = 0, warnings = []) {
    const metrics = Object.fromEntries(
      METRIC_FIELDS.map((key) => [key, metricValue(event, key, index, warnings)])
    );
    const components = {};
    let score = 0;

    Object.entries(WEIGHTS).forEach(([key, weight]) => {
      const component = (metrics[key] / 5) * weight;
      components[key] = Number(component.toFixed(2));
      score += component;
    });

    Object.entries(PENALTIES).forEach(([key, weight]) => {
      const component = (metrics[key] / 5) * weight;
      components[key] = Number((-component).toFixed(2));
      score -= component;
    });

    score = Math.min(Math.max(score, 0), 100);
    const roundedScore = Number(score.toFixed(1));
    return {
      title: event.title || event.claim || "未命名事件",
      score: roundedScore,
      grade: grade(roundedScore),
      confidence: confidence(metrics, roundedScore),
      components,
      notes: event.notes || "",
    };
  }

  function summarize(results) {
    if (!results.length) {
      return { event_count: 0, average_score: 0, overall_grade: "no_events" };
    }
    const average = results.reduce((total, item) => total + item.score, 0) / results.length;
    return {
      event_count: results.length,
      average_score: Number(average.toFixed(1)),
      overall_grade: grade(average),
      highest_score: Math.max(...results.map((item) => item.score)),
      lowest_score: Math.min(...results.map((item) => item.score)),
    };
  }

  function scorePayload(payload) {
    const warnings = [];
    let events = payload && payload.events;
    if (!payload || !Object.prototype.hasOwnProperty.call(payload, "events")) {
      warnings.push("events array is missing; using []");
      events = [];
    }
    if (!Array.isArray(events)) {
      throw new TypeError("input JSON must contain an events array");
    }

    const results = [];
    events.forEach((event, index) => {
      if (!event || typeof event !== "object" || Array.isArray(event)) {
        warnings.push(`events[${index}] is not an object; skipped`);
        return;
      }
      results.push(scoreEvent(event, index, warnings));
    });

    const output = { summary: summarize(results), events: results };
    if (warnings.length) output.warnings = warnings;
    return output;
  }

  return {
    WEIGHTS,
    PENALTIES,
    METRIC_FIELDS,
    clamp,
    grade,
    gradeLabel,
    confidence,
    scoreEvent,
    scorePayload,
  };
});
