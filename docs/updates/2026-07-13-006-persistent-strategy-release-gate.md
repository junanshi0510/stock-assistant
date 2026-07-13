# 持久化策略注册与运行时发布门禁

## 1. 更新信息

- 更新编号：`2026-07-13-006`
- 功能：持久化策略注册表、生命周期审计、Shadow 隔离和运行时发布门禁
- 策略门禁：`strategy_release_gate@1.0.0`
- 工具：`strategy.release.check@1.0.0`
- 个性化决策：`personalized_fund_decision@1.3.0`
- Agent 结果：`fund_deep_research.v4`
- 风险级别：R0 读取策略治理状态；R1 确定性个人决策，不下单
- 数据原则：不伪造评测报告、不伪造审批、不把少量 Outcome 包装成策略胜率

## 2. 为什么本轮必须做这个功能

上一轮已经能按来源原生同类平均评估基金结果，但生产中只有极少 Outcome，尚不能回答一个策略是否稳定创造超额收益。

如果此时直接实现“策略胜率榜”“自动淘汰”或“收益排名”，会产生三个严重问题：

1. 样本量不足，统计结果没有代表性。
2. 非方向性动作、用户未执行动作和策略信号可能被混为实际交易结果。
3. 同一个基金、相邻日期和重叠前瞻窗口高度相关，不能当作独立样本。

但当前代码中的 `fund_conditioned_forward_return@1.0.0` 已经可能影响个性化金额决策。该策略明确存在：

- 没有冻结的独立样本外区间。
- 3、6、12 个月前瞻窗口可能重叠。
- 只比较基金自身无条件历史分布，不是可投资同类基准。
- 没有申购、赎回、汇率和机会成本模型。
- 没有足够的按策略版本分组的 Shadow Outcome。
- 没有独立 Reviewer 的生产发布审批。

因此当前最必要的不是给策略加一个好看的评分，而是确保未经验证的策略不能进入用户金额决策。这个门禁是继续做样本外评测、Shadow、Canary、漂移监测和策略淘汰的前置基础。

## 3. 本轮明确不做什么

### 3.1 不做虚假的策略表现分数

当前没有足够生产样本，不计算“策略胜率”“年化超额”“最大回撤”或“盈利概率”。缺少的数据保持失败发布检查，不使用演示数字补齐。

### 3.2 不把现有策略标为 Active

当前历史条件策略迁移为 `shadow`。它仍可计算和展示研究统计，但 `decision_use_allowed=false`，不得生成新增投入金额。

### 3.3 不开放公网策略写接口

当前项目没有完整身份认证、RBAC 和管理员会话。策略注册和状态迁移只允许登录服务器后通过 CLI 操作；公网 API 只读。

### 3.4 不自动交易

本轮没有券商连接、基金申赎或资金写入能力。策略状态只控制研究结果能否进入个人决策规则。

## 4. 持久化数据模型

### 4.1 `agent_strategy_versions`

每个精确策略版本保存：

| 字段 | 含义 |
|---|---|
| `strategy_id` | 稳定的小写策略 ID |
| `strategy_version` | 语义化版本 |
| `name` | 策略名称 |
| `strategy_kind` | 策略类型，例如 `alpha_signal` |
| `owner_id` | 策略负责人 |
| `status` | 当前生命周期状态 |
| `previous_status` | 上一个状态 |
| `manifest_json` | 不可变策略清单 |
| `manifest_sha256` | 规范 JSON 的 SHA-256 |
| `registered_at` | 注册时间 |
| `status_updated_at` | 状态更新时间 |

`strategy_id + strategy_version` 唯一。同版本清单哈希发生变化时，应用拒绝启动注册，不允许静默覆盖。

### 4.2 `agent_strategy_audit_events`

每个策略版本拥有独立追加式哈希链，记录：

- 注册事件。
- 原状态和目标状态。
- 操作角色和操作人。
- 迁移原因。
- 迁移时发布门禁版本和通过数量。
- 前一事件哈希与当前事件哈希。

运行时同时验证：

1. 每个事件哈希正确。
2. 事件序号连续。
3. 前后哈希连续。
4. 注册事件中的清单哈希等于当前清单哈希。
5. 从审计事件重放得到的状态等于数据库当前状态。

直接修改数据库状态、清单或审计事件都会导致运行门禁失败。

## 5. 策略清单契约

`strategy_manifest.v1` 至少包含：

- 策略 ID、版本、名称、类型和负责人。
- 适用资产、市场、频率和用户场景。
- 依赖工具、最短历史和必需字段。
- 方法、参数和已知限制。
- 必须通过的发布检查。
- 每项检查的真实状态、说明和证据引用。
- Canary 百分比或明确用户范围。
- 回滚版本。

策略 ID 必须是 3-128 位小写稳定标识，版本必须是语义化版本。新版本只能以 `draft` 或迁移期 `shadow` 注册，不能直接注册为 `canary` 或 `active`。

## 6. 生命周期状态机

```text
draft -> review -> shadow -> canary -> active
   \        \         \         \        \
    paused / retired   paused    paused    paused

paused -> shadow -> canary -> active
retired -> 终态
```

