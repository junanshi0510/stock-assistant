# 2026-07-23 更新 006：市场状态、策略适配与动态风险预算中枢

## 1. 为什么这是一个“大功能”

平台此前已经形成一条可审计的研究与资金主链：

1. 机会工厂从 A 股、港股、美股明确候选池中运行版本化策略；
2. 扫描结果冻结为纸面组合，后续只观察冻结之后的真实行情；
3. 收益实验室按精确 5/20/60 个交易日验证成本后收益、同市场基准超额、回撤、置信区间和跨策略多重检验；
4. 投资委员会淘汰近期失效和重复策略，形成策略袖套与候选共识；
5. 全组合资金引擎再经过真实持仓、IPS、可信估值、集中度和压力情景。

但旧链路把所有市场环境混在一起回答“策略历史上是否有效”。它无法回答：

- 该策略的有效样本主要来自偏强、震荡还是防守阶段；
- 当前 A 股、港股、美股候选池分别处于什么状态；
- 一个累计结果合格的策略，在当前同类环境下是否已经连续失效；
- 高波动、单一来源或状态分歧时，委员会总风险应缩到多少；
- 旧纸面组合当时的环境能否被可靠还原，而不是用今天的状态改写历史；
- 市场状态变化后，旧的风险预算和策略适配判断能否审计。

因此本次新增的不是一张“市场温度卡”，而是位于收益验证和投资委员会之间的正式状态与风险层：

```text
不可变机会扫描
  ↓
A / H / 美股候选池状态共识
  ├─ 趋势中位
  ├─ 上涨广度
  ├─ 候选池波动
  ├─ 来源新鲜度
  └─ 来源厚度 / 一致度
  ↓
冻结时市场状态
  ↓
当前环境 × 同环境独立前瞻批次
  ├─ 样本不足：中性
  ├─ 同环境衰减：降权
  ├─ 三期连续失配：熔断
  └─ 强证据：最高 10% 窄幅优先
  ↓
动态风险预算（只能维持或降低）
  ↓
投资委员会 v1.1
  ↓
全组合资金决策 v3
```

## 2. 专业框架参考

本次只参考专业平台或指数公司的官方材料，不采用“AI 选股胜率”营销口径。

