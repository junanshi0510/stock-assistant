# 固定日历预登记量化研究与 Point-in-time 质量价值因子

发布日期：2026-07-26

## 1. 这次解决什么问题

此前量化链路已经具备历史时点股票池、事件驱动撮合、非重叠样本外窗口、成本压力、不可变 shadow mandate 和真实未知行情的 5/20/60 日前向验证，但仍存在两个研究治理缺口：

1. 用户可以在任意时点手工运行，随后只保留或接入看起来最好的结果；
2. 价格因子具备严格历史时点，财务质量和估值尚未进入同一无前视框架。

本次把两者合并成一条可持续、可审计的量化研究主链：

```text
首轮运行前冻结策略
  → 一次性预登记未来 6–24 个固定日历槽位
  → 到期槽位不可跳过
  → 历史时点股票池
  → 公告日可见财务 + 当日可见估值
  → 多因子排名
  → 次日开盘事件驱动撮合
  → 非重叠样本外 / Rank IC / 成本翻倍
  → 12 项研究门槛
      ├─ 未通过或失败：永久留痕
      └─ 全部通过：冻结 mandate → 5/20/60 日真实前向验证
```

它不把回测变成自动交易。系统继续固定：

```json
{
  "execution_authorized": false,
  "broker_connected": false,
  "quantity_generated": false
}
```

## 2. 同行方法与本项目取舍

