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

test("review history validation accepts a contiguous timezone-aware chain", () => {
  const history = [
    {
      action: "created",
      from_status: null,
      to_status: "pending",
      created_at: "2026-07-14T08:00:00+08:00",
    },
    {
      action: "note_updated",
      from_status: "pending",
      to_status: "pending",
      review_note: "等待核对原文",
      created_at: "2026-07-14T00:00:00Z",
    },
    {
      action: "status_changed",
      from_status: "pending",
      to_status: "accepted",
      review_note: "原文已核对",
      created_at: "2026-07-14T08:05:00+0800",
    },
  ];

  assert.doesNotThrow(() => evidence.validateReviewHistory(history, "accepted"));
  assert.doesNotThrow(() => evidence.validateReviewHistory([], "accepted"));
});

test("review history validation rejects broken audit semantics", () => {
  const created = {
    action: "created",
    from_status: null,
    to_status: "pending",
    created_at: "2026-07-14T00:00:00Z",
  };
  const invalidCases = [
    {
      label: "history is not an array",
      history: null,
      status: "pending",
      message: /审核记录必须是数组/,
    },
    {
      label: "evidence status is invalid",
      history: [],
      status: "unknown",
      message: /证据审核状态无效/,
    },
    {
      label: "missing initial created",
      history: [{ ...created, action: "note_updated" }],
      status: "pending",
      message: /必须以 created 开始/,
    },
    {
      label: "initial from_status is set",
      history: [{ ...created, from_status: "pending" }],
      status: "pending",
      message: /首条 from_status 必须为空/,
    },
    {
      label: "created appears twice",
      history: [created, { ...created, created_at: "2026-07-14T00:01:00Z" }],
      status: "pending",
      message: /只能包含一条 created/,
    },
    {
      label: "status chain is discontinuous",
      history: [
        created,
        {
          action: "status_changed",
          from_status: "accepted",
          to_status: "rejected",
          created_at: "2026-07-14T00:01:00Z",
        },
      ],
      status: "rejected",
      message: /状态不连续/,
    },
    {
      label: "timestamp has no timezone",
      history: [{ ...created, created_at: "2026-07-14T00:00:00" }],
      status: "pending",
      message: /包含时区/,
    },
    {
      label: "timestamp is not a calendar date",
      history: [{ ...created, created_at: "2026-02-30T00:00:00Z" }],
      status: "pending",
      message: /时间必须有效/,
    },
    {
      label: "timestamps decrease",
      history: [
        { ...created, created_at: "2026-07-14T00:01:00Z" },
        {
          action: "note_updated",
          from_status: "pending",
          to_status: "pending",
          created_at: "2026-07-14T00:00:00Z",
        },
      ],
      status: "pending",
      message: /时间必须非递减/,
    },
    {
      label: "timestamps decrease within one millisecond",
      history: [
        { ...created, created_at: "2026-07-14T00:00:00.000999Z" },
        {
          action: "note_updated",
          from_status: "pending",
          to_status: "pending",
          created_at: "2026-07-14T00:00:00.000001Z",
        },
      ],
      status: "pending",
      message: /时间必须非递减/,
    },
    {
      label: "status_changed keeps the same status",
      history: [
        created,
        {
          action: "status_changed",
          from_status: "pending",
          to_status: "pending",
          created_at: "2026-07-14T00:01:00Z",
        },
      ],
      status: "pending",
      message: /status_changed 必须改变状态/,
    },
    {
      label: "note_updated changes status",
      history: [
        created,
        {
          action: "note_updated",
          from_status: "pending",
          to_status: "accepted",
          created_at: "2026-07-14T00:01:00Z",
        },
      ],
      status: "accepted",
      message: /note_updated 不得改变状态/,
    },
    {
      label: "final status differs from evidence",
      history: [created],
      status: "accepted",
      message: /最终状态必须与证据状态一致/,
    },
  ];

  invalidCases.forEach(({ label, history, status, message }) => {
    assert.throws(() => evidence.validateReviewHistory(history, status), message, label);
  });
});
