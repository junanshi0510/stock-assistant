# 不可变策略样本 Cohort 与分层绩效门禁

## 1. 更新信息

- 更新编号：`2026-07-13-008`
- 功能：策略 Shadow Outcome 可比样本分层
- Cohort 协议：`strategy_shadow_cohort.v1`
- 分类器：`fund_strategy_shadow_cohort@1.0.0`
- Shadow Outcome Evidence：`1.1.0`
- 分层报告：`strategy_shadow_report@1.1.0`
- 数据范围：策略信号产生时已经保存的市场画像 Evidence 和策略信号 Evidence
- 存储：追加式 SQLite Cohort 索引、不可变 Evidence、Run 审计事件
- 聚合原则：预测周期、市场、资产类别或基金载体不同，禁止池化总体绩效
- 发布影响：无。Cohort 达到披露门槛也不会自动发布策略或生成投入金额

## 2. 为什么本轮必须做

上一轮已经可以按策略精确版本积累真实结果，但原报告仍以 `strategy_id + strategy_version` 作为汇总范围。这个范围可能同时包含：

- 3、6、12 个月不同预测周期。
- 中国内地、香港、美国、全球或跨市场基金。
- 权益、固收、混合资产、商品等不同资产类别。
- QDII、非 QDII 跨境和境内基金载体。
- MA60 上方、MA60 下方、常规回撤和深度回撤等不同信号输入状态。

这些样本的收益分布、净值确认日历、汇率暴露和风险来源不同。直接合并后可能出现两个严重问题：

1. 策略只在某一个市场有效，却被总体胜率包装成普遍有效。
2. 每个分组表现都偏弱，但由于组间样本权重变化，总体指标看起来更好，形成辛普森悖论。

项目 PRD 明确要求模型和策略按市场、资产、波动环境和预测窗口分层评估。上一轮更新也把市场分层列为下一阶段准入条件。因此本轮不是展示功能，而是策略绩效能否可信解释的必要门禁。

该能力不能直接增加收益，也不保证盈利。它的价值是阻止不可比样本制造虚假优势，让后续策略淘汰和资金风险预算只使用同口径证据。

## 3. 被否决的方案

### 3.1 只在前端按基金名称筛选

基金名称不是稳定分类字段，也无法证明分类使用了信号产生时已知的信息。前端筛选还可以被绕过，不能作为策略发布门禁。

### 3.2 观察结果产生后重新请求当前基金类型

基金名称、分类、比较基准和投资范围可能变化。使用结果日的当前资料分类历史信号会引入后验信息。本轮只使用 Run 当时已经保存且通过哈希校验的 Evidence。

### 3.3 修改上一轮的冻结信号快照

旧快照已经通过 SHA-256 和审计链绑定。原地增加字段会使历史记录全部失效。本轮建立独立追加式 Cohort 绑定，旧快照保持一个字节不变。

### 3.4 把所有 QDII 归为同一类

香港科技、美国宽基、全球股票、债券和商品 QDII 的风险来源完全不同。QDII 只作为基金载体维度，不能替代市场和资产类别。

### 3.5 未知类别自动归入“其他”并参与统计

“其他”会继续混合不同风险来源。未知值可以保存并显示，但 `release_eligible=false`，不能进入策略绩效披露。

### 3.6 多个 Cohort 样本相加后披露总体胜率

即使每个 Cohort 单独达到门槛，也不能直接合并。当前只允许分组指标；总体指标仅在所有已观察样本属于同一个可比 Cohort 时返回。

## 4. 功能范围与非目标

本轮实现：

- 自动为新 Shadow 入组记录创建 Cohort。
- 从旧 Run 的不可变 Evidence 回填历史 Cohort。
- 冻结分类器版本和两个来源 Evidence 的载荷哈希。
- 持久化市场、资产类别、载体、周期和信号状态。
- 创建独立 Cohort Evidence 和追加式审计事件。
- Worker 在真实结果请求前验证 Cohort。
- 策略报告按可比 Cohort 分组并执行独立披露门槛。
- Agent 页面展示当前样本的 Cohort 和完整性状态。

本轮不实现：

