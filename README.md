# A-Share Catalyst Lens

面向中国 A 股和中国相关股票的 Codex Skill 与本地网站，用于把新闻、公告、政策、资金流、板块题材和价格成交行为整理成可追溯的“利好/利空催化分析”。

它不会承诺预测股价，也不会给出确定性交易指令。它的目标是帮助你把分散信息拆成事实、推理、反证、市场确认和失效条件，并用统一框架给出催化强度、置信度和后续观察清单。

![A-Share Catalyst Lens 网站界面](web/assets/preview.png)

## 适合什么场景

- 判断某条 A 股新闻到底是利好、利空、混合影响，还是证据不足。
- 分析政策、行业标准、地方补贴、监管变化对板块或个股的影响。
- 解读公司公告、业绩预告、订单合同、回购分红、并购重组、产品获批等事件。
- 检查涨停、连板、放量、换手、板块轮动等市场行为是否支持新闻逻辑。
- 将公开信息整理成一份带引用、带反证、带置信度的中文研究摘要。
- 对结构化事件证据进行 0-100 分催化强度评分。

## 核心能力

- 证据优先：先查公告、交易所、监管机构、公司 IR、权威媒体和市场数据，再给判断。
- 事件台账：区分事实、来源、影响通道、市场反应、反证和缺失信息。
- 催化分类：覆盖政策、业绩、订单、回购、分红、并购、产品获批、资金流、板块题材和传闻。
- 利好评分：从来源可靠性、影响实质性、时效性、新颖性、确认度、市场配合度、是否已反映和反证强度综合打分。
- 风险约束：明确指出已经 price-in、估值拥挤、执行风险、监管不确定性和财务质量问题。
- 中文输出：默认适配中文 A 股分析语境，同时保留英文命令和字段，方便脚本化。

## 仓库结构

```text
A-Share-Catalyst-Lens/
├── .github/
│   └── workflows/
│       ├── ci.yml
│       └── pages.yml
├── LICENSE
├── SKILL.md
├── agents/
│   └── openai.yaml
├── examples/
│   └── events.json
├── references/
│   ├── catalyst-rubric.md
│   └── data-sources.md
├── scripts/
│   └── catalyst_score.py
├── tests/
│   ├── test_catalyst_score.py
│   └── test_web_scoring.js
└── web/
    ├── assets/
    │   ├── lens-mark.svg
    │   └── preview.png
    ├── app.js
    ├── index.html
    ├── scoring.js
    └── styles.css
```

文件说明：

- `.github/workflows/ci.yml`：GitHub Actions 自动验证 Python/JavaScript 语法、单元测试和示例评分。
- `.github/workflows/pages.yml`：将 `web/` 静态网站自动发布到 GitHub Pages。
- `LICENSE`：MIT 开源许可证。
- `SKILL.md`：Codex Skill 主入口，定义触发条件、工作流、资源和验证规则。
- `agents/openai.yaml`：Skill 在 Codex 界面中的展示名称、简介和默认提示词。
- `examples/events.json`：可直接运行的结构化事件评分示例。
- `references/catalyst-rubric.md`：催化事件台账、分类、评分规则和报告模板。
- `references/data-sources.md`：A 股分析的数据源优先级、实用选择和上下文检查清单。
- `scripts/catalyst_score.py`：对结构化事件 JSON 进行确定性评分的辅助脚本。
- `tests/`：Python 与浏览器评分内核测试。
- `web/`：零依赖网站，支持多事件、实时评分、导入导出和本地自动保存。

## 网站版

网站提供一个双栏 A 股催化分析工作台，支持：

- 同时管理多条关联事件并比较分数。
- 实时查看总分、置信度、分项贡献和风险扣分。
- 自动生成证据台账、正向逻辑、反证和后续观察清单。
- 导入、导出 JSON，复制中文分析摘要。
- 使用浏览器本地存储自动保存草稿，输入不会上传到服务器。

直接打开 [`web/index.html`](web/index.html) 即可使用。也可以在仓库根目录运行本地静态服务：

```bash
python -m http.server 8000 --directory web
```

然后访问 `http://localhost:8000`。

GitHub Pages 工作流会尝试发布到：

https://dongpen-max.github.io/A-Share-Catalyst-Lens/

## 安装

### 方法一：作为 Codex Skill 安装

将仓库克隆到你的 Codex skills 目录：

