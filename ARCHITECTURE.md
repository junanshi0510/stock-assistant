# 投资助手架构约定

## 业务边界

- 市场与股票：行情、个股研究、板块、批量筛选、多股比较和市场机会日报。
- 基金：基金发现、单只研究、同类比较、替代品和持仓重合度。
- 我的组合：用户确认的持仓、自选、OCR 导入与提醒。
- 投资 Agent：持久化研究任务、版本化工具、证据引用和审计；当前只允许公共只读基金数据。

## 后端

`backend/main.py` 只创建 FastAPI 应用、配置 CORS、装配路由和启动监控。HTTP 路由位于
`backend/routers/market.py`、`backend/routers/funds.py`、`backend/routers/portfolio.py` 和
`backend/routers/agent.py`。
数据抓取、分析和存储仍位于同名领域服务模块；路由层只负责请求校验、错误映射和服务编排。

`backend/agent/` 是独立运行时边界：`registry.py` 管理版本化工具白名单，
`workflow.py` 定义确定性工作流和执行截止时间，`repository.py` 保存 Run、Step、Evidence、
Claim 与追加式 Audit，`worker.py` 负责恢复和领取任务。当前 Worker 只适合单实例迁移期部署；
扩展到多实例前必须迁移到 PostgreSQL 和 Temporal，不能把进程内线程当作最终调度系统。

## 前端

顶层应用只负责工作区切换。页面按工作区拆分，跨页面跳转通过有限的导航回调完成。
请求按领域放在 `frontend/src/api/market.js`、`frontend/src/api/funds.js`、
`frontend/src/api/portfolio.js`，共享的 HTTP 错误处理放在 `frontend/src/api/client.js`。
Agent 工作台位于 `frontend/src/tabs/AgentTab.jsx`，只通过 `/api/v1/agent/...` 读取 Run、
Evidence 和 Audit，不直接调用底层基金接口拼装“Agent 结果”。

## 数据原则

- 不展示伪造行情、基金净值、持仓或投资结论。
- 实时或历史源失败时，接口必须返回明确的失败原因；页面显示该错误，不能用示例数据代替。
- 用户持仓分析仅使用用户保存或确认过的持仓记录。
- Agent 的数值 Claim 必须绑定同一 Run 内的 Evidence；来源失败时收窄结论或终止任务。
- 未完成登录与租户隔离前，Agent 不得注册私人持仓、OCR、交易或写操作工具。

## 迭代规则

- 新功能先归属到一个工作区，再新增对应领域接口，避免跨域堆叠在通用文件中。
- 现有 `/api/...` 路径是前后端契约；重构时应保持兼容，并先做接口回归。
- 大型页面应优先提取纯展示组件和独立数据加载钩子，不能把新的状态继续堆入单一页面文件。