- 不计算用户实际申购、赎回、费用、税费或汇兑成本。
- 不构建宏观市场牛熊模型。
- 不根据当前持仓反推历史基金底层资产。
- 不把基金名称关键词当成实时持仓。
- 不改变现有策略 Manifest 或发布状态。
- 不自动交易或自动扩大仓位。

## 5. Cohort 维度

### 5.1 预测周期

| 周期 | 后续确认净值数量 |
|---|---:|
| `3m` | 63 |
| `6m` | 126 |
| `12m` | 252 |

周期和观测数量必须同时匹配策略 Evidence 和 Shadow 入组记录。任何不一致都会拒绝绑定。

### 5.2 市场

支持稳定值：

- `mainland`
- `hong_kong`
- `united_states`
- `global`
- `cross_border_mixed`
- `unknown_cross_border`

跨境混合市场必须冻结至少两个已识别市场，并把排序后的市场集合写入 Cohort Key。`unknown_cross_border` 可以保留，但不能进入发布绩效。

### 5.3 资产类别

分类只使用数据源原生 `fund_type`：

- `equity`
- `fixed_income`
- `mixed`
- `cash`
- `commodity`
- `real_estate`
- `fund_of_funds`
- `alternative`
- `unknown`

规则按明确关键词和优先级执行。例如“债券指数型”先识别为固收，“指数型-海外股票”识别为权益。无法确认时保持 `unknown`，不猜测。

### 5.4 基金载体

- `domestic`：境内且未识别跨境风险。
- `qdii`：市场画像明确为 QDII。
- `cross_border_non_qdii`：跨境但不是 QDII，例如可能使用港股通的境内基金。

### 5.5 信号状态

直接冻结策略用于历史条件匹配的输入：

- 趋势：`above_ma60`、`below_ma60`。
- 回撤：`near_high`、`normal_pullback`、`deep_drawdown`。

这不是重新计算市场状态，而是证明结果属于哪一种原始信号条件。

## 6. 稳定 Cohort Key

发布可比单元：

```text
horizon={horizon}|market={market_bucket}|asset={asset_class}|vehicle={vehicle_type}
```

信号状态单元：

```text
{release_cohort_key}|trend={trend}|drawdown={drawdown_band}
```

示例：

```text
horizon=6m|market=hong_kong|asset=equity|vehicle=qdii
horizon=6m|market=hong_kong|asset=equity|vehicle=qdii|trend=below_ma60|drawdown=deep_drawdown
```

Cohort Key 只由版本化分类器产生，不接受 API 参数或管理员手工输入。

## 7. 来源 Evidence 绑定

每条 Cohort 必须绑定：

1. `fund_market_profile@1.0.0` 对应的市场画像 Evidence ID 和 payload SHA-256。
2. `fund_conditioned_forward_return@1.0.0` 所在分析 Evidence ID 和 payload SHA-256。
3. Shadow enrollment ID、Run ID、策略版本、Manifest 哈希和信号快照哈希。
4. 基金代码、基线日期、信号方向、主周期和确认净值数量。

服务重新构建 Cohort 时，任意一项不匹配都会失败。来源 Evidence 必须属于同一个 Run，且载荷完整性为真。

## 8. 持久化模型

新增 `agent_strategy_shadow_cohorts`：

| 字段组 | 内容 |
|---|---|
| 身份 | Cohort ID、Enrollment ID、Run ID、策略 ID/版本、基金代码 |
| 分类器 | taxonomy ID、taxonomy version |
| 来源 | 市场 Evidence ID/hash、信号 Evidence ID/hash |
| 结果 Evidence | Cohort Evidence ID |
| 可比维度 | horizon、observation days、market、asset、vehicle、trend、drawdown |
| Key | release cohort key、regime cohort key |
| 门禁 | release eligible |
| 不可变载荷 | cohort JSON、cohort SHA-256、created_at |

表没有更新接口和 `updated_at`。每个 Enrollment 最多一条 Cohort，数据库唯一约束阻止重复绑定。

## 9. 原子 Evidence 与审计

同一个数据库事务内完成：

1. 校验 Enrollment 和两个来源 Evidence。
2. 写入 `strategy_shadow_cohort` Evidence。
3. 写入 Cohort 索引行。
4. 追加 `evidence.created`。
5. 追加 `strategy.shadow.cohort.bound`。

