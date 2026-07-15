# Catalyst Watch 融合契约

> 状态：Phase 2 手动刷新行情
> 调研基线：`ZhuLinsen/daily_stock_analysis@55946536a9765b3d4e2620edef6a50e79d0928d0`
> 扫描日期：2026-07-14

## 定位

Catalyst Watch 通过周期采集可信行情和公告，把透明规则命中的异常转换为待审核证据，而不是直接生成买卖信号。

自动化只负责缩短检索和盯盘时间。用户仍然决定哪些材料可以采纳，只有 `accepted` 证据才能进入现有评分与报告。

## 调研结论

`daily_stock_analysis` 是一个大型、多市场、多数据源的分析与通知系统。它在调研时约有 57,143 Stars，采用 MIT 许可证，但仓库体量和产品边界都远大于 A-Share Catalyst Lens。本项目只提炼能力契约，不复制其整体架构。

值得借鉴的部分：

- 统一的行情数据契约，显式记录来源时间、抓取时间、时效性和缺失字段。
- 数据源优先级、fallback、熔断和 last-good cache。
- 调度运行锁、失败隔离、重试与诊断记录。
- 规则、触发、通知尝试和冷却状态分层存储。
- 周期轮询的真实边界：其调度循环最低粒度约 30 秒，默认告警轮询约 5 分钟，不是逐笔行情或 WebSocket 交易终端。

不融合的部分：

- 买入、卖出、目标价、止损价和趋势预测。
- 自动交易、模拟交易、回测和大量交易策略。
- 多市场全量支持、多智能体和重型 LLM 依赖栈。
- 任意搜索 URL、任意自定义 Webhook 或未认证公网服务。

## 核心数据流

```text
自选股
→ monitor_run
→ market_snapshot 原始观察
→ monitor_finding 规则发现
→ automatic + pending evidence
→ 人工审核
→ accepted 后进入现有评分与报告
```

三个层次必须分开：

- `market_snapshot` 是对原始行情的可追溯观察，不是证据。
- `monitor_finding` 是透明规则对快照的命中结果，不是交易信号。
- `evidence` 可以由 finding 转换，但必须是 `origin=automatic` 且 `status=pending`。

## Phase 1：自选股地基

Phase 1 只实现自选股增删、启停、排序和持久化，不访问任何外部行情源。

### 数据表

`watchlist_items`：

| 字段 | 契约 |
| --- | --- |
| `id` | UUID 主键 |
| `stock_code` | 唯一，trim 后必须是 6 位 ASCII 数字 |
| `company` | 可空，最长 100 字符 |
| `enabled` | 是否参与后续盯盘；Phase 1 不触发任务 |
| `sort_order` | 从 0 开始的连续顺序 |
| `created_at` / `updated_at` | UTC ISO 8601 时间 |

`stock_code` 只做词法校验，不根据交易所前缀过早拒绝北交所或未来新号段。代码创建后不可 PATCH，需要更换时删除后重新添加。

### API

- `GET /api/watchlist`
- `POST /api/watchlist`
- `PATCH /api/watchlist/{item_id}`
- `DELETE /api/watchlist/{item_id}`

POST 重复代码时返回现有项与 `created=false`，不覆盖原公司名、启停状态或排序。PATCH `sort_order` 表示“移到目标索引”，服务端在一个 `BEGIN IMMEDIATE` 事务中移动相邻项。DELETE 会同事务压紧后续顺序。

### 运行模式

- 静态模式：自选股保存在独立的 `localStorage` key，不进入事件 JSON 导入导出，也不被“重置事件数据”清除。
- 混合模式：SQLite/API 是唯一权威来源；不自动合并、覆盖或回放静态列表。
- API 操作失败时保留当前界面并显示错误，不静默退化为本地写入。

## Phase 2：手动刷新行情

Phase 2 引入可替换的 provider 契约、`monitor_runs` 和 `market_snapshots`。默认实现只访问腾讯财经固定 HTTPS 单股接口，股票 symbol 由服务端根据已校验的自选股代码生成，不接受用户 URL 或任意抓取目标。

