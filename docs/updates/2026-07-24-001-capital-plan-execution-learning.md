# 2026-07-24 更新 001：资本计划兑现与决策学习中枢

## 1. 为什么这是一个完整功能，而不是新增一张“收益卡片”

此前平台已经可以回答：

1. 哪些研究策略通过了独立前瞻、成本、回撤和统计门禁；
2. 多个合格策略如何经过投资委员会形成候选共识；
3. 当前真实持仓、投资政策、可信估值和压力情景允许投入多少；
4. 如何冻结一份可复算、不可修改的组合资金计划。

但冻结计划之后仍存在关键断点：平台不知道用户是否执行、执行了哪些真实成交、实际投入了多少人民币、是否买了计划外标的，也无法把后续结果拆成“冻结计划本身的选择结果”和“用户实际执行造成的实施差值”。如果跳过这一层，历史盈利无法正确归因，月度预算也可能被下一份计划重复使用。

本次更新建立完整闭环：

```text
冻结计划
  → 绑定用户已记录的真实买入流水
  → 确认券商实际人民币结算金额
  → 计划/实际成交对账
  → 解释并复核执行偏差
  → 精确第 5/20/60 个交易日观察
  → 计划选择结果 / 真实执行结果 / 市场基准三方归因
  → 形成跨计划学习记分卡
  → 约束下一份月度资金计划
```

它记录已经发生的事实，不连接券商、不创建订单，也不承诺未来盈利。

## 2. 专业产品与方法参考

本实现吸收的是专业平台的方法边界，不复制其专有数据、算法或商业能力。