任一步失败都会回滚，不会出现只有索引没有 Evidence，或有 Evidence 没有审计绑定的中间状态。

验证时检查：

- Cohort JSON 哈希。
- Cohort 表冗余字段与载荷一致。
- 两个来源 Evidence 的 ID、Run、hash 和载荷完整性。
- 使用当前同版本分类器重建后的 hash。
- Cohort Evidence 类型、载荷和 hash。
- Run 审计链及唯一 Cohort 绑定事件。

## 10. 旧样本迁移

Worker 启动时先回填历史 Shadow Enrollment，再扫描最多 1000 条缺失 Cohort 的旧记录。回填只读取原 Run 已有 Evidence，追加 Cohort 表、Evidence 和审计事件。

迁移明确禁止：

- 修改原 `signal_snapshot_json`。
- 修改原 `signal_snapshot_sha256`。
- 用当前网络数据替换原市场画像。
- 删除未知或表现不佳的样本。

单条回填失败不会阻塞其他记录。失败记录保持缺失，报告 `classification_complete=false` 并拒绝披露绩效。

## 11. Worker 门禁

真实净值 Outcome Worker 的顺序变为：

```text
验证 Enrollment 快照和状态审计
        |
验证 Cohort 快照、来源 Evidence 和审计
        |
调用真实净值结果工具
```

Cohort 缺失或篡改时，Provider 不会被调用，Enrollment 进入 `blocked`，错误码为 `SHADOW_COHORT_INTEGRITY_FAILED`。

观察完成后的 Outcome Evidence 额外绑定：

- Cohort ID 和 SHA-256。
- Cohort Evidence ID。
- taxonomy ID/version。
- release cohort key 和 regime cohort key。
- release eligibility。

## 12. 分层绩效披露

每个可比 Cohort 独立要求：

1. 至少 30 个完整同类比较 Outcome。
2. 至少 10 只不同基金。
3. 全量扫描完成。
4. Enrollment、Outcome、Cohort 和审计完整性错误均为 0。
5. 所有用于计算的 Cohort 均为 release eligible。

只有满足门槛的单个 Cohort 才返回命中率、Wilson 95% 区间、符号调整收益和同类超额方向指标。

总体 `metrics` 只有在所有已观察结果属于同一个可比 Cohort 时才可能返回。只要出现多个 Cohort，总体指标固定为 `null`，即使各分组已经分别达到门槛。

报告同时返回信号状态分布数量，但不会在小样本状态下披露绩效，以防止从大量切片中挑选偶然最优结果。

## 13. API

沿用只读 API，不增加公网写入口：

```text
GET /api/v1/agent/strategies/{strategy_id}/{strategy_version}/shadow-outcomes
GET /api/v1/agent/runs/{run_id}/strategy-shadow-outcome
GET /api/v1/agent/runs/{run_id}/evidence/{evidence_id}
```

Run 响应新增：

- `cohort`
- `cohort_verification`

策略报告新增：

- `cohort_binding`
- `segments`
- `disclosure_gate.comparability_unit`
- `disclosure_gate.cross_cohort_pooling=forbidden`

公开 Cohort 不返回租户、用户、原始来源载荷或内部错误详情。

## 14. 前端

Agent 策略区域新增 Cohort 状态带：

- 市场和资产类别。
- QDII/境内/非 QDII 跨境载体。
- 预测周期、趋势和回撤状态。
- Cohort Evidence 与审计验证结果。
- 当前策略版本已绑定数量。
- Cohort Evidence 查看入口。

策略样本标题从“策略版本样本”调整为“可比 Cohort 样本”。多个 Cohort 时明确显示分开统计，禁止混合总体胜率。

## 15. 自动化测试

新增和扩展测试覆盖：

- 内地混合基金分类。
- 香港权益 QDII 分类。
- 跨境混合市场集合冻结。
- 未识别 QDII 保留但不得发布。
- 债券、商品和未知资产分类优先级。
- 信号方向、周期、基线不一致时拒绝。
- 新 Enrollment 自动创建 Cohort Evidence。
- 同一 Enrollment 幂等绑定，不重复 Evidence。
- 旧 Enrollment 回填且原信号哈希不变。
- Cohort JSON 篡改时 Provider 零调用并阻断。
- Outcome Evidence 绑定 Cohort hash。
- 单一 Cohort 达门槛后可披露。
- 香港和美国两个 Cohort 各自可披露，但总体指标保持隐藏。
- 分类扫描不完整时分组和总体指标全部隐藏。
- 公开响应移除来源载荷和用户字段。

