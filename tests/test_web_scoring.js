"use strict";

const assert = require("node:assert/strict");
const test = require("node:test");
const scoring = require("../web/scoring.js");

const sample = {
  title: "Company announces a verified large order",
  source_reliability: 5,
  materiality: 4,
  immediacy: 4,
  novelty: 3,
  confirmation: 4,
  market_alignment: 3,
  priced_in_risk: 2,
  counterevidence: 1,
};

test("browser scorer matches the Python sample", () => {
  const result = scoring.scoreEvent(sample);
  assert.equal(result.score, 58);
  assert.equal(result.grade, "mixed_or_weak_positive");
  assert.equal(result.confidence, "Medium");
  assert.equal(result.components.priced_in_risk, -4);
});

test("payload summary averages multiple events", () => {
  const output = scoring.scorePayload({ events: [sample, { ...sample, title: "Second event" }] });
  assert.equal(output.summary.event_count, 2);
  assert.equal(output.summary.average_score, 58);
  assert.equal(output.events.length, 2);
});

test("invalid values produce warnings and clamping", () => {
  const output = scoring.scorePayload({
    events: [{ ...sample, source_reliability: 9, materiality: "bad" }],
  });
  assert.ok(output.warnings.some((warning) => warning.includes("source_reliability=9")));
  assert.ok(output.warnings.some((warning) => warning.includes("materiality is not numeric")));
  assert.equal(output.events[0].components.source_reliability, 20);
});
