"use strict";

const assert = require("node:assert/strict");
const test = require("node:test");
const evidence = require("../web/evidence.js");

const official = {
  status: "accepted",
  source_type: "exchange_announcement",
  source_name: "巨潮资讯",
  direction: "positive",
  url: "https://static.cninfo.com.cn/demo.pdf",
  quote: "重大合同公告",
  reliability: 5,
  relevance: 4,
  freshness: 5,
  materiality: 4,
  immediacy: 5,
  novelty: 5,
  market_alignment: 0,
  priced_in_risk: 0,
  counterevidence: 0,
};

const market = {
  status: "accepted",
  source_type: "market_data",
  source_name: "手动行情记录",
  direction: "positive",
  url: "https://example.com/market",
  quote: "个股与板块同步放量",
  reliability: 3,
  relevance: 4,
  freshness: 5,
  materiality: 3,
  immediacy: 4,
  novelty: 3,
  market_alignment: 4,
  priced_in_risk: 1,
  counterevidence: 0,
};

test("pending evidence never affects accepted analysis", () => {
  const output = evidence.analyze([{ ...official, status: "pending" }]);
  assert.equal(output.accepted_evidence_count, 0);
  assert.equal(output.evidence_confidence, 0);
  assert.equal(output.coverage_score, 0);
  assert.equal(output.metrics.materiality, 0);
});

test("accepted evidence derives metrics, confidence, and coverage", () => {
  const output = evidence.analyze([official, market]);
  assert.equal(output.accepted_evidence_count, 2);
  assert.equal(output.evidence_confidence, 83);
  assert.equal(output.coverage_score, 60);
  assert.equal(output.coverage.official_source, true);
  assert.equal(output.coverage.market_data, true);
  assert.equal(output.metrics.materiality, 4);
  assert.equal(output.metrics.market_alignment, 2);
});

test("manual overrides replace only selected derived metrics", () => {
  const output = evidence.analyze([official, market], { market_alignment: 5 });
  assert.equal(output.metrics.market_alignment, 5);
  assert.equal(output.metrics.materiality, 4);
});

test("contradictory accepted evidence lowers confidence", () => {
  const baseline = evidence.evidenceConfidence([official, market]);
  const contradictory = evidence.evidenceConfidence([
    official,
    { ...market, direction: "negative", source_name: "反证记录" },
  ]);
  assert.equal(contradictory, baseline - 10);
});
