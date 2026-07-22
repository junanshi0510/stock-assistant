# 2026-07-22 更新 008：跨市场可信组合估值与决策门禁

## 1. 这次解决什么

平台此前能保存 A 股、港股、美股和基金持仓，也能做集中度、行动报告、Agent 上下文和组合压力测试，但这些功能主要依赖用户最后一次手工填写的人民币金额。它存在四个生产问题：

1. 有份额也不会自动使用确认净值或未复权收盘价重新估值；
2. 港币、美元资产没有统一、可追溯的人民币换算口径；
3. 持仓变化后，旧金额仍可能被不同分析模块继续读取；
4. 页面只能看到一个总额，无法回答“哪一个价格、哪一天、哪个来源、是否过期”。

本次增加的不是另一个股票评分，而是组合决策的事实层：

```text
用户确认持仓（份额 / 最近确认金额）
                 |
                 v
      market-data Worker 受控抓取
        |         |          |
        v         v          v
   未复权日线   确认净值   USD/HKD -> CNY
        |         |          |
        +---------+----------+
                  |
                  v
        不可变市场观察 + 来源凭据
                  |
                  v
       不可变人民币组合估值快照
                  |
          +-------+--------+
          |                |
          v                v
   覆盖率/时效门禁     持仓哈希绑定
          |                |
          +-------+--------+
                  |
                  v
 今日决策 / 组合复盘 / Agent / 暴露 / 数字孪生
```

它提高的是组合事实一致性、审计能力和风险分析可靠性，不预测未来涨跌，不授权交易，也不承诺收益。

## 2. 行情、净值与汇率口径

### 2.1 股票

- A 股、港股、美股统一读取真实未复权日线的最新收盘价，避免把复权后的历史价格直接乘当前份额；
- 数据源沿用现有专业优先路由：A/H 股优先 Tushare，美股优先 Massive 或 Alpha Vantage，并保留实际命中的来源名称；
- BaoStock、腾讯、Yahoo 和东方财富只可能作为明确标记的后备来源，不会被伪装成首选专业来源；
- 缺价或代码无法识别时不生成模拟价格。

### 2.2 基金

- 只使用基金管理人已经确认的单位净值；
- 盘中估算净值继续只用于独立的价位研究，不进入正式组合市值；
- QDII 等跨境基金仍按人民币确认净值计价，不把底层证券货币重复换算。

### 2.3 汇率

