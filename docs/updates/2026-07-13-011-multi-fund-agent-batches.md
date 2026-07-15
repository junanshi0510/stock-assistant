# 投资 Agent 批量基金研究

日期：2026-07-13  
状态：已实现并完成本地真实数据验收

> 后续能力：2026-07-15 已在该批次模型上增加“唯一总预算 + 组合级不可变分配快照”，见 `2026-07-15-008-portfolio-batch-allocation.md`。本文保留首次引入 Batch 时的实现边界与验收记录。

## 1. 目标

让用户一次提交多只基金，持续看到每只基金的真实执行状态，并在同一批次中比较：

- 市场与风险分布；
- 每只基金的确定性动作和可选模型动作；
- 近一年收益、当前回撤和证据覆盖；
- 基于真实披露前 N 大持仓的重合下界；
- 每只基金独立的 Evidence、Audit 和 Outcome。

本功能不把批量比较变成简单收益排名，不复制单笔投资金额，也不使用模拟数据填补失败基金。

## 2. 领域模型

### Agent Batch

`agent_batches` 保存批次身份、输入快照、输入哈希和幂等键。

### Batch Item

`agent_batch_items` 保存批次、子 Run、顺序和基金代码的不可重复关系。

### Agent Run

每只基金仍创建原有 `fund_deep_research` Run。Run 的 Step、Evidence、Claim、模型调用摘要、审计链、Outcome 和重跑能力均不改变。

Batch 和全部子 Run 在同一个 SQLite `BEGIN IMMEDIATE` 事务内创建。任一插入失败时整批回滚，不留下半批次或孤立 Run。

## 3. API

### 创建批次

```http
POST /api/v1/agent/batches
Idempotency-Key: <client-generated-key>
Content-Type: application/json
```

```json
{
  "intent": "fund_deep_research",
  "codes": ["013403", "014089"],
  "months": 60,
  "include_market_intelligence": true,
  "include_ai_synthesis": true,
  "include_portfolio_context": false,
  "question": "比较这些基金的真实风险、持仓和重合度。",
  "intelligence_holding_limit": 2,
  "news_per_holding": 1
}
```

约束：

- 至少 2 只基金；
- 默认最多 6 只，代码硬上限 8；
- 代码必须为 6 位数字；
- 同一批次不能重复；
- 批次子任务数加现有活动任务数不能超过 `AGENT_MAX_PENDING_RUNS`；
- 重复幂等键返回原批次，不重复创建子 Run。

### 查询

- `GET /api/v1/agent/batches?limit=6`
- `GET /api/v1/agent/batches/{batch_id}`
- `POST /api/v1/agent/batches/{batch_id}/cancel`

取消只向尚未终止的子 Run 写入取消请求。已经完成的 Evidence 不删除、不覆盖。

## 4. 批次状态

- 所有子 Run 等待：`queued`
- 任一子 Run 已启动或已有部分终态：`running`
- 全部为完整结果：`completed`
- 全部终止但存在部分结果、数据不足、失败或混合状态：`partial`
- 全部失败：`failed`
- 全部取消：`cancelled`

批次进度按终态子 Run 数计算，不把 Step 数不同的基金错误地当作同等步骤进度。

## 5. 跨基金持仓重合

只读取每个子 Run 已保存的 `market_intelligence.holding_pulse.items`。

对基金 A、B 的共同披露持仓 `h`，使用：

```text
overlap_lower_bound(A, B) = Σ min(weight_A(h), weight_B(h))
```

该值必须标记为“重合下界”，原因包括：

- 当前只抓取每只基金前 N 大持仓；
- 基金披露存在日期滞后；
- 不同基金的披露日期可能不同；
- 债券、现金、衍生品和未披露仓位可能未覆盖；
- 数据源失败时不推断缺失持仓。

## 6. 并发策略

内置 `AgentWorker` 从单线程扩展为受控线程池：

- 默认并发 `2`；
- 环境变量 `AGENT_WORKER_CONCURRENCY` 可调；
- 代码硬限制 `1-4`；
- 每条通道独立领取一个持久化 queued Run；
- SQLite 使用 WAL、`BEGIN IMMEDIATE` 和 30 秒 busy timeout；
- 服务重启后原有 running Run 按既有恢复规则重新排队。

2 核 4 GB 云服务器保持并发 2。单基金市场情报内部已经使用并行网络 I/O，继续提高 Run 并发会放大第三方数据源压力。

## 7. 前端

Agent 研究入口新增“单基金 / 批量基金”分段模式。

批量模式支持：

- 换行、空格、中英文逗号和分号分隔；
- 前端自动去重并显示识别数量；
- 非 6 位内容立即提示；
- 少于 2 只、超过 6 只或存在非法内容时禁止提交；
- 批次历史、状态刷新和整批取消；
- 一只基金一行，点击“详情”进入原单基金 Run；
- 手机端改为两列信息布局，不产生页面横向滚动。

批量模式不显示“本次计划投入”。系统不会把一笔金额复制给多个子 Run；金额必须在逐只风险门禁和组合总暴露约束中计算。

## 8. 验收结果

自动化：

- 后端完整测试：200 项通过；
- 前端 Vite 生产构建通过；
- 新增原子创建、幂等、重复代码、投资档案版本固定、持仓重合下界和两路并发测试。

真实数据：

- Batch：`batch_8808be9978b142eeaea9ae25cf6f553c`
- 基金：`013403`、`014089`
- 两个 Run 启动时间相差约 0.08 秒，证明两路并发领取；
- 总耗时约 55 秒；
- 市场情报覆盖 2/2；
- 新闻数量分别为 1、2；
- 每只基金用于重合检查的真实披露持仓为 2 只；
- 模型未配置，因此两只均明确返回 `model_not_configured`，没有模板补齐；
- 本批前两大持仓没有共同标的，重合 pair 数为 0；
- 桌面端和 390x844 手机视口通过，无横向溢出，逐行详情跳转正确。

## 9. 已知边界

- 当前 Batch 没有单独的大模型跨基金总结；每只基金可独立调用模型，批次级聚合保持确定性。
- 当前持久化仍是单用户迁移架构；多用户开放前必须完成认证、租户隔离和数据库迁移。
- Batch 不负责自动调仓或下单。
- 完整组合重合还需要更广的持仓披露覆盖和统一披露日期处理。
