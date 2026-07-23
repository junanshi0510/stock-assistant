# 2026-07-23 更新 005：自适应策略投资委员会与候选共识模型组合

## 1. 为什么这是一个“大功能”

平台此前已经具备完整的候选、验证和资金链路：

1. 机会工厂生成版本化候选与约束后纸面组合；
2. 收益实验室只用冻结后的 5/20/60 个真实交易日前瞻结果，验证成本后收益、基准超额、独立批次、回撤、置信区间和跨策略多重检验；
3. 投资指挥台把合格策略接入真实持仓、IPS、可信人民币估值、组合穿透和压力情景。

真正缺失的是“多个合格策略如何共同决策”。旧资金引擎最多选择三个合格策略并等额分配研究袖套，但它无法回答：

- 两个策略是否只是重复买入同一批股票；
- 历史累计仍合格、最近已经连续失效的策略是否应继续拿钱；
- 哪些候选得到多个独立策略共同支持；
- 单一策略、重复策略或单一股票过度集中时应保留多少现金；
- 新一期模型变化多大才值得再平衡；
- 当策略失效时，旧指令和淘汰理由能否被完整审计。

本次新增的投资委员会不是又一张评分卡，而是位于“前瞻收益资格”和“全组合资金金额”之间的正式组合构建层。

```text
机会扫描
  ↓
冻结纸面组合
  ↓
独立前瞻收益记分卡
  ↓
策略投资委员会
  ├─ 当前记分卡准入
  ├─ 三期失效熔断
  ├─ 策略重叠 / 前瞻相关
  ├─ 独立贡献与袖套权重
  ├─ 候选共识与单票上限
  └─ 漂移带与不可变指令
  ↓
全组合资金决策 v2
  ↓
IPS / 已有仓位 / 估值 / 行业 / 压力情景
  ↓
人工研究上限 + 现金保留（不下单）
```

## 2. 同行与专业平台参考

本次只参考官方方法或官方产品文档，不复制营销式“AI 选股胜率”：

