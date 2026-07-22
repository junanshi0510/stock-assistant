# 2026-07-22 更新 006：组合数字孪生与反向压力测试中心

## 1. 这次解决什么

已有的机会工厂解决“候选如何进入研究池”，成本后回测解决“历史规则在费用后是否站得住”，但用户仍缺少一个组合层决策闭环：

1. 当前真实持仓在一个明确坏情景下会损失多少；
2. 基金披露不完整会让结果多不确定；
3. 用户准备的调仓能否真的降低组合脆弱性；
4. 冲击需要恶化到什么程度才会触碰亏损预算；
5. 如果已经越线，最少需要把多少名义金额转为现金；
6. 本次计算使用了哪一版持仓、披露和投资政策，日后能否原样复核。

本次新增“我的资产 → 情景实验室”，形成以下完整链路：

```text
用户确认持仓 + 有效投资政策
              |
              v
market-data Worker 刷新真实基金披露穿透
              |
              v
冻结持仓/暴露/政策/情景并校验哈希一致性
              |
              v
当前组合 vs WHAT-IF 组合损益区间
              |
              +--> 反向压力：首次破线倍数
              +--> 脆弱性地图：持仓损失贡献
              +--> 最小降险：减持并转现金草案
              |
              v
不可更新、不可删除的组合运行历史
```

它提高的是决策可解释性、风险预算纪律和错误发现能力，不预测哪些股票一定上涨，也不承诺收益。

## 2. 同行官方产品调查

本次只用厂商官方页面做产品映射，没有依据二手营销文章猜测能力。

