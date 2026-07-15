# Catalyst Watch 融合契约

> 状态：Phase 4 本地定时任务与数据源韧性
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
- 非有限数、负价格、负成交量和负成交额按缺失值处理，不能以 `ok` 质量写入；负涨跌幅仍是合法行情。
- 单源阶段 `fallback_from` 固定为空，后续如引入备用源才记录来源切换。

`unavailable` 快照会保留在历史中供审计，但计为失败，也不会覆盖 `latest` 的最后可用快照。逐股错误带原始 `watchlist_item_id`，避免删除并重加同代码后误归属。单股异常不阻断后续股票；存储异常会重新抛出，同时尽力把当前运行收敛到终态，终态运行计数始终满足 `requested_count = success_count + failure_count`。

腾讯接口是本地、低频、best-effort 的公开行情来源，不承诺交易终端级实时性、完整性或持续可用性。Phase 2 不做交易日判断，因此收盘后快照会自然显示为较旧。

这一阶段不生成 finding 或 evidence，不改变评分，不引入调度、通知、交易动作或 LLM。

## Phase 3：异常转换为待审核证据

Phase 3 在快照与 evidence 之间增加不可省略的 `monitor_finding` 层。刷新成功后才运行固定、可复核的规则；finding 自身不是证据，也不参与评分。

首批规则：

- `change_percent_threshold@1`：涨跌幅绝对值达到 `5.00%` 时命中，保留有符号观测值和正负方向。
- `volume_ratio@1`：当前累计成交量达到同时间段历史中位数的 `2.00` 倍时命中。
- 成交量基线使用北京时间 30 分钟时段桶，每个历史日期最多选一个最接近当前分钟的同 provider 快照，至少需要 3 个不同历史日期，最多使用最近 20 个日期。
- 同日早晚快照、其他时段、不同 provider、`unavailable`、缺失时间或非正成交量都不进入成交量基线。基线不足时不生成 finding。

finding 去重键由股票代码、provider、服务商观察时间、规则类型和规则版本确定。同一服务商观察被多次手动刷新时不会重复制造 finding；服务商时间缺失时才退化到本地快照标识。

### 数据表

- `monitor_findings`：保存快照、运行、自选股、股票代码、provider、服务商时间、规则类型与版本、方向、观测值、阈值、基线、基线数量、规则细节和稳定去重键。
- `monitor_finding_evidence`：按 finding 与 case 关联 evidence；同一 finding 在同一 case 下只能转换一次，删除 evidence 后关联自动清理。

finding 全局归属于市场观察，不擅自归属于所有同代码案例。网页只在用户点击“刷新盯盘”且当前事件股票代码与 finding 一致时，调用批量转换 API；刷新期间会锁定事件选择和股票代码，响应返回后仍会重新确认点击时的事件身份。其他自选股命中只显示 finding，等待用户切换到对应事件后再次刷新。

### API

- `POST /api/monitor/refresh`：除行情结果外返回 `findings`、`created_finding_count` 和独立的 `finding_errors`。规则失败不篡改行情成功/失败计数。
- 网页会将 `finding_errors` 按自选股归位，显示具体股票与错误原因；它与行情刷新失败分开呈现。
- `GET /api/monitor/latest`：按快照 ID 或精确的股票、provider 与服务商时间，返回最后可用快照及对应 finding。
- `GET /api/monitor/findings`：按运行或股票代码复核 finding 历史。
- `POST /api/cases/{case_id}/monitor/findings`：批量、幂等地把同股票代码 finding 转换为 evidence；全批在单个 `BEGIN IMMEDIATE` 事务内重读校验并写入，失败时整批回滚。

转换生成的 evidence 固定为 `origin=automatic`、`source_type=market_data`、`status=pending`，并在 metadata 中保留 finding ID、快照 ID、运行 ID、provider、服务商时间、规则版本、观测值、阈值、基线和去重键。重复转换不会重置用户已经完成的审核状态。只有用户明确改为 `accepted` 后，证据才允许进入现有评分、置信度和覆盖率计算。

Phase 3 不调用 LLM，不新增调度、通知、交易动作、预测分数或收益口径。静态 GitHub Pages 不具备本地 API，因此仍只保留手动能力。

## Phase 4：本地定时任务与数据源韧性

Phase 4 在 Phase 3 的刷新流程外增加本地调度器和运行诊断，不改变 snapshot、finding、evidence 的分层，也不改变评分入口。默认配置下调度器关闭，手动刷新仍只调用 provider 一次且始终可用。

### 调度边界

