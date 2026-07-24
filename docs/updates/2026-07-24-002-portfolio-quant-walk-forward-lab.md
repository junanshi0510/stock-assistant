# 量化组合 Walk-Forward 实验与模拟调仓中枢

日期：2026-07-24

## 1. 为什么这是一个大功能

平台此前已经能回答“当前持仓是什么、估值是否可信、风险是否越界、候选策略是否通过前瞻验证、资金计划执行得怎样”，但仍缺少一个组合量化层：

1. 当前股票仓位的风险是不是过度集中；
2. 等权、逆波动、风险平价和最小方差在相同真实数据、相同成本下分别表现怎样；
3. 模型是在样本内看起来漂亮，还是在严格未见过的数据上仍有基本稳定性；
4. 如果只从风险角度再分配，人民币目标金额和换手成本是多少；
5. 什么条件下只允许研究，什么条件下最多可冻结纸面调仓指令。

本次新增的 `portfolio_walk_forward_optimizer@1.0.0` 把以上问题放进一条用户隔离、可恢复、可审计、失败关闭的链路。它不预测某只股票明天涨跌，也不承诺赚钱；其价值是减少集中度、回测泄漏、隐性成本和主观挑模型造成的错误决策。

## 2. 专业产品与方法参考

| 参考 | 本项目吸收的做法 | 本项目没有照搬的部分 |
| --- | --- | --- |
| [QuantConnect Walk Forward Optimization](https://www.quantconnect.com/docs/v2/writing-algorithms/optimization/walk-forward-optimization) | 用滚动训练窗口估计参数，再应用到随后测试窗口；控制调参频率 | 不在每个窗口搜索超参数，不用测试结果自动挑赢家 |
| [QuantConnect Portfolio Construction](https://www.quantconnect.com/docs/v2/writing-algorithms/algorithm-framework/portfolio-construction/key-concepts) | 把组合目标与执行分层，目标先进入风险与执行边界 | 本项目不连接执行模型或券商 |
| [QuantConnect Slippage Models](https://www.quantconnect.com/docs/v2/writing-algorithms/reality-modeling/slippage/supported-models) | 回测必须显式考虑滑点，成本与换手相关 | 日线版尚未模拟订单簿、成交量冲击和排队 |
| [QuantConnect Optimization Objectives](https://www.quantconnect.com/docs/v2/cloud-platform/optimization/objectives) | 使用 Probabilistic Sharpe 作为统计诊断之一 | PSR 不解释为未来盈利概率，也不单独放行 |
| [CFA Institute Backtesting and Simulation](https://www.cfainstitute.org/insights/professional-learning/refresher-readings/2026/backtesting-and-simulation) | 披露回测偏差、交易成本、样本外验证和模拟边界 | 当前持仓股票池不能代表历史可投资全市场 |
| [Alpaca Paper Trading](https://docs.alpaca.markets/us/docs/paper-trading) | 明确纸面结果与真实成交之间仍有市场冲击、延迟和成交差异 | 本项目当前连纸面券商都不连接，只冻结内部研究指令 |

## 3. 产品闭环

```text
用户确认持仓
  -> 当前持仓 SHA-256
  -> 当前可信 CNY 估值快照
  -> 有效投资政策
  -> 用户选择风险构建方法和成本参数
  -> 创建不可变实验 + 持久 market-data 作业
  -> 逐资产真实复权日线与价格序列 SHA-256
  -> 共同交易日对齐
  -> 严格滚动训练/测试
  -> 五模型成本后对照
  -> 最新风险贡献和 CNY 目标金额
  -> 12 项纸面准入门禁
  -> research_only 或不可变 paper mandate
```

运行开始后，持仓、估值、投资政策、参数和证据摘要全部冻结。运行期间用户修改持仓不会改写历史；冻结纸面指令时会重新读取当前事实，任何绑定变化都要求重跑。

## 4. 股票池与数据边界

### 4.1 股票池

- 只使用运行开始时用户已经确认的 A 股、港股或美股直接股票持仓；
- 最少 2 只，最多 12 只；
- 基金、现金、债券和未知资产不进入协方差模型；
- 组合总市值仍保留在目标权重口径中，量化模型只再分配股票袖套；
- 当前持仓股票池天然存在选择偏差和幸存者偏差，因此结果不能称为“选股策略历史业绩”。

### 4.2 历史行情

- 每只资产读取真实复权日线；
- 清洗后至少 150 个价格点；
- 对每只资产冻结来源、首末日期、价格数和完整价格序列 SHA-256；
- 所有资产按共同交易日内连接，任何缺失日不会用前值或模拟值填补；
- 研究允许部分失败，但纸面指令要求请求资产 100% 覆盖。

### 4.3 来源治理

纸面准入当前只承认专业历史来源：

- A/H 股：Tushare；
- 美股：Massive 的底层 Polygon 标识或 Alpha Vantage；
- 其他公开免费备用源可用于明确标注的研究结果，不能冻结纸面指令；
- 最后共同交易日距离运行日不得超过 7 天。

这里的来源门禁不意味着某家供应商永远正确，而是防止公开网页中断、反爬、字段漂移或陈旧缓存被包装成可执行证据。

## 5. Walk-Forward 口径

用户可选：

- 训练窗口：126、252 或 504 个共同交易日；
- 测试/再平衡窗口：21 或 63 个共同交易日。

第 \(k\) 个窗口满足：

```text
train_k = [test_start_k - lookback, test_start_k)
test_k  = [test_start_k, test_start_k + rebalance)
```

训练窗口只负责协方差和目标权重估计，随后测试窗口只负责观察成本后表现。测试数据绝不回流到同一窗口的权重估计。每个完整测试窗口形成一个 fold，纸面准入至少需要 6 个 fold。

系统会计算全部五种模型，但最终研究方案由用户在运行前明确选择。系统不会查看样本外结果后自动改选表现最好的模型，因为这种“事后挑赢家”本身会形成新的数据窥探。

## 6. 风险模型

### 6.1 协方差收缩

训练窗口样本协方差记为 \(\Sigma\)，其对角阵记为 \(D\)：

```text
Σ_shrunk = 0.75 × Σ + 0.25 × D
```

固定收缩用于降低短样本下非对角估计噪声。引擎版本和收缩比例写入冻结政策，不能在运行后改写。

### 6.2 五种对照

1. `current_weights`：以运行开始时股票袖套人民币金额占比作为基线；
2. `equal_weight`：资产等权，并受单股上限投影约束；
3. `inverse_volatility`：权重与训练期波动倒数成比例；
4. `risk_parity`：迭代缩小各资产风险贡献与均等目标的差异；
5. `minimum_variance`：在非负、单股上限和总权重不超过 100% 下最小化组合方差。

模型不建立预期收益率，也不最大化历史收益：

```text
expected_return_model = none
objective = risk_only_no_historical_return_maximization
```

单股上限不足以容纳全部股票袖套时，未分配部分按零收益现金处理，不为了凑满仓位突破上限。

### 6.3 风险贡献

组合波动由 \(w^\top\Sigma w\) 得到。第 \(i\) 个资产的边际风险贡献为：

```text
RC_i = w_i × (Σw)_i
```

页面展示归一化后的逐资产风险贡献百分比，并用其平方和形成风险贡献集中度 HHI。风险平价不等于资金等权，也不代表每只股票具有相同涨跌概率。

## 7. 成本与换手

每个 fold 开始时，从上一 fold 结束后的漂移权重转向本 fold 目标权重：

```text
buy_turnover  = Σ max(target_i - previous_i, 0)
sell_turnover = Σ max(previous_i - target_i, 0)

cost =
  buy_turnover  × (commission_bps + slippage_bps)
  + sell_turnover × (commission_bps + slippage_bps + sell_tax_bps)
```

成本在测试窗口首日从组合收益中扣除。页面同时展示买入换手、卖出换手、总换手、单边换手和估算人民币成本。

该模型仍是日线近似，尚未包含：

- 订单簿深度与参与率；
- 大单市场冲击；
- 网络/券商延迟；
- 价格优先和队列位置；
- A 股涨跌停、停牌与不同市场整手/零股规则。

因此成本后结果依旧不能视为可复制的实盘成交。

## 8. 统计与风险诊断

五个模型使用完全相同的样本外日收益序列口径，展示：

- 累计和年化收益；
- 年化波动；
- Sharpe；
- Sortino；
- 最大回撤；
- 日度 95% CVaR；
- Probabilistic Sharpe；
- 平均和最高单边换手；
- 最新训练窗口的风险贡献与风险集中度。

PSR 用样本长度、偏度和峰度校正观察到的 Sharpe，回答“成本后 Sharpe 大于零的统计证据有多强”，不是“未来赚钱概率”。纸面准入的 55% 只是最低诊断门槛，还必须同时通过其余事实、来源、风险与换手门禁。

## 9. 人民币目标金额

选定模型基于最新训练窗口生成股票袖套目标权重，再映射到冻结时可信人民币金额：

```text
target_amount_i = stock_sleeve_value_cny × target_weight_i
delta_amount_i  = target_amount_i - current_amount_i
```

小于用户最小调仓金额的差额显示为 `hold_small_delta`。结果只包含：

- 当前与目标人民币金额；
- 增持、减持或小差额保持；
- 股票袖套和总组合目标权重；
- 当前目标换手和估算成本。

结果明确设置：

```json
{
  "quantity_generated": false,
  "execution_authorized": false
}
```

系统不读取券商可用现金、冻结资金、保证金、实时盘口或可卖数量，不生成股数和订单。

## 10. 纸面调仓准入

全部检查必须同时通过：

1. 有效投资政策；
2. 可信人民币调仓金额；
3. 单一市场，避免缺失历史汇率收益；
4. 至少 6 个完整 walk-forward fold；
5. 冻结股票池历史覆盖 100%；
6. 专业历史源覆盖 100%；
7. 最后共同交易日不超过 7 天陈旧；
8. 样本外平均单边换手、最高单边换手和当前目标单边换手均不超过用户上限；
9. 选定模型样本外波动不高于当前权重的 105%；
10. 选定模型样本外最大回撤不比当前权重多 2 个百分点；
11. 成本后 PSR 不低于 55%；
12. 波动至少下降 5%，或风险贡献 HHI 至少下降 10%。

通过后的状态是 `paper_ready`，不是 `trade_ready`。冻结时还必须：

- 用户勾选“只用于纸面调仓研究”；
- 客户端提交刚刚读取的 Result SHA-256；
- 当前持仓 SHA 与运行一致；
- 当前估值快照仍是同一份且 `trade_amount_eligible=true`；
- 当前有效投资政策版本仍与运行一致。

相同证据内容寻址复用，避免重复冻结；历史纸面指令拒绝修改和删除。

## 11. 持久化与完整性

迁移 `portfolio-quant-lab.v1` 建立：

| 表 | 作用 | 不可变规则 |
| --- | --- | --- |
| `portfolio_quant_runs` | 冻结输入、作业状态、完成结果与双哈希 | 冻结输入不可修改；完成结果不可重写 |
| `portfolio_quant_run_events` | 创建、排队、运行、进度和终态事件 | 前序哈希链；拒绝 UPDATE/DELETE |
| `portfolio_quant_mandates` | 通过门禁后的纸面人民币目标 | Evidence/Target 双哈希；拒绝 UPDATE/DELETE |

所有读取按 `tenant_id + user_id` 限定。列表只返回轻量摘要；详情读取会复算输入、结果和事件链。生产 PostgreSQL 缺少任一表时，`portfolio_quant_schema=false`，API readiness 拒绝接流量。

## 12. 异步与高可用

生产创建运行时：

1. API 先在 PostgreSQL 写入冻结 Run；
2. 创建 `portfolio_quant_run` 持久作业；
3. Redis/Celery 消息只携带 Job ID；
4. `market-data` Worker 领取租约、更新心跳、读取真实行情并计算；
5. 完成结果写回 PostgreSQL，再追加终态哈希事件；
6. 前端轮询用户隔离的 Run 状态。

任务软/硬时限为 900/960 秒。API 副本不执行外部历史抓取；Redis 暂时失败时不会让未冻结输入绕过 PostgreSQL事实源。运行查询还会对后台作业失败状态做终态协调。

## 13. API

全部接口要求登录，资源详情同时校验租户与用户所有权：

| 方法 | 路径 | 作用 |
| --- | --- | --- |
| GET | `/api/portfolio/quant-lab/overview` | 当前边界、默认参数、最近运行和指令 |
| POST | `/api/portfolio/quant-lab/runs` | 冻结证据并创建量化实验 |
| GET | `/api/portfolio/quant-lab/runs` | 运行历史摘要 |
| GET | `/api/portfolio/quant-lab/runs/{run_id}` | 详情、完整结果和事件链 |
| POST | `/api/portfolio/quant-lab/runs/{run_id}/mandates` | 重新校验并冻结纸面指令 |
| GET | `/api/portfolio/quant-lab/mandates` | 纸面指令历史 |
| GET | `/api/portfolio/quant-lab/mandates/{mandate_id}` | 指令详情与完整性 |

请求模型使用 `extra="forbid"`，未知参数不会被静默接受。所有冲突、输入错误、队列不可用和资源不存在均映射为明确 HTTP 状态，不回退模拟结果。

## 14. 前端工作台

入口：`我的资产 → 量化组合`

页面按决策顺序组织：

1. 股票池、估值和投资政策前提；
2. 用户选择模型、训练/测试窗口、成本、单股上限、换手上限和最小金额；
3. 持久运行状态；
4. 五模型成本后横向表；
5. 最新风险贡献；
6. 人民币目标动作；
7. 逐 fold 样本外明细；
8. 12 项纸面准入；
9. Evidence/Result/Event 哈希谱系；
10. 纸面指令历史。

桌面和手机都不产生页面级横向溢出；宽表只在自身容器滚动。失败值显示为缺失或明确错误，不渲染为零。

## 15. 本地验收

已完成：

- 后端全量：`563 passed`、`11 subtests passed`；
- 新功能专项：`9 passed`；
- 新功能与路由契约：`16 passed`；
- 前端 Vite 生产构建：`1855 modules transformed`；
- OpenAPI：`169` 条路径、`197` 个操作；量化实验室为 `6` 条路径、`7` 个操作；
- 本地正常 API readiness：`portfolio_quant_schema=true`；
- 隔离浏览器账户验证桌面布局、`390×844` 手机布局、参数联动和无全局横向溢出；
- 隔离完成态数据验证五模型、风险贡献、人民币目标、fold、12 项门禁与哈希谱系；
- 浏览器应用控制台告警/错误：`0`；
- 验收未点击冻结纸面指令，不存在真实交易副作用。

全量测试只有既有 FastAPI `on_event` 弃用提示和 Pillow 超大图保护提示，没有失败。

## 16. 已知限制与下一步

当前大功能已经能做严谨的“现有股票袖套风险再分配”，但还不是机构级 OMS/EMS：

- 当前股票池不是历史时点全市场成分，不能评估选股 alpha；
- 未建立逐日历史汇率序列，跨市场只能研究；
- 尚未接入公司行动完整账本、退市股票历史和 point-in-time 基本面；
- 成本仍是日线 bps 模型，未按成交量和订单参与率建模冲击；
- 没有税务批次、整手、零股、涨跌停、停牌和实时可卖量优化；
- 没有券商连接、订单生命周期、风控审批或自动交易；
- 再平衡方法是风险模型，不是未来收益预测器。

下一阶段应优先建设“历史时点可投资股票池 + point-in-time 因子数据 + 事件驱动模拟撮合”，并把量化候选策略先接入已有机会工厂的独立前瞻观察与投资委员会，而不是直接扩大实盘权限。

## 17. 生产发布记录

本节在 GitHub 功能提交、PostgreSQL 迁移、双副本滚动发布、生产临时账户验收、发布后加密备份和隔离恢复全部完成后补充。部署未完成前，README 与页面不得把本地通过描述为生产上线。
