# 多周期 Alpha 概率实验室与模型放行治理

发布日期：2026-07-26

## 1. 为什么这是决策级大功能

平台此前已经具备历史时点选股、事件驱动撮合、成本后回测、固定日历研究计划和真实前向收益观察，但仍缺少一条完整的“概率是否可信”证据链：

1. 历史规则分数不能回答“这个概率是否校准”；
2. 普通回测容易把训练、调参、校准和最终评价混在一起；
3. 历史样本外通过不等于未来仍有效；
4. 股票与基金具有不同交易周期、基准和确认规则，不能共用一个含糊目标；
5. 如果模型证据不足，产品必须能够明确弃权，而不是为了给答案强行输出“会涨/会跌”。

本次新增独立的“研究中心 → 概率实验室”，形成下面的治理链：

```text
冻结研究计划与股票/基金池
  → 真实历史价格或确认净值
  → 时间分组滚动样本外预测
  → 仅用较早样本外结果拟合概率校准器
  → 在更晚、未参与校准的样本外结果上评价
  → 10 项固定统计门槛 + 冻结池/来源发布边界
  → 历史通过后仅发布 shadow 概率
  → 按真实未来交易日/确认净值日结算
  → 7 项前向放行门槛
  → 仅后续运行可标记 decision_eligible
```

这项功能用于提高研究证据对人工决策的价值，不连接券商、不生成订单、不自动下单，也不承诺盈利。

## 2. 参考方法与产品原则

设计参考了专业量化平台和研究机构公开的方法边界：

