# 量化策略无前视前向验证与资金委员会桥

发布日期：2026-07-25

## 1. 为什么这是一个大功能

历史时点股票池、样本外回测和事件驱动撮合只能回答“这套固定规则在已知历史上是否值得继续研究”。它们不能回答：

- 规则冻结以后，真实未知行情中的表现是否继续存在；
- 多次运行是否属于同一套规则，还是换过参数后挑出的赢家；
- 第 5/20/60 个交易日结果是否严格可复算；
- 交易成本、覆盖、回撤、普通置信区间和多策略选择偏差是否同时通过；
- 证据通过后，是否有资格进入投资委员会，而不是直接生成交易。

本次功能把量化选股实验室和已经上线的机会收益实验室、策略投资委员会、全组合资金计划连接为一条可审计主链：

```text
历史时点股票池与固定因子
  → 非重叠样本外回测 / Rank IC / 撮合门禁
  → 不可变 quant shadow mandate
  → 实际接入时间冻结
  → 下一真实交易日复权开盘
  → 独立 5/20/60 日前向批次
  → 成本、基准、回撤、置信区间与多重检验
  → 不可变收益记分卡
  → 策略投资委员会
  → 最高 3% 的人工研究上限
```

链路终点仍不是订单。所有输出继续固定：

```json
{
  "execution_authorized": false,
  "broker_connected": false,
  "quantity_generated": false
}
```

## 2. 因果时间与禁止历史补填

### 2.1 入场时间

系统分别保存：

- `signal_date`：量化实验最后一次目标权重形成日；
- `mandate_frozen_at`：原量化纸面指令成为不可变事实的时间；
- `enrolled_at`：用户把指令真正接入前向验证的时间；
- `activation_market_date`：`enrolled_at` 转换到 A/H/美股本地时区后的日期。

入场锚点为：

```text
entry_after_date = max(signal_date, activation_market_date)
```

真实入场必须是：

```text
date > entry_after_date 的第一条有效复权开盘价
```

因此，旧 mandate 今天才接入时也只能从今天之后观察，不能把已经发生的涨跌补成“前向收益”。这同时防止用户事后只挑历史上已经上涨的旧指令进入样本。

### 2.2 开盘尚未形成

如果行情中还没有严格晚于锚点的有效开盘价，位置状态为：

```text
pending_entry
```

它会在后续调度中自动重试：

- 不使用信号日收盘价；
- 不使用 mandate 冻结前价格；
- 不把等待入场算作行情失败；
- 不写入虚构的零收益。

### 2.3 策略与基准同起点

每只股票使用首个合格交易日的复权开盘价入场；同市场基准也使用同一交易日的复权开盘价。第 5/20/60 个持有交易日均从该入场会话开始按真实交易日序号精确结算，补跑只重建同一目标窗口，不会把更晚日期冒充目标日期。

## 3. 策略家族与独立批次

策略家族指纹固定包含：

```text
tenant_id
+ user_id
+ quant engine version
+ normalized quant policy SHA-256
```

结果是：

- 相同用户、相同引擎、完全相同政策的后续 mandate 进入同一策略家族；
- 每份 mandate 仍形成独立的 Opportunity Run 和纸面篮子；
- 因子、股票池、调仓、组合、成本或风险政策任一变化都会生成新家族；
- 不同用户和租户永远不能共享前向样本；
- 运行结果本身不会参与策略家族指纹，避免“按结果选家族”。

收益实验室继续按冻结起点预先选择非重叠代表批次。重叠批次保留审计，但不增加有效样本量；新策略版本也不会混入旧版本结果。

## 4. 收益、成本与精确窗口

每只股票在窗口 `h` 的收益为：

```text
r(i,h) = adjusted_close(i,h) / adjusted_open(i,entry) - 1
```

组合毛收益、基准收益和成本后超额为：

```text
gross(h) = Σ frozen_weight(i) × r(i,h)
benchmark(h) = Σ frozen_weight(i) × benchmark_return(market(i),h)
cost_drag = invested_weight × round_trip_cost_bps
net_excess(h) = gross(h) - cost_drag - benchmark(h)
```

冻结往返成本压力为：

```text
round_trip_cost_bps =
  2 × (commission_bps + slippage_bps) + sell_tax_bps
```