| 产品 | 官方能力重点 | 本项目吸收的设计 | 本项目不冒充的能力 |
|---|---|---|---|
| [Interactive Brokers Risk Navigator](https://www.interactivebrokers.com/en/trading/risk-navigator.php) | 全组合风险、持仓下钻、可编辑 What-If 组合，以及价格、日期和波动率情景 | 全组合 WHAT-IF、显式输入、调仓前后对照 | 当前没有期权全价重估、波动率曲面或券商实时保证金 |
| [BlackRock Aladdin Wealth](https://www.blackrock.com/aladdin/platforms/solutions/aladdin-wealth) / [Market-Driven Scenario](https://www.blackrock.com/aladdin/platforms/solutions/aladdin-wealth/making-of-a-market-driven-scenario) | 全组合压力分析、市场驱动情景、风险与损益解释，并把分析用于下一步沟通 | 场景库、组合级损益、下一步风险动作 | 不宣称拥有 Aladdin 的机构级风险模型、资产覆盖或历史校准 |
| [Bloomberg PORT](https://professional.bloomberg.com/products/bloomberg-terminal/portfolio-analytics/) | 组合和风险分析、情景与归因工作流 | 组合级风险工作台、暴露下钻 | 不宣称拥有 Bloomberg 数据、终端权限或机构基准库 |
| [Koyfin Portfolio Exposures](https://www.koyfin.com/help/portfolio-exposures/) / [Model Portfolios](https://www.koyfin.com/help/model-portfolios/) | 组合暴露查看、模型组合和持仓比较 | 市场/行业暴露、当前与假设组合对照 | 不把未披露基金底层仓位猜成精确暴露 |
| [Morningstar Direct](https://www.morningstar.com/business/products/direct/portfolio-management-tool) | 组合研究、比较、监控和报告 | 冻结运行、可复核历史、组合报告语言 | 不宣称拥有 Morningstar 数据许可或同类数据库 |
| [Composer Backtest Basics](https://help.composer.trade/article/67-backtest-basics) | 规则策略回测和可组合研究工作流 | 把场景、回测和纸面观察区分为不同证据层 | 压力情景不是回测，也不把说明性模板包装成历史胜率 |

同行常见模块是 What-If、压力场景、组合暴露、模型组合和监控。本项目的差异化不是单个图表，而是把下面五件事放进同一条可审计闭环：

- 基金披露缺失保留为区间，而不是静默填补；
- 反推风险预算破线阈值，而不只计算预设冲击结果；
- 在明确线性边界内求最小名义金额降险草案；
- 持仓、披露、政策、情景和结果分别哈希；
- 数据门禁失败时仍可查看研究结果，但不得标记为可用于决策。

## 3. 用户能完成的完整任务

### 3.1 编辑压力情景

内置四个说明性模板：

- 全球风险偏好收缩；
- 中国权益集中回撤；
- 美股成长估值重定价；
- 披露盲区审计。

模板全部可编辑，且响应明确返回：

```json
{
  "assumption_type": "illustrative_user_editable",
  "historical_calibration": false
}
```

用户可以设置 A 股、港股、美股、全球、未识别权益五类市场冲击，最多 12 个行业叠加冲击、30 个个券总冲击、亏损预算和最小调整金额。

### 3.2 比较调仓前后

WHAT-IF 只允许修改已经存在的非现金持仓：

- 目标非现金金额总和不得超过当前组合总金额；
- 差额自动进入零冲击现金；
- 不允许隐含杠杆、外部注资、卖空或新增陌生标的；
- 当前持仓与假设组合使用同一冻结情景和同一披露快照。

页面同时显示两边的最坏损失、最好边界、预算使用率、剩余预算和现金比例变化。

### 3.3 查看反向压力阈值

当所有市场、行业和个券冲击均不大于 0 时，各持仓最坏损益随统一倍数单调不增。系统用确定性二分求解首次触碰亏损预算的倍数，并返回该倍数对应的五类市场冲击。

如果情景含正向冲击，组合损益在封顶后可能非单调。此时返回 `unsupported_mixed_direction`，不发布看似精确但数学前提不成立的单一阈值。

### 3.4 获取最小降险草案

当 WHAT-IF 组合越过亏损预算时，系统：

1. 计算每项持仓单位名义金额对应的最坏损失率；
2. 从最坏损失率最高的持仓开始减持；
3. 将减持金额转入零冲击现金；
4. 直到回到预算内或所有可减持仓位耗尽；
5. 展示 0%、25%、50%、75%、100% 动作下的修复前沿。

只有在“一阶线性最坏损失、仅允许减持并转现金、忽略税费滑点和最小持仓”的范围内，按边际损失率排序才对应最小名义调整额。页面和响应都保留这条最优性边界，不把草案描述为个性化买卖指令。

## 4. 确定性计算口径

### 4.1 直接股票

若股票金额为 `A`，市场冲击为 `s_m`，匹配的行业叠加为 `s_i`：

```text
PnL = A × (s_m + s_i)
```

如果用户提供个券总冲击 `s_p`，它替代市场与行业组合：

```text
PnL = A × s_p
```

每项持仓总损失封顶为金额的 95%，总收益封顶为金额的 100%，防止用户输入叠加后形成无界结果。

### 4.2 基金暴露区间

基金只使用已保存的真实定期披露：

- 已识别市场金额按对应市场冲击计算；
- 未识别市场权益在基金权益下界与上界之间取区间；
- 已披露行业按用户设置的同名行业冲击叠加；
- 未分类行业权益在所有行业冲击与 0 之间取最不利/最有利归属；
- 没有披露的 Beta、行业、相关性或底层持仓不会被估算成精确值。

因此组合返回：

```text
PnL_interval = [sum(position_lower), sum(position_upper)]
uncertainty_width = upper - lower
```

区间宽度是数据不确定性的金额成本，不是置信区间或发生概率。

### 4.3 风险预算

有效亏损预算取用户本次情景与已激活投资政策的更严格者：

```text
effective_budget_pct = min(scenario_budget_pct, policy_max_drawdown_pct)
budget_amount = portfolio_total × effective_budget_pct
utilization = worst_loss / budget_amount
```

投资政策未激活时仍允许研究，但 `investment_policy_active=false`，整个运行只能标记为 `partial`。

## 5. 数据与完整性门禁

运行前通过 `portfolio.exposure_snapshot` 进入独立 `market-data` Worker，刷新并持久化基金穿透。Worker 返回后 API 再读取一次持仓；如果用户在刷新期间修改了持仓，快照哈希与当前持仓哈希不一致，运行不会获得决策资格。

完整门禁包括：

- `holdings_hash_matches`；
- `exposure_snapshot_verified`；
- `exposure_decision_eligible`；
- `investment_policy_active`。

运行记录分别保存以下 SHA-256：

- `scenario_sha256`；
- `holdings_sha256`；
- `exposure_sha256`；
- `profile_sha256`；
- `result_sha256`。

SQLite 和 PostgreSQL 都拒绝对 `portfolio_twin_runs` 执行 UPDATE 或 DELETE。列表接口只返回情景和哈希元数据，不把“列表中的部分检查”标记成完整验证；前端打开最新运行或任一历史运行后，详情接口才读取五段载荷并复算全部哈希。

## 6. 接口与持久化

新增接口：

```http
GET  /api/portfolio/decision-twin/presets
POST /api/portfolio/decision-twin/runs
GET  /api/portfolio/decision-twin/runs?limit=20
GET  /api/portfolio/decision-twin/runs/{run_id}
```

所有运行接口使用认证主体的 `subject_id` 隔离用户数据；详情不存在或不属于当前用户时统一返回 404。

新增生产迁移：

```bash
cd /opt/stock-assistant/backend
/opt/stock-assistant/venv/bin/python -m migrations.portfolio_decision_twin_v1
```

迁移在 PostgreSQL advisory transaction lock 内建立表、两个索引、不可变触发器和 `portfolio-decision-twin.v1` 标记。应用启动不会替生产数据库自动建表；缺表时 `/health/ready` 返回失败，并显示 `portfolio_twin_schema=false`。

## 7. 前端工作台

“情景实验室”包含：

- 产品边界和四类场景假设；
- 五市场冲击滑杆与精确数值输入；
- 行业叠加、亏损预算、最小调整金额；
- 每项持仓的目标金额和可选个券总冲击；
- 不可变运行历史；
- 当前/WHAT-IF 五项关键指标；
- 风险预算对照、反向压力、修复动作和修复前沿；
- 按最坏损失贡献排序的脆弱性地图；
- 证据门禁、未建模事项和数据血缘。

手机端使用单列/双列响应式网格，宽表只在自己的容器内滚动。验收中发现情景卡曾继承纵向 `flex`，使 `180px` 横向卡宽误变成卡高；已改为稳定网格，并为动态行业输入增加稳定 React key，避免输入时组件重建和焦点丢失。

## 8. 明确不做什么

当前方法版本是 `first_order_exposure_interval.v1`，不建模：

- 危机期间相关性动态变化；
- 期权、可转债和债券全价重估、久期与凸性；
- 汇率二阶传导；
- 市场冲击对流动性、点差和滑点的反馈；
- 税费、申赎限制、整手和真实成交队列；
- 压力发生概率、未来收益率或股票上涨/下跌概率。

预设冲击是可编辑研究假设，不是历史校准。降险方案不会连接券商、不会自动下单，也不能替代最新价格、交易规则和小额模拟盘复核。

## 9. 本地验收结果

- 后端全量回归：`457 passed`、`4 subtests passed`；
- 组合孪生、路由契约和市场数据网关定向回归：`21 passed`；
- 测试覆盖三市场股票精确损益、基金缺失披露区间、WHAT-IF、禁止外部注资、单调性弃权、反向压力、最小降险、用户隔离、五段哈希、SQLite 不可变触发器、API 用户作用域和 PostgreSQL 迁移契约；
- Vite 生产构建通过，`1864` 个模块完成转换；
- 本地使用独立临时 SQLite 和三项合成持仓做端到端接口验收，没有污染默认数据库；
- 第一次运行得到当前/WHAT-IF 最坏损失 `-¥11,550`；第二次把茅台目标金额从 `¥60,000` 调为 `¥30,000`，最坏损失改善至 `-¥8,550`；
- 在 8% 亏损预算下，系统给出减持腾讯 `¥3,666.67` 的线性最小降险草案，并显示 0/25/50/75/100% 修复前沿；
- 两次运行均可从不可变历史回放，旧运行仍恢复“未设置调仓假设”的原结果；
- 桌面端和 `390×844` 手机端完成交互检查，浏览器控制台无 warning/error；
- 验收后已停止自建 Vite/API 进程并删除独立临时数据库和日志。

## 10. 生产发布与回滚要求

发布顺序：

1. 记录旧提交和服务状态；
2. 执行 PostgreSQL 自定义格式备份、SHA-256 校验和私有 OSS 上传；
3. 在隔离临时库做恢复核验；
4. 拉取新提交并安装锁定依赖；
5. 执行 `migrations.portfolio_decision_twin_v1`；
6. 重建前端并保留旧静态站副本；
7. 重启 API、五个 Worker 和 Celery Beat；
8. 验证迁移标记、表、触发器、readiness、匿名 401、公网静态资产和目标测试。

代码回滚与数据回滚分开：新表是追加式不可变记录，代码回退时通常保留 PostgreSQL 数据和迁移标记；只有管理员明确决定丢弃发布后数据时才允许恢复数据库备份。旧静态站和旧 Git 提交必须使用独立目录保留，不能用未验证构建覆盖回滚点。

生产发布结果将在完成云端迁移和验收后追加到本记录，不能把“本地测试通过”写成“云端已上线”。