- scikit-learn 的[概率校准说明](https://scikit-learn.org/stable/modules/calibration.html)强调校准数据应与模型拟合数据分离，并使用可靠性图、Brier Score 等评价概率质量；
- scikit-learn 的[`TimeSeriesSplit`](https://scikit-learn.org/stable/modules/generated/sklearn.model_selection.TimeSeriesSplit.html)说明时间序列验证必须保持训练样本先于测试样本；
- Bailey 等人的[回测过拟合概率研究](https://papers.ssrn.com/sol3/Papers.cfm?abstract_id=2326253)说明反复搜索并只报告最佳回测会显著夸大策略证据；
- CFA Institute 的[回测与模拟框架](https://www.cfainstitute.org/insights/professional-learning/refresher-readings/2026/backtesting-and-simulation)强调样本外评价、交易成本、偏差识别和稳健性验证；
- QuantConnect 的[Algorithm Framework](https://www.quantconnect.com/docs/v2/writing-algorithms/algorithm-framework/alpha/key-concepts)把 Alpha 观点与组合构建、风险和执行分离，本平台同样不让预测组件拥有资金或交易权限；
- NBER 的[动量崩溃研究](https://www.nber.org/papers/w20439)说明历史有效的横截面效应会在特定市场状态下出现显著尾部风险，因此平台不把单一历史统计直接升级为长期有效的交易授权。

实现没有进行自动参数搜索，也不会在一批结果出现后修改门槛。模型家族、特征、目标、周期、成本、股票/基金池和运行频率均在计划创建时冻结。

## 3. 股票与基金的研究目标

### 3.1 股票

| 市场 | 默认基准 | 周期 | 默认往返成本 |
| --- | --- | --- | --- |
| A 股 | `000300.SH` 沪深 300 指数 | 5/20/60 个交易日 | 30 bps |
| 港股 | `02800` 盈富基金代理基准 | 5/20/60 个交易日 | 40 bps |
| 美股 | `SPY` | 5/20/60 个交易日 | 20 bps |

标签固定为：

```text
股票目标收益 = 个股未来收益 - 同期基准收益 - 冻结往返成本
标签 = 股票目标收益 > 0
```

历史价格使用已有真实数据源接力，不使用合成行情。每条序列明确分成 `professional`、`research_grade`、`public_fallback` 或 `unknown`：Tushare、Massive/Polygon、Alpha Vantage、富途属于专业源；BaoStock 属于研究级；腾讯、Yahoo、东方财富股票行情属于公共降级。A 股使用已有 `a_share_research` 专用链路，指数基准使用独立指数读取；公共降级或未知源即使统计指标很好也不能发布概率。

### 3.2 基金

基金周期固定为 20/60/120 个确认净值观察，默认往返成本为 50 bps：

```text
基金目标收益 = 未来确认单位净值收益 - 冻结往返成本
标签 = 基金目标收益 > 0
```

盘中估值、近似日期和合成净值不能进入训练或结果结算。开放式基金不同于可盘中成交的股票，页面不会把预测日期描述为可成交价格或订单时点。

## 4. 固定模型与防前视切分

引擎版本为 `calibrated_multi_horizon_alpha@1.0.0`，模型家族为 `fixed_logistic_sigmoid_calibration`。本版使用固定逻辑回归基模型与固定特征，不进行超参数或特征搜索：

- 1/5/20/60 日收益与趋势；
- 20 日年化波动、下行波动与最大回撤；
- 成交量变化和流动性特征（基金允许为空）；
- 股票相对基准的 20/60 日收益；
- 基准 20 日收益和波动。

严格时间口径：

1. 同一个信号日期的多个资产只能整体进入同一训练或测试组；
2. 每个 walk-forward fold 的训练日期严格早于测试日期；
3. 训练行的 `label_end_date` 必须严格早于测试起点，避免长周期标签跨进测试区间；
4. 每个 fold 只使用当时已经形成的历史；
5. 全部样本外预测按日期再次前后切开，较早部分只拟合 sigmoid 校准器；
6. 较晚部分既不拟合基模型，也不拟合校准器，只用于最终历史门槛；
7. 最终当前截面模型只在历史门槛完成后拟合，并保存 shadow 概率；历史门槛失败时发布概率为 `null`。

## 5. 概率评价、10 项统计门槛与数据发布边界

最终历史评价同时覆盖概率准确性、区分能力、校准度、经济性和跨折稳定性：

| 门槛 | 股票阈值 | 基金阈值 |
| --- | --- | --- |
| 最终评估样本 | `>=30` | `>=20` |
| 最终评估日期 | `>=5` | `>=4` |
| 横截面资产覆盖 | `>=4` | `>=4` |
| 独立校准样本 | `>=30` | `>=20` |
| Brier Skill | `>0`，优于校准期常数基准 | 同左 |
| Log Loss 改善 | `>0` | 同左 |
| ROC AUC | `>=0.52` | 同左 |
| Expected Calibration Error | `<=0.12` | 同左 |
| 高/低概率组成本后收益差 | `>0%` | 同左 |
| 跨 fold 方向稳定率 | `>=0.50` | 同左 |

结果还保存 Brier Score、基准 Brier、Log Loss、基准 Log Loss、可靠性分箱、概率四分位组收益和逐 fold 元数据。任何一项失败时：

- `historical_gate_passed=false`；
- `calibrated_probability=null`；
- shadow 概率仅保留在受审计结果中用于诊断；
- 状态固定为“证据不足 · 弃权”；
- 不能进入多周期支持结论或前向放行。

统计门槛通过后还必须同时满足：

1. 创建时冻结的资产池达到 `100%` 真实历史覆盖，不能只保留成功下载的幸存资产；
2. 每个资产和股票基准的来源至少为 `research_grade`，公共降级或未知源直接弃权；
3. 股票要进入未来 `decision_eligible`，资产和基准必须全部为 `professional`；BaoStock 只允许 shadow 研究；
4. 基金只使用确认单位净值，并把当前东方财富/天天基金净值链路标为 `research_grade`，不冒充专业付费源。

## 6. 真实前向放行

历史门槛通过只代表可以开始收集真实未来证据。每条 shadow 预测冻结：

- 计划、运行、资产、周期和信号日期；
- 历史门槛状态、校准概率和校准期基准概率；
- 信号日资产起点与股票基准起点；结算不得用后来重载的历史起点覆盖；
- 目标定义、基准、成本和预计最早成熟日期；
- 内容哈希、引擎版本和创建时间。

调度器只在足够的后续真实交易日或确认净值日形成后写入 outcome。历史价格后来扩展不会改写已结算结果。

每个周期只统计创建时已经满足决策来源边界的预测结果：股票资产与基准必须均为 `professional`，基金必须是至少 `research_grade` 的确认净值。来源等级不足的成熟 outcome 保留审计，但不进入放行样本。在此前提下必须同时通过 7 项前向门槛：

| 门槛 | 阈值 |
| --- | --- |
| 独立成熟结果 | `>=30` |
| 独立运行日期 | `>=6` |
| 资产覆盖 | `>=4` |
| 前向 Brier Skill | `>0` |
| 前向 ECE | `<=0.12` |
| 前向高低概率组收益差 | `>0%` |
| 逐批优于概率基准比例 | `>=0.50` |

放行具有明确的时间方向：某周期在运行开始前已经通过前向门槛，该次运行的新预测才可标记 `decision_eligible=true`。用于首次跨过门槛的旧预测不会被追溯改写成当时已可决策。

## 7. 多周期共识

页面按资产展示不同周期的概率矩阵和共识：

- 所有可发布周期方向一致时显示“多周期支持”；
- 短中长期方向冲突时显示“周期冲突”；
- 概率接近中性或只有部分周期可用时显示“中性/证据有限”；
- 任一概率只有在对应历史门槛通过时才展示；
- `decision_eligible` 还必须满足运行前已经存在的真实前向放行。

共识是研究排序信号，不是仓位、资金金额或订单。

## 8. 六张生产表与不可变边界

| 表 | 作用 | 可变性 |
| --- | --- | --- |
| `alpha_forecast_programs` | 冻结资产池、目标、基准、成本、周期、频率和模型政策 | 只允许受控状态与调度字段变化 |
| `alpha_forecast_program_events` | 计划创建、运行、暂停、恢复、完成事件 | 只追加、前序哈希链 |
| `alpha_forecast_runs` | 一次历史训练/校准/评价与当前截面运行 | 输入不可变，完成结果不可重写 |
| `alpha_forecast_run_events` | queued/running/completed/failed 事件 | 只追加、前序哈希链 |
| `alpha_forecasts` | 每资产、每周期的 shadow 预测 | 不允许 UPDATE/DELETE |
| `alpha_forecast_outcomes` | 成熟后的真实标签、收益、基准和成本 | 不允许 UPDATE/DELETE |

所有业务记录按 `tenant_id + user_id` 隔离。完成结果、政策、事件和 outcome 使用 SHA-256 或哈希链校验；PostgreSQL 与 SQLite 测试路径都安装不可变/状态保护。迁移标记为 `alpha-forecast-lab.v1`，readiness 门禁为 `alpha_forecast_schema`。

## 9. API、任务和权限

| 方法 | 路径 | 权限 |
| --- | --- | --- |
| GET | `/api/v1/alpha-forecasts/overview` | 当前登录用户 |
| POST | `/api/v1/alpha-forecasts/programs` | 当前登录用户 + CSRF |
| GET | `/api/v1/alpha-forecasts/programs/{program_id}` | 资源所有者 |
| POST | `/api/v1/alpha-forecasts/programs/{program_id}/runs` | 资源所有者 + CSRF |
| POST | `/api/v1/alpha-forecasts/programs/{program_id}/actions` | 资源所有者 + CSRF |
| POST | `/api/v1/alpha-forecasts/programs/{program_id}/settle` | 资源所有者 + CSRF |
| GET | `/api/v1/alpha-forecasts/runs/{run_id}` | 资源所有者 |
| POST | `/api/v1/alpha-forecasts/maintenance` | 管理员 + CSRF |

API 先把完整请求写入 PostgreSQL，再向 `market-data` 队列发送唯一 Run ID。手动核对真实结果也只发送 Program ID，由 Worker 重新读取租户范围；API 进程不直接抓取行情。Redis 消息不包含股票池、概率、用户数据或 API Key。Celery Beat 默认每 6 小时执行一次幂等维护，结算成熟 outcome、回收/重派异常任务并派发到期计划。

## 10. 前端

“研究中心 → 概率实验室”提供：

- A 股、港股、美股和基金预设；
- 冻结股票/基金池、历史窗口、成本、频率和确认提示；
- 计划状态、下一运行时间、运行历史和错误审计；
- 每周期历史门槛、指标与前向 scorecard；
- 股票 5/20/60 日、基金 20/60/120 净值观察的概率矩阵；
- 多周期方向、冲突和弃权状态；
- 历史通过、前向收集中、前向已放行三层状态；
- 数据源、目标、基准、成本、模型版本和自动交易关闭提示。

宽表只在自身容器内滚动。桌面与移动端都不把 `null` 概率渲染为 0，也不会把 shadow 概率标成可执行建议。

## 11. 本地验收

- Alpha 专项与任务协议：`28 passed`、`6 subtests passed`；
- Alpha、任务与路由关键回归：`35 passed`、`6 subtests passed`；
- 后端全量：`637 passed`、`13 subtests passed`；
- 前端动态模块重试专项：`4 tests passed`；
- 前端 Vite 生产构建：`1861 modules transformed`；
- `npm audit --omit=dev --audit-level=high`：`0 vulnerabilities`；
- OpenAPI：`196 paths / 226 operations`；
- `git diff --check`：通过；
- 真实浏览器空状态与有结果状态均通过；
- 桌面端和 `390×844` 移动视口无页面级横向溢出；
- 股票计划、基金计划表单切换和禁用目标口径正确；
- 浏览器控制台 error/warn：`0`。

浏览器有结果验收使用独立本地 QA 数据库，不写入生产，也不调用券商或生成订单。

## 12. 生产发布记录

### 12.1 首轮功能发布

- 功能提交 `cb61477c56b111a0387202945888c0300029e9cf` 已推送 GitHub `main`，并原子滚动发布到 `http://8.148.67.79/`；
- 发布前 PostgreSQL 备份已上传私有 OSS，启用服务端 `AES256` 加密，对象为 `backups/postgresql/2026/07/stock-assistant-iZn4ai1fm0tr284w21h4kmZ-20260726T083731Z.dump`，大小 `2,490,457` 字节，SHA-256 为 `19cbf82274336c096f35a6e1b6792f5a29e4e204076c54a6215cc5b6a3acf152`；
- 发布前备份已经在隔离 PostgreSQL 中恢复并核对 `86` 张表、`16` 个迁移标记；
- `alpha-forecast-lab.v1` 迁移成功，生产数据库现有 `92` 张公开表、`17` 个迁移标记、6 张 Alpha 表、8 个非内部不可变触发器、8 个外键和 20 个 Alpha 索引；
- `8001/8002` 两个 API 副本均报告 `ready=true`、`full_service_ready=true`、`alpha_forecast_schema=true` 且 release 一致；Nginx、PostgreSQL、Redis、私有 OSS、5 个 Worker 和 Celery Beat 均 active；
- 两副本 OpenAPI 均为 `196 paths / 226 operations`；5 个 Celery 节点均在线，market-data Worker 已注册运行、维护和结算三项 Alpha 任务；
- 匿名读取概率总览返回 `401`；普通用户在两个副本均可读取自己的空总览，缺失或跨用户计划返回 `404`，管理员维护操作对普通用户返回 `403`；管理员在另一副本执行维护返回 `200`；
- 首轮 RBAC 临时普通账户与管理员账户均已停用，活跃会话为 `0`；生产没有写入合成 Alpha 计划、运行、预测或 outcome。

### 12.2 公网验收发现的可恢复性加固

真实公网页面登录成功后，验收链曾捕获一次 `Failed to fetch dynamically imported module`。同一静态资源从公网和服务器本机均返回 `200`，说明是瞬时传输失败；原前端只有 `Suspense`，最终失败会导致工作区空白。

本次因此统一增加：

- 顶级工作区与研究工具的动态模块最多三次受限重试；
- 顶级 tab 与研究 domain 各自独立错误边界，单个工作区失败不会永久阻断其他工作区；
- 重试仍失败时显示可操作恢复页，不再渲染空白页面；
- “重新加载页面”只刷新前端，不删除服务端已经保存的持仓、研究和审计事实；
- 纯 Node 专项覆盖瞬时失败后成功、非网络初始化错误不重试、重试预算耗尽保留最终错误，共 `4 tests passed`。

最终公网页面、发布后错误日志与发布后隔离恢复证据在可恢复性版本滚动发布后追加。

## 13. 已知边界

- 当前固定资产池不是交易所历史全量成分，仍可能存在选择偏差；计划会明确冻结资产池来源；
- 概率校准改善历史频率一致性，不代表市场结构不会变化；
- Brier、AUC、ECE 和高低组收益差都可能因样本有限而波动；
- 多周期预测高度相关，不能把三个周期当成三个独立下注；
- 基金确认净值具有披露延迟，不能用于盘中执行；
- 成本是冻结研究假设，不等于用户券商、税务或销售平台最终成本；
- 日线研究不建模涨跌停排队、停牌、整手、盘中冲击或实际成交容量；
- 前向放行只影响后续运行，不授权资金、不替代投资政策和组合风险门禁；
- 系统输出仅供研究和人工决策辅助，不构成投资建议，不保证盈利。