并限制在 10–500 bps。当前桥接政策固定：

| 项目 | 口径 |
| --- | --- |
| 观察窗口 | 5 / 20 / 60 个持有交易日 |
| 主窗口 | 20 个交易日 |
| 最低股票覆盖 | 90% |
| 最低基准覆盖 | 90% |
| 最少独立成熟批次 | 6 |
| 最低平均成本后超额 | 0.5% |
| 最低跑赢比例 | 55% |
| 最大人工试运行比例 | 3% |
| 最新篮子有效期 | 30 天 |

回撤上限继承量化实验的最大回撤预算，并限制在 3%–25%。

## 5. 资金资格与投资委员会

20 日主窗口必须同时通过：

1. 至少 6 个独立成熟批次；
2. 股票和基准覆盖均达到 90%；
3. 平均成本后基准超额达到冻结政策门槛；
4. 跑赢基准比例达到冻结政策门槛；
5. 保守成分回撤不超过预算；
6. 双侧 95% t 区间下界高于 0；
7. 把历史上测试过的策略版本数量纳入 Bonferroni 家族校正后，下界仍高于 0；
8. 最新纸面篮子仍在有效期内。

门禁状态只有：

- `collecting`：样本、覆盖或成熟度不足；
- `suspended`：收益、回撤或统计门禁失败；
- `limited_manual_pilot`：可以进入受限人工复核。

实时页面刚刚过线仍不能直接进入投资委员会。用户还必须在收益实验室冻结当前内容寻址的不可变记分卡；只有当前记分卡仍与最新证据绑定时，`committee_ready=true`。

## 6. 单事务跨域接入

一次接入在同一个数据库事务中创建或复用：

1. Opportunity Strategy；
2. Opportunity Strategy Version；
3. Opportunity Profit Policy Version；
4. 已完成的导入 Run；
5. 两个前序哈希连接的 Run Event；
6. 前向纸面篮子；
7. 量化指令与以上对象的不可变映射。

所有 ID 都由租户、用户、来源 mandate 和政策内容确定性生成。重复请求内容寻址复用，不会生成第二份批次；来源摘要变化、跨用户对象或已有对象内容冲突会整笔回滚。

## 7. 数据模型与完整性

迁移 `quant-selection-forward-validation.v1` 新增：

| 表 | 作用 | 保护 |
| --- | --- | --- |
| `quant_selection_forward_validations` | 连接 Quant mandate/run/hash 与 Opportunity strategy/run/basket/profit policy | 租户/用户隔离；mandate 唯一；basket 唯一；跨域外键；Payload SHA-256；拒绝 UPDATE/DELETE |

映射只保存来源、策略、批次、政策和因果时间，不另造一套收益表。真实观察继续写入既有 `opportunity_paper_observations`，收益记分卡继续写入既有 `opportunity_profit_scorecards`，投资委员会因此只消费一个证据模型。

生产 readiness 把新表纳入现有 `quant_selection_schema`。缺表时：

```text
quant_selection_schema = false
full_service_ready = false
```

## 8. 高可用观察

前向篮子复用现有 Opportunity 自动观察调度：

1. Celery Beat 按小时检查；
2. 未入场或未完成 60 日窗口的篮子进入候选；
3. 同一篮子至少间隔 18 小时；
4. PostgreSQL 持久作业先落库，Redis 只传 Job ID；
5. `market-data` Worker 读取真实行情；
6. 相同行情截面按入场日期、入场价格、当前日期、当前价格和目标窗口结果幂等去重。

手动刷新也创建持久任务，同一验证在同一个 UTC 小时重复点击只会复用作业。Redis 短时不可用时返回明确 `503`，不会在 API 进程里偷偷切换到另一套生产执行路径。

本地 SQLite 开发模式使用嵌入执行，便于测试；生产必须继续使用 PostgreSQL、Redis/Celery 和独立 Worker。

## 9. API

新增 3 个受认证操作：

| 方法 | 路径 | 作用 |
| --- | --- | --- |
| GET | `/api/v1/quant-selection/forward-validations` | 返回用户自己的前向映射、入场状态、5/20/60 结果、收益记分卡和委员会资格 |
| POST | `/api/v1/quant-selection/shadow-mandates/{mandate_id}/forward-validations` | 校验 Snapshot SHA-256 并原子接入 |
| POST | `/api/v1/quant-selection/forward-validations/{validation_id}/observations` | 创建或复用持久行情观察任务 |