```bash
git clone https://github.com/dongpen-max/A-Share-Catalyst-Lens.git ~/.codex/skills/a-share-catalyst-lens
```

Windows PowerShell 示例：

```powershell
git clone https://github.com/dongpen-max/A-Share-Catalyst-Lens.git "$env:USERPROFILE\.codex\skills\a-share-catalyst-lens"
```

安装后，在 Codex 中可以这样调用：

```text
Use $a-share-catalyst-lens to analyze whether this A-share news is bullish, with citations, a catalyst score, confidence, and invalidation checks.
```

也可以直接用中文：

```text
使用 $a-share-catalyst-lens 分析这条 A 股新闻是否构成利好，给出证据、反证、催化分数、置信度和失效条件。
```

### 方法二：只使用评分脚本

如果你只想使用结构化评分脚本，可以直接运行：

```bash
python scripts/catalyst_score.py examples/events.json --pretty --strict
```

脚本只依赖 Python 标准库，不需要额外安装包。`--strict` 会在字段缺失、非数字或超出 0-5 时返回错误码，适合 CI 和批处理校验。

## 快速示例

### 示例请求

```text
使用 $a-share-catalyst-lens 分析：
某上市公司公告获得 20 亿元大额订单，公司去年营收 35 亿元。公告发布当天股价涨停，板块指数上涨 2.1%，但公司此前一个月股价已经上涨 38%。这是否还算强利好？
```

### 期望输出结构

Skill 会倾向于输出类似结构：

```text
结论：偏利好，但需要警惕已部分反映。
催化分数：xx/100
置信度：Medium

证据表：
- 时间
- 来源
- 事实
- 影响通道
- 引用

利好逻辑：
1. ...
2. ...

反证和 price-in 风险：
1. ...
2. ...

市场验证：
- 个股表现
- 板块表现
- 成交量/换手
- 同行业对比

后续观察：
- 合同执行进度
- 回款和毛利率
- 公司后续公告
- 股价跌破或放量滞涨等失效信号

免责声明：仅供研究参考，不构成投资建议。
```

## 催化评分框架

评分范围为 0-100 分。分数是研究辅助，不是收益预测。

| 维度 | 权重 | 说明 |
|---|---:|---|
| 来源可靠性 | 0-20 | 官方公告、多源确认、交易所/监管来源更高；匿名消息或社交平台更低 |
| 影响实质性 | 0-20 | 直接影响收入、利润、现金流、估值或监管环境更高 |
| 时效性 | 0-10 | 短期可落地、短期可验证更高 |
| 新颖性 | 0-10 | 新信息更高；市场早已知晓的信息更低 |
| 确认度 | 0-10 | 有独立验证、数据支撑、公告细节更高 |
| 市场配合度 | 0-10 | 个股、板块、指数、成交量和同行反应支持逻辑更高 |
| 已反映风险 | 0 到 -10 | 涨幅过大、交易拥挤、估值透支时扣分 |
| 反证强度 | 0 到 -10 | 执行风险、政策不确定性、财务压力、相反数据越强扣分越多 |

评分等级：

| 分数 | 解释 |
|---:|---|
| 80-100 | 强正向催化，但仍需后续验证 |
| 65-79 | 偏正向，证据较好但不绝对 |
| 50-64 | 混合或弱正向 |
| 35-49 | 低置信度，多为情绪或已反映 |
| 0-34 | 不构成利好或偏负面 |

## 评分脚本用法

创建一个事件 JSON：

```json
{
  "events": [
    {
      "title": "Company announces a verified large order",
      "source_reliability": 5,
      "materiality": 4,
      "immediacy": 4,
      "novelty": 3,
      "confirmation": 4,
      "market_alignment": 3,
      "priced_in_risk": 2,
      "counterevidence": 1,
      "notes": "Official announcement with sector follow-through."
    }
  ]
}
```

运行：

```bash
python scripts/catalyst_score.py events.json --pretty
```

输出示例：

```json
{
  "summary": {
    "event_count": 1,
    "average_score": 58.0,
    "overall_grade": "mixed_or_weak_positive",
    "highest_score": 58.0,
    "lowest_score": 58.0
  },
  "events": [
    {
      "title": "Company announces a verified large order",
      "score": 58.0,
      "grade": "mixed_or_weak_positive",
      "confidence": "Medium",
      "components": {
        "source_reliability": 20.0,
        "materiality": 16.0,
        "immediacy": 8.0,
        "novelty": 6.0,
        "confirmation": 8.0,
        "market_alignment": 6.0,
        "priced_in_risk": -4.0,
        "counterevidence": -2.0
      },
      "notes": "Official announcement with sector follow-through."
    }
  ]
}
```

