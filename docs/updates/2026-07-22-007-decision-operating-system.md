# 2026-07-22 更新 007：统一投资决策操作系统与信号可信度治理

## 1. 这次解决什么

上一版本已经具备持仓复盘、投资政策、基金 Agent、机会工厂、纸面组合、成本后回测和组合数字孪生，但这些能力仍然像多个并排工具：

1. 只有持仓和市场日报能进入“今日决策”任务，Agent、机会扫描和压力测试各自在自己的页面结束；
2. 原证据闭环把交易账本和组合报告放在 Agent 研究之前，没有历史交易的新用户会永久卡住；
3. 技术规则分经过固定 Logistic 函数后被命名为“上涨概率”，数字看起来精确，却没有概率校准；
4. 单股机器学习在一次 70/30 历史切分后重新拟合全部数据，并发布“最新上涨概率”，没有真正未见的前瞻样本；
5. 独立批量筛选、机会工厂、基金机会雷达、关键词新闻情绪和 ML 卡片承担相近的“找机会”叙事，却没有统一验证出口。

本次不是继续增加一个页面，而是重构产品主链：

```text
真实持仓 / 投资政策 / 持有纪律
                 |
                 v
       决策前证据门槛（4 项）
                 |
       +---------+----------+
       |                    |
       v                    v
投资 Agent         机会工厂 / 组合情景实验室
       |                    |
       +---------+----------+
                 |
                 v
        统一证据源 + 持久任务收件箱
                 |
          +------+------+
          |             |
          v             v
      纸面前瞻验证   真实执行后账本/报告
```

系统提高的是证据质量、行动排序和结果可验证性，不承诺帮助用户必然盈利，也不自动下单。

## 2. 统一研究证据源

新增 `backend/decision_sources.py`，读取每位用户最新的三类持久化结果：

| 证据源 | 读取对象 | 完整性依据 | 可能生成的下一步 |
|---|---|---|---|
| 机会工厂 | 最新成功/部分成功 Run、纸面组合和观察点 | Run 结果 SHA-256、纸面快照与观察载荷哈希 | 冻结纸面组合、记录首个观察点、修复部分覆盖 |
| 投资 Agent | 最新完成/部分完成 Run | Evidence 与 Run 审计链一致性 | 人工复核结论、修复缺失 Evidence、重跑失败研究 |
| 组合情景实验室 | 最新不可变 Twin Run | 情景/持仓/暴露/政策/结果五段哈希 | 复核破线情景、查看降险草案、补齐证据门禁 |

统一返回 `decision_research_sources.v1`：

```json
{
  "status": "available",
  "sources": [
    {
      "id": "opportunity",
      "status": "succeeded",
      "ready": true,
      "evidence_status": "verified",
      "validation_state": "paper_tracking",
      "latest_run_id": "opp_run_..."
    }
  ],
  "actions": [],
  "resolution_evidence_complete": true,
  "summary": {
    "ready_source_count": 1,
    "paper_tracking_count": 1,
    "paper_pending_count": 0
  }
}
```

三个读取器独立失败隔离。一个仓库不可用时，另外两个结果仍显示；但 `resolution_evidence_complete=false`，旧风险任务不会因为本轮证据缺失而被自动解决。

## 3. 统一任务契约

高级研究结果现在与持仓和市场风险共用原有持久任务收件箱。研究动作除稳定 `id`、优先级、说明、证据和目标页面外，还增加：

```json
{
  "evidence_status": "verified",
  "validation_state": "paper_pending",
  "execution_authorized": false
}
```

规则如下：

- 机会扫描形成约束后持仓但没有纸面组合时，只要求“先冻结并观察”，不要求买入；
- Agent 结论形成后，只要求核对 Evidence、个人政策和适用范围；
- 情景亏损预算破线时进入高优先级复核，但降险草案仍不授权交易；
- 已有完整纸面观察且没有覆盖缺口时，不为了制造活跃度重复生成任务；
- 观察载荷哈希失败或只覆盖部分标的时保持“纸面观察待补齐”，不会错误点亮前瞻验证门禁；
- 任务 ID 绑定不可变 Run/Basket/Observation ID，同一证据刷新不会制造重复任务。

数据库任务表没有新增字段。本轮证据/验证标签属于当前决策响应的展示契约，原任务持久化和状态机保持兼容。

## 4. 决策闭环 v2

原 `investment_decision_workflow.v1` 是五步刚性串联：

```text
持仓 -> 政策 -> 持有纪律 -> 交易账本 -> 组合报告 -> Agent
```

它把“交易后的测量能力”错误地当成“研究前置条件”。新 `investment_decision_workflow.v2` 改为六个阶段、三个门禁：