本轮本地全量后端测试：`177 passed`。

前端生产构建：Vite 转换 `1840` 个模块并成功生成静态产物。

## 16. 真实数据验证

### 16.1 香港市场

- 基金：`013403` 华夏恒生科技 ETF 发起式联接 (QDII) C。
- 市场：`hong_kong`。
- 资产类别：`equity`。
- 载体：`qdii`。
- 周期：`6m / 126` 个确认净值。
- 信号状态：`below_ma60 + deep_drawdown`。
- Cohort 完整性和审计：通过。

### 16.2 中国内地市场

- Run：`run_e8ac37530161463798504eff1d343da8`。
- 基金：`001480` 财通成长优选混合 A。
- 市场：`mainland`。
- 资产类别：`mixed`，来源类型为“混合型-灵活”。
- 载体：`domestic`。
- 周期：`6m / 126`。
- 信号状态：`above_ma60 + deep_drawdown`。
- Cohort 完整性和审计：通过。

### 16.3 美国市场

- Run：`run_6d1f4b78506f461bb79015add12bb6e1`。
- 基金：`040046` 华安纳斯达克 100 ETF 联接 (QDII) A。
- 市场：`united_states`。
- 资产类别：`equity`，来源类型为“指数型-海外股票”。
- 载体：`qdii`。
- 周期：`6m / 126`。
- 信号状态：`above_ma60 + near_high`。
- Cohort 完整性和审计：通过。

本地策略报告最终状态：

- 3 条 Enrollment。
- 3 条 Cohort 绑定。
- 0 缺失。
- 0 Cohort 完整性失败。
- 市场分布：香港 1、内地 1、美国 1。
- 资产分布：权益 2、混合 1。
- 观察结果仍为 0，因此所有绩效指标保持隐藏。
- `cross_cohort_pooling=forbidden`。

## 17. 界面与运行验证

- 桌面宽度无横向溢出。
- 390px 移动端无横向溢出，Cohort 状态带完整换行。
- Cohort Evidence 按钮可打开真实 Evidence 详情。
- Evidence 显示 `strategy_shadow_cohort.v1` 和 SHA-256。
- 桌面和移动端控制台均无 warning/error。
- 本地数据库 `PRAGMA integrity_check=ok`。
- 本地运行日志无 `Traceback`、`ERROR` 或 `Exception`。

## 18. 已知限制

- 市场画像仍来自基金元数据和比较序列，不等于实时底层持仓。
- `fund_type` 是数据源分类，无法完全代表当前投资组合。
- 当前信号状态是基金净值趋势和回撤，不是宏观市场状态。
- Cohort 解决可比性，不解决手续费、税费、汇率归因和实际成交。
- 每个 Cohort 的 30/10 门槛是最低披露门槛，不代表统计能力一定充分。
- 不同基金即使属于同一 Cohort，也可能共享高度相关的市场风险。
- 当前报告最多扫描 2000 条；超过时 `scan_complete=false` 并拒绝披露。
- SQLite 适合当前单机部署，多实例写入前仍需迁移 PostgreSQL。

## 19. 回滚

1. 停止 Shadow Worker，避免新的 Cohort 绑定。
2. 回滚应用提交，但保留新增 Cohort 表、Evidence 和审计事件。
3. 使用部署前 SQLite 在线备份恢复数据库时，先核对备份完整性。
4. 回滚后验证旧版服务能够忽略新增表和新增 Evidence 类型。
5. 重新部署前运行全量测试、`PRAGMA integrity_check` 和审计链验证。

禁止为了回滚删除表现不佳的 Cohort 或 Outcome。追加式证据必须保留。

## 20. 下一阶段准入

在 Cohort 账本稳定运行后，下一项应优先建设策略费用与汇率执行成本模型。原因是当前 Outcome 只能证明基金净值方向和同类相对结果，仍不能回答用户实际申购后是否在费用、汇率和持有期约束下获得可实现收益。