- 人民币是当前唯一组合基准货币；CNY/CNY 固定使用可审计的恒等汇率 1；
- 港股和美股优先读取 Massive 的前一日外汇聚合；
- Massive 不可用时，使用 Frankfurter 汇总的央行参考汇率，并保留首选源失败原因；
- 参考实现依据 [Massive Forex REST 文档](https://massive.com/docs/rest/forex/overview?auth=login%2Fgetting-started) 和 [Frankfurter 官方 API 说明](https://frankfurter.dev/)；参考汇率不等于券商实际结算汇率。

## 3. 持久行情观察

新增 `market_observations`：

- 类型固定为 `price`、`nav` 或 `fx`；
- 保存市场、代码、币种、值、数据日期、来源、质量等级、读取时间、过期时间和原始摘要；
- 载荷使用规范 JSON 计算 SHA-256；
- SQLite 和 PostgreSQL 都使用触发器拒绝 UPDATE/DELETE；
- 同一观察按事实身份生成稳定 ID，重复写入不会覆盖旧值；
- 强制刷新失败时，如果旧观察仍在有效期内，会显式标记 `cache_fallback_current` 并继续使用；只有真正过期的缓存才会阻断风险门禁。

所有供应商异常在写入观察、快照和 API 响应前脱敏。`apiKey`、`token`、Access Key 和已配置密钥不会进入不可变审计数据。

## 4. 不可变组合估值快照

新增 `portfolio_valuation_snapshots` 和 `portfolio_valuation_snapshot.v1`：

- 每次刷新冻结用户、执行人、基准币种、持仓 SHA-256、估值方法、逐项结果、覆盖率、门禁和有效期；
- 每项持仓保存份额、单价/NAV、原币市值、汇率、人民币市值、来源日期、观察 ID、缓存状态和问题说明；
- 当前持仓只在 SHA-256 与快照完全一致时绑定；保存或删除持仓会立即使旧快照失效；
- 快照载荷计算 SHA-256，生产表拒绝修改和删除；历史快照可以按用户读取和重新校验；
- 市场观察可跨用户复用公开行情，但组合快照查询必须同时匹配 `tenant_id + user_id`。

估值优先使用 `份额 × 确认价格/NAV × 汇率`。份额、价格或汇率不足时，只允许回退到最近七天内的用户确认人民币金额，并明确标记 `manual_confirmed_amount`。手工金额不会被包装成自动行情估值。

## 5. 三层门禁

| 门禁 | 条件 | 允许用途 |
|---|---|---|
| `allocation_eligible` | 每项持仓都有正的人民币估值 | 组合比例和集中度计算 |
| `risk_analysis_eligible` | 配置门禁通过，且没有过期价格、净值、汇率或手工金额 | 组合风险、行动报告、Agent 和数字孪生事实输入 |
| `trade_amount_eligible` | 风险门禁通过，自动估值金额覆盖至少 95%，专业/确认行情来源覆盖至少 90% | 只表示金额精度较高，仍不授权交易 |

`execution_authorized` 永远为 `false`。系统不会因为第三层门禁通过就生成订单、调用券商或替用户执行买卖。

## 6. 产品主链接入

- “我的资产”顶部新增可信估值面板，显示人民币总值、持仓覆盖、自动覆盖、专业来源覆盖、门禁、有效期和逐项来源；
- 保存或删除持仓后自动重新读取绑定状态，旧快照不会继续显示为当前；
- “今日决策”由四项决策前证据升级为五项，新增“可信估值”；有持仓但估值缺失、过期或绑定旧持仓时，决策门禁保持未就绪；
- 首页组合总额只在估值风险门禁通过时标记为“可信估值总额”，否则明确显示“用户确认总额”；
- 组合再平衡复盘、暴露快照、行动报告、Agent 组合上下文和数字孪生优先读取同一份当前估值；
- 行动报告规则升级为 `portfolio_action_rules.v3` 并绑定准确估值快照 ID；快照变化或过期后，即使人民币总额碰巧相同，旧报告也会失效；
- 再平衡、组合暴露和 Agent 个性化金额在估值门禁失败时只返回数据修复要求，不继续使用旧金额生成超限金额、分批金额或数字孪生运行；
- Agent 在有持仓但估值不可用时增加 `portfolio_valuation_not_current` 数据缺口，不允许模型把旧金额解释成当前事实。

## 7. API

| 方法 | 路径 | 作用 |
|---|---|---|
| `GET` | `/api/portfolio/valuations/latest` | 读取当前用户最新快照、持仓绑定和运行时门禁 |
| `POST` | `/api/portfolio/valuations/refresh` | 通过 `market-data` Worker 刷新观察并生成新快照 |
| `GET` | `/api/portfolio/valuations` | 按当前用户读取不可变历史 |
| `GET` | `/api/portfolio/valuations/{snapshot_id}` | 读取并校验一份当前用户快照 |

客户端不能提交 `user_id` 或伪造快照归属。生产刷新继续使用 PostgreSQL 持久任务和 Celery `market-data` 队列，Redis 不保存权威估值结果。

## 8. 数据库迁移与 readiness

新增生产迁移：

```bash
cd /opt/stock-assistant/backend
/opt/stock-assistant/venv/bin/python -m migrations.portfolio_valuation_v1
```

迁移在 PostgreSQL advisory lock 和单个事务内建立两张表、索引、不可变触发器及 `portfolio-valuation.v1` 标记。应用不会在生产启动时自动补表。`/health/ready` 新增 `portfolio_valuation_schema`；缺少任一估值表时拒绝接流量。

## 9. 测试与本地验收

自动化覆盖包括：

- A/H/美股和基金统一换算人民币；
- 手工确认金额的受限回退；
- 供应商失败、当前缓存接管和过期门禁；
- 持仓变化使快照绑定失效；
- 快照覆盖到持仓副本且不修改用户原始事实；
- API Key 在持久化前脱敏；
- 市场观察/估值快照不可更新、不可删除；
- 不同用户不能读取彼此快照；
- 决策中心、行动报告、暴露、Agent 和数字孪生兼容回归；
- 四条新 API 的 OpenAPI 路由契约。

本地最终验收为后端 `481 passed`、`5 warnings`、`4 subtests passed`；完整前端 `npm audit` 为 `0 vulnerabilities`，Vite `8.1.5` 生产构建成功并转换 `1848` 个模块。云端验收结果记录在本文“生产发布结果”，不以本地测试代替生产状态。

## 10. 明确边界

- 当前使用确认净值和日终收盘，不是券商盘中可成交余额；
- 未计入滑点、税费、停牌、申赎确认、公司行为、整手限制和券商现金余额；
- Frankfurter 是参考汇率，不是用户账户真实结汇价；
- 手工金额只能维持风险复盘连续性，不能满足精确金额门禁；
- 数据完整只说明输入可用于确定性计算，不说明某只股票会上涨；
- 平台不能保证赚钱。它能做的是减少旧数据、口径混乱和不可审计结论进入决策的概率。

## 11. 生产发布结果

### 11.1 发布与回滚点

- 功能提交 `d4b0e63` 已推送到 GitHub `main`，生产目录 `/opt/stock-assistant` 与该提交一致且工作树干净；
- 发布前 PostgreSQL 自定义格式备份为 `backups/postgresql/2026/07/stock-assistant-iZn4ai1fm0tr284w21h4kmZ-20260722T155336Z.dump`，SHA-256 为 `42b62b65a9614ecf7d82a61d6218c11aaf8f474ca0b95da7ec2ad31fc18d8993`，大小 `1,382,575` 字节，使用 AES256 保存到私有 OSS；
- 旧代码保存在 `/opt/stock-assistant-backups/releases/d4b0e63-predeploy-2fa14e0`；旧静态站和切换时静态站分别保存在 `/var/www/stock-assistant.previous-2fa14e0-before-d4b0e63` 与 `/var/www/stock-assistant.cutover-2fa14e0-to-d4b0e63`；
- 生产迁移返回 `applied portfolio-valuation.v1`，新增两张估值表和两个不可变触发器。数据库最终为 `60` 张 public 表、`5` 个迁移标记，其中估值迁移标记恰好一条。

### 11.2 服务与公网验收

- 服务器定向回归 `79 tests` 通过；生产前端 `npm ci`、`npm audit` 和 Vite 构建通过，审计结果为 `0 vulnerabilities`，共转换 `1848` 个模块；
- API、PostgreSQL、Redis、私有 OSS、scheduler、agent、market-data、llm 和 ocr 运行链路均正常，`/health/ready` 返回 ready，且 `portfolio_valuation_schema=true`；
- 公网页面和本次 CSS/主 JS/资产页/决策页静态资源均返回 `200`；未登录访问 `/api/portfolio/valuations/latest` 返回 `401`；
- 服务重启后的结构化日志没有真实 `ERROR`、Traceback 或 critical 事件。浏览器可以读取已有登录会话和生产页面；本机 VPN/Chrome 控制通道在进一步导航时持续超时，因此没有把未完成的资产页视觉导航写成通过项，视觉组件由生产静态资源检查、前端构建和 API 端到端结果共同覆盖。

### 11.3 临时账户端到端验收

临时普通用户按真实公开数据链路保存四项持仓，分别覆盖 A 股、港股、美股和基金。验收结果如下：

- 第一次和第二次刷新都生成 `complete` 快照，四项持仓均得到自动估值；自动金额覆盖 `100%`，专业/确认来源金额覆盖 `100%`，过期项为 `0`；
- 修改一项持仓金额后，第一份快照的 `holdings_binding.current` 立即变为 `false`；重新刷新后生成新的当前快照，证明决策消费者不会继续读取旧持仓绑定；
- 历史列表返回两份不可变快照，旧快照 SHA-256 完整性校验通过；再平衡读取同一估值并返回 eligible，今日决策中的可信估值阶段为 complete；
- 整体决策门禁保持未通过是预期结果，因为临时用户没有配置投资政策、持仓论点和完整研究证据；系统没有把“估值可用”错误解释为“可以买卖”；
- 验收结束后通过 `AuthService.update_user` 停用该临时用户，账户状态为 `disabled`、活跃会话为 `0`，变更进入认证审计链。

### 11.4 部署后可恢复性

最终状态再次执行生产备份和隔离恢复：

- 私有 OSS 对象：`backups/postgresql/2026/07/stock-assistant-iZn4ai1fm0tr284w21h4kmZ-20260722T161829Z.dump`；
- SHA-256：`0e466c7eb7b859a4b470d427364ee20657ba2e53e0702180285791e7f2e234f8`；
- 大小：`1,397,605` 字节；服务端加密：AES256；
- `stock-assistant-backup.service` 与 `stock-assistant-backup-verify.service` 均返回 `Result=success`、`ExecMainStatus=0`；
- 备份先通过 checksum 和 `pg_restore --list`，随后恢复到临时数据库并核对 `60` 张表、`5` 个迁移标记，临时验证库由脚本清理。

至此，本版本完成“代码提交 → GitHub → 发布前备份 → 生产迁移 → 定向测试/构建 → 静态切换 → 服务重启 → 公网门禁 → 跨市场真实账户 E2E → 账号清理 → 发布后备份与隔离恢复”的闭环。它提升了数据和决策链的可用性，但仍不授权交易、不承诺收益。