| 阶段 | 所属门禁 | 是否阻塞个人研判 | 完成条件 |
|---|---|---:|---|
| 持仓事实 | decision | 是 | 持仓可读取且金额完整 |
| 投资政策 | decision | 是 | 用户确认版本有效、无需复核且完整性通过 |
| 持有纪律 | decision | 是 | 每项真实持仓都有活动且已验证的 Thesis |
| 研究证据 | decision | 是 | 三个研究引擎至少一个形成证据完整且哈希可核验的持久结果；部分结果只进入复核任务 |
| 前瞻验证 | validation | 否 | 最新纸面候选已有真实后续观察；无候选时为不适用 |
| 执行后复盘 | measurement | 否 | 实际有交易后，流水、现金流收益和当前报告完整 |

`decision_ready` 只由前四项决定。`validation_ready` 与 `measurement_ready` 独立返回。没有交易流水时，执行后复盘显示“当前不适用”，不再阻止用户研究；一旦真实交易发生，账本和报告缺口才成为下一步。

## 5. 技术信号可信度治理

### 5.1 删除伪概率

删除 `analysis.score_to_probability()`。原函数只是：

```text
p = 1 / (1 + exp(-0.045 * (score - 50)))
```

它没有使用真实未来标签做概率校准，不能被称为上涨概率。现在 `analysis.score()` 只返回：

- `score`：0-100 技术强度；
- `direction`：`技术偏强`、`技术中性`、`技术偏弱`；
- `signal_integrity.kind=rule_based_technical_state`；
- `calibrated_probability=false`；
- `decision_eligible=false`；
- `validation_required=true`。

该契约同步覆盖单股分析、自选、热门体检、批量 API、多股比较和机会工厂候选。

### 5.2 单股 ML 只保留历史诊断

`/api/ml` 不再使用全部已知样本重训后发布最新概率。它仍可用于开发者查看一次顺序 70/30 历史切分的准确率、AUC 和相对基准差异，但固定返回：

```json
{
  "research_status": "historical_validation_only",
  "latest_forecast_available": false,
  "calibrated_probability": false,
  "decision_eligible": false
}
```

该端点退出主研究页。原因不是机器学习必然无用，而是当前证据不足以支撑一个面向用户的最新概率产品。

### 5.3 基金分数改为候选初筛

基金“机会雷达/机会分”改名为“候选初筛/初筛强度”，字段从 `opportunity_score` 改为 `screening_score`，同时返回 `rule_based_candidate_filter` 声明。它只负责把真实榜单缩小为研究池，下一步必须进入单基金研究。

## 6. 产品入口收敛

- 删除股票工作区独立“批量筛选”视图；`/api/scan` 暂时保留为兼容研究 API，但不再输出概率；
- 跨标的候选构建统一使用机会工厂，因为它具备策略版本、硬门槛、失败隔离、组合约束、不可变结果和纸面跟踪；
- 删除单股主页面的 ML 最新预测卡片和关键词新闻情绪卡片；新闻数据仍可作为 Agent 的可引用 Evidence，不再用词典分冒充结论；
- 首页新增三张统一研究源状态卡，可直接跳转 Agent、机会工厂或组合情景实验室；
- 研究动作卡新增“证据已验证/部分证据/完整性异常”“待纸面验证/待人工复核”等标签，并固定显示“不授权交易”。

## 7. API 变化

本轮没有数据库迁移，但有以下有意的响应契约收紧：

| 接口/模块 | 删除 | 新增/替换 |
|---|---|---|
| `/api/analyze` | `probability` | `signal_integrity`，方向改为技术状态 |
| `/api/scan` | 每项 `probability` | 每项 `signal_integrity` |
| `/api/multi_compare` | 每项 `probability` | 每项 `signal_integrity` |
| `/api/ml` | `latest_up_probability` | 历史验证状态和不可决策声明 |
| 基金候选接口 | `opportunity_score` | `screening_score`、`score_integrity` |
| `/api/decision-center` | 仅持仓/市场行动 | `research`、Workflow v2、统一研究行动 |

旧前端与新后端不能混用这些已删除字段，因此生产发布必须原子切换后端与静态资源，并保留旧代码和旧静态目录用于整体回滚。

## 8. 测试与本地验收

- 新增 `test_decision_sources.py`：覆盖三个引擎汇总、非执行动作、纸面观察闭环和单仓库失败隔离；
- 新增 `test_signal_integrity.py`：覆盖规则分不含概率、单股分析 API 完整性声明和 ML 不发布最新预测；
- 更新决策中心测试：覆盖四项决策门槛、纸面/测量门禁和“没有交易历史不阻塞研究”；
- 机会工厂、决策中心和信号定向回归 `24 passed`；
- 后端全量回归 `466 passed`、`5 warnings`、`4 subtests passed`；
- Vite 从 `5.4.21` 升级到 `8.1.5`，`@vitejs/plugin-react` 升级到 `5.2.0`，移除旧开发服务器的已知路径读取风险；
- 完整 `npm audit` 为 `0 vulnerabilities`；Vite 8 生产构建通过，`1847` 个模块完成转换；
- 本地默认库只读验收成功识别：机会工厂 `succeeded`、结果哈希已验证、纸面组合 `paper_tracking`，Agent/Twin 无运行时明确显示 `empty`，没有生成虚假结果。