字段取值建议为 0-5：

- `0`：没有证据或完全不支持。
- `1-2`：较弱。
- `3`：中等。
- `4`：较强。
- `5`：非常强。

`priced_in_risk` 和 `counterevidence` 是扣分项，数值越高代表风险越大。

### 严格模式和 stdin

严格模式会把输入质量问题当作失败处理：

```bash
python scripts/catalyst_score.py examples/events.json --pretty --strict
```

也可以从 stdin 读取 JSON：

```bash
type examples/events.json | python scripts/catalyst_score.py - --pretty
```

在 macOS/Linux 上：

```bash
cat examples/events.json | python scripts/catalyst_score.py - --pretty
```

非严格模式下，脚本会继续输出评分，并在 JSON 顶层加入 `warnings` 字段。严格模式下，只要存在 warning，脚本会返回退出码 `2`。

## 数据源建议

分析时优先使用：

1. 官方和一手来源：公司公告、交易所、监管机构、巨潮资讯、公司投资者关系页面。
2. 市场数据：交易所行情、公开金融数据接口、持牌数据终端、用户本地数据。
3. 权威媒体：新华社、证券时报、中国证券报、上海证券报、第一财经、财新、东方财富、新浪财经等。
4. 二级来源和社交平台：只作为情绪观察，不应作为唯一利好依据。

对于 A 股，建议额外检查：

- 个股相对板块和主要指数的表现。
- 成交量、换手率、涨停/跌停、连板、封单和开板情况。
- 同行业公司是否同步反应。
- 催化是否影响收入、利润率、现金流、估值或只是短期叙事。
- 是否存在 ST、停复牌、减持、质押、再融资、问询函、商誉和回款风险。

## 输出原则

使用这个 skill 时，建议坚持以下原则：

- 先证据，后结论。
- 先事实，后推理。
- 同时写利好和反证。
- 明确哪些信息已经验证，哪些只是推断。
- 不把短线涨跌等同于基本面改善。
- 不把题材热度等同于公司业绩兑现。
- 不把评分当作买卖建议。

## 常见问题

### 这个项目能预测股票涨跌吗？

不能。它用于结构化分析公开信息和事件催化强度，不提供确定性预测，也不构成投资建议。

### 催化分数越高就越值得买吗？

不是。催化分数只表示事件证据和影响通道的强弱。交易决策还需要考虑估值、仓位、风险承受能力、流动性、市场环境和个人策略。

### 新闻已经导致股价大涨，还会给高分吗？

不一定。评分框架会对“已反映风险”和“交易拥挤”扣分。好消息如果已经充分 price-in，分数可能下降。

### 可以分析港股或中概股吗？

可以分析中国相关股票，但默认框架最贴合 A 股。分析港股或中概股时，应替换为对应交易所公告、监管规则、市场数据和媒体来源。

### 可以自动交易吗？

不建议，也不是本项目目标。这个 skill 只做研究辅助和证据整理，不负责下单、组合管理或风险控制。

## 开发与验证

验证 Skill 结构：

```bash
python path/to/quick_validate.py .
```

验证脚本语法：

```bash
python -m py_compile scripts/catalyst_score.py
```

运行评分脚本：

```bash
python scripts/catalyst_score.py examples/events.json --pretty
```

运行严格模式：

```bash
python scripts/catalyst_score.py examples/events.json --pretty --strict
```

运行单元测试：

```bash
python -m unittest discover -s tests
```

检查网站脚本并运行前端评分测试：

```bash
node --check web/scoring.js
node --check web/app.js
node --test tests/test_web_scoring.js
```

GitHub Actions 会在 push 和 pull request 时自动执行 Python 与 JavaScript 的语法检查、单元测试和示例评分。

## 免责声明

本项目仅用于信息整理、研究辅助和教育用途。所有分析结果都依赖输入信息和可验证数据的质量，不保证完整、及时、准确，也不构成任何投资建议、交易建议、收益承诺或风险承诺。投资有风险，决策需独立判断。

## License

This project is released under the [MIT License](LICENSE).
