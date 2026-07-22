# 2026-07-22 更新 003：跨市场机会工厂

## 1. 这次不是再加一个评分卡

此前股票研究已经能回答单只股票的技术、基本面、相对基准和历史回测问题，但用户仍需要自己在热门榜、自选、个股详情和回测之间来回切换，也没有一条可复现的链路回答：

- 本次到底检查了哪些股票，是否遗漏或悄悄换了候选范围；
- 哪些股票因真实数据、风险或基本面门槛被淘汰；
- 综合分来自哪些可见因子，缺失数据如何处理；
- 多只入围股票放在一起后，相关性、集中度和现金是否可接受；
- 冻结当时的候选后，未来真实表现是否支持这套方法，而不是只看回测。

本轮新增第六个顶级工作区“机会工厂”，把这些环节组织为一条完整流水线：

```text
版本化策略
  -> 明确候选池（非交易所全量）
  -> 真实数据与硬门槛
  -> 同市场五因子横向分位
  -> 入围 / 观察 / 淘汰 / 数据失败
  -> 候选池市场状态
  -> 相关性、仓位和现金约束
  -> 冻结纸面组合
  -> 追加式真实收盘观察
  -> 与同策略上一期比较
```

该功能用于提高研究覆盖、规则一致性和复盘质量，不预测“必涨股票”，不连接券商，不提交订单，也不保证用户盈利。

## 2. 专业产品参考与本项目取舍

本轮先研究了专业产品的产品结构，再按本项目的真实数据与审计要求落地：

| 参考产品 | 官方能力 | 本项目采用的思想 | 没有照搬的部分 |
| --- | --- | --- | --- |
| TradingView Stock Screener | 多市场筛选、可配置过滤器、保存筛选器和结果导出 | 把候选来源、门槛和组合约束保存成策略；提供多个起始模板 | 当前没有授权的全交易所历史成分库，因此不声称全市场覆盖，也暂不提供导出后绕过证据门禁的交易入口 |
| Seeking Alpha Quant Ratings | 使用大量指标形成 Value、Growth、Profitability、Momentum、EPS Revisions 等因子等级，并与板块同类比较 | 使用多因子而不是单一技术分；展示每个因子的组成指标、相对分位和证据覆盖 | 当前只在同一市场、本次候选池内比较，不冒充行业中性或完整板块同类分位；没有 EPS revisions 专业数据就不估算 |
| Koyfin | 把 idea discovery、watchlist、dashboard、portfolio 和 performance analysis 放在连续工作流中 | 从候选发现一直推进到组合约束和冻结后的表现跟踪 | 当前纸面组合不是用户真实账户，不读券商订单或现金余额，也不将纸面结果包装为真实 P&L |
| TipRanks Smart Score | 聚合多个市场维度形成统一分数，并延伸到组合分析 | 提供综合分用于排序，同时保留多维构成 | 本项目不只给一个黑箱总分；数据、风险和基本面硬门槛可以直接否决高总分 |
| TradingView AI Filter | 把自然语言转换成现有筛选条件 | 后续可在不改变确定性规则的前提下增加“策略解释器” | 本轮不让大模型生成隐藏条件；先把所有参数做成可见、可版本化、可校验的表单 |

参考来源均为产品官方页面：

