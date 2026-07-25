# 历史时点量化选股、样本外验证与事件驱动撮合实验室

日期：2026-07-25

## 1. 为什么这是一个大功能

上一版“量化组合”已经能对用户当前持仓做严格 Walk-Forward 风险再分配，但它不能回答选股问题：当前持仓本身是今天已经存在的名单，用它回看过去会天然带入选择偏差和幸存者偏差，也无法证明某组因子曾经能在当时真实可投资的股票之间形成稳定排序。

本次新增 `point_in_time_quant_selection@1.0.0`，把选股研究扩展为一条完整、可恢复、可审计的组合链路：

```text
历史时点可投资股票池
  -> 信号日可见数据
  -> 固定多因子横截面排名
  -> 目标权重与现金约束
  -> 次日开盘事件驱动撮合
  -> 成交量容量、部分成交与订单超时
  -> 成本后策略/基准曲线
  -> 非重叠样本外窗口 + Rank IC
  -> 成本翻倍压力测试
  -> 10 项前向纸面门禁
  -> research_only 或不可变 shadow mandate
```

它不是“给今天的热门股打分再回看”的排行榜，也不输出伪上涨概率。系统只提高研究证据、成本估算和风险纪律，不能保证盈利，不连接券商，不生成真实订单。

## 2. 专业方法参考