- `CATALYST_MONITOR_INTERVAL_SECONDS=0` 时调度关闭；启用时最小间隔为 300 秒，最大为 86400 秒。
- 定时任务只在北京时间 `09:30-11:30`、`13:00-15:00` 运行，不宣称逐笔、WebSocket 或交易终端级实时性。
- 交易日判断 fail-closed。必须同时配置 `CATALYST_MARKET_CALENDAR_YEAR`、该年份全部 `CATALYST_MARKET_HOLIDAYS`，并显式设置 `CATALYST_MARKET_CALENDAR_COMPLETE=true`。缺少完整性确认、年份不匹配、周末、休市日或非交易时段都不请求 provider。
- 同一 provider、间隔和 UTC 时间桶形成唯一调度槽。重复 tick 只记录 `duplicate_slot`，不会重复运行。
- 成功执行后按配置间隔等待；未执行的 tick 最多 5 分钟后重新判断交易会话、运行锁和调度槽，避免长间隔永久锚定在午休或盘后。重新判断本身不请求 provider。
- 调度器每次启动使用新的事件循环状态；关闭服务时取消中的 run 与 slot 会收敛到终态。启动时会恢复无有效租约的孤立 `running` run，以及超过租约窗口的 `claimed/running` slot。

### 运行锁与终态

`monitor_runtime_locks` 使用 SQLite 租约实现跨进程单实例互斥。旧 owner 不能续租已经过期的 lease；provider await 或退避返回后必须重新校验所有权，长退避会分段续租。快照、finding、provider health 和成功终态写入都在各自 SQLite 事务内验证 owner 与有效期，失锁任务不得继续落库。新任务获取锁和 Doctor 读取诊断时都会先收敛已经过期的孤立 run/slot，覆盖服务在旧租约到期前快速重启的边界。

每个 run 在 `monitor_runs` 保留原有计数与终态，在 `monitor_run_runtime` 保存真实 `trigger`、`trace_id`、`scheduled_for`、逐次 attempts 和 finding errors。保留旧表 `trigger=manual` 约束是为了无破坏升级；定时触发值由伴随表覆盖。旧数据库中没有伴随行的 run 读取为 `trigger=manual`、`trace_id=null`、空 attempts 和空 finding errors。

run 终态、逐股错误、attempts 和 finding errors 由单个 `finalize_monitor_run` 事务提交，避免终态已写但诊断丢失。内部存储故障仍会向调用方报错，同时尽力收敛 run 并释放租约。

### 重试、熔断与 last-good

- 手动刷新始终只有一次请求，不因定时配置而重试。
- 定时任务逐股最多尝试 1-3 次，使用有界指数退避；单股失败不阻断其余股票。
- 传输、上游服务或适配器级错误推动全局 provider 连续失败与熔断。单个股票无可用行情、全字段缺失等 item-specific 结果记录为失败和 `unavailable` attempt，但不打开全局熔断，避免无行情代码阻断后续正常股票。
- 熔断只跳过定时请求；手动刷新可作为显式探测。一次可用的手动或定时响应会清空连续失败并关闭熔断。
- `unavailable` 快照仍保留用于审计，不能记为 provider success，也不能覆盖 last-good。
- last-good 策略固定为 `preserve_only`：只返回已经存在的最后可用快照，不把旧数据复制成带新时间戳的“新鲜”观察。
- 当前仍只有腾讯单一 provider。`fallback_from` 契约继续保留，但本阶段不伪造不存在的备用源，也不把 last-good 冒充 provider fallback。

### 伴随表与 Doctor

- `monitor_run_runtime`：run 触发方式、trace、调度时间、attempts 与 finding errors。
- `monitor_runtime_locks`：跨进程租约。
- `monitor_schedule_slots`：时间桶幂等、运行结果和计数摘要。
- `monitor_scheduler_state`：最近 tick、决策、消息、trace 与 run。
- `market_provider_health`：连续 provider-wide 失败、熔断截止时间、最近成功/失败和错误。

`GET /api/monitor/doctor` 返回配置、调度任务实际生命周期、交易会话、脱敏后的运行锁、provider health、最近调度状态与槽、最近 run 和 last-good 覆盖。网页只在本地 API 明确声明 `monitor_doctor=true` 时读取并显示可折叠诊断区；静态 GitHub Pages 和旧 API 不请求该端点。

### 配置

| 环境变量 | 默认值 | 约束 |
| --- | ---: | --- |
| `CATALYST_MONITOR_INTERVAL_SECONDS` | `0` | 0 或 300-86400 |
| `CATALYST_MONITOR_RETRY_ATTEMPTS` | `2` | 1-3，仅定时任务 |
| `CATALYST_MONITOR_RETRY_BASE_SECONDS` | `1` | 0-30 |
| `CATALYST_MONITOR_LOCK_SECONDS` | `900` | 60-3600 |
| `CATALYST_MONITOR_CIRCUIT_FAILURES` | `3` | 1-20 |
| `CATALYST_MONITOR_CIRCUIT_SECONDS` | `300` | 60-3600 |
| `CATALYST_MARKET_CALENDAR_YEAR` | 空 | 2000-2100 |
| `CATALYST_MARKET_HOLIDAYS` | 空 | 同一日历年份的 ISO 日期全集 |
| `CATALYST_MARKET_CALENDAR_COMPLETE` | `false` | 严格布尔值；true 表示明确确认全年清单完整 |

## 后续阶段

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