[QuantConnect 的 Scheduled Universes](https://www.quantconnect.com/docs/v2/writing-algorithms/algorithm-framework/universe-selection/scheduled-universes)允许按固定日期规则选择股票池；本项目采用同类“日历先于结果”的思想，但把未来所有槽位在首轮运行前直接写入不可变账本，进一步约束漏跑和事后删样本。

[QuantConnect 的 Fundamental Universes](https://www.quantconnect.com/docs/v2/writing-algorithms/universes/equity/fundamental-universes)强调基本面字段存在缺失与时间调度边界。本项目不把缺失财务项补成 0，也不把报告期当作披露日期；覆盖不足会降低研究资格，而不是由综合分掩盖。

A 股数据字段使用 Tushare 官方接口：

- [`fina_indicator`](https://tushare.pro/document/2?doc_id=79)：读取 `ann_date`、`end_date`、ROE、毛利率、经营现金流/营业收入、资产负债率和修订标记；
- [`daily_basic`](https://tushare.pro/document/2?doc_id=32)：读取交易日对应的 `pe_ttm` 与 `pb`。

接口权限和积分由 Tushare 账户决定。平台不会在权限不足时回退到新浪、东方财富或虚构财务值；对应股票会明确失败或被排除，计划批次仍保留。

## 3. 固定日历预登记

创建研究计划时冻结：

- 完整标准化量化政策；
- 因子及全部权重；
- 股票池模式、指数代码或冻结股票列表；
- 调仓、成交、成本、容量和风险参数；
- 市场对应 IANA 时区；
- 月度或季度频率；
- 首次本地运行日期与收盘后时间；
- `6–24` 个未来槽位；
- 用户对失败留痕与仅纸面验证边界的确认。

市场时区固定为：

| 市场 | 时区 |
| --- | --- |
| A 股 | `Asia/Shanghai` |
| 港股 | `Asia/Hong_Kong` |
| 美股 | `America/New_York` |

首次日期只允许每月 1–28 日，避免月末在短月份漂移。每个槽位同时保存本地时间与 UTC 时间，夏令时由 IANA 时区规则转换，不手工写死偏移量。

计划不提供“暂停并跳过本期”。用户可以终止整个计划，但所有尚未运行的未来槽位会转成 `retired_unrun`，不会被删除。

## 4. 批次状态机

```text
scheduled
  → dispatching
  → run_queued / run_running
      ├─ failed
      ├─ research_only
      └─ forward_enrolled

scheduled + 计划终止
  → retired_unrun
```

终态含义：

| 状态 | 含义 |
| --- | --- |
| `research_only` | 历史研究已完成，但 12 项门槛至少一项未通过 |
| `forward_enrolled` | 历史门槛全部通过，mandate 已冻结并接入真实前向验证 |
| `failed` | 派发、数据、运行或绑定阶段发生明确失败 |
| `retired_unrun` | 用户终止计划时尚未运行的预登记槽位 |

调度认领使用条件更新；一个槽位只能从 `scheduled` 被认领一次。进程在认领后、绑定 Run 前崩溃时，下一次核对会把孤儿派发标记为失败，不会悄悄补一个不同时间的批次。

## 5. 财务数据的严格可见时间

### 5.1 财务质量

一行财务指标只有满足以下条件才可用于信号：

```text
ann_date <= signal_date
end_date <= signal_date
```

候选行按报告期优先排序，再在相同报告期内取最新公告：

```text
max(end_date, then ann_date)
```

因此，对旧年度报告的晚到修订不会覆盖已经公开的更新季度报告。`end_date > ann_date`、日期无法解析或同一公告日/报告期出现无法排序的不同值时，相关记录会被排除并进入证据异常计数。

财务陈旧度按 `signal_date - end_date` 计算，而不是按最近修订公告日计算；这样旧报告昨天刚修订也不会伪装成新鲜基本面。政策允许 `90–900` 天，默认 `550` 天。

财务质量原始分数为：

```text
quality_raw =
  0.35 × ROE
  + 0.25 × gross_profit_margin
  + 0.25 × operating_cash_flow / revenue
  - 0.15 × debt_to_assets
```

至少 3 个分项有效才计算。缺失分项不会补 0；其余分项按绝对权重重新归一。最终因子仍在同一信号日、同一可投资股票池内做横截面百分位。

### 5.2 时点估值

估值只使用：

```text
trade_date <= signal_date
```

原始分数为：

```text
value_raw =
  0.65 × earnings_yield_pct
  + 0.35 × book_to_price_pct

earnings_yield_pct = 100 / pe_ttm
book_to_price_pct = 100 / pb
```

`pb` 缺失或非正时排除。Tushare 对亏损公司可能返回空 PE；本项目使用明确的 `-25%` earnings-yield 惩罚，不直接删掉亏损公司，避免形成未披露的盈利过滤器。

每日估值使用独立的 `3–30` 天陈旧度门槛，默认 `7` 天，不与财报默认的 `550` 天共用，避免把数月前 PE/PB 当作当前信号输入。

### 5.3 只请求启用的接口

- 只启用财务质量：仅请求 `fina_indicator`；
- 只启用时点估值：仅请求 `daily_basic`；
- 两者都启用：请求两类历史；
- 两者都未启用：完全不请求财务接口。

这既减少额度消耗，也避免无关接口的缺失错误阻断当前策略。

## 6. 因子覆盖与晋级门槛

引擎 `point_in_time_quant_selection@2.0.0` 支持：

| 因子 | 方向 |
| --- | --- |
| 中期动量（跳过最近一月） | 越高越好 |
| 趋势质量 | 越高越好 |
| 低波动 | 越低越好 |
| 流动性 | 越高越好 |
| 披露日财务质量 | 越高越好 |
| 时点估值 | 越高越好 |

只有权重大于 0 的因子参与缺失检查、横截面排名和综合分。权重为 0 的因子在结果中保留列定义，但 percentile 为 `null`，不会因为无关字段缺失而排除股票。

启用财务因子时，12 项历史研究门槛新增：

1. `point_in_time_fundamentals`：公告日/交易日证据严格通过；
2. `fundamental_coverage`：通过价格基础检查后的信号截面中，财务因子有效观察覆盖至少 80%。

覆盖分母只统计已经通过基础价格、历史长度、陈旧度、最低价格和流动性检查、真正进入财务判断的观察，不把无行情股票错误归因成财务缺失。

## 7. 自动前向接入

每个到期批次独立运行冻结策略：

- Run 失败或取消：`failed`；
- 历史门槛未全过：`research_only`；
- 历史门槛全过：冻结不可变 shadow mandate；
- 使用 mandate Snapshot SHA-256 幂等接入既有前向验证；
- 批次保存 Run、mandate、validation 三个链接和最终摘要。

自动接入不改变既有前向验证的因果边界。入场仍必须晚于真实接入市场日期和原信号日期，不能补算已知历史收益；资金资格仍需要多个独立成熟批次、成本后基准超额、覆盖、回撤、普通置信区间和多重检验共同通过。

## 8. 数据模型与不可变性

迁移 `quant-research-program.v1` 新增：

| 表 | 作用 |
| --- | --- |
| `quant_research_programs` | 冻结策略、日历、确认及其 SHA-256 |
| `quant_research_program_events` | 计划创建/终止的前序哈希事件链 |
| `quant_research_cycles` | 全部预登记槽位、运行链接和终态结果 |
| `quant_research_cycle_events` | 每个槽位的追加式状态事件链 |

保护规则：

- 计划、计划事件、批次事件拒绝 UPDATE/DELETE；
- 批次拒绝 DELETE；
- 槽位、顺序、租户、用户、市场时间和创建时间不可改；
- Run/mandate/validation 链接一旦设置不可换绑；
- 终态 outcome 一旦写入不可改；
- PostgreSQL 使用触发函数，SQLite 使用等价触发器；
- PostgreSQL 通过 advisory transaction lock 幂等安装迁移。

Readiness 单独返回：

```text
quant_research_program_schema
```

四张表缺任一张时，`full_service_ready=false`。

## 9. 调度与高可用边界

新增持久调度任务：

```text
stock_assistant.scheduler.quant_research_programs
```

默认每 900 秒运行一次，最短可配置为 300 秒，路由到独立 scheduler 队列。到期 `scheduled` 优先于运行中轮询，避免大量长运行批次长期饿死新到期槽位。

手动核对在 SQL 查询阶段就应用 `tenant_id + user_id + program_id`，再执行 LIMIT，避免其他用户的大量队列占满前 50 条后让当前用户看不到自己的到期批次。

生产仍依赖：

- PostgreSQL 权威事实；
- Redis/Celery 持久任务；
- scheduler Worker 与 Celery Beat；
- market-data Worker；
- 已配置且有权限的 Tushare Token。

## 10. API

新增 5 个受认证操作：

| 方法 | 路径 | 作用 |
| --- | --- | --- |
| GET | `/api/v1/quant-selection/research-programs` | 当前用户计划总览 |
| POST | `/api/v1/quant-selection/research-programs` | 冻结策略并一次性预登记全部槽位 |
| GET | `/api/v1/quant-selection/research-programs/{program_id}` | 当前用户计划详情 |
| POST | `/api/v1/quant-selection/research-programs/{program_id}/reconcile` | 手动核对当前计划到期/运行中批次 |
| POST | `/api/v1/quant-selection/research-programs/{program_id}/retire` | 终止计划并保留未来槽位 |

请求使用 `extra="forbid"`。跨用户读取表现为 `404`；策略、时间和确认错误返回 `400`；唯一性或终态冲突返回 `409`。

当前 OpenAPI：

```text
182 paths
212 operations
```

## 11. 前端工作台

入口：`研究中心 → 量化选股`。

页面新增：

- 固定日历计划创建器；
- 月度/季度、首次日期、本地时间和批次数设置；
- 失败留痕与不自动交易确认；
- 计划总数、已完成、前向验证、未通过/失败统计；
- 6–24 个槽位状态条；
- 下一批 UTC 时间和策略哈希；
- 手动核对与终止留痕；
- 两个新因子权重；
- 财务最大陈旧天数；
- 每日估值最大陈旧天数；
- Point-in-time 来源、验证详情、覆盖和逐股票失败；
- 六因子动态结果表。

## 12. 本地验收

- 后端全量：`597 passed`、`13 subtests passed`；
- 新计划、PIT 因子与既有量化链专项：`30 passed`；
- 前端生产构建：`1857 modules transformed`；
- 生产依赖审计：`0 vulnerabilities`；
- Python 新模块编译检查通过；
- OpenAPI：`182` 条路径、`212` 个操作；
- 未来公告不能改变早期信号；
- 旧报告晚到修订不能覆盖更新报告期；
- 同日歧义修订会整组排除；
- 单因子只调用所需 Tushare 接口；
- 所有未来槽位一次性预登记；
- 终止后槽位仍存在且数据库拒绝删除；
- 研究未通过的失败检查和结果摘要不可变保留；
- 多租户核对先按用户过滤再限量；
- 终态批次不能追加伪造的运行中事件；
- 浏览器真实创建 6 个未来槽位；
- 桌面端与 `390×844` 移动端均无页面级横向溢出；
- 应用控制台错误为 0。

全量测试只有既有 FastAPI `on_event` 弃用提示和 Pillow 超大图保护提示，没有失败。

## 13. 已知限制

- 财务质量与时点估值目前只支持 A 股 Tushare；港股和美股暂不开放这两个权重；
- 当前是日期级而非盘中级公告可见性，同一公告日无法可靠排序的不同修订会被排除；
- 财务质量为跨行业固定线性分数，尚未做历史行业中性化，金融与非金融企业不能只看该分数直接比较；
- Tushare 接口权限、积分、频率和历史范围由实际账户决定；
- A 股历史指数成分仍另需 `index_weight` 权限；
- 调度器与双 API 位于同一主机，不是跨主机或跨可用区容灾；
- 日线撮合不模拟真实盘口、集合竞价队列、涨跌停封单或券商拒单；
- 预登记、无前视和前向验证能减少研究偏差，不能保证因子未来继续有效或带来盈利。

## 14. 生产发布记录

### 14.1 发布与迁移

- 主功能提交：`3192886`；
- A 股生产可用性修复：`1f98549`、`435567c`、`3e59e1f`；
- 所有提交均已推送 GitHub `main`；
- 最终功能 release：`3e59e1f9d6f3b2fc867f69b99a5707eecd18a653`；
- 云端仓库：`/opt/stock-assistant`；
- 内容寻址 release：`/opt/stock-assistant-releases/3e59e1f9d6f3b2fc867f69b99a5707eecd18a653`；
- `8001/8002` 双 API 副本和原子前端符号链接均指向同一 release；
- PostgreSQL 最终为 `81` 张表、`15` 个迁移标记；
- `quant-research-program.v1` 已登记，4 张新表、5 个不可变触发器、6 个外键均核对通过。

迁移先在发布前备份的隔离 PostgreSQL 克隆中连续执行两次，确认幂等；隔离库完成 6 槽位创建、跨用户隐藏、更新/删除拒绝和终止后槽位保留后销毁。生产没有写入合成研究计划。

### 14.2 服务与接口

- 两个副本均为 `ready=true`、`traffic_ready=true`、`full_service_ready=true`；
- `quant_research_program_schema=true`；
- OpenAPI：`182 paths`、`212 operations`；
- Agent、market-data、LLM、OCR、scheduler 五个 Worker 与 Celery Beat 全部 active；
- `stock_assistant.scheduler.quant_research_programs` 已在 scheduler Worker 注册，队列为 `scheduler`，周期 `900` 秒；
- `systemctl --failed` 为空；
- 最终发布窗口 API/Worker/Beat error 日志为 0。

普通用户认证验收确认：量化预设总览返回 `200`，匿名返回 `401`，默认预设为 `a_frozen_price_research`，时点估值预设同时保留并披露额度要求。临时用户随后停用，活跃会话 `0`、临时量化 Run `0`，认证审计链 `138` 个事件校验通过。

最终云端发布包专项 `15 tests` 通过。默认 A 股研究预设使用真实数据完成：

```text
候选请求       12
候选成功       12
候选失败       0
股票来源       BaoStock 12/12
基准           000300.SH
组合交易日     488
调仓信号       24
晋级状态       research_only
自动交易授权   false
```

### 14.3 备份与恢复

发布前私有 OSS AES256 备份：

```text
object  backups/postgresql/2026/07/stock-assistant-iZn4ai1fm0tr284w21h4kmZ-20260726T030219Z.dump
bytes   2,394,941
sha256  b04241573806e44b4ed0ac28aa9aec6a61ed505264a38fa0ad5519b92245dca6
restore 77 tables / 14 migrations
```

最终发布后私有 OSS AES256 备份：

```text
object  backups/postgresql/2026/07/stock-assistant-iZn4ai1fm0tr284w21h4kmZ-20260726T040236Z.dump
bytes   2,423,218
sha256  1b7f3554df0586271bae5e098db5c1c0065e620c3234efb405d296424a1d408c
restore 81 tables / 15 migrations
```

两次均校验本地 SHA-256；最终备份已从 OSS 下载并恢复到隔离数据库，表数和迁移数一致。