| 参考 | 本项目吸收的做法 | 本项目边界 |
| --- | --- | --- |
| [QuantConnect Universe Historical Data](https://www.quantconnect.com/docs/v2/writing-algorithms/historical-data/universe-data) | 历史研究必须按当时生效的 universe 选择资产，不能用当前名单覆盖过去 | 当前只有 A 股 Tushare 历史指数权重模式可验证成员时点 |
| [QuantConnect Universe Key Concepts](https://www.quantconnect.com/docs/v2/writing-algorithms/universes/key-concepts) | universe 选择与后续组合构建分层，并显式保留选择时间 | 不实现实时盘中 universe 变更 |
| [QuantConnect Latest Price Fill Model](https://www.quantconnect.com/docs/v2/writing-algorithms/reality-modeling/trade-fills/supported-models/latest-price-model) | 信号与成交必须分离，不能在产生信号的同一时间戳偷用成交价 | 本项目固定为收盘形成目标、后续交易日开盘撮合 |
| [QuantConnect Slippage Models](https://www.quantconnect.com/docs/v2/writing-algorithms/reality-modeling/slippage/supported-models) | 滑点应随订单相对成交量增大，容量不足应产生部分成交 | 当前是日线成交额参与率与二次冲击近似，不是订单簿模型 |
| [CFA Institute Backtesting and Simulation](https://www.cfainstitute.org/insights/professional-learning/refresher-readings/2026/backtesting-and-simulation) | 明确披露前视、幸存者、样本外和交易成本偏差 | 不把历史显著性解释为未来收益概率 |
| [MSCI Diversified Multiple Factor Indexes Methodology](https://www.msci.com/eqb/methodology/meth_docs/MSCI_Diversified_Multiple_Factor_With_Low_Volatility_Indexes_Methodology_May2018.pdf) | 在母股票池内组合多个相互补充的因子，并同时约束风险与可投资性 | 本项目使用固定、透明的技术与流动性因子，不声称复制 MSCI 指数 |
| [Tushare 股票列表](https://tushare.pro/document/1?doc_id=25) 与 [指数成分权重](https://tushare.pro/document/2?doc_id=96) | 证券上市/退市状态和历史指数权重应作为 point-in-time 股票池证据 | 实际可用性取决于用户 Tushare Token 的积分与接口权限 |
| [Massive Stocks Plans](https://massive.com/pricing?product=stocks) 与 [Custom Bars](https://massive.com/docs/rest/stocks/aggregates/custom-bars) | Basic 当前为 5 次/分钟、2 年历史；Starter 为不限 API 调用、5 年历史，复权参数由官方聚合接口提供 | 平台按低额度默认节流，不把套餐历史上限伪装成用户请求的完整区间 |

## 3. 历史时点股票池

### 3.1 A 股专业模式

`tushare_index` 当前支持：

- 沪深 300：`000300.SH`；
- 中证 500：`000905.SH`；
- 中证 1000：`000852.SH`。

服务逐月读取 Tushare `index_weight`，按该月实际返回权重排序，保留每期 8–24 只成分。每个信号日只使用不晚于该日的最近一份快照。以下条件全部满足时，股票池才标记为历史时点已验证：

- 至少 12 份有效历史快照；
- 请求月份没有静默失败；
- 相邻快照最大间隔不超过 70 天；
- 成分代码、权重和生效日期完整。

若 Token 未配置、权限不足、月份返回为空或历史快照断裂，运行明确失败或降级，绝不把当前成分名单伪装成历史成分。

### 3.2 A/H/美股冻结研究池

`frozen_symbols` 允许用户冻结 6–40 只 A 股、港股或美股，用于验证因子、组合和执行链路。即使用户声明它是历史名单，系统仍保持：

```text
point_in_time_verified = false
promotion_status = research_only
```

原因是客户端声明不能替代有来源、可复算的历史成员序列。该模式可用于研究，但不能通过幸存者偏差门禁。

## 4. 因子与组合构建

每个调仓信号日只读取该日及此前数据。当前固定四个横截面因子：

1. `momentum`：中期动量，并跳过最近 21 个交易日，降低短期反转与信号/成交重叠；
2. `trend_quality`：对数价格趋势斜率乘拟合优度，避免只看起终点收益；
3. `low_volatility`：历史日收益波动率的反向排名；
4. `liquidity`：近期平均成交额排名。

每个因子只在当期可算股票之间转为百分位分数，综合分为运行开始前冻结的固定权重加权：

```text
composite_score =
  w_momentum × rank(momentum)
  + w_trend × rank(trend_quality)
  + w_low_vol × rank(low_volatility)
  + w_liquidity × rank(liquidity)
```

系统不会在样本外结果出来后自动搜索或替换权重。入选股票继续经过最低价格、最低平均成交额、最大陈旧天数、最低综合分、最多持仓、单股上限和最低现金约束。组合可选等权、逆波动或“综合分 × 逆波动”，未分配部分保留现金。

## 5. 信号、价格和防前视

同一证券保留两套价格证据：

- 复权日线：只用于因子和收益连续性；
- 未复权开盘与成交额：用于订单价格、容量和成本。

服务把未复权开盘映射到复权信号坐标，同时保留原始成交额作为容量事实。调仓流程固定为：

冻结自定义名单天然不能通过历史成员门禁，因此不会为了一个无法升级的实验重复消耗第二份未复权专业接口额度。此时模拟器明确使用复权价研究回退，并分别披露专业复权覆盖、独立未复权覆盖和专业双价格覆盖；独立未复权覆盖不足仍会阻止策略升级。Tushare 历史指数模式具备升级可能，继续强制读取复权/未复权两套数据。

```text
交易日 t 收盘后：
  使用 universe_snapshot <= t
  使用 price_date <= t
  计算排名与目标权重

后续第一个存在有效开盘和成交额的交易日：
  先卖后买
  按真实容量和现金撮合
```

订单不得在信号日成交，也不得使用信号日之后的数据生成同一信号。单元测试会在未来价格被极端修改后复算此前排名，要求结果完全不变。

## 6. 事件驱动模拟撮合

每张目标订单进入逐日账本，记录目标金额、剩余金额、年龄、成交和拒单原因。

单日最大可成交金额：

```text
capacity_notional =
  raw_turnover × max_volume_participation_pct / 100
```

价格冲击使用参与率的二次函数：

```text
participation = fill_notional / capacity_notional
impact = impact_bps × participation²
```

有效成交价格还包含基础滑点；买卖均扣佣金，卖出额外扣市场税费。容量不足时只做部分成交，剩余订单在后续交易日继续；超过 `max_order_age_sessions` 后过期。开盘、成交额缺失或为零时拒绝当日成交。结果完整保存：

- 订单数、成交数和部分成交数；
- 成交金额、买卖金额和累计换手；
- 佣金、滑点、冲击与卖出税；
- 未成交申请比例、容量利用率；
- 零量/停牌拒单和订单过期；
- 最近成交明细以及完整后端账本。

成本翻倍压力测试会同时把佣金、基础滑点、容量冲击和卖出税乘 2，重新运行整条撮合链，不是简单从最终收益减一个常数。

## 7. 严格样本外与因子诊断

### 7.1 非重叠样本外窗口

完整成本后日度组合曲线按用户冻结的 126/252 个交易日切成非重叠窗口。每段独立展示：

- 策略收益；
- 同市场基准收益；
- 净超额；
- 最大回撤；
- 是否跑赢。

门禁至少要求 4 个窗口，且跑赢比例不低于 60%。系统不挑选表现最好的一段，也不删除亏损窗口。

### 7.2 Rank IC

每个信号日把横截面综合排名与之后一个调仓周期的实际收益做 Spearman Rank IC，展示均值、正值占比、观察数和 IC 信息比。纸面门禁要求至少 6 次有效观察且平均 Rank IC 为正。

Rank IC 衡量排序方向的一致性，不是单只股票上涨概率，也不能单独放行策略。

## 8. 10 项前向纸面门禁

全部条件同时通过才得到 `paper_ready`：

1. 历史时点股票池已由专业来源验证；
2. 每期股票池至少 8 只；
3. 候选股票复权/未复权专业行情双源覆盖 100%；
4. 至少 4 个非重叠样本外窗口；
5. 样本外跑赢窗口占比至少 60%；
6. 全期成本后基准超额为正；
7. 成本翻倍后基准超额仍为正；
8. 最大回撤不超过用户预算；
9. 未成交比例不超过 5%，且不存在陈旧持仓日；
10. Rank IC 观察和均值通过。

任何一项失败都固定为 `research_only`。通过后用户还必须明确勾选纸面边界，并提交当前 Result SHA-256，才能冻结不可变 shadow mandate。它只保存最新研究目标、规则、来源和结果摘要：

```json
{
  "execution_authorized": false,
  "broker_connected": false,
  "quantity_generated": false
}
```

## 9. 数据模型、隔离与完整性

迁移 `quant-selection-lab.v1` 新增：

| 表 | 作用 | 不可变规则 |
| --- | --- | --- |
| `quant_selection_runs` | 冻结政策、股票池输入、作业状态和完成结果 | 输入创建后不可改；完成结果不可重写 |
| `quant_selection_run_events` | 创建、排队、运行和终态事件 | 前序哈希链；拒绝 UPDATE/DELETE |
| `quant_selection_shadow_mandates` | 通过门禁后的前向纸面快照 | Result 内容寻址幂等；拒绝 UPDATE/DELETE |

所有读取使用 `tenant_id + user_id` 隔离。Run 输入、完成结果和每个事件分别计算 SHA-256；详情读取重新计算哈希链。列表只返回轻量摘要，不加载完整日度曲线和成交账本。

生产 PostgreSQL 缺少任一表时：

```text
quant_selection_schema = false
full_service_ready = false
```

## 10. 异步与高可用

生产创建运行的顺序：

1. API 在 PostgreSQL 冻结 Run 和输入 SHA；
2. 创建 `quant_selection_run` 持久作业；
3. Redis/Celery 消息只传 Job ID；
4. `market-data` Worker 领取租约并写运行事件；
5. Worker 读取历史股票池与真实行情，执行组合和撮合；
6. 结果、Result SHA 与终态事件写回 PostgreSQL；
7. 前端轮询用户自己的 Run。

任务软/硬时限为 1800/1860 秒，Worker 租约为 2100 秒。API 副本不直接执行生产行情抓取；Redis 短时不可用时，冻结输入和作业仍保留在 PostgreSQL，由现有恢复调度重新派发。

Massive/Alpha Vantage 等低配套餐不能承受候选池并发突发。`provider_transport.py` 在同一主机的线程和进程之间串行认领供应商请求槽位，默认 Massive 最短间隔 12.5 秒；收到 `429` 时读取 `Retry-After` 并在上限内重试。Massive API Key 会从旧的 Query 参数移到官方支持的 Bearer Header，避免反向代理访问日志和 HTTP 异常 URL 泄露凭据。量化选股还会先获取基准，再并发提交候选；基准失败立即给出真实失败，不会生成无基准的相对收益。付费套餐可按实际合同额度调整：

```text
MASSIVE_MIN_REQUEST_INTERVAL_SECONDS
MASSIVE_RATE_LIMIT_MAX_ATTEMPTS
MASSIVE_MAX_RETRY_AFTER_SECONDS
ALPHAVANTAGE_MIN_REQUEST_INTERVAL_SECONDS
ALPHAVANTAGE_RATE_LIMIT_MAX_ATTEMPTS
ALPHAVANTAGE_MAX_RETRY_AFTER_SECONDS
```

## 11. API

新增 7 个受认证操作：

| 方法 | 路径 | 作用 |
| --- | --- | --- |
| GET | `/api/v1/quant-selection/overview` | 预设、边界、最近运行和纸面快照 |
| POST | `/api/v1/quant-selection/runs` | 冻结参数并创建持久实验 |
| GET | `/api/v1/quant-selection/runs` | 轻量运行历史 |
| GET | `/api/v1/quant-selection/runs/{run_id}` | 完整结果、哈希和事件链 |
| POST | `/api/v1/quant-selection/runs/{run_id}/shadow-mandates` | 校验 Result SHA 并冻结前向纸面快照 |
| GET | `/api/v1/quant-selection/shadow-mandates` | 用户自己的纸面快照历史 |
| GET | `/api/v1/quant-selection/shadow-mandates/{mandate_id}` | 纸面快照详情与完整性 |

请求模型使用 `extra="forbid"`；未知参数、错误市场/股票池组合、无权限来源、队列不可用、哈希冲突和跨用户读取都返回明确状态，不回退模拟数据。

## 12. 前端工作台

入口：`研究中心 → 量化选股`。

页面按研究顺序展示：

1. A 股历史指数成分、美股冻结池、港股冻结池三个预设；
2. 股票池、历史区间、因子、调仓和组合约束；
3. 成交、容量、税费、样本外与回撤高级参数；
4. 可恢复运行历史；
5. 成本后策略/基准曲线；
6. 最新目标篮子和逐因子排名；
7. 非重叠样本外窗口与 Rank IC；
8. 成交容量、交易摩擦和成本翻倍压力；
9. 10 项纸面门禁；
10. 股票池、来源、Result/Input/Event 哈希和限制。

浏览器验收发现并修复两项实际问题：

- 后端预设包含的 `schema_version`、`policy_version` 和空 `index_code` 曾被前端原样合并进严格请求，现改为按表单白名单重建政策；
- FastAPI 结构化 `422 detail[]` 曾显示为 `[object Object]`，现会显示精确字段和错误信息。

桌面和 `390×844` 手机视口均无页面级横向溢出；宽表只在自身容器内滚动。

## 13. 本地验收

- 后端全量：`577 passed`、`13 subtests passed`；
- 新功能专项：`11 passed`；
- 配额传输、量化选股与专业榜路由联合专项：`28 passed`；
- 前端生产构建：`1857 modules transformed`；
- 生产依赖审计：`0 vulnerabilities`；
- OpenAPI：`175` 条路径、`204` 个操作；本功能新增 `6` 条路径、`7` 个操作；
- 真实浏览器从 UI 完成 6 只美股、36 个月历史实验；
- 实测结果包含 641 个组合交易日、119 笔成交、5 个非重叠样本外窗口、30 次 Rank IC 观察；
- 本地公开降级行情专业双源覆盖为 0%，系统正确保持 `research_only`，没有误放行为纸面策略；
- `390×844` 视口 `scrollWidth == clientWidth`，无全局横向溢出；
- 浏览器应用控制台告警/错误：`0`；
- 未连接券商、未生成股数、未冻结未过门槛策略，也未产生真实交易。

全量测试只有既有 FastAPI `on_event` 弃用提示和 Pillow 超大图保护提示，没有失败。

## 14. 已知限制与下一步

- Tushare 历史指数成分模式依赖 Token 积分和 `index_weight` 权限；
- Massive Basic/低配额度下，大股票池首次冷启动会按额度排队而不是突发请求；这是正确限流，后台进度会持续保留，付费套餐应按合同额度调整最短间隔；
- 目前没有港股和美股的专业历史指数成员序列，冻结名单只能研究；
- 因子仍是价格/波动/流动性因子，尚未接入 point-in-time 财务报表、分析师预期和公司行动完整账本；
- 日线撮合没有订单簿、逐笔队列、真实涨跌停封单、券商拒单、港股整手和税务批次；
- Rank IC、历史超额和压力测试都不能证明未来继续有效；
- shadow mandate 仍是内部前向观察对象，不是交易许可。

该阶段已经由[量化策略无前视前向验证与资金委员会桥](2026-07-25-002-quant-selection-forward-validation.md)完成：通过门禁的 shadow mandate 可以接入现有机会收益实验室，以接入后的下一交易日开盘为起点，积累独立的 5/20/60 交易日前向批次，再由策略投资委员会决定是否给极小人工研究预算。下一阶段应持续积累真实批次并增加 point-in-time 基本面、公司行动和真实成交对账，不能因为一次回测漂亮就扩大资金或自动下单。

## 15. 生产发布记录

### 15.1 发布与基础设施

- 功能提交：`87857cd84fa7e09b3cac18439eb75a3f4dc3737e`；
- 显式执行边界提交：`620efee911def176121d787a071a279fa6b9c6db`；
- 专业行情配额加固提交/最终运行 release：`6d3e8e4d6b978422859efd0723f8cf19cab6d497`；
- 以上提交均已推送 GitHub `main`，最终 release 已原子滚动发布到 `8001/8002` 两个 API 副本；
- 两副本均返回 `ready=true`、`full_service_ready=true`、`quant_selection_schema=true`，release 一致；
- PostgreSQL、Redis、OSS、5 类 Worker 和 Celery Beat 正常；5 个队列深度均为 0；
- 公网 Edge、首页均为 `200`，匿名量化选股接口为 `401`；
- OpenAPI 为 `175` 条路径、`204` 个操作；量化选股为 `6` 条路径、`7` 个操作；
- 数据库为 `76` 张表、`13` 个迁移标记，3 个量化选股不可变触发器在线；
- 云端发布包专项 `45 tests` 通过。

发布器第一次以临时 systemd 单元运行时在任何构建和切流前被 Git `safe.directory` 拒绝，线上旧 release 未受影响。为 root 配置精确的 `/opt/stock-assistant` 安全目录后重新发布成功，没有扩大到通配目录。

### 15.2 真实供应商与端到端任务

首次生产冷任务揭示 Massive Basic 配额会被旧的 6 路突发请求打出 `429`，基准 SPY 因而失败。加固后用两个隔离普通账户验证同一 8 只美股冻结池：账户 A 创建任务，账户 B 验证越权读取被拒绝。

| 验收项 | 生产结果 |
| --- | --- |
| 冷启动 / Worker 缓存复跑 | 约 107 秒 / 约 6 秒 |
| 候选加载 | 8/8；每只 501 根 Massive/Polygon 复权日线 |
| 专业复权 / 独立未复权 / 专业双价格覆盖 | 100% / 0% / 0% |
| 组合交易日 / 撮合 | 375 日 / 86 笔 |
| 非重叠样本外窗口 / Rank IC | 3 段 / 17 次观察 |
| 结果与事件链 | Input、Result、3 个 Event 全部校验通过 |
| 跨用户读取 | `404` |
| 未过门禁冻结 shadow mandate | `409` |
| 数据库篡改 | policy、完成 result、event delete 共 3 类全部拒绝 |
| 自动交易边界 | `execution_authorized=false`、`broker_connected=false`、`quantity_generated=false` |
| 账户清理 | 2 个临时账户全部停用，活跃会话 0，认证审计链 111 个事件通过 |

终态是预期的 `partial + research_only`，不是行情失败：`fetch_failures=[]`，`partial` 来自冻结当前名单的幸存者偏差警告。现有生产 Key 返回约 2 年/501 根日线，与 Massive 当前 Basic 的官方边界一致；扣除 126 日因子回看后只能形成 3 个 126 日窗口，未达到 4 窗口门禁。系统没有降低门槛或把不足历史包装成通过。若要让美股模式具备达到该门禁的基础，当前官方 Starter 为 5 年历史、Unlimited API Calls；这仍不保证 Rank IC、超额、回撤和容量等其余门禁通过。

生产 Tushare Token 已配置，但实际 `index_weight` 权限探测返回无访问权限。因此 A 股历史指数成分模式目前会明确失败；用户需要在 Tushare 账户开通该接口权限后再复验，不能用当前成分替代。

### 15.3 备份与恢复

发布前私有 OSS AES256 备份：

```text
object: backups/postgresql/2026/07/stock-assistant-iZn4ai1fm0tr284w21h4kmZ-20260725T132448Z.dump
bytes: 2144590
sha256: 53bdafece45298c6d79ec0966db834770ea9447e0b02d1a136c01566db9d8af8
restore: 73 tables / 12 migrations
```

发布后私有 OSS AES256 备份：

```text
object: backups/postgresql/2026/07/stock-assistant-iZn4ai1fm0tr284w21h4kmZ-20260725T141827Z.dump
bytes: 2240591
sha256: 0db20f4b88ce293208b4c948d331a7dc13f825a062c0d21ce866e6276ba4bf80
restore: 76 tables / 13 migrations
```

两份备份均完成 SHA-256 校验；发布后备份已真实恢复到隔离数据库并核对表数与迁移标记。
