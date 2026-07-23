# 2026-07-23 投资指挥台与全组合资金决策引擎

## 1. 为什么这是一个“大功能”

平台此前已经有真实持仓、投资政策、可信人民币估值、持仓行动报告、组合穿透、数字孪生、机会工厂和前瞻收益实验室，但用户仍要在多个页面手工回答四个关键问题：

1. 已有仓位是否有更优先的风险或纪律动作；
2. 本期新增资金究竟应该投入、保留还是暂停；
3. 哪些候选拥有冻结后的前瞻证据，而不是一次扫描高分；
4. 候选加入真实组合后，是否突破单品、权益、行业或亏损预算。

如果缺少统一组合编排，局部正确的功能仍可能拼出错误决策：收益实验室认为一个策略可小额试运行，不代表用户当前组合还有行业容量；候选分数高，也不能覆盖某个已有仓位已经需要降仓；月度预算存在，也不等于券商账户实时现金可用。

本次新增的是组合级“下一最佳行动”引擎，而不是另一种选股分数。它将已有事实和研究结果约束在同一条可复算决策链中，并只在全部关键证据同时成立时输出受限人工研究金额。

## 2. 同行能力参考与本项目映射

调研只参考官方产品说明，不复制品牌、数据或专有模型。

| 产品 | 官方能力重点 | 本项目吸收的工作流原则 | 本项目没有宣称的能力 |
|---|---|---|---|
| [BlackRock Aladdin Risk](https://www.blackrock.com/aladdin/platforms/products/aladdin-risk) / [Aladdin Wealth](https://www.blackrock.com/aladdin/platforms/solutions/aladdin-wealth) | 全组合风险视图、压力测试、what-if 与可行动洞察 | 所有新候选必须放回当前组合，展示计划前后同口径压力结果 | 不拥有 Aladdin 模型、机构风险因子或数据许可 |
| [QuantConnect Algorithm Framework](https://www.quantconnect.com/docs/v2/writing-algorithms/algorithm-framework/overview) | Alpha、组合构建、风险管理和执行分层 | 把前瞻策略证据、组合资金构建、风险缩放和执行边界拆开 | 不连接券商、不自动执行，也不把回测 Alpha 当成资金授权 |
| [Interactive Brokers Risk Navigator](https://www.interactivebrokers.com/en/trading/risk-navigator.php) | 组合 what-if、风险度量和压力情景 | 新增资金前后并排比较，先处理已有组合风险 | 不读取 IBKR 实时账户、保证金、现金或订单 |
| [Morningstar Portfolio X-Ray](https://www.morningstar.com/help-center/portfolio/xray) | 组合穿透、资产与行业配置分析 | 使用基金披露区间和行业容量，而不是只看表面基金名称 | 不拥有 Morningstar 分类、评级或数据库 |
| [Seeking Alpha Quant Ratings](https://seekingalpha.com/article/4263303-quant-ratings-and-factor-grades-faq) | 多因子评级与历史表现跟踪 | 候选研究保留多因子证据，但必须经过前瞻、成本和组合门禁 | 不把因子等级解释为上涨概率 |
| [Composer Backtesting Basics](https://www.composer.trade/learn/backtesting-basics) | 策略组合与历史检验 | 策略必须先冻结并形成可观察批次，再进入资金资格 | 不把历史回测或单次上涨包装成未来收益保证 |

本项目的差异化闭环是：

```text
真实组合事实
  → 已有仓位行动抢占
  → 冻结后的前瞻策略资格
  → 组合级新增资金上限
  → 单品/权益/行业容量
  → 计划前后压力情景
  → 不可变人工研究计划
```

## 3. 决策输入与强绑定

一次计划必须读取并绑定：

- 用户当前确认持仓及 `holdings_sha256`；
- 当前且治理完整的投资政策版本；
- 与当前持仓绑定、仍在有效期内的人民币估值快照；
- 与当前持仓、估值和投资政策绑定的持仓行动报告；
- 与当前持仓、估值和投资政策绑定的组合穿透快照；
- 收益实验室中当前策略版本的不可变记分卡；
- 记分卡对应的冻结纸面组合。

以下任一情况都会把新增金额归零：

- 投资政策未配置、已失效或完整性失败；
- 没有真实持仓或可信组合总额；
- 估值过期、缺价、缺汇率或绑定旧持仓；
- 行动报告缺失、绑定失效或哈希失败；
- 穿透快照缺失、绑定旧政策/持仓/估值或质量不合格；
- 已有仓位出现 `data_required`、`reduce_review`、`risk_review` 或 `thesis_review`；
- 当前组合在保守政策检查中已经越界；
- 压力计算不能使用当前证据。

收益实验室实时结果即使刚刚通过，也必须已经冻结为当前、完整性通过的记分卡才可进入金额分配。这样可以防止页面状态变化后，历史计划无法解释当时使用了哪份统计证据。

## 4. 组合级资金预算

设：

- `M`：投资政策中的月度新增预算；
- `V`：当前可信人民币组合市值；
- `p_i`：第 `i` 个合格策略允许的人工试运行比例；
- `p_hard = 5%`：引擎不可上调的全局硬上限。

全局候选预算：

```text
p_global = min(p_hard, max(p_i))
B_global = min(M, V × p_global)
```

当前实现最多选择 3 个合格策略，排序首先使用家族校正后超额收益置信区间下界，其次使用胜基准比例。排序只决定有限槽位，不用于把更多金额追给历史点收益最高者。

设合格策略数为 `K`，每个策略先获得相同研究袖套：

```text
B_strategy = B_global / K
```

每个策略实际袖套还要受其收益实验室 `planned_budget_cny` 与 `pilot_cap_cny` 的更小值约束；袖套内再按冻结纸面组合权重分配。相同市场/代码来自多个策略时合并金额和证据来源。

## 5. 风险容量与压力缩放

计划后组合基数使用：

```text
V_post = V + M
```

新增权益容量：

```text
C_equity =
max(0, V_post × IPS.max_equity_ratio - 当前权益暴露上界金额)
```

新增行业容量：

```text
C_industry =
max(0, V_post × IPS.max_industry_ratio - 当前最大行业暴露上界金额)
```

保守组合风险容量：

```text
C_risk = min(B_global, C_equity, C_industry)
```

每个候选还受单品上限约束：

```text
C_single,j =
max(0, V_post × IPS.max_single_ratio - 该标的当前金额)
```

由于当前尚无覆盖 A/H/美股的专业证券行业主数据，新候选股票统一按同一个最坏未知行业桶占用行业容量。这会比真实行业分散更保守，但不会在缺少分类证据时虚构分散。

引擎复用组合数字孪生的 4 组说明性压力预设，对“全部月度资金先保留现金”的基线与候选计划进行同口径比较。检查包括：

- 单品仓位上限；
- 权益暴露上限；
- 行业暴露上限；
- 最大回撤/情景亏损预算。

若初始计划越界，引擎在 `[0, 1]` 上进行 24 轮确定性二分缩放，寻找仍通过全部政策门禁的最大金额比例。预设情景不是历史发生概率，也不表示未来会按该路径运行。

## 6. 输出状态与用户行动

计划状态只有三类：

| 状态 | 含义 |
|---|---|
| `blocked` | 关键事实、绑定、已有仓位动作或当前组合政策存在硬阻断，新增金额为 0 |
| `watch` | 事实可用，但没有冻结后合格策略、月度预算为 0，或风险容量不足，资金保持未投入 |
| `ready` | 存在通过全部门禁的非零“受限人工试运行”金额 |

首页“投资指挥台”展示：

- 一条最优先主行动；
- 当前组合、月度预算、人工试运行上限、计划投入和现金保留；
- 全部证据与风险门禁；
- 已有仓位逐项行动；
- 新候选金额、策略来源、前瞻样本、成本后超额、胜基准比例、家族校正区间和回撤；
- 四组计划前后最坏损失对照；
- 不可变历史计划及完整性校验。

“重建全套证据”按可信估值、持仓行动报告、组合穿透快照的顺序刷新，再重新读取资金计划。任何中间步骤失败都会保留明确错误，不使用演示数据补齐。

## 7. 执行边界

所有计划固定：

```text
execution_authorized = false
automatic_order_creation = false
share_quantity_provided = false
return_guaranteed = false
cash_source_confirmed = false
```

月度预算来自用户投资政策，不代表券商账户实时可用现金。计划金额是人工研究上限，不是买卖指令；执行前仍需人工核对现金、整手、停牌、涨跌停、交易费、税、滑点和订单状态。

这个功能的目标是减少证据不完整、追涨、忽略旧仓风险和突破组合上限等可避免错误，提高决策一致性与风险可控性；它不能保证赚钱。

## 8. 不可变存储与完整性

新增表：

```text
portfolio_capital_decision_plans
```

每条记录保存：

- `tenant_id/user_id/actor_id`；
- Schema 与引擎版本；
- 计划状态和决策日；
- 投资政策、估值、行动报告、穿透快照、记分卡和纸面组合 ID；
- 完整 Evidence JSON 与 SHA-256；
- 完整 Result JSON 与 SHA-256；
- 创建时间。

唯一约束：

```text
(tenant_id, user_id, engine_version, evidence_sha256)
```

相同用户、相同引擎版本和相同证据重复冻结时返回原计划，不制造不同墙钟时间的伪历史。SQLite 使用 UPDATE/DELETE 拒绝触发器；PostgreSQL 使用 `BEFORE UPDATE OR DELETE` 触发器。详情读取会复算双哈希并交叉核对所有绑定列。

生产迁移：

```text
portfolio-capital-decision.v1
```

迁移使用 PostgreSQL advisory transaction lock；应用启动不会在生产自动建表。`/health/ready` 新增 `portfolio_capital_schema`，缺表时 API 副本不能将数据库视为 ready。

## 9. API

新增 4 个受认证操作：

| 方法 | 路径 | 用途 |
|---|---|---|
| GET | `/api/portfolio/capital-decision` | 读取当前实时组合计划与最新冻结绑定状态 |
| POST | `/api/portfolio/capital-decision/plans` | 冻结当前证据和结果；相同证据幂等 |
| GET | `/api/portfolio/capital-decision/plans` | 读取当前用户的轻量历史 |
| GET | `/api/portfolio/capital-decision/plans/{plan_id}` | 读取完整计划并复算完整性 |

当前 OpenAPI：

```text
174 operations
150 paths
```

## 10. 实现位置

后端：

- `backend/portfolio_capital_decision.py`：证据加载、已有仓位抢占、候选构建、风险容量和压力缩放；
- `backend/portfolio_capital_repository.py`：不可变计划、幂等、用户隔离与双哈希校验；
- `backend/migrations/portfolio_capital_decision_v1.py`：生产 PostgreSQL Schema；
- `backend/portfolio_decision_twin.py`：新增静态组合评估适配器，复用同一套暴露与政策计算；
- `backend/routers/portfolio.py`：4 个受认证接口；
- `backend/health.py`：生产 Schema readiness。

前端：

- `frontend/src/features/decision/CapitalDecisionCommand.jsx`：投资指挥台；
- `frontend/src/tabs/DashboardTab.jsx`：首页第一决策面；
- `frontend/src/api/portfolio.js`：当前计划、冻结、历史和详情 API；
- `frontend/src/index.css`：桌面与移动端布局。

## 11. 测试与本地验收

全量后端：

```text
523 passed
11 subtests passed
```

专项测试覆盖：

- 合格策略按冻结权重得到 3000/2000 元且总额不超过 5%；
- 已有仓位 `reduce_review` 抢占全部新增资金；
- 实时资金门禁通过但记分卡未冻结时保持 `watch`；
- 月度预算小于组合 5% 时以月度预算为严格上限；
- 未知候选行业按最坏行业桶精确缩减，容量按金额而不是显示占比反推；
- 当前组合已突破行业政策时全部新增资金归零；
- 绑定旧证据的持仓行动报告失败关闭；
- 计划冻结幂等、不可变且用户隔离；
- PostgreSQL 迁移包含租户范围、唯一证据键、迁移标记与不可变触发器。

前端与契约：

```text
npm audit: 0 vulnerabilities
Vite: 1851 modules transformed
OpenAPI: 174 operations / 150 paths
```

本地真实旧 SQLite 原地验收：

- 当前计划接口返回 `200`；
- 本地证据不完整时准确返回 `blocked/complete_evidence`，没有伪造候选金额；
- 冻结接口返回 `200`，计划完整性复算通过；
- 历史接口只返回当前用户记录；
- Chrome 实际点击重复冻结后显示幂等复用，历史详情显示 Evidence/Result 双哈希通过；
- `390×844` 下文档宽度未超过视口、投资指挥台和历史区块无横向溢出；
- 页面控制台无 warning/error。

## 12. 已知限制与下一轮方向

- 当前不读取券商实时现金、未结算资金、保证金或订单；因此只能输出研究金额上限。
- 新候选缺少统一专业行业主数据时使用最坏行业桶，可能过度保守。下一轮应引入有许可、跨市场且带生效日期的证券主数据，并把分类版本冻结进 Evidence。
- 压力情景是一阶线性、说明性模型，不含相关性突变、波动率曲面、流动性冲击或路径依赖。
- 收益实验室的前瞻样本会随时间积累；刚上线时多数策略应处于观察状态，而不是为了显示“可买”放松门禁。
- 当前不会根据税务地位、账户类型、最小佣金和整手反推可成交股数。
- 任何统计门禁通过都只表示历史冻结样本在当前政策下获得有限研究资格，不保证未来收益。

## 13. 生产发布

发布顺序：

1. 生成加密 PostgreSQL 备份并上传私有 OSS；
2. 在隔离数据库恢复并核对表与迁移标记；
3. 执行 `python -m migrations.portfolio_capital_decision_v1`；
4. 原子滚动发布两个 API 副本和前端静态资源；
5. 逐个重启五类 Worker 与 Celery Beat；
6. 核对 `portfolio_capital_schema=true`、两个副本 release 一致及严格健康检查；
7. 核对新表、唯一约束、不可变触发器、匿名 `401` 和新静态资产；
8. 使用临时普通用户完成 API/浏览器验收并停用账户；
9. 再次备份并执行隔离恢复。

生产实测于 2026-07-23 完成，结果如下：

- 功能提交 `4d949d43c5d51d0264017cc8d1081f239d2e10f4` 已推送 GitHub `main`，并通过内容寻址 release 原子滚动发布到 `http://8.148.67.79/`。
- 发布前 PostgreSQL 备份已使用 AES256 上传私有 OSS，SHA-256 为 `04b26025672fc38c23f7b33ab0346e4d8662abe5d73fa434cbeb469191e42912`；隔离恢复核对 `64` 张表、`7` 个迁移标记通过。
- `portfolio-capital-decision.v1` 已执行；生产数据库核对为 `65` 张表、`8` 个迁移标记，新表 `17` 列且存在 1 个 UPDATE/DELETE 拒绝触发器。事务内真实 UPDATE 探针收到 `integrity_constraint_violation`，回滚后残留行数为 `0`。
- `stock-assistant-api@8001/8002` 均为 active，两个副本的 release 都是 `4d949d43c5d51d0264017cc8d1081f239d2e10f4`；`/health/ready` 与 `/health/full` 均为 `operational`，并返回 `portfolio_capital_schema=true`。五类 Worker、Celery Beat、PostgreSQL、Redis、Nginx 和私有 OSS 均正常。
- 两个副本的 OpenAPI 都核对为 `150` 条路径、`174` 个操作，4 个资金决策操作完整存在。服务器运行时使用生产依赖执行资金决策、路由契约和健康检查共 `20` 项，全部通过。
- 公网首页和本次版本化 JS/CSS 资源全部返回 `200`；匿名读取当前计划和匿名冻结计划都返回 `401`。Chrome 已真实渲染公网登录页；公网静态资产名称与本地完成桌面、冻结历史及 `390×844` 验收的构建产物一致。
- 临时普通用户完成真实生产 API 验收：注册 `201`、登录/会话 `200`、缺少 CSRF 的冻结请求 `403`、当前计划 `200/blocked`；首次冻结 `created=true`，相同证据再次冻结 `created=false` 且复用同一计划，历史数量为 `1`，详情的 Schema、Evidence、Result 与全部绑定校验均为 `true`。结果继续保持 `execution_authorized=false`、`cash_source_confirmed=false`。验收后账户已停用，活跃会话为 `0`。
- 发布后备份再次使用 AES256 上传私有 OSS，SHA-256 为 `0f0a881e119daffc1369a8833f45284f2a5bad8888f3f6aa1e2a4b3389c7156b`；独立恢复成功核对 `65` 张表、`8` 个迁移标记。

本次验收确认的是决策链、资金约束、证据完整性和故障关闭行为正确，不代表策略已经积累足够前瞻样本，也不构成未来收益保证。