约束：

- `draft -> review`：owner 或 strategy_manager。
- `review -> shadow`：reviewer 或 strategy_manager。
- `shadow -> canary`：必须全部发布检查通过，只允许 reviewer，且 reviewer 不能是负责人。
- `canary -> active`：同样要求独立 reviewer 和全部检查通过。
- `active/canary/shadow -> paused`：operator、reviewer 或 strategy_manager 可紧急暂停。
- `paused -> shadow`：恢复后必须重新经过 Shadow 和 Canary，不允许直接回到 Active。
- `retired`：不可恢复。
- 每次迁移要求 `expected_status`，并在事务内比较，防止并发管理员覆盖彼此操作。
- 迁移原因至少 12 个字符，禁止无说明操作。

## 7. 发布检查

当前 Alpha 信号策略要求六项检查：

| 检查 | 当前状态 | 原因 |
|---|---|---|
| 独立样本外区间 | fail | 使用同一基金历史匹配，没有冻结 OOS |
| 非重叠评测窗口 | fail | 前瞻窗口可能重叠 |
| 可投资同类基准 | fail | 当前只比较自身无条件分布 |
| 交易成本模型 | fail | 没有申赎、汇率和机会成本 |
| 最小 Shadow Outcome | fail | 生产样本不足 |
| 独立策略评审 | fail | 尚无发布审批事件 |

因此当前 `passed_check_count=0`、`required_check_count=6`、`release_ready=false`，状态为 `shadow`。

发布检查写入不可变版本清单。评测方法或结果变化必须产生新策略版本，不能修改旧版本使其“变绿”。

## 8. 运行时精确版本门禁

Agent 工作流在基金分析后执行 `strategy.release.check@1.0.0`，输入包括：

- 精确策略 ID 和版本。
- 资产类型。
- 已识别市场。
- 用户场景。
- 用户 ID，用于确定性 Canary 分桶。

输出 `strategy_runtime_gate.v1`，包含：

- 注册状态和生命周期状态。
- 清单哈希。
- 清单、状态和审计链完整性。
- 发布检查明细。
- 适用范围判断。
- Canary 固定分桶。
- 是否允许计算。
- 是否允许影响用户决策。
- 明确的拒绝原因代码。

只有以下条件全部成立时 `decision_use_allowed=true`：

1. 精确版本已注册。
2. 清单哈希正确。
3. 审计链和当前状态绑定正确。
4. 所有必需发布检查为 `pass`。
5. 资产、市场和用户场景适用。
6. 状态为 `active`，或状态为 `canary` 且用户命中固定灰度范围。

系统不查找最近版本、不回退旧版、不把其他策略当替代。

## 9. 失败关闭规则

| 情况 | 计算 | 影响金额决策 |
|---|---|---|
| unregistered | 禁止 | 禁止 |
| draft/review | 禁止 | 禁止 |
| shadow | 允许保留研究 Evidence | 禁止 |
| canary 未命中 | 允许 Shadow 观察 | 禁止 |
| active 且检查通过 | 允许 | 允许进入后续 IPS/组合门禁 |
| paused/retired | 禁止 | 禁止 |
| 清单哈希失败 | 禁止 | 禁止 |
| 审计链或状态绑定失败 | 禁止 | 禁止 |
| 适用范围不匹配 | 禁止 | 禁止 |

即使策略发布通过，也只是允许进入后续个人风险门禁，不代表自动形成买入结论。

## 10. Agent 工作流变化

基金深度研究现在依次形成：

1. 真实基金分析 Evidence。
2. 真实基金市场画像 Evidence。
3. 用户确认组合和 IPS Evidence。
4. 不可变组合穿透 Evidence。
5. 策略发布治理 Evidence。
6. 个性化决策 Evidence。

治理 Evidence 类型为 `governance`，包含运行时发布快照。个性化决策 Evidence 同时引用基金分析、市场、组合、穿透、治理和自身六项 Evidence。

持久化步骤恢复也被收紧：只有步骤 Key、工具名、工具版本和规范输入全部一致时才能复用旧 Evidence；工作流升级后遇到旧步骤契约会拒绝跨版本复用。

## 11. 个性化决策 1.3.0

新增 `strategy_release` 硬门禁。

当前策略为 Shadow 时：

- 研究统计仍显示。
- 决策状态为 `abstained`。
- 动作为 `strategy_not_released`。
- `allowed_full_amount=null`。
- `first_tranche_amount=null`。
- IPS、市场、汇率、回撤、单品、权益和行业风险门禁仍全部计算并显示。

历史 `v3/1.2.0` Run 不修改原 Evidence。前端发现历史正向动作没有治理快照时，会显示“历史金额建议已停用”并隐藏正向金额，防止旧结果绕过新门禁。

## 12. API

新增只读接口：

- `GET /api/v1/agent/strategies`
- `GET /api/v1/agent/strategies/{strategy_id}/{strategy_version}`