| 专业做法 | 官方参考 | 本项目吸收的原则 |
|---|---|---|
| 多 Alpha 先进入 Portfolio Construction，再经过 Risk 和 Execution | [QuantConnect Algorithm Framework](https://www.quantconnect.com/docs/v1/algorithm-framework/overview)、[Portfolio Construction](https://www.quantconnect.com/docs/v2/writing-algorithms/algorithm-framework/portfolio-construction/key-concepts) | 把策略信号和最终资金金额拆层；委员会只构建模型组合，IPS 和压力引擎保留最终否决权 |
| 多模型必须用真实 live / validation 结果持续评分，并关注独立贡献、换手和特征中性 | [Numerai Signals Scoring](https://docs.numer.ai/numerai-signals/scoring)、[Meta Model Contribution](https://docs.numer.ai/numerai-tournament/scoring/meta-model-contribution-mmc) | 只接纳冻结后的前瞻证据；用独立贡献而不是单纯最高历史收益决定袖套；保留漂移和换手门禁 |
| 一个账户可以配置多个模型组合和动态目标，并在目标变化后再平衡 | [IBKR Model Portfolios](https://www.interactivebrokers.com/en/trading/model-portfolios.php) | 使用策略袖套和候选目标组合，不把每个策略各自的买入清单直接相加 |
| 再平衡应由目标漂移触发，并避免没有经济意义的小额交易 | [Betterment Portfolio Rebalancing](https://www.betterment.com/help/portfolio-rebalancing) | 使用包含现金的单边换手度量；候选漂移未达到 10% 且没有策略进出时不要求再平衡 |

这些做法的共同点不是“预测每只股票明天涨跌”，而是把信号质量、模型组合、风险预算和执行纪律分开。项目沿用这一原则，并继续拒绝把规则分数映射成伪概率。

## 3. 委员会准入

策略必须同时满足以下条件才进入委员会候选：

1. 收益实验室当前状态为 `limited_manual_pilot`；
2. 当前资本计划为 `available`；
3. 当前策略版本、收益政策和证据截止时间已经冻结为不可变记分卡；
4. 记分卡 SHA-256 完整性通过；
5. 当前冻结纸面组合仍存在并含有正权重候选；
6. 最近三个独立前瞻批次没有触发连续失效熔断。

实时页面刚刚过线但没有冻结记分卡时仍不能获得委员会权重。回测、技术分、一次上涨和 LLM 文本均不参与准入。

## 4. 近期失效与熔断

每个策略只读取主验证窗口中已经成熟、起点独立的前瞻批次，并按冻结时间取最近三个：

- 三个批次全部 `net_excess_return_pct <= 0`：`suspended`，权重立即归零；
- 未连续失败，但最近三期均值不高于 0 或胜基准比例低于 50%：保留准入但应用 `0.55` 衰减系数；
- 少于三个近期批次：不触发衰减判断，但仍受原收益实验室最少样本门禁约束。

该逻辑故意不让长期累计平均值覆盖近期连续失败。熔断状态、三个批次的结果和原因都会进入委员会 Evidence，历史不可修改。

## 5. 策略冗余与独立贡献

### 5.1 当前候选权重重叠

将每个冻结纸面组合的正权重归一化后，两个策略的重叠定义为：

```text
overlap(i,j) = Σ min(weight(i,k), weight(j,k))
```

完全相同的候选和权重为 100%，完全不同为 0%。

### 5.2 前瞻超额相关性

委员会按 `outcome_date_max`（缺失时使用冻结时间）归入自然月，每个策略每月先取成本后净超额均值。只有两个策略至少存在四个共同月份时才计算 Pearson 相关性；否则返回：

```json
{
  "cohort_excess_correlation": null,
  "correlation_decision_eligible": false,
  "aligned_cohort_months": 3
}
```

样本不足不会被 0 或估算值替代。

### 5.3 最终冗余与独立贡献

```text
redundancy(i,j) = max(
  current_position_overlap(i,j),
  max(0, forward_excess_correlation(i,j))
)

unique_contribution(i) = 1 - mean(redundancy(i, peers))
```

负相关不会被当作惩罚，高正相关或高度候选重叠会降低独立贡献。该指标是组合分配系数，不是收益预测或显著性概率。

## 6. 策略袖套与主动现金

委员会最多启用三个策略。分配遵循：

1. 等权是锚；
2. 家族校正置信区间下界、胜基准比例、成熟批次数和回撤只允许形成 `0.9~1.1` 的窄幅证据倾斜；
3. 独立贡献形成 `0.7~1.0` 的去冗余倾斜；
4. 近期衰减策略再乘 `0.55`；
5. 单策略权重硬上限 50%。

现金不是“没算完”，而是模型的正式结果：

| 场景 | 委员会最大可投入 |
|---|---:|
| 无入选策略 | 0% |
| 只有一个入选策略 | 50% |
| 至少两个策略，平均冗余低于 65% | 100% |
| 平均冗余达到 65% | 85% |
| 平均冗余达到 80% | 70% |

如果超过三个策略通过，委员会使用“证据效用 × 与已选策略的非冗余度”逐步选择，未入选策略保留为 `reserve`，不删除其证据。

## 7. 候选共识模型组合

候选原始目标权重为：

```text
raw_target(stock) =
  Σ committee_strategy_weight(strategy)
    × within_strategy_frozen_weight(stock)
```

随后应用 25% 单候选模型上限。超出部分不向其他股票追量再分配，直接保留现金，避免一个被多个相似策略共同持有的股票绕过集中度控制。

每只候选返回：

- 委员会排名；
- 模型目标权重；
- 支持策略数量和来源；
- 支持策略权重占委员会可投入部分的一致度；
- `committee_consensus`、`diversified_support` 或 `single_strategy_candidate`；
- 是否触发 25% 上限；
- `calibrated_probability=false`；
- `execution_authorized=false`。

没有进入当前模型的股票只表示“没有当前前瞻共识”，不能被自动解释为一定下跌。

## 8. 漂移与再平衡

委员会把候选和策略权重都补入 `CASH` 后计算单边换手：

```text
one_way_turnover =
  0.5 × Σ |current_weight(asset) - previous_weight(asset)|
```

满足任一条件才返回 `rebalance_required`：

- 候选单边换手达到 10%；
- 有策略新进入；
- 有策略退出或被熔断。

低于阈值的变化返回 `within_band`。这只是模型组合复核信号，不是订单或自动调仓授权。

## 9. 不可变数据模型

新增表：

```text
opportunity_committee_mandates
```

每条指令保存：

- 用户、操作者、Schema 和 Engine 版本；
- 状态和证据截止时间；
- 完整 Evidence JSON / SHA-256；
- 完整 Result JSON / SHA-256；
- 当前策略版本、记分卡、纸面组合与证据哈希绑定；
- 创建时间。

唯一键为：

```text
(user_id, engine_version, evidence_sha256)
```

相同证据重复冻结会复用已有指令；SQLite 与 PostgreSQL 均拒绝 UPDATE/DELETE。读取时重新校验 Evidence、Result、Schema、Engine 和 Evidence→Result 绑定。

## 10. API 与前端

新增受认证接口：

| 方法 | 路径 | 作用 |
|---|---|---|
| GET | `/api/v1/opportunities/committee` | 读取当前委员会、与上一指令的漂移和绑定状态 |
| POST | `/api/v1/opportunities/committee/mandates` | 冻结当前不可变委员会指令 |
| GET | `/api/v1/opportunities/committee/mandates` | 按当前用户列出历史指令 |
| GET | `/api/v1/opportunities/committee/mandates/{mandate_id}` | 读取并验证一份完整指令 |

机会工厂新增“投资委员会”工作区，展示：

- 委员会运行/集中/降级/收集状态；
- 入选策略、袖套权重、家族校正下界、独立贡献和近期三期状态；
- 候选共识模型组合和现金保留；
- 策略冗余矩阵；
- 10% 漂移控制带；
- 不可变指令历史；
- 方法和不授权交易边界。

投资指挥台的候选卡同步显示委员会排名、策略支持、一致度和观点标签，并可以直接跳转委员会。

## 11. 全组合资金引擎 v2

`whole_portfolio_next_best_action.v2` 不再对全部合格策略简单等权：

1. 先运行委员会并只保留非零袖套；
2. 用全局人工试运行上限乘委员会策略权重；
3. 继续受每个策略自己的可用计划上限约束；
4. 聚合候选后再次应用委员会 25% 候选目标上限；
5. 再经过已有仓位动作、允许市场、单品、权益、保守行业容量和四组全组合压力情景；
6. 最终 Evidence 绑定 `committee_evidence_sha256`。

委员会只能减少、分散或保留现金，不能覆盖任何现有硬门禁。

## 12. 迁移与 readiness

新增 PostgreSQL 迁移：

```bash
python -m migrations.opportunity_committee_v1
```

迁移使用独立 advisory lock，创建表、索引、不可变触发器和 `opportunity-investment-committee.v1` 迁移标记。生产 readiness 新增：

```json
{
  "opportunity_committee_schema": true
}
```

该值未通过时 API 不接入权威流量。SQLite 只允许本地开发和测试时原地建表；生产不能依赖应用启动自动迁移。

## 13. 验证范围

专项测试覆盖：

- 单策略 50% 投入与候选 25% 上限；
- 重复策略惩罚和独立策略增权；
- 最近三期连续不跑赢基准的自动熔断；
- 小于 10% 漂移不触发再平衡；
- 指令内容寻址去重；
- 用户隔离；
- SQLite / PostgreSQL 不可变保护；
- 全组合金额对委员会现金和候选上限的实际继承；
- 新 API 路由契约；
- 前端生产构建。

本地结果：

- 后端全量 `unittest discover`：`529 tests` 通过；
- 委员会、资金引擎、收益实验室和路由专项：`35 tests` 通过；
- 前端 Vite 生产构建：`1852` 个模块转换；
- `npm audit --audit-level=high`：`0 vulnerabilities`；
- OpenAPI：`153` 条路径、`178` 个操作；机会工厂 `20` 条路径、`24` 个操作；
- 真实浏览器：收集态、首次冻结、相同 Evidence 重复冻结幂等、历史当前绑定、`null` 指标显示“—”、桌面/窄屏无全局横向溢出、控制台无 warning/error 均通过。

生产迁移、双 API 副本发布、真实认证/API 和公网浏览器验收结果会在发布完成后追加到本记录。

## 14. 明确限制

- 本功能提高策略淘汰、组合分散、现金纪律和决策可审计性，不能保证盈利。
- 当前相关性按至少四个共同自然月的前瞻超额计算；样本仍小，不代表长期稳定相关结构。
- 当前候选共识只包含获得资金资格的冻结组合，不覆盖交易所全部股票；没有入选不能被解释为一定下跌。
- 跨市场纸面收益仍未计入持有期汇率变化，统一成本情景也不等于用户实际券商费用。
- 委员会不读取实时券商现金、整手、涨跌停、停牌、税费、滑点和市场冲击，不产生股数或订单。
- 项目仍然坚持“无证据则留现金、证据失效则停用”，不会为了显示一个答案而降低门槛。