| 参考 | 可借鉴方法 | 本项目落地 | 明确未声称 |
|---|---|---|---|
| [QuantConnect Live Reconciliation](https://www.quantconnect.com/docs/v2/research-environment/meta-analysis/live-reconciliation) 与 [Live Trading Reconciliation](https://www.quantconnect.com/docs/v2/cloud-platform/live-trading/reconciliation) | 把研究/预期路径与真实运行结果分开核对，定位数据、成交、费用等偏差 | 冻结计划与真实成交分账；覆盖率、金额偏差、计划外成交、执行延迟单独记录 | 不拥有 QuantConnect 数据、经纪执行或算法引擎 |
| [QuantConnect Live Results](https://www.quantconnect.com/docs/v2/cloud-platform/live-trading/results) | 运行结果必须基于真实执行事实持续更新 | 真实成交后自动进入 5/20/60 交易日观察 | 不把纸面组合冒充真实账户净值 |
| [Interactive Brokers Reporting](https://www.interactivebrokers.com/en/whyib/reporting.php) 与 [PortfolioAnalyst](https://www.interactivebrokers.com/campus/trading-lessons/portfolioanalyst-overview/) | 用账户交易与现金流建立可审计绩效报告 | 只接受用户账本中的真实股票买入，并固定交易快照 SHA-256 与人民币结算事实 | 不读取 IBKR 账户、不宣称券商级完整税费或现金流报表 |
| [IBKR Performance Attribution](https://www.interactivebrokers.com/images/common/Statements/performance_attribution_white_paper.pdf) | 将组合结果拆解为可解释来源 | 分开报告冻结选择结果、真实执行结果和实施差值 | 不是完整 Brinson、因子或行业归因 |
| [MSCI 组合归因实践](https://www.msci.com/www/product-documentation/practical-applications-from-the/0163895862) | 归因必须固定组合、基准与观察区间 | 冻结计划篮子、真实执行篮子和同市场可交易基准使用同一精确交易日窗口 | 不拥有 MSCI 指数授权或机构归因数据库 |
| [CFA Institute GIPS 概览](https://www.cfainstitute.org/insights/professional-learning/refresher-readings/2026/overview-of-the-global-investment-performance-standards) | 绩效输入、现金流、费用和时间口径必须一致且可复核 | 使用用户确认的人民币实际结算金额；费用保留在交易快照中；自然日不能替代目标交易日 | 平台没有宣称 GIPS 合规 |
| [Sharesight Portfolio Performance](https://www.sharesight.com/uk/investment-portfolio-performance/) | 面向个人投资者，把交易、收益和基准比较放进统一工作流 | 在“我的资产 → 决策学习”集中呈现计划队列、成交、偏差、归因和学习状态 | 不复制其税务、分红或券商导入覆盖 |

## 3. 执行事实模型

### 3.1 只绑定真实交易账本

可绑定流水必须同时满足：

- `asset_type=stock`；
- `trade_type=buy`；
- 所有者与当前登录用户一致；
- 成交日在冻结计划日及其后 45 个自然日内；
- 尚未绑定到其他资金计划。

一笔流水只能归属一个计划。跨计划唯一约束位于数据库，而不是只依赖前端。

### 3.2 人民币实际结算金额

A 股本币成交会提供“份额 × 单价 + 费用”的参考值，但仍由用户确认。港股、美股必须填入券商实际人民币结算金额；系统不会用不可靠的即时汇率改写已发生的成交事实。

月度已投入金额取当前月份所有完整性通过的执行事件中的累计确认结算额。删除或修改原交易流水会使执行完整性失败，但不会释放已经占用的预算，从而避免通过改账本重复使用资金。

### 3.3 追加式事件，不允许重写历史

第一次确认产生执行事件 1。后续只能追加新流水并产生事件 2、3……，不能：

- 删除已确认流水；
- 把已确认人民币金额改小；
- 把同一流水改绑到另一计划；
- 跳过前序事件哈希；
- 修改或删除历史事件。

每个事件包含：

- 计划 Evidence/Result 哈希；
- 逐笔交易快照及交易 SHA-256；
- 前序事件哈希；
- 本事件 Evidence/Result SHA-256；
- 本事件总哈希；
- 计划金额、实际结算金额、覆盖率、计划外金额和执行延迟。

读取时会重新加载当前交易账本并校验交易 SHA-256；保存时通过不代表之后永久有效。

## 4. 计划与实际对账

每个冻结候选分别计算：

```text
候选实际结算额 = 所有同 market + symbol 已绑定流水的人民币结算额之和
候选偏差额     = 候选实际结算额 - 候选计划金额
计划覆盖率     = min(候选实际结算额, 候选计划金额) 之和 / 计划总额
绝对偏差率     = Σ|候选偏差额| + 计划外金额，再除以计划总额
```

生命周期为：

| 状态 | 含义 |
|---|---|
| `not_applicable` | 计划未获资金资格，无需虚构成交 |
| `awaiting_execution` | 可执行计划尚未绑定真实成交 |
| `partial` | 有真实执行，但计划覆盖不足 |
| `reconciled` | 计划覆盖和金额偏差在边界内，且没有计划外成交 |
| `deviated` | 超额投入、明显少投或计划外成交，需要解释 |
| `reviewed` | 用户已经复核偏差；事实、偏差和预算占用保持不变 |
| `integrity_failed` | 事件链或当前交易流水与冻结快照不一致 |

偏差复核是新的不可变事件，不是把 `deviated` 行原地改成 `reviewed`。

## 5. 5/20/60 交易日结果归因

每次观察同时重建三个路径：

1. `planned_path`：按冻结计划金额归一化的候选篮子；
2. `executed_path`：按确认人民币结算金额归一化的真实成交篮子；
3. `benchmark_path`：同市场可交易基准近似。

窗口只接受冻结/成交基线后的精确第 5、20、60 个真实交易日。补跑发生得更晚时仍读取目标交易日，不能把补跑日价格冒充目标价格。

```text
冻结选择超额 = 冻结计划篮子收益 - 市场基准收益
真实执行超额 = 真实执行篮子收益 - 市场基准收益
实施差值     = 真实执行超额 - 冻结选择超额
```

只有计划篮子、执行篮子和基准覆盖均达到 90% 时，窗口才为 `complete`；否则保持 `collecting` 或 `partial`。这使平台可以区分：

- 计划选得好，但用户执行时点或金额拖累；
- 计划本身没有跑赢，但执行偶然改善；
- 两条路径都跑赢；
- 行情证据不足，暂时不能评价。

## 6. 决策学习与下一计划门禁

学习记分卡按 5/20/60 日分别汇总：

- 成熟计划数；
- 冻结选择平均/中位超额；
- 真实执行平均/中位超额；
- 正超额比例；
- 平均实施差值；
- 最差一期；
- 20 日市场状态切片。

至少需要 6 个独立、完整的 20 日结果才进入 `decision_eligible`。样本不足只能“积累样本”，不能因为一两次盈利自动提高预算。

组合资金引擎升级为 `whole_portfolio_next_best_action.v4`：

- 本月剩余预算 = 投资政策月度预算 - 本月已确认人民币结算额；
- 上一份 `ready` 计划未完成对账/偏差复核时，不再叠加下一份试投计划；
- 重复冻结会返回仍待处理的当前计划；
- 交易流水完整性失败时暂停新增计划；
- 历史结果只影响流程复核，不自动放大仓位或创建订单。

## 7. 高可用结果观察

真实行情观察不再占用 API 请求直到供应商返回：

1. `POST .../outcomes` 校验计划和执行链；
2. 将用户范围、计划 ID 和执行事件 ID写入 PostgreSQL 持久任务；
3. 立即返回 `202 Accepted`、`job_id` 和轮询地址；
4. 生产环境把任务 ID 发送到 `market-data` Worker；
5. Redis 暂时不可用时任务保持 `queued`，scheduler 在恢复后重新派发；
6. 本地 SQLite 模式在 HTTP 响应后执行嵌入式后台任务；
7. 前端短轮询任务状态，超时后解除页面忙碌，用户可离开并稍后刷新。

任务状态、尝试次数、错误摘要和事件哈希链可审计；查询接口同时校验租户、用户和任务内 `user_id`，不能通过猜测任务 ID 读取其他账户结果。

## 8. 数据模型与接口

PostgreSQL/SQLite 新增三张业务表：

- `portfolio_capital_execution_events`
- `portfolio_capital_transaction_bindings`
- `portfolio_capital_outcome_snapshots`

三张表均按租户/用户隔离。执行事件和结果快照拒绝 UPDATE/DELETE；交易绑定具有跨计划唯一约束。PostgreSQL 迁移标记为 `portfolio-capital-learning.v1`，readiness 增加 `portfolio_capital_learning_schema`。

新增接口：

| 方法 | 路径 | 用途 |
|---|---|---|
| GET | `/api/portfolio/capital-decision/learning` | 计划队列、月度预算与学习记分卡 |
| GET | `/api/portfolio/capital-decision/plans/{plan_id}/execution` | 当前执行上下文、可绑定流水和完整性 |
| POST | `/api/portfolio/capital-decision/plans/{plan_id}/execution-events` | 追加真实成交执行事件 |
| POST | `/api/portfolio/capital-decision/plans/{plan_id}/execution-review` | 追加偏差复核事件 |
| GET | `/api/portfolio/capital-decision/plans/{plan_id}/outcomes` | 读取该计划的结果快照 |
| POST | `/api/portfolio/capital-decision/plans/{plan_id}/outcomes` | 异步派发真实结果观察 |
| GET | `/api/portfolio/capital-decision/outcome-jobs/{job_id}` | 读取用户隔离的后台任务状态 |
| GET | `/api/portfolio/capital-decision/outcomes/{outcome_id}` | 读取并复核单个结果快照 |

Celery Beat 新增 `observe-capital-plan-outcomes`，实际行情读取仍路由到 `market-data`，不会在 API 或 scheduler 进程内同步抓取。

## 9. 用户界面

“我的资产”新增“决策学习”工作区，首页投资指挥台增加“兑现与学习”入口。页面同时展示：

- 本月已确认投入和可执行计划数；
- 冻结计划 → 绑定成交 → 解释偏差 → 学习结果四阶段流程；
- 5/20/60 日学习记分卡；
- 冻结计划历史队列；
- 真实成交绑定与人民币金额确认；
- 计划覆盖、绝对偏差、计划外金额和执行延迟；
- 偏差复核；
- 冻结选择超额、真实执行超额和实施差值；
- Evidence/Result/Event 哈希与边界说明。

`blocked/watch` 计划显示“无需执行”，不出现虚构成交确认表单，也不进入收益归因。

## 10. 验证范围

自动化覆盖：

- 真实流水绑定、月度预算扣减和跨计划唯一性；
- 追加事件不能删除或改写已确认流水；
- 原流水被删除/修改后的动态完整性失败；
- 计划内、部分、超额和计划外成交对账；
- 偏差复核保留事实与预算；
- 精确 5/20/60 日归因及最小学习样本；
- 调度按执行事件与日期幂等；
- 后台任务持久化、用户隔离、事件链和嵌入式完成；
- PostgreSQL 迁移、不可变触发器、readiness、路由和 Celery 协议。

真实浏览器隔离账户验证了两条完整路径：

1. 计划内 ¥1,000 + 计划外 ¥303 → 30.30% 偏差 → 复核后预算仍为 ¥1,303，审计事件由 1 增至 2；
2. 计划内 ¥1,000 → 100% 覆盖、0% 偏差 → 异步结果观察约 0.6 秒返回 `202`，长耗时行情任务不再锁住页面。

浏览器控制台无应用错误；临时数据位于隔离 SQLite，不写入用户原账本。

## 11. 仍然存在的边界

- 平台不会自动下单，也不知道券商实时可用现金；
- 人民币结算金额依赖用户或券商账单确认；
- 尚未自动导入分红、税务、拆股、融资利息和全部券商费用；
- 基准是可交易市场近似，不是用户自定义机构基准；
- 5/20/60 日结果是历史观察，不是未来上涨概率；
- 六个成熟样本只是最低学习门槛，不代表统计结论一定稳定；
- 当前只阻止错误归因和重复预算，不能保证投资获利。

## 12. 生产发布验收计划

发布顺序固定为：

1. 创建 PostgreSQL AES256 加密备份并上传私有 OSS；
2. 在隔离数据库恢复并核对表数、迁移标记和备份 SHA-256；
3. 执行 `python -m migrations.portfolio_capital_learning_v1`；
4. 核对三张新表、迁移标记、交易唯一约束和不可变触发器；
5. 在目标提交上运行资金决策、学习服务、路由和任务协议专项测试；
6. 逐个重启 `market-data`、`scheduler` Worker 与 Celery Beat；
7. 使用原子滚动发布器依次更新 `8001/8002` 两个 API 副本和静态资源；
8. 核对两个副本 release、`portfolio_capital_learning_schema=true`、OpenAPI 与 `/health/full`；
9. 通过公网验证匿名 `401`、登录/CSRF、学习总览、阻断计划“无需执行”和异步任务 `202`；
10. 使用临时普通账户执行计划内/计划外成交、偏差复核和任务用户隔离测试；
11. 停用临时账户、撤销会话，创建发布后备份并再次完成隔离恢复。

## 13. 生产发布实测结果

本功能已于 2026-07-24 以提交 `1865cc9cffcb575318412274899c379ce613bcd0` 推送 GitHub，并部署到 `http://8.148.67.79/`。

### 13.1 数据安全与迁移

- 发布前备份上传私有 OSS，SHA-256 为 `09eae8a0042974b723351bcb6b12463f0cb83f247e9116643d5682cdd0105942`，大小 `1,722,196` 字节，AES256 加密；隔离恢复验证为 `67` 张表、`10` 个迁移标记。
- `portfolio-capital-learning.v1` 只执行一次；三张业务表、三组不可变触发器和迁移标记均存在。
- 发布后再次生成私有 OSS AES256 备份 `backups/postgresql/2026/07/stock-assistant-iZn4ai1fm0tr284w21h4kmZ-20260724T011337Z.dump`，SHA-256 为 `6668c4202db5d74471d50c956c5da42b9ec3d1720832e22b9a13a12dc83eea96`，大小 `1,752,188` 字节。
- 发布后备份已在隔离 PostgreSQL 中真实恢复，核对结果为 `70` 张表、`11` 个迁移标记，不是只检查文件存在。

### 13.2 服务与协议

- 云端专项回归 `34 passed`；`market-data`、`scheduler` 等 6 个 Worker/Beat 服务均为 active。
- 原子滚动发布依次完成 `8001/8002` 两个 API 副本；两个副本 release 均为 `1865cc9`，`full_service_ready=true`、`portfolio_capital_learning_schema=true`。
- 两个副本 OpenAPI 均为 `163` 条路径、`190` 个操作，并包含用户隔离的 outcome job 查询接口。
- 公网页面与边缘健康检查返回 `200`；匿名访问学习接口返回 `401`。认证审计链共 `62` 个事件，哈希、顺序与前序哈希全部校验通过。
- `observe-capital-plan-outcomes` 已注册到 Celery Beat，真实行情读取仍由 `market-data` 队列执行。

### 13.3 生产账户端到端验证

生产验证使用两个临时普通账户，不使用管理员账户，也不接触既有用户持仓：

1. 第一条链路绑定计划内 ¥1,000 和计划外 ¥303，系统计算实际投入 ¥1,303、偏差 30.30%，要求追加偏差复核；复核后执行事件增至 2 条，本月预算仍扣除 ¥1,303。测试夹具把市场字段错误编码为 `A?` 时，后台作业按预期以 `MARKET_INPUT_INVALID` 拒绝，没有把未知市场当成有效 A 股。
2. 第二条链路使用有效 `A股` 市场，计划和实际均为 ¥1,000；对账状态为 reconciled。结果观察接口约 `1009 ms` 返回持久任务，Worker 成功执行并生成 1 个 collecting 快照及 5/20/60 日三个观察窗口。当天没有成熟窗口，完成数为 0，符合交易日边界而不是伪造收益。
3. outcome job 匿名读取返回 `401`；任务审计链通过。两个临时账户最终均为 disabled，活跃会话均为 0；全站发布验收结束时活跃会话总数也为 0。

结论：本次发布完成了“冻结决策—真实执行—偏差解释—持续归因—规则学习”的生产闭环，并验证了恢复、权限、异步执行和错误输入边界。它能提高资金纪律和决策复盘质量，但不承诺任何股票上涨或投资获利。