只有用户在本地混合模式点击“刷新盯盘”才会发送带 JSON 请求体的 `POST`。服务端按当前已启用自选股逐只请求；不会定时运行，不重试，也不切换备用源。静态 GitHub Pages 和不具备 `market_provider` 能力的旧 API 不会请求任何 `/api/monitor/*` 路径。

### API

- `POST /api/monitor/refresh`：手动刷新所有已启用自选股；请求体固定为 `{}`。
- `GET /api/monitor/latest`：返回最近运行以及每个当前自选股最后一条可用快照。
- `GET /api/monitor/runs`：列出手动运行记录。
- `GET /api/monitor/snapshots`：按运行或股票代码复核原始快照。

### 数据表

`monitor_runs` 保存 `trigger=manual`、provider、运行状态、请求/成功/失败数量、逐股错误和开始/完成时间。`market_snapshots` 保存 provider、关联运行和自选股，以及价格、涨跌幅、成交量、成交额和以下质量字段：

- `fetched_at`、`provider_timestamp`
- `is_stale`、`stale_seconds`
- `fallback_from`、`data_quality`、`missing_fields`

字段单位与语义：

- `price` 为人民币价格，`change_percent` 为百分比。
- `volume` 统一为股；腾讯字段口径不稳定时，使用价格、换手率和流通市值在“股”与“手”之间交叉校验，无法校验才按手折股。
- `turnover` 为成交额，单位人民币元；优先使用腾讯精确成交额字段，缺失时才使用万元字段换算。
- `fetched_at` 是本系统收到数据的 UTC 时间；`provider_timestamp` 由服务商北京时间转换为 UTC。
- `stale_seconds` 是服务商时间与获取时间的非负秒差；严格超过 900 秒时 `is_stale=true`。
- 服务商时间缺失时，`is_stale` 与 `stale_seconds` 都是 `null`，表示未知，不冒充“新鲜”。
- `data_quality` 只能是 `ok`、`partial` 或 `unavailable`；缺失字段逐项写入 `missing_fields`。
- 单源阶段 `fallback_from` 固定为空，后续如引入备用源才记录来源切换。

`unavailable` 快照会保留在历史中供审计，但计为失败，也不会覆盖 `latest` 的最后可用快照。单股异常不阻断后续股票；运行计数始终满足 `requested_count = success_count + failure_count`。

腾讯接口是本地、低频、best-effort 的公开行情来源，不承诺交易终端级实时性、完整性或持续可用性。Phase 2 不做交易日判断，因此收盘后快照会自然显示为较旧。

这一阶段不生成 finding 或 evidence，不改变评分，不引入调度、通知、交易动作或 LLM。

## 后续阶段

### Phase 3：异常转换为待审核证据

首批只做涨跌幅阈值和成交量异常。finding 保留快照 ID、来源、数据时间、规则阈值和去重键。转换的 `market_data` 证据一律为 `automatic + pending`，不调用 LLM。

### Phase 4：本地定时任务

增加交易日和交易时段判断、单实例运行锁、幂等与去重、有界重试、熔断、last-good cache、运行历史和 provider Doctor。默认低频轮询，单股失败不阻断整批任务。

### Phase 5：通知与可选 AI

分层存储 `alert_rule`、`alert_trigger`、`alert_notification` 和 `alert_cooldown`。通知只表达“发现 N 条待审核信息”，不发送买卖建议或收益预测。通知渠道需逐个接入，使用固定类型与域名白名单，记录尝试、失败和冷却。LLM 只可做摘要、归类和生成核验问题，输出仍为 `pending`。

## 不变式

1. 自动发现或生成的证据默认 `pending`。
2. 只有 `accepted` 证据参与评分。
3. 不宣称股价预测准确率、胜率或确定收益。
4. 不自动请求用户输入的任意 URL。
5. 最终采纳权属于用户。
6. GitHub Pages 保留手动能力；调度和自动发现只在本地后端启用。
7. 催化强度、证据置信度和资料覆盖率始终分开，都不命名为“准确度”。

## 明确留待后续的问题

已存在服务端的证据在离线 PATCH 失败后，仍需独立的操作队列按顺序重放中间审核动作。该能力不属于 Catalyst Watch 行情阶段，不得用单一 dirty 标记替代。