详情接口包含清单状态、发布检查和生命周期审计链验证。没有公网 POST、PUT、PATCH 或 DELETE 策略接口。

工具目录新增：

- `strategy.release.check@1.0.0`

## 13. SSH 运维命令

入口：

```bash
cd /opt/stock-assistant/backend
/opt/stock-assistant/venv/bin/python strategy_admin.py list
/opt/stock-assistant/venv/bin/python strategy_admin.py show fund_conditioned_forward_return 1.0.0
/opt/stock-assistant/venv/bin/python strategy_admin.py verify fund_conditioned_forward_return 1.0.0
```

从结构化 JSON 注册新 Draft：

```bash
/opt/stock-assistant/venv/bin/python strategy_admin.py register /secure/path/manifest.json \
  --actor strategy-owner
```

紧急暂停示例：

```bash
/opt/stock-assistant/venv/bin/python strategy_admin.py transition \
  strategy_id 1.2.3 \
  --expected-status active \
  --to paused \
  --actor-role operator \
  --actor oncall-name \
  --reason "线上表现异常，立即暂停新增策略调用"
```

CLI 依赖服务器 OS/SSH 权限。多管理员正式上线前仍需接入统一 IAM、MFA、RBAC 和审批系统。

## 14. 前端变化

策略区新增：

- 生产发布状态。
- 发布检查通过数。
- 是否允许进入个人决策。
- 明确拒绝原因。
- 独立“治理 Evidence”入口。

个人决策区新增策略版本、状态、检查数量和金额使用权限。Shadow 使用警示色，不把研究方向显示为正式发布信号。

## 15. 测试覆盖

本轮新增或强化：

- 默认策略以 Shadow 幂等迁移。
- 同版本清单哈希变化拒绝。
- 直接注册 Active 拒绝。
- 未注册策略默认拒绝。
- 资产或市场范围不匹配拒绝。
- 发布检查失败不能进入 Canary。
- 负责人不能审批自己的 Canary/Active。
- Canary 固定用户范围。
- Active 发布完整状态链。
- Operator 紧急暂停立即生效。
- 清单、审计事件和当前状态直接篡改均拒绝。
- Shadow 形成不可变 Governance Evidence。
- Shadow 不能产生金额。
- 已发布测试策略下原 IPS、权益和行业门禁继续回归。
- API 路径契约包含新增只读接口。
- 公网策略审计事件隐藏操作人标识和内部原因，但保留状态迁移与链上哈希。
- 工具超时、取消、幂等、Outcome 和调度器回归。

本地全量后端结果：

```text
Ran 145 tests in 6.897s
OK
```

本地前端生产构建：

```text
1840 modules transformed
build passed in 2.73s
```

本地真实工作流与界面验收：

- 使用真实基金 `013403` 生成 `fund_deep_research.v4` Run，策略状态为 Shadow，金额决策为拒绝。
- Governance Evidence 的 Schema、有效时间、质量和 SHA-256 可在前端独立查看。
- 桌面端 `1280x900` 和移动端 `390x844` 无横向溢出，发布检查和操作按钮会响应式换行。
- 组合快照明确区分“可用于决策”与“哈希有效，数据不完整”，不把哈希正确误表述为数据完整。
- 浏览器 Console 无 error 和 warning。

## 16. 安全与隐私

- 策略读取不访问用户持仓内容。
- Canary 只保存确定性分桶结果，不返回其他用户信息。
- 公网 API 只读。
- 公网审计投影不返回 `actor_id` 和原始变更原因；服务端仍使用完整原始事件校验哈希链。
- 策略状态写入要求 SSH/OS 权限。
- 所有状态变更追加审计，不更新旧事件。
- 没有自动交易或资金写权限。
- 当前公网 HTTP、无登录和单用户存储仍是正式多人开放的阻塞项。

## 17. 已知限制

1. 当前注册表保存在 SQLite；公开生产规模需要迁移 PostgreSQL、RLS 和独立 Strategy Service。
2. 当前 CLI 操作人身份依赖服务器账户，尚未接入企业 IAM。
3. 只有历史条件 Alpha 策略纳入本轮注册门禁；风险控制策略、模型和提示词需要后续逐项治理。
4. 当前没有足够 Shadow Outcome，不能发布任何 Alpha 策略。
5. 暂停开关在下一次运行时生效，不会终止已经完成的历史 Run；历史正向金额由前端停用显示。
6. 数据源商业授权仍需单独建立授权台账，策略发布通过不代表数据授权通过。

## 18. 回滚

1. 回滚应用代码到上一稳定提交。
2. 不删除 `agent_strategy_versions` 和 `agent_strategy_audit_events`。
3. 已形成的 Governance Evidence 保持不可变。
4. 回滚后旧代码不会读取新增表，但历史状态和审计仍保留。
5. 恢复新版本时重新验证清单哈希、状态重放和审计链。
6. 不把 Shadow 状态改为 Active 作为回滚手段。

## 19. 生产部署与验证

本节在云端部署完成后追加真实备份、迁移、构建、服务、策略状态、真实 Run、Evidence、审计和日志验证结果。