## 9. 明确不做什么

- 不声称技术强度能预测下一日、下一周或任意固定窗口涨跌；
- 不把历史准确率、AUC、胜率或回测收益命名为未来概率；
- 不因为 Agent、机会工厂或数字孪生完成就自动创建订单；
- 不把纸面组合表现当成用户真实收益；
- 不用一个研究源的成功掩盖另一个来源的失败；
- 不在没有交易发生时伪造账本或要求用户补录不存在的流水；
- 不承诺该系统可以稳定赚钱。它只能提高信息质量、纪律、风险识别和验证效率。

## 10. 生产发布与回滚

本轮无数据库迁移。发布仍必须先备份 PostgreSQL 并上传私有 OSS，保留旧 Git 提交与旧静态目录，然后原子更新后端和前端。验收至少包括：

1. 服务器定向测试与前端生产构建；
2. API、五个 Worker、Celery Beat、Nginx、PostgreSQL、Redis 全部健康；
3. `/health/ready` 完整通过；
4. 登录用户的决策中心返回 `research.sources` 和 Workflow v2；
5. 公网新静态资产返回 200，匿名业务 API 继续返回 401；
6. 搜索生产日志中没有新增 ERROR、Traceback 或 CRITICAL；
7. 旧静态目录和旧提交可独立回滚。

以下结果来自实际备份、推送、部署和公网验收，不以本地测试替代生产验证。

## 11. 生产发布结果

功能提交 `17e2df1` 与前端工具链安全升级提交 `0f00a1e` 已推送 GitHub `main` 并发布到 `http://8.148.67.79/`。本轮没有数据库迁移。

发布前保护：

- 旧提交为 `1ac1fa4dcfe78fd7897cae212bf925ec023d6650`，工作树干净，根分区剩余约 41 GiB；
- API、Nginx、PostgreSQL、Redis、Celery Beat 和五个队列 Worker 在发布前均为 `active`，readiness 为 `ready=true`；
- PostgreSQL 自定义格式备份大小为 `1,367,507` 字节，以 AES256 上传私有 OSS：`backups/postgresql/2026/07/stock-assistant-iZn4ai1fm0tr284w21h4kmZ-20260722T093134Z.dump`；
- 备份 SHA-256 为 `45a5f48677314e6961acf4ccf8e739da5b727ee03bffb1535ee21f2c4c41d28c`，隔离恢复实查通过，核对到 58 张表和 4 个迁移标记；
- 旧代码归档位于 `/opt/stock-assistant-backups/releases/17e2df1-predeploy-1ac1fa4`，Vite 安全升级前代码归档位于 `/opt/stock-assistant-backups/releases/0f00a1e-predeploy-17e2df1`；
- 静态回滚点包括 `/var/www/stock-assistant.previous-1ac1fa4-before-17e2df1`、`/var/www/stock-assistant.cutover-1ac1fa4-to-17e2df1`、`/var/www/stock-assistant.previous-17e2df1-before-0f00a1e` 和 `/var/www/stock-assistant.cutover-17e2df1-to-0f00a1e`。

服务器与公网验收：

- 服务器使用生产虚拟环境的 `unittest` 执行统一研究源、信号完整性、决策中心和机会工厂共 24 项目标测试，全部通过；
- 云端 Node `20.20.2` 满足 Vite 8 引擎要求；`npm ci`、完整 `npm audit` 和 Vite `8.1.5` 构建成功，审计为 `0 vulnerabilities`，构建完成 1847 个模块转换；
- 生产真实库只读探针返回 `decision_research_sources.v1`、`status=available`、`resolution_evidence_complete=true`；被抽查活动账户如实显示机会工厂 `empty`、Agent `partial`、组合情景实验室 `empty`，没有把部分结果伪装成 ready；
- 探针生成的研究复核动作全部为 `execution_authorized=false`。为避免提取登录密钥或污染真实任务状态，本次没有伪造生产登录会话，也没有在真实账户下创建测试 Run；
- API、五个 Worker、Celery Beat、Nginx、PostgreSQL、Redis 全部 `active`，失败单元数为 0；readiness 同时确认 PostgreSQL、Redis、私有 OSS、机会工厂 Schema、组合孪生 Schema 与五个队列 Worker 全部 ready；
- 公网首页、主包 `index-BWMcq6_k.js` 和决策中心分包 `DashboardTab-CbcRSvQY.js` 均返回 200，匿名访问 `/api/decision-center` 返回 401；
- 发布窗口内 API 与全部 Worker 日志没有新增 `ERROR`、`Traceback` 或 `CRITICAL`。