成本模型必须继续使用真实费率、确认规则和币种数据；在数据未齐时应停止净收益结论，而不是填入行业平均值。

## 21. 生产部署验收

- 功能提交：`0f3c43e`。
- 部署目录：`/opt/stock-assistant`。
- 部署前在线备份：`/opt/stock-assistant-backups/stock_assistant-pre-comparable-cohorts-20260713T074449Z.db`。
- 备份权限：`600`。
- 备份校验：源库和备份库均为 `integrity=ok`；对象数量及 17 个 Run、62 条 Evidence、2 条 Shadow Enrollment 完全一致。
- 服务器隔离数据库全量测试：`177 passed`。
- 前端生产构建：`1840` 个模块。
- 生产依赖审计：`npm audit --omit=dev` 为 0 个漏洞。
- 开发构建链仍有上一轮记录的 2 个 Vite/esbuild 告警；生产只部署静态产物，不运行 Vite 开发服务器。
- Nginx 配置检查通过，API 与 Nginx 服务均为 active。
- 公网页面和分层报告 API 均返回 HTTP 200，并加载本次资源哈希。

### 21.1 旧样本迁移

服务启动日志：

```text
策略 Shadow Cohort 回填:created=2 failed=0
```

使用部署前备份和部署后生产库逐字段比较：

- 两条原 Enrollment 的 ID、Run ID、状态和 `signal_snapshot_sha256` 完全不变。
- 部署前 Cohort：0；部署后 Cohort：2。
- 部署前 Cohort Evidence：0；部署后 Cohort Evidence：2。
- 部署前后数据库均为 `integrity=ok`。

旧生产 Run：

| Run | Enrollment 状态 | 市场 | 资产 | 载体 | 周期 | 信号状态 | Cohort 校验 |
|---|---|---|---|---|---|---|---|
| `run_4857f68bcde94aec9c25e3ded60542e1` | `scheduled` | 香港 | 权益 | QDII | 6m/126 | MA60 下方 + 深回撤 | 通过 |
| `run_6b526785d3df40cb99128b78e3615406` | `excluded` | 香港 | 权益 | QDII | 6m/126 | MA60 下方 + 深回撤 | 通过 |

两条 Cohort 都拥有独立 Evidence、SHA-256 和唯一 `strategy.shadow.cohort.bound` 审计事件。第二条仍保持非重叠排除，没有因为新增分类被重新纳入样本。

### 21.2 生产三市场真实验证

新增两个只读研究 Run：

| Run | 基金 | 市场 | 资产 | 载体 | 信号 | Enrollment |
|---|---|---|---|---|---|---|
| `run_16f73f10549e4136b495a1d970824879` | `001480` 财通成长优选混合 A | 内地 | 混合 | 境内 | MA60 上方 + 深回撤 | `scheduled` |
| `run_d5082912f3004b3a82e66a63793b9f8f` | `040046` 华安纳斯达克 100 ETF 联接 QDII A | 美国 | 权益 | QDII | MA60 上方 + 接近高点 | `scheduled` |

两条 Run 均为 `fund_deep_research.v4`、状态 `completed`，Cohort Evidence 和审计验证通过。

### 21.3 最终生产状态

- Shadow Enrollment：4 条，其中 `scheduled=3`、`excluded=1`。
- Cohort：4 条。
- Cohort Evidence：4 条。
- Cohort 审计绑定事件：4 条。
- Cohort 缺失：0。
- Cohort 完整性失败：0。
- 市场分布：香港 2、内地 1、美国 1。
- 资产分布：权益 3、混合 1。
- Shadow Outcome Evidence：0。
- 报告协议：`strategy_shadow_report@1.1.0`。
- `cross_cohort_pooling=forbidden`。
- 汇总 metrics：`null`。
- 数据库最终 `PRAGMA integrity_check=ok`。
- 最近服务日志没有 `Traceback`、`ERROR` 或 `Exception`。

该结果证明生产系统已经真实区分 A 股相关基金、香港基金和美国 QDII，而不是把三个市场合并成一个看似更好看的策略胜率。观察窗口尚未到达，因此没有提前生成任何 Outcome 或收益结论。