请求体使用 `extra="forbid"`。跨用户读取返回 `404`；摘要变化、来源门禁失败或完整性失败返回 `409`；队列不可用返回 `503`。

当前 OpenAPI：

```text
178 paths
207 operations
```

## 10. 前端工作台

入口：`研究中心 → 量化选股`。

新“量化策略前向验证中枢”展示：

- 最早可入场日期以及同日成交/历史补填禁令；
- 20 日独立成熟批次进度；
- 冻结往返成本压力；
- 资金/委员会门禁；
- 5/20/60 日本批成本后超额；
- 各窗口历史平均超额和跑赢比例；
- 下一步动作；
- 自动观察边界和手动刷新。

新 mandate 冻结成功后会自动尝试接入；已有合格 mandate 可以人工接入，但入场锚点仍使用实际接入时间，不能补历史收益。任何接入失败都保留原量化结果，不会把研究失败变成交易动作。

## 11. 安全边界

- 只有量化 `paper_shadow_eligible=true` 且 Run/Mandate 哈希全部通过才能接入；
- Quant 与 Opportunity 两侧都按当前用户读取；
- 映射创建后不可改写或删除；
- 结果文本和 LLM 不参与收益、置信区间或金额计算；
- 不保存 API Key、Cookie、券商账户或订单信息；
- 不提供“必涨/必跌”标签；
- 不连接富途、券商或交易所下单接口；
- 通过门禁也只代表研究证据达到预定规则，不代表未来盈利。

## 12. 本地验收

- 后端全量：`587 passed`、`13 subtests passed`；
- 前向验证与路由专项：`16 passed`；
- 前端生产构建：`1857 modules transformed`；
- 生产依赖审计：`0 vulnerabilities`；
- OpenAPI：`178` 条路径、`207` 个操作；
- SQLite 原子接入、幂等复用、同政策多批次、改政策分家族通过；
- Snapshot 摘要冲突、跨用户读取和 UPDATE/DELETE 篡改均被拒绝；
- 延迟接入只从接入日之后开始，旧 mandate 历史收益不会被补入；
- 股票与基准同一下一交易日复权开盘、精确 5/20/60 日窗口通过；
- 等待下一开盘显示 `pending_entry`，`failed_count=0`；
- Celery 手动刷新同一小时幂等；
- 隔离数据库浏览器真实展示 5 日完成、20/60 日待成熟、36 bps 成本和 0/6 批次；
- 页面无全局横向溢出，应用控制台错误为 0。

全量测试只有既有 FastAPI `on_event` 弃用提示和 Pillow 超大图保护提示，没有失败。

## 13. 已知限制与下一步

- 这是日线级前向验证，不模拟盘中订单簿、集合竞价队列、整手、涨跌停、停牌和真实冲击；
- 使用复权开盘衡量策略收益，与真实券商可成交价仍有差异；
- 跨市场组合尚未计入持有期汇率变化；
- A 股历史指数模式仍依赖 Tushare `index_weight` 权限；
- 美股长期样本仍受当前 Massive 套餐历史长度与额度约束；
- 6 个批次、置信区间和多重校正降低误判风险，但不能证明未来盈利；
- 目前接入由用户冻结 mandate 触发，后续应增加按固定日历自动生成候选批次，进一步减少人为选择时点；
- 下一阶段应接入 point-in-time 财务报表、分析师预期与公司行动账本，并把通过委员会的小额人工执行继续绑定真实成交对账，而不是增加自动下单权限。

## 14. 生产发布记录

### 14.1 提交、迁移与双副本发布

