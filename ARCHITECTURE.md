# 投资助手架构约定

## 业务边界

- 市场与股票：行情、个股研究、板块、批量筛选、多股比较和市场机会日报。
- 基金：基金发现、单只研究、同类比较、替代品和持仓重合度。
- 我的组合：用户确认的持仓、自选、OCR 导入与提醒。
- 投资 Agent：持久化单基金 Run 与多基金 Batch、版本化只读工具、受控并发、确定性风险门禁、证据约束的模型合成和审计；不拥有交易权限。
- 身份与权限：服务端会话、管理员/用户 RBAC、个人数据隔离和认证审计；不承担市场分析或交易逻辑。

## 后端

`backend/main.py` 只创建 FastAPI 应用、配置 CORS 和统一认证边界、装配路由并启动监控。HTTP 路由位于
`backend/routers/auth.py`、`backend/routers/market.py`、`backend/routers/funds.py`、
`backend/routers/portfolio.py` 和 `backend/routers/agent.py`。
数据抓取、分析和存储仍位于同名领域服务模块；路由层只负责请求校验、错误映射和服务编排。

`backend/auth.py` 是身份边界：密码哈希、受限流的普通用户自助注册、服务端 Session、CSRF、RBAC 和认证审计均在此实现。公开注册不能接受角色字段，管理员授权只存在于受保护的管理接口。
业务路由不得接收客户端 `user_id`，只能从 `request.state.principal.subject_id` 取得数据所有者。
普通用户按资源 ID 查询时必须同时校验所有权；管理员例外必须显式经过 `require_admin` 或等价的
服务端角色判断。前端菜单可见性只是体验层，不是授权边界。

`backend/agent/` 是独立运行时边界：`registry.py` 管理版本化工具白名单，
`workflow.py` 定义确定性工作流和执行截止时间，`repository.py` 保存 Run、Step、Evidence、
Claim 与追加式 Audit，`worker.py` 负责恢复和领取任务。当前 Worker 只适合单实例迁移期部署；
扩展到多实例前必须迁移到 PostgreSQL 和 Temporal，不能把进程内线程当作最终调度系统。

`fund_intelligence.py` 在模型调用前聚合基金披露、底层持仓行情、板块和新闻。`llm_gateway.py`
只负责调用显式批准的模型端点，`synthesis.py` 负责最小化上下文、结构化输出协议和质量门禁。
模型不直接访问工具，不重算收益、仓位或预算，也不能改变 `personalized_fund_decision` 给出的
允许动作。模型未配置、调用失败、Schema 不合法、引用不存在 Evidence 或触发安全检查时，
本轮模型输出只能是 `unavailable`，不得回退到模板化投资结论。

## 前端

顶层应用只负责工作区切换。页面按工作区拆分，跨页面跳转通过有限的导航回调完成。
请求按领域放在 `frontend/src/api/market.js`、`frontend/src/api/funds.js`、
`frontend/src/api/portfolio.js` 和 `frontend/src/api/auth.js`，共享的 Cookie/CSRF 与 HTTP 错误处理放在
`frontend/src/api/client.js`。浏览器不得把会话 Token 写入 `localStorage` 或传给业务组件。
Agent 工作台位于 `frontend/src/tabs/AgentTab.jsx`，只通过 `/api/v1/agent/...` 读取 Batch、
Run、Evidence 和 Audit，不直接调用底层基金接口拼装“Agent 结果”。Batch 只负责原子创建、
排队、进度聚合和跨基金披露持仓重合下界；每只基金仍拥有独立 Run 与完整审计链。
`AISynthesisView.jsx` 将模型合成与确定性门禁明确分层，展示提供商、模型、调用时延、Evidence
引用和私有组合是否进入模型上下文；运行历史默认折叠，避免治理信息抢占主决策路径。

## 数据原则

- 不展示伪造行情、基金净值、持仓或投资结论。
- 实时或历史源失败时，接口必须返回明确的失败原因；页面显示该错误，不能用示例数据代替。
- 用户持仓分析仅使用用户保存或确认过的持仓记录。
- Agent 的数值 Claim 必须绑定同一 Run 内的 Evidence；来源失败时收窄结论或终止任务。
- 批次不得把单笔计划金额复制给多个子 Run；跨基金重合只计算已获取披露持仓的下界，不填补未知持仓。
- 内置 Worker 默认 2 路并发且硬限制为 4 路；批次大小和全局活动 Run 数分别受独立门禁控制。
- 新闻、网页和 OCR 文本进入模型前一律标记为不可信外部内容，不能改变系统指令或触发工具。
- 私有组合默认不发往模型；即使部署显式开启，也只发送去标识化聚合摘要，不发送姓名、账户号或原始流水。
- 登录与当前 SQLite 用户隔离已经完成，但外部模型私人组合传输仍需独立的逐用户明示同意；未获同意时必须保持关闭。

## 迭代规则

- 新功能先归属到一个工作区，再新增对应领域接口，避免跨域堆叠在通用文件中。
- 现有 `/api/...` 路径是前后端契约；重构时应保持兼容，并先做接口回归。
- 大型页面应优先提取纯展示组件和独立数据加载钩子，不能把新的状态继续堆入单一页面文件。