| 专业做法 | 官方材料 | 本项目吸收的原则 |
|---|---|---|
| Alpha、Portfolio Construction、Risk、Execution 分层 | [QuantConnect Algorithm Framework](https://www.quantconnect.com/docs/v1/algorithm-framework/overview) | 市场状态和策略适配不能直接创建订单；它们只调整进入组合构建层的相对效用和风险预算 |
| 风险模型在执行前调整 Portfolio Targets | [QuantConnect Risk Management](https://www.quantconnect.com/docs/v1/algorithm-framework/risk-management) | 状态层位于委员会目标与最终资金金额之前，并保留 IPS、集中度和压力引擎的最终否决权 |
| 组合目标由独立 Portfolio Construction 层形成 | [QuantConnect Portfolio Construction](https://www.quantconnect.com/docs/v1/algorithm-framework/portfolio-construction)、[Key Concepts](https://www.quantconnect.com/docs/v2/writing-algorithms/algorithm-framework/portfolio-construction/key-concepts) | 策略适配只在策略袖套之间窄幅倾斜，不把每个策略的信号直接当成买入金额 |
| Risk Control 指数根据实现波动动态降低风险资产暴露 | [S&P Dow Jones Indices Index Mathematics Methodology](https://www.spglobal.com/spdji/es/documents/methodologies/methodology-index-math.pdf) | 当候选池波动高于保守区间时降低乘数；不承诺实现目标波动，也不使用状态层增加杠杆 |
| 波动控制方法需要明确目标、实现波动和现金配置 | [MSCI Risk Control Indexes Methodology](https://www.msci.com/eqb/methodology/meth_docs/MSCI_Risk_Control_Indexes_Methodology_Sept2022.pdf) | 风险预算与现金缓冲必须显式展示，不能只在模型内部隐藏调整 |
| 组合风险应支持组合级钻取与 What-if | [IBKR Risk Navigator](https://www.interactivebrokers.com/en/trading/risk-navigator.php) | 状态层输出继续进入已有全组合压力矩阵，而不是用一个“市场分数”覆盖用户真实组合风险 |

这些框架的共同点是：先区分信号、组合、风险和执行，再让风险层调整目标。它们并不要求发布“某只股票明天上涨 73%”这类不可校准概率。

## 3. 当前市场状态的数据边界

### 3.1 只读取不可变扫描

状态中枢只接受：

- `status in ('succeeded', 'partial')` 的机会扫描；
- `result_json` 与 `result_sha256` 完整性通过；
- 扫描结果中的 `market_regimes` 状态属于 `risk_on / mixed / defensive`；
- 结果生成日距当前 UTC 决策日不超过 14 天。

每个市场、每个策略版本只保留最新一份扫描，随后最多取 5 个不同策略版本。这样可以避免一个策略短时间重复运行，把自己重复计算成多个独立来源。

### 3.2 为什么仍称“候选池状态”

扫描状态来自该策略本次可用候选的：

- 三月收益中位数；
- 正收益候选比例；
- 年化波动中位数。

候选池可能来自内置种子、自选、手工代码和专业热门榜，并非交易所全量历史成分。因此所有后端结果和前端卡片都明确标注：

> 状态只覆盖策略候选池，不代表交易所全市场，也不是上涨或下跌概率。

平台不会因为显示需要，把候选池状态改名成“牛市概率”。

## 4. 多来源状态共识

### 4.1 来源权重

每个来源的权重为：

```text
freshness = max(0.15, 1 - UTC整日年龄 / 14)
sample_reliability = min(1, 候选样本数 / 8)
source_weight = max(0.05, freshness × sample_reliability)
```

来源年龄按 UTC 日历日冻结，而不是按秒。这样同一决策日重复读取相同不可变输入会得到相同 Evidence SHA-256；状态仍会在下一 UTC 决策日自然滚动。

状态映射为：

```text
risk_on   = +1
mixed     =  0
defensive = -1
```

按来源权重求均值后：

```text
score >= +0.35  → risk_on
score <= -0.35  → defensive
其他             → mixed
```

状态卡同时保留：

- 来源数；
- 候选样本总数；
- 主导状态权重占比；
- 加权三月收益中位数；
- 加权上涨广度；
- 加权候选池年化波动；
- 最新来源时间与整日年龄；
- 被排除的过期来源数；
- 每个来源的运行 ID、策略版本 ID 和结果 SHA-256。

### 4.2 证据等级

- 至少 3 个来源、主导状态权重不低于 60%、最新来源不超过 7 天：`strong`；
- 至少 2 个来源：`usable`；
- 只有 1 个来源：`thin`；
- 无 14 天内来源：`insufficient`。

`evidence_grade` 是来源厚度标签，不是统计置信概率。

## 5. 动态风险预算

### 5.1 状态基础乘数

```text
risk_on     1.00
mixed       0.85
defensive   0.60
insufficient 0.50
```

### 5.2 波动折扣

候选池年化波动中位数进一步形成上限：

```text
vol <= 25%   1.00
vol <= 35%   0.90
vol <= 50%   0.75
vol >  50%   0.60
```

单一来源还会把乘数上限限制到 `0.85`。最终市场风险乘数为状态、波动、来源厚度三个上限中的最小值：

```text
market_risk_multiplier
  = min(1, state_multiplier, volatility_multiplier, source_multiplier)
```

该公式有一个硬不变量：

```text
0 <= market_risk_multiplier <= 1
```

状态层永远不能把投资委员会原始风险上限放大，也不允许杠杆。

## 6. 冻结时市场状态

### 6.1 新纸面组合

新建纸面组合时，快照新增：

```json
{
  "market_regimes": [],
  "regime_basis": "冻结本次扫描的候选池市场状态；不是交易所全市场状态，后续不会用新行情改写"
}
```

这些字段与策略版本、运行 ID、运行结果 SHA-256、冻结持仓和权重共同进入纸面组合快照哈希。

### 6.2 旧纸面组合

历史纸面组合不做数据库回填，也不修改原快照。状态中枢只在以下条件同时成立时临时还原：

1. 纸面组合 `snapshot_verified=true`；
2. 绑定运行 `result_verified=true`；
3. 快照中的 `run_result_sha256` 与运行表中的 `result_sha256` 完全一致；
4. 原运行结果仍含有对应 `market_regimes`。

任一条件不满足时，该批次的冻结状态为 `insufficient`，不会猜测。

### 6.3 跨市场组合状态

按冻结持仓市场权重聚合状态分数。已识别市场覆盖低于 50% 时不输出组合状态；覆盖低于 80% 时，即使其余市场偏强，风险乘数也会受到额外保守上限。

## 7. 策略同环境适配

### 7.1 当前策略暴露

优先使用收益实验室当前可用人工试运行计划中的冻结仓位。如果用户尚未配置 IPS 或可信估值，资金计划会依法阻断，但研究层仍可使用该策略最新、完整性通过的冻结纸面组合识别市场暴露。

这种回退只恢复研究上下文，不会：

- 把策略标记为资金合格；
- 绕过收益记分卡冻结；
- 绕过 IPS 或可信估值；
- 生成金额、股数或订单。

前端会记录 `positions_source=live_capital_plan` 或 `latest_verified_paper_basket`。

### 7.2 同环境样本隔离

每个策略只读取主验证窗口中已经成熟、起点独立的前瞻批次，并要求：

```text
frozen_regime_status == current_regime_status
frozen_regime_coverage_pct >= 70
```

其他环境的批次仍保留在审计 Evidence 中，但不参与当前适配统计。这样防守阶段的结果不会被偏强阶段的大涨样本平均掉。

### 7.3 适配规则

少于 4 个同环境独立批次：

```text
fit_status = collecting
raw_tilt = 1.00
```

至少 4 个样本，且最近 3 个同环境批次全部 `net_excess_return_pct <= 0`：

```text
fit_status = avoid
allocation_tilt = 0
```

否则，当同环境平均超额不为正或跑赢比例低于 50%：

```text
fit_status = underweight
raw_tilt = 0.75
```

只有以下条件全部成立才允许窄幅优先：

```text
mean_net_excess_return_pct > 0
positive_excess_rate_pct >= 60
95% t 区间下界 > 0
```

此时：

```text
fit_status = preferred
raw_tilt = 1.10
```

所有非熔断倾斜再按样本数向中性收缩：

```text
reliability = min(1, matched_cohort_count / 8)
allocation_tilt = 1 + (raw_tilt - 1) × reliability
```

因此 4 个样本即使满足优先条件，也只得到一半倾斜；8 个及以上才达到完整的 `1.10`。

## 8. 投资委员会与资金引擎接入

### 8.1 投资委员会 `adaptive_strategy_committee@1.1.0`

委员会新增两条独立作用：

1. `allocation_tilt` 参与策略袖套之间的相对效用；
2. `market_risk_budget_multiplier` 限制委员会总可投入比例。

环境 `avoid` 会把原本通过收益门禁的策略设为 `suspended`。其原因、同环境批次数、近期三个结果、均值、跑赢比例、95% 区间和状态 Evidence SHA-256 都进入委员会 Evidence。

委员会先按原规则得到：

```text
base_committee_investable_pct
```

再应用状态风险上限：

```text
committee_investable_pct
  = base_committee_investable_pct
    × selected_strategy_regime_risk_multiplier
```

前端同时展示：

- 状态前原始上限；
- 状态风险乘数；
- 状态层新增现金；
- 每个策略的当前环境、同环境样本和适配倾斜；
- 当前状态快照是否已冻结绑定。

### 8.2 全组合资金引擎 `whole_portfolio_next_best_action.v3`

决策顺序升级为：

```text
事实完整性
→ 已有仓位风险 / 持有纪律
→ 前瞻策略资格
→ 市场状态 / 策略适配 / 风险预算
→ 策略失效 / 冗余 / 共识委员会
→ 月度预算
→ 单品 / 权益 / 行业容量
→ 全组合压力情景
```

状态层缩小后的委员会袖套自然传导到候选金额；后续所有原有门禁继续生效。

## 9. 不可变数据模型

新增表：

```text
opportunity_regime_snapshots
```

关键字段：

| 字段 | 作用 |
|---|---|
| `user_id` | 用户隔离 |
| `actor_id` | 冻结操作者 |
| `engine_version` | 状态与适配引擎版本 |
| `status` | `risk_on / mixed / defensive / insufficient` |
| `evidence_cutoff_at` | 本次状态证据截止时间 |
| `evidence_json` / `evidence_sha256` | 输入来源、运行、纸面组合、记分卡、同环境批次和计算绑定 |
| `result_json` / `result_sha256` | 市场状态、策略适配、风险预算、变化和方法边界 |
| `created_at` | 冻结时间 |

唯一约束：

```text
UNIQUE(user_id, engine_version, evidence_sha256)
```

SQLite 与 PostgreSQL 都通过数据库触发器拒绝 UPDATE/DELETE。重复冻结同一 UTC 决策日、同一不可变底层证据时复用原记录。

## 10. API

新增 4 个受认证操作：

| 方法 | 路径 | 作用 |
|---|---|---|
| `GET` | `/api/v1/opportunities/regime` | 当前市场状态、策略适配、风险预算与最新快照绑定 |
| `POST` | `/api/v1/opportunities/regime/snapshots` | 冻结或按 Evidence 内容寻址复用当前快照 |
| `GET` | `/api/v1/opportunities/regime/snapshots` | 当前用户历史快照 |
| `GET` | `/api/v1/opportunities/regime/snapshots/{snapshot_id}` | 当前用户单份完整快照、Evidence、Result 与完整性 |

当前 OpenAPI：

```text
156 条路径
182 个操作
机会工厂 23 条路径 / 28 个操作
```

## 11. PostgreSQL 迁移与 readiness

迁移模块：

```text
python -m migrations.opportunity_regime_v1
```

迁移标记：

```text
opportunity-regime-allocation.v1
```

迁移使用独立 PostgreSQL advisory lock，在同一事务中建立表、索引、不可变函数与触发器，并写入 `platform_schema_migrations`。

数据库 readiness 新增：

```json
{
  "opportunity_regime_schema": true
}
```

生产 API 只有在该表存在时才可以接流量，不能依赖应用启动自动建表。

## 12. 前端工作台

机会工厂新增第五个视图“市场状态”，包括：

- 当前整体候选池状态与风险预算；
- 状态来源数、候选样本数和同环境批次数；
- A 股、港股、美股状态卡；
- 三月趋势、上涨广度、候选池波动和来源一致度；
- 状态基础乘数、波动折扣和最终风险预算；
- 策略 × 当前环境适配矩阵；
- 同环境样本、平均超额、跑赢比例、95% 区间、倾斜与熔断；
- 与上一快照的市场状态变化；
- 首次冻结、重复冻结幂等和不可变历史；
- “偏强不等于满仓、防守不等于预测下跌”的决策护栏。

投资委员会页面同步展示状态前原始上限、状态层新增现金、状态风险乘数以及每个策略的同环境适配。

## 13. 测试与验收

### 13.1 自动测试

新增或扩展测试覆盖：

- 多策略版本状态共识；
- 14 天过期来源排除；
- 高波动只减不增；
- 同 UTC 决策日 Evidence SHA-256 稳定；
- 同环境样本隔离；
- 4/8 样本门槛与收缩；
- 三个同环境连续失败熔断；
- 旧纸面组合精确运行哈希回溯；
- 无 IPS 时最新冻结纸面组合研究暴露回退；
- 状态快照内容寻址、不可变和用户隔离；
- PostgreSQL 不可变迁移；
- 委员会状态风险硬上限；
- 环境失配策略停用；
- 防守状态向全组合金额传导；
- 4 个新增路由契约。

结果：

```text
专项：28 tests 通过
后端全量：542 tests 通过
前端：1853 modules transformed
npm audit：0 vulnerabilities
```

### 13.2 本地真实浏览器

使用真实本地管理员会话完成：

1. 机会工厂五个子视图可见；
2. “市场状态”显示 A 股防守、港股/美股证据不足，不用 0 冒充缺失；
3. 候选池范围、无概率、无杠杆和不自动交易边界可见；
4. 未配置 IPS 时，资金计划继续阻断，但已有纸面组合仍可识别 A 股 100% 研究暴露；
5. 首次冻结生成不可变快照；
6. 同一底层证据重复冻结复用原记录；
7. 历史显示风险预算、优先与熔断数量；
8. 投资委员会显示引擎 `adaptive_strategy_committee@1.1.0` 和状态层绑定；
9. 页面无功能错误。

浏览器验收发现并修复了两个不能靠静态单测完全暴露的问题：

- Vite 开发进程旧模块缓存导致懒加载白屏，重启后生产模块导出正常；
- 来源年龄按秒进入 Evidence 导致几秒后重复冻结生成新记录，现已改为 UTC 整日截面并增加稳定性测试。

## 14. 安全边界

本功能明确不做：

- 不输出某只股票上涨或下跌的校准概率；
- 不把候选池状态冒充交易所全市场状态；
- 不用当前环境改写历史纸面组合；
- 不把回测样本混入同环境前瞻样本；
- 不因偏强状态提高原始总风险或使用杠杆；
- 不绕过收益记分卡、IPS、可信估值、已有仓位和压力门禁；
- 不计算交易股数；
- 不连接券商或创建订单；
- 不承诺盈利。

它的价值是减少“正确策略用在错误环境”和“高波动时仍按满风险运行”的决策错误，并把每次状态、适配和风险预算完整冻结下来。它不能消除市场风险，也不能保证下一批次继续有效。

## 15. 生产发布

生产发布必须按以下顺序：

1. 发布前创建 PostgreSQL 加密备份并完成隔离恢复验证；
2. 推送 GitHub `main`；
3. 在新 release 环境执行 `migrations.opportunity_regime_v1`；
4. 核对迁移标记、表、索引和 UPDATE/DELETE 拒绝触发器；
5. 运行状态、委员会、资金引擎、路由和健康检查专项；
6. 使用双 API 滚动发布器部署 `8001/8002`；
7. 核对 `opportunity_regime_schema=true` 和严格健康检查；
8. 使用临时普通用户完成匿名 `401`、首次冻结、重复冻结、历史与详情双哈希验收；
9. 验证公网新静态资源和浏览器工作台；
10. 停用临时账户并撤销会话；
11. 发布后再次创建加密备份并完成隔离恢复。

生产实测结果将在发布完成后追加到本记录，不提前声明成功。