- 功能提交：`28f63c30a131023dcc3bde5a09ec5502998a360d`；
- OSS 备份脚本工作目录加固：`67503cb33158205eedf133de2b4e6910c759b578`；
- 两个提交均已推送 GitHub `main`；
- `quant-selection-forward-validation.v1` 已在 PostgreSQL 事务和 advisory lock 下应用；
- 数据库由 `76` 张表、`13` 个迁移标记升级为 `77` 张表、`14` 个迁移标记；
- 新表在线 `1` 个 PostgreSQL 不可变触发器、`7` 条跨域外键；
- release `28f63c3` 已由原子发布器逐个排空并切换 `8001/8002` 两个 API 副本；
- 两副本均返回 `ready=true`、`traffic_ready=true`、`full_service_ready=true`、`quant_selection_schema=true`，且 release ID 完全一致；
- 静态站点链接指向同一 release 的 `frontend/dist`；
- Nginx、PostgreSQL、Redis、双 API、5 类 Worker 和 Celery Beat 全部 `active`；
- 5 条队列均为 0，systemd 失败单元为 0，发布后应用/Nginx error 级日志为 0；
- Nginx 配置检查通过，Edge `/health/ready` 与首页均为 `200`。

### 14.2 API、认证和运行 release

生产 OpenAPI 实测为：

```text
178 paths
207 operations
```

三个新增端点的方法均与契约一致。匿名前向列表返回 `401`。

一个隔离普通账户完成：

| 验收项 | 结果 |
| --- | --- |
| 登录后读取前向总览 | `200`，`quant_selection_forward_overview.v1` |
| 初始前向验证数量 | `0` |
| 不存在的 mandate 接入 | `404` |
| 不存在的 validation 观察 | `404` |
| 登出 | `200` |
| 账户清理 | 已停用，活跃会话 `0` |
| 认证审计 | `115` 个事件，前序哈希链通过 |

该账户没有创建量化 Run、mandate 或前向业务记录。生产 `quant_selection_forward_validations` 仍为 `0`，因此没有把合成测试收益混入真实用户决策。

运行 release 自身使用生产依赖直接执行 `9` 项前向功能 unittest，全部通过。完整路由测试脚本默认创建 SQLite，而 release 目录按发布器设计为只读，因此没有在 release 内强行写测试数据库；三条新路由改为直接从两个生产 OpenAPI 契约和真实 HTTP 状态验证。

### 14.3 隔离 PostgreSQL 成功路径

为验证不只在 SQLite 可用，发布前 OSS 备份被恢复到一次性隔离 PostgreSQL 数据库，然后应用第 14 个迁移并运行完整成功路径：

| 验收项 | 隔离 PostgreSQL 结果 |
| --- | --- |
| 原子接入 | 成功 |
| 相同 mandate 重试 | 内容寻址幂等复用 |
| 延迟接入锚点 | `2026-08-17` |
| 第一可用开盘 | `2026-08-18`，严格晚于锚点 |
| 5/20/60 日结果 | 60 日最大窗口已精确完成 |
| 20 日独立成熟批次 | `1` |
| 资金门禁 | `collecting`，没有因单批漂亮结果提前放行 |
| UPDATE 篡改 | PostgreSQL 触发器拒绝 |
| Schema | `77` 张表、`14` 个迁移标记 |

测试结束后隔离数据库已强制销毁；最终审计确认恢复演练库残留数量为 `0`。

### 14.4 备份、恢复与运维加固

发布前私有 OSS AES256 备份：

```text
object: backups/postgresql/2026/07/stock-assistant-iZn4ai1fm0tr284w21h4kmZ-20260725T155125Z.dump
bytes: 2256212
sha256: 2b864809911254513440685b44ab79961a00046466611250a64dae717acb5382
restore: 76 tables / 13 migrations
```

最终发布后私有 OSS AES256 备份：

```text
object: backups/postgresql/2026/07/stock-assistant-iZn4ai1fm0tr284w21h4kmZ-20260725T160530Z.dump
bytes: 2267209
sha256: d02f376f9d9f880d0eee6a4a98576573cddeabe77e0bb58e61883dd3281298c9
restore: 77 tables / 14 migrations
```

两份备份均完成 `pg_restore --list`、SHA-256 校验和真实隔离恢复。

发布演练发现 `backup-postgres.sh` 的 OSS Python 模块解析曾依赖调用者先进入 `backend/`。脚本现根据自身路径确定应用后端目录，并允许显式 `APP_BACKEND` 覆盖；加固后从 `/opt/stock-assistant` 项目根目录执行，成功上传 OSS 并完成 `77/14` 隔离恢复，不再依赖运维人员的当前工作目录。