- [TradingView Stock Screener overview](https://www.tradingview.com/support/solutions/43000718866-tradingview-stock-screener-trade-smarter-not-harder/)
- [TradingView Screeners walkthrough](https://www.tradingview.com/support/solutions/43000718885-tradingview-screeners-walkthrough/)
- [TradingView AI Filter](https://my.tradingview.com/support/solutions/43000785770/)
- [Seeking Alpha Quant Ratings and Factor Grades FAQ](https://seekingalpha.com/article/4263303-quant-ratings-and-factor-grades-faq)
- [Seeking Alpha exclusive stock ratings](https://help.seekingalpha.com/what-exclusive-stock-ratings-does-seeking-alpha-offer)
- [Koyfin portfolio tools](https://www.koyfin.com/features/portfolio-tools/)
- [Koyfin features](https://www.koyfin.com/features/)
- [TipRanks enterprise data and Smart Score](https://enterprise.tipranks.com/)

这些参考只用于产品结构。因子公式、门槛、状态机、组合约束和证据链均由本项目确定性实现，并受现有数据许可范围限制。

## 3. 不可变策略

每个策略版本冻结以下内容：

- 模板 ID、名称、说明和 9-60 个月历史窗口；
- A 股、港股、美股中的一个或多个市场；
- 内置种子池、用户自选、手工代码及可用热门榜来源；
- 趋势动量、估值、盈利质量、成长、风险韧性五类因子权重；
- 历史长度、数据新鲜度、技术分、三月收益、波动、回撤、基本面、因子覆盖和综合分门槛；
- 最大股票数、单股上限、最低现金、两两相关上限、防守状态现金增量和权重方法。

修改策略不会更新原版本，而是追加新的 `opportunity_strategy_versions` 记录。每次 Run 保存 `strategy_version_id`、版本号和定义 SHA-256，历史扫描始终能解释“当时使用了什么规则”。

内置提供四个起点：

1. 跨市场均衡雷达；
2. 质量趋势共振；
3. 活跃强势验证；
4. 低波动防守池。

模板是可见参数的起点，不是承诺有效的秘密策略。

## 4. 候选池边界

候选可从以下来源合并并按 `市场 + 代码` 去重：

- `preset`：A/H/美股共享的人工维护种子池；
- `watchlist`：当前登录用户自选；
- `manual`：用户逐行输入的市场、代码和名称；
- `hot:active`、`hot:gainers`、`hot:losers`：真实来源可用时的成交活跃、涨幅和跌幅榜。

单次最多 80 只，任何截断和来源失败都会进入结果警告。输出固定包含：

```json
{
  "scope": "candidate_pool",
  "licensed_full_market": false,
  "source_counts": {},
  "truncated_count": 0,
  "warnings": []
}
```

因此页面只使用“候选池（非交易所全量）”这一口径。没有授权的交易所全量历史成分股、退市证券和专业财务库时，不能消除幸存者偏差，也不能声称找到了全市场最优股票。

## 5. 真实数据门禁

每只候选先读取真实历史行情和基本面，并记录：

- 实际行情源、抓取时间、首末交易日、历史交易日数和最后收盘价；
- 基本面是否可用、财务截止日、供应商评分和错误；
- 1/3/6 月收益、技术分、年化/下行波动、最大回撤；
- PE、PB 及历史估值分位；
- ROE、净利率、资产负债率、现金流质量；
- 营收/利润增速和连续增长年数。

真实行情抓取失败时状态直接为 `unavailable`。系统不会用模拟 K 线、旧页面示例或另一个市场的字段补齐。港股专业基本面当前覆盖不足；若策略要求基本面必须可用，港股候选会带 `fundamentals_required` 明确淘汰，而不是估算。

硬门槛按股票逐项保留：

| 代码 | 含义 |
| --- | --- |
| `history_too_short` | 有效历史少于策略最小交易日 |
| `data_stale` | 最后行情超过允许陈旧天数 |
| `technical_below_gate` | 技术评分未过线 |
| `momentum_below_gate` | 三月趋势未过线 |
| `volatility_above_gate` | 年化波动超过上限 |
| `drawdown_above_gate` | 历史最大回撤超过上限 |
| `fundamentals_required` | 策略要求基本面但真实证据缺失 |
| `factor_coverage_below_gate` | 可用因子的加权覆盖不足 |
| `composite_below_gate` | 硬门槛已通过但综合分不足，只进入观察 |

前八类属于硬淘汰；综合分不足属于 `watch`，与数据失败和风险淘汰分开展示。

## 6. 同市场五因子透明评分

各指标只与同一市场、本次有效候选比较，采用处理并列值的横向百分位：

| 因子 | 组成指标 |
| --- | --- |
| 趋势动量 | 近 1/3/6 月收益、技术评分 |
| 估值 | PE、PB、PE 历史分位、PB 历史分位 |
| 盈利质量 | ROE、净利率、资产负债率、现金流质量 |
| 成长 | 营收同比、净利润同比、营收/利润连续增长年数 |
| 风险韧性 | 年化波动、下行波动、最大回撤 |

低估值、低负债、低波动和低回撤按“越低越好”排序，其余按“越高越好”。负 PE 或负 PB 不会因数值较小变成高估值分，而是保留真实值并排在正倍数之后。

若某因子完全缺失：

1. 综合分中固定使用中性 50 分；
2. 不把该权重分配给其他因子；
3. 同时降低加权 `factor_coverage`；
4. 覆盖率低于策略门槛时硬淘汰。

因此高分不能掩盖数据缺口，缺失数据也不会让剩余因子被动放大。

## 7. 候选漏斗和状态

运行结果把股票划分为四类：

- `qualified`：数据、全部硬门槛和最低综合分均通过；
- `watch`：硬门槛通过，但综合分尚未过线；
- `rejected`：至少一个数据/风险/基本面硬门槛失败；
- `unavailable`：真实市场数据无法读取或分析异常。

漏斗依次展示候选池数量、数据可用、硬门槛后、综合入围和最终组合。每只股票显示总排名、五因子、覆盖率、优势、关注点和全部门槛实际值/阈值。第二次运行同一策略后，还会基于已校验的上一期冻结结果展示新入围、退出和排名变化。

## 8. 候选池市场状态

系统按每个市场当前候选池的三月收益中位数和正收益宽度分类：

- 中位收益大于 5% 且正收益宽度至少 60%：`risk_on`；
- 中位收益低于 -5% 或正收益宽度低于 40%：`defensive`；
- 其余：`mixed`；
- 少于 2 个有效样本：`insufficient`。

该状态只描述本次候选池，不代表全市场牛熊。页面和 API 都固定披露这一限制。

## 9. 约束后纸面组合

组合只读取 `qualified` 股票，按排名逐只检查两两历史相关性。至少 40 个共同交易日才计算相关系数；超过策略上限的候选会进入 `correlation_exclusions`，不会被其他高分抵消。

入选后支持三种确定性原始权重：

- `score_inverse_vol`：综合分除以历史年化波动；
- `inverse_vol`：历史波动倒数；
- `equal`：等权。

随后执行：

1. 最大持仓数；
2. 单股仓位上限；
3. 最低现金比例；
4. 多数已选市场处于 `defensive` 时增加现金；
5. 无法在单股上限内投出的金额继续保留现金。

若至少两只股票具有 40 个以上共同收益日，系统使用样本协方差矩阵估算组合历史年化波动。该值是历史风险描述，不是未来波动上限。

跨市场组合暂不换算汇率，未纳入佣金、税费、整手、涨跌停、停牌、融资、市场冲击和真实成交偏差，因此只作为继续观察的权重实验。

## 10. 冻结后的前瞻纸面跟踪

用户可把一次通过约束的组合冻结为纸面批次。快照保存策略、Run、股票、权重、基准日期/价格、行情源、现金和限制，并计算 `snapshot_sha256`。同一用户同一 Run 重复启动保持幂等。

每次“更新真实收盘表现”重新读取各股票真实复权收盘价，追加一个不可变观察点：

- 股票本币收益 = 当前真实收盘 / 冻结基准收盘 - 1；
- 组合贡献 = 冻结权重 × 本币收益；
- 行情失败股票保持失败，权重不重新分配；
- 现金收益固定按 0；
- 每个观察点绑定载荷 SHA-256、前序事件哈希和事件哈希。

观察只发生在冻结之后，不回填冻结前历史表现，也不更新基准。它用于逐步积累真正的前瞻证据，而不是把历史回测再次包装成模拟盘。

## 11. 持久化、任务与恢复

新增 6 张用户级 PostgreSQL 表：

1. `opportunity_strategies`；
2. `opportunity_strategy_versions`；
3. `opportunity_runs`；
4. `opportunity_run_events`；
5. `opportunity_paper_baskets`；
6. `opportunity_paper_observations`。

策略版本、运行事件、纸面组合和观察点由数据库触发器禁止 UPDATE/DELETE；Run 的结果一旦写入也禁止替换。每条读取链路都会重新校验定义、结果、快照或事件哈希。

本地开发使用 SQLite 并在首次访问时安装等价表和触发器。生产必须先执行 `opportunity-factory.v1` PostgreSQL 增量迁移；应用不会在启动时自动建表，缺少任一机会表时 readiness 返回失败。

生产扫描进入专用 Celery 任务 `stock_assistant.market.execute_opportunity_scan`，仍路由到 `market-data` 队列，软/硬时限分别为 870/900 秒。完整策略与结果留在 PostgreSQL，队列只传 Run ID。Redis、Worker 或数据库不可用时明确失败，不在 API 进程偷偷回退抓取。

## 12. API

机会工厂位于 `/api/v1/opportunities`，共 11 条路径、14 个操作：

| 方法与路径 | 作用 |
| --- | --- |
| `GET /templates` | 读取版本化起始模板和范围声明 |
| `GET /overview` | 一次读取用户策略、最近运行、纸面组合和摘要 |
| `GET/POST /strategies` | 列出或创建策略 |
| `GET/DELETE /strategies/{strategy_id}` | 读取或归档策略 |
| `POST /strategies/{strategy_id}/versions` | 追加不可变新版本 |
| `GET/POST /runs` | 列出或启动扫描 |
| `GET /runs/{run_id}` | 获取进度、终态结果、哈希验证和事件链 |
| `POST /runs/{run_id}/paper-baskets` | 从冻结 Run 幂等创建纸面组合 |
| `GET /paper-baskets` | 列出用户纸面组合 |
| `GET /paper-baskets/{basket_id}` | 读取快照、观察历史和完整性状态 |
| `POST /paper-baskets/{basket_id}/observations` | 使用真实行情追加观察点 |

所有用户数据只从服务端会话的 `subject_id` 取所有者，不接受客户端传入 `user_id`。普通用户按资源 ID 读取时仍同时校验所有权。

## 13. 前端工作区

`OpportunityTab` 提供两个完整子工作区：

### 策略与扫描

- 左侧策略库、版本和运行历史；
- 四段策略编辑器：研究方法、候选池、因子/门槛、组合约束；
- 运行进度、不可变结果哈希和候选漏斗；
- 可筛选候选表、股票因子详情和实际淘汰原因；
- 候选池市场状态、上期变化和组合实验室；
- 方法、证据边界和限制说明。

### 纸面跟踪

- 冻结纸面组合列表；
- 组合本币近似收益、覆盖权重、现金和最新观察；
- 每只股票的基准/观察日期、价格、收益、贡献和真实行情源；
- 不可变观察历史与跨市场/成本限制。

桌面采用策略侧栏 + 主结果区，手机改为单列卡片；表格只在局部容器滚动，顶级导航在窄屏允许自身横向滚动。

## 14. 本地验证结果

- 后端全量回归：`437 passed`，另有 4 个 unittest subtests 通过；
- 机会工厂专项及路由/任务协议：17 项通过；
- 前端 Vite 生产构建通过，机会工厂异步 chunk 约 38.16 kB，gzip 约 12.80 kB；
- `git diff --check` 通过；
- Chrome 桌面端与 `390×844` 手机端无页面横向溢出，控制台无 error/warning。

真实数据验收：

1. 默认严格门槛扫描 `600519`、`000858`、`600036`，全部由 BaoStock 返回截至 2026-07-21 的历史数据；结果为 2 只 `watch`、1 只 `rejected`、0 只 `qualified`，组合保持 100% 现金。候选池状态为防守，三月收益中位数 -4.85%，上涨宽度 0%。
2. 为验证完整组合链路，另建只用于验收的放宽门槛策略；3 只均入围并形成 3 只纸面组合，快照哈希和第 1 个观察点哈希均校验通过，观察日与冻结日相同所以收益为 0。
3. 本地跨市场冒烟中，港股 `00700` 和美股 `AAPL` 均由 Yahoo Finance 返回截至 2026-07-21 的真实历史行情；在放宽门槛下入围并形成 2 只组合。两者因专业基本面缺失只有 50% 因子覆盖，缺口没有被隐藏。

严格场景最终不生成组合，证明系统在没有足够证据时会保留现金，而不是为了看起来“有用”强行推荐股票。

## 15. 生产迁移与发布

首次部署必须先运行 PostgreSQL/OSS 备份，再执行：

```bash
sudo bash -lc '
  set -a
  source /etc/stock-assistant/stock-assistant.env
  set +a
  cd /opt/stock-assistant/backend
  /opt/stock-assistant/venv/bin/python -m migrations.opportunity_factory_v1
'
```

迁移使用 PostgreSQL advisory transaction lock，在单个事务内创建 6 张表、索引、不可变触发器和 `opportunity-factory.v1` 迁移标记。迁移后必须同时验证：

- `/health/ready` 返回数据库、Redis、OSS、全部 Worker 和 `opportunity_schema` 正常；
- market-data Worker 注册 `stock_assistant.market.execute_opportunity_scan`；
- 前端静态资源包含机会工厂 chunk；
- 本地、GitHub、云端提交哈希一致。

## 16. 尚未解决的边界

- 没有授权的全市场实时/历史证券主数据、退市股票和历史指数成分，因此不能消除幸存者偏差；
- 当前分位是同市场候选池相对分位，不是行业/规模/风格中性因子；
- 港股财务覆盖不足，美股云服务器访问 Yahoo Finance 可能被 403；需要接入有 SLA、许可和服务器可达性的专业供应商；
- 没有分析师盈利预测修正、机构持仓变化、期权隐含信息、逐笔流动性或另类数据；
- 候选池状态不是完整市场状态模型，没有宏观、波动率曲线或市场宽度全量数据；
- 组合没有汇率换算、交易成本、税费、整手、容量、成交冲击、停牌和涨跌停仿真；
- 纸面组合没有定时日终调度、再平衡纪律、基准指数归因和统计显著性门槛；
- 因子权重和门槛尚未做完整滚动样本外、PBO、多重检验和跨市场稳定性验证；
- 任何“入围”都只是进入进一步研究的候选，不是买入建议。

下一阶段若要继续提升实际决策价值，优先级应是：接入有授权的专业 A/H/美股全量与基本面供应商，建立历史成分股和供应商健康门禁；然后增加日终自动纸面观察、基准/行业中性评估、滚动样本外组合评测和用户真实投资政策约束。只有这些证据逐步积累后，才适合讨论小额人工模拟盘，不能直接跳到自动交易。
