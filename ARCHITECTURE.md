# 投资助手架构约定

## 业务边界

- 市场与股票：行情、个股研究、板块、多股比较和市场机会日报；跨标的批量候选统一进入带版本与纸面验证的机会工厂。
- 机会工厂：版本化候选池、持久扫描、同市场多因子、硬门槛、约束后纸面组合和冻结后的前瞻观察；收益实验室继续验证精确交易日、成本后基准超额、独立批次、跨策略统计校正和资金资格。不拥有交易权限，也不代表交易所全量扫描。
- 基金：基金发现、单只研究、同类比较、替代品和持仓重合度。
- 我的组合：用户确认的持仓、自选、OCR 导入、跨市场人民币可信估值、提醒，以及基于真实暴露区间的组合数字孪生；不拥有交易权限。
- 投资 Agent：持久化单基金 Run 与多基金 Batch、版本化只读工具、受控并发、确定性风险门禁、证据约束的模型合成和审计；不拥有交易权限。
- 身份与权限：服务端会话、管理员/用户 RBAC、个人数据隔离和认证审计；不承担市场分析或交易逻辑。
- 投资任务：确定性决策条件以及 Agent、机会工厂、收益实验室、组合情景实验室的可核验持久结果统一同步为用户级任务。用户确认不等于风险解决；只有所有相关来源完整且新证据不再触发条件时才能自动转为 `resolved`，全部状态变化进入不可变事件链。
- 决策门禁：持仓事实、当前可信估值、投资政策、持有纪律和至少一个证据完整且哈希可核验的研究结果决定 `decision_ready`；部分结果仍可进入复核任务，但不能把门禁标为就绪。纸面前瞻验证和真实执行后的账本/报告独立衡量，不反向阻塞研究。
- 信号口径：规则技术强度只描述历史价量状态，必须返回 `calibrated_probability=false` 和 `decision_eligible=false`；未经校准的规则分不得命名为上涨概率。
- 可用性控制：平台必须把“API 流量可达”“双副本冗余”“已保存事实可读”和“全部异步能力可用”分开。一个 API 副本失联只能降低冗余，不能误报全站中断；依赖故障只能关闭受影响的新鲜动作，不能把旧数据伪装为当前数据；权威数据库不可用时整体失败关闭。

## 后端

`backend/main.py` 只创建 FastAPI 应用、配置 CORS 和统一认证边界、装配路由、健康检查与指标。生产模式不在 API 进程启动数据抓取、Agent 或定时监控线程。HTTP 路由位于
`backend/routers/auth.py`、`backend/routers/market.py`、`backend/routers/funds.py`、
`backend/routers/portfolio.py`、`backend/routers/opportunities.py`、`backend/routers/availability.py` 和 `backend/routers/agent.py`。
数据抓取和分析仍位于同名领域服务模块；生产路由通过 `market_data_gateway.py` 创建 PostgreSQL 持久任务并等待 `market-data` Worker 结果，路由层只负责请求校验、错误映射和服务编排。Redis 或 Worker 不可用时明确失败，不允许回到 API 进程执行外部抓取。

`backend/auth.py` 是身份边界：密码哈希、受限流的普通用户自助注册、服务端 Session、CSRF、RBAC 和认证审计均在此实现。公开注册不能接受角色字段，管理员授权只存在于受保护的管理接口。
业务路由不得接收客户端 `user_id`，只能从 `request.state.principal.subject_id` 取得数据所有者。
普通用户按资源 ID 查询时必须同时校验所有权；管理员例外必须显式经过 `require_admin` 或等价的
服务端角色判断。前端菜单可见性只是体验层，不是授权边界。

`backend/agent/` 是独立运行时边界：`registry.py` 管理版本化工具白名单，
`workflow.py` 定义确定性工作流和执行截止时间，`repository.py` 保存 Run、Step、Evidence、
Claim 与追加式 Audit，`worker.py` 执行已领取 Run。生产由 Redis/Celery 把 Run ID 送入独立 `agent` Worker；工具输入和输出保存在 PostgreSQL，`market-data` 与 `llm` 队列消息只包含任务 ID。租约到期后 scheduler 从 PostgreSQL 恢复并重派发。SQLite 内置线程只允许本地开发和单元测试，不是生产回退路径。

`backend/opportunity_service.py` 是机会工厂的确定性编排边界，负责候选来源合并、真实数据抓取、同市场横向因子分、硬门槛、候选池市场状态、相关性约束组合和纸面观察。`opportunity_repository.py` 保存不可变策略版本、运行事件、冻结纸面组合和追加式观察点；任何结果都必须能重新校验策略、结果或事件 SHA-256。生产扫描通过 `stock_assistant.market.execute_opportunity_scan` 进入 `market-data` 队列，任务 ID 之外的完整策略和结果只保存在 PostgreSQL。候选池上限为 80，只能来自明确预设、自选、手工代码和可用热门榜；没有授权的历史全市场成分库时不得标记为交易所全量。

`backend/opportunity_profit_service.py` 是机会策略的资金资格边界。它只读取冻结纸面组合和追加式真实观察，不读取回测收益作为放行依据；A 股、港股、美股分别用 `510300`、`02800`、`SPY` 近似同市场基准，从冻结日起按第 5/20/60 个真实交易日重建固定窗口，并按政策成本压力计算净收益和净超额。每个窗口只允许冻结起点至少间隔 `ceil(N×7/5)` 个自然日的代表批次进入样本，重叠批次仍可审计但状态改为 `excluded`。平均超额同时计算普通 95% t 区间和基于历史全部已冻结策略版本数量的 Bonferroni 家族校正区间；归档或升级不能减少研究族。只有独立成熟样本、覆盖、成本后超额、胜基准比例、回撤和两层置信区间全部通过才进入 `limited_manual_pilot`，且仍只生成受 IPS、月度预算、当前可信估值、允许市场和单品上限约束的人工复核金额。

`backend/opportunity_profit_repository.py` 保存版本化收益政策与不可变收益记分卡。生产表由 `opportunity-profit-engine.v1` 在 PostgreSQL 事务和 advisory lock 中建立并拒绝 UPDATE/DELETE；纸面观察新增用户/组合级幂等键。Celery Beat 周期调用 `stock_assistant.scheduler.opportunity_observations`，调度器只创建带用户范围和日期幂等键的持久市场作业，真实行情仍由 `market-data` Worker 读取。相同行情截面不会追加重复观察，完成最大窗口后停止调度。API 和调度器均不持有券商凭据或订单能力。

`backend/portfolio_decision_twin.py` 是组合数字孪生的确定性计算边界。它只对用户确认金额、真实基金披露暴露区间和显式情景做一阶压力计算，负责当前/WHAT-IF 对照、亏损预算、单调情景反向破线、脆弱性贡献和“减持并转现金”的线性最小名义调整草案；不得补全 Beta、相关性、行业或底层持仓，也不得生成交易订单。运行前通过 `portfolio.exposure_snapshot` 进入 `market-data` Worker 刷新披露；`portfolio_twin_repository.py` 按用户保存情景、持仓、暴露、政策和结果五段哈希，生产表由独立迁移建立并拒绝 UPDATE/DELETE。列表只能返回轻量元数据，完整性通过必须在读取详情并复算五段载荷后才能成立。

`backend/portfolio_valuation.py` 是组合金额的统一事实边界。股票只使用未复权日线，基金只使用确认单位净值，外币资产按可追溯 USD/HKD→CNY 参考汇率换算；份额、价格或汇率不足时，只能显式回退最近七天的用户确认人民币金额。`portfolio_valuation_repository.py` 将价格、净值和汇率保存为跨用户可复用的公开不可变观察，并将逐用户组合保存为 `tenant_id + user_id` 隔离的不可变快照。持仓 SHA-256、有效期或载荷完整性任一失效时，运行时门禁必须关闭。组合复盘、暴露、行动报告、Agent 和数字孪生优先读取同一当前快照；门禁失败时不得继续输出超限金额、个性化分批金额或新的数字孪生运行。行动报告还必须绑定准确的估值快照 ID，不能只比较碰巧相同的总金额。精确金额门禁通过也不得产生订单权限。供应商异常在持久化前必须脱敏。

`backend/hot_stocks.py` 是热门榜与专业行情路由边界。每个市场维护有序专业路线：A 股/港股为富途 OpenD → Tushare Pro，美股为富途 OpenD → Massive 全市场日终 → Alpha Vantage 官方三榜。东方财富/Yahoo 只能作为带 `public_fallback` 和 `degraded=true` 的 best-effort 降级源，新浪不在任何读取路由中。一次市场级 bundle 同时返回所需榜单、来源等级、截止时间、时效、质量摘要、方法和逐供应商尝试，机会工厂与市场日报不得再按榜单种类重复请求。供应商缓存和熔断按 provider ID 隔离；一个源失败后可以接力下一专业源。Massive 日榜用相邻两份 grouped daily 全市场快照本地计算，7/30 日榜用 SPY 真实交易日序列定位基准，不能用自然日冒充交易日；美东 18:00 前不把当日快照当成完整 EOD，未配置 Key 时保持零网络请求。`GET /api/market/providers` 是零额度状态读取；`POST /api/market/providers/probe` 是用户显式触发、30 秒防重复且禁止公开降级的真实验证。所有异常在写入状态、日志或响应前必须脱敏。

机会综合分只在同一市场、本次候选池内计算趋势动量、估值、盈利质量、成长和风险韧性分位。缺失因子固定按中性 50 分参与综合分，并由加权覆盖率单独否决，不能把缺失权重静默分配给其他因子。历史长度、数据新鲜度、技术分、三月收益、年化波动、最大回撤、基本面可用性和综合分均为显式硬门槛；任一失败不得被其他高分抵消。组合只使用入围候选并依次应用持仓数、单股上限、现金、候选池防守状态和相关性约束；权重和历史协方差风险均由确定性代码计算，不调用大模型。

多基金金额决策由 `backend/agent/batch_allocations.py` 编排，并只调用确定性的
`portfolio_batch_allocation@1.0.0`。子 Run 的 `personalized_fund_decision@1.4.0` 在
`portfolio_batch` 作用域只保存适用性、单品/组合容量以及持仓、IPS、穿透快照绑定，不返回金额。
组合分配必须等待全部子 Run 终态，绑定整批结果哈希，使用真实年化波动、披露持仓重合下界、
单品容量和联合权益/行业最坏上界形成一次不可变事件。模型不参与数值分配，事件也不产生订单权限。

分配后的批量申购复核由 `backend/agent/batch_purchase_preflight.py` 编排，并只调用确定性的
`portfolio_batch_purchase_preflight@1.0.0`。输入必须逐只来自销售平台本次申购页，所有基金共享同一
分配预算约束。服务重新读取当前持仓、IPS、基金市场画像和组合内全部基金定期披露，投影扣除真实
申购费后的组合金额，并联合校验市场/汇率权限、单品仓位、权益与行业最坏上界。结果写入
`agent_batch_purchase_preflight_events` 追加式哈希链；报价超过 24 小时或任一绑定变化后动态失效。
该事件最多开放人工确认，不产生订单、交易授权或盈利承诺。

`backend/decision_check_worker.py` 只执行用户主动开启的组合检查计划。计划、租约、失败退避和审计事件持久化在 PostgreSQL，由 `scheduler` 队列单独执行。数据源部分失败属于可审计的 `partial`，不会触发旧风险自动解决；执行器异常才进入重试。

`backend/availability_service.py` 是应用级可用性控制边界。它主动读取两个 loopback API 副本的 readiness/release、数据库、Redis、OSS、Worker 和队列健康状态，通过 `market-data` Worker 读取供应商状态，按需执行三市场深度探测，并生成用户能力矩阵。副本端点只接受无凭据 loopback HTTP origin；一个副本失联标记冗余降低，两个都失联才关闭 API 流量能力，两个在线副本版本不一致时显式降级。固定调度探针必须按时间桶幂等，未知状态不得归入正常；队列严重积压、消费者缺失或供应商失败必须关闭受影响的新鲜动作。`availability_repository.py` 只追加探针快照和事故事件，连续失败/恢复达到阈值后才写事故转换，事件按 incident 独立哈希链接。SLO 只允许使用固定间隔 `scheduled` 样本，手动/部署探测不得进入可用率分母；API 流量使用“任一副本成功”，冗余使用“全部副本成功”，两者不能混成一个百分比。

## 运行基础设施

- Nginx 将动态请求分配到 `api-8001/api-8002` 两个 systemd 模板实例，使用最少连接和被动失败摘除。两个副本必须无状态并共享 PostgreSQL/Redis/OSS；响应携带脱敏 replica/release 身份。Nginx 不启用 `non_idempotent` 上游重放，写请求仍由事务、CSRF、唯一约束和业务幂等键保证。
- 公网负载均衡健康契约只有拓扑脱敏的 `/health/edge`；包含数据库目标、对象存储、Worker 和队列明细的 `/health/ready`、`/health/full` 只能经 loopback 访问，Prometheus 指标仍由 `/internal/metrics` 的回环 ACL 保护。
- API 使用 root 持有、应用用户只读的内容寻址 release 目录。固定槽位符号链接和前端 current 链接原子切换；滚动发布通过独立 upstream include 先主动排空目标副本，再逐副本验证目标身份与 readiness，失败时按反向顺序恢复 upstream、旧槽位和静态 release。数据库迁移必须 expand/contract 并同时兼容回退 release。
- PostgreSQL 是生产唯一事实源，保存用户、持仓、交易、不可变市场观察与组合估值、Agent、机会策略/运行/纸面观察、收益政策与记分卡、组合数字孪生运行、Evidence、任务载荷、租约、结果哈希和审计事件。应用启动不得自动运行 SQLite DDL；缺少迁移表时拒绝启动。
- `database.py` 提供 PostgreSQL 连接池和现有 Repository 的兼容接口。SQLite 只用于开发、测试和首次迁移输入，生产连接失败不得回退。
- Redis 只承担 Celery 传输；消息只含 Run ID 或 Job ID。Redis 设置 AOF 和 `noeviction`，丢失后由 PostgreSQL 中的 queued/running 租约恢复。
- `background_jobs.py` 是数据、LLM、OCR 的统一持久任务信封，输入/结果都有 SHA-256，状态变化进入不可变事件链；旧 Worker 在租约失效后不能提交结果。
- OCR 上传先在受限进程中解码、纠正方向、限制像素并剥离元数据，然后写入私有 OSS。数据库只保存对象元数据和哈希；没有 OSS 时拒绝上传，不使用本地文件兜底。
- `runtime_identity.py` 只暴露安全的 API 副本、release 和进程启动时间；`observability.py` 输出带 request/task/run/job 关联 ID 的 JSON 日志、队列与可用性 Prometheus 指标，并隐藏凭据。`health.py` 将 `/health/ready` 定义为权威数据库和全部生产 Schema 可用，将 `/health/full` 定义为 PostgreSQL、Redis、OSS 和全部队列 Worker 都可用；liveness 不探测外部依赖。systemd 严格检查要求两个副本都 ready，但 Nginx 在一个副本故障时继续服务。
- PostgreSQL 每日生成可校验自定义格式备份并上传加密 OSS；每周恢复到隔离数据库并核对表与迁移标记。SQLite 切换备份只作为回滚归档，不形成双主。

`fund_intelligence.py` 在模型调用前聚合基金披露、底层持仓行情、板块和新闻。`llm_gateway.py`
只负责调用显式批准的模型端点，`synthesis.py` 负责最小化上下文、结构化输出协议和质量门禁。
模型不直接访问工具，不重算收益、仓位或预算，也不能改变 `personalized_fund_decision` 给出的
允许动作。模型未配置、调用失败、Schema 不合法、引用不存在 Evidence 或触发安全检查时，
本轮模型输出只能是 `unavailable`，不得回退到模板化投资结论。

基金替代链路由 `fund_switch_cost_service.py`、`fund_switch_quote_service.py`、
`fund_switch_execution_service.py` 和 `fund_switch_lifecycle_service.py` 分层编排。披露成本、平台报价、
执行前审查和执行后事实分别写入独立的不可变记录；执行审查必须绑定报价事件、IPS、持有逻辑、
当前持仓和预计持仓穿透哈希。`ready_for_redemption_review` 只代表可进入人工赎回复核，不是订单授权。

执行后的 `fund_switch_lifecycle_events` 是追加式事件流，顺序固定为 `redemption_settled`、零到多次
`purchase_requoted`、`purchase_recorded`、`holdings_reconciled`、零到多次 `attribution_snapshot`。
到账事件绑定实际卖出流水和到账现金；申购事件只能绑定到账后的报价与真实买入流水；对账必须让当前
确认持仓和 FIFO 剩余份额一致。归因只比较同一真实确认净值日上的实际替换路径与继续持有反事实，
遇到来源失败、成交净值不匹配或未入账分红/拆分时关闭。流水变化通过动态完整性校验使批次失效，
不修改历史事件。任何状态都不产生订单权限。

## 前端

顶层应用只负责工作区切换。页面按工作区拆分，跨页面跳转通过有限的导航回调完成。
请求按领域放在 `frontend/src/api/market.js`、`frontend/src/api/funds.js`、
`frontend/src/api/portfolio.js`、`frontend/src/api/opportunities.js`、`frontend/src/api/availability.js` 和 `frontend/src/api/auth.js`，共享的 Cookie/CSRF 与 HTTP 错误处理放在
`frontend/src/api/client.js`。浏览器不得把会话 Token 写入 `localStorage` 或传给业务组件。
登录后的顶栏只显示脱敏平台状态；管理员控制台才可读取完整组件、事件、内部 SLO 和主动探测接口。用户界面必须把 `normal`、`read_only_degraded` 和 `unavailable` 明确区分，监控快照过期时不得继续显示绿色正常状态。
机会工厂位于 `frontend/src/tabs/OpportunityTab.jsx`，策略编辑、扫描结果、纸面跟踪和收益实验室分别由 `features/opportunities/` 下的独立组件承担。页面必须同时展示候选范围、策略版本、数据源/数据日、因子覆盖率、硬门槛原因、组合限制和纸面跟踪限制；收益实验室必须展示独立/排除批次、成本、基准、统计区间、门禁原因和自动交易关闭状态。`null` 收益不得渲染为 0，未通过资金门禁不得为了“给建议”分配金额。宽表只允许在自身容器内滚动，不能扩大手机页面宽度。
组合数字孪生位于 `frontend/src/features/portfolio/PortfolioDecisionTwin.jsx`，只能从“我的资产”进入。页面必须同时展示说明性情景边界、当前/WHAT-IF 同口径对照、反向压力前提、最小降险最优性范围、证据门禁和未建模事项；不得把预设情景描述成历史校准、把降险草案描述成订单，或在混合方向损益非单调时显示单一破线倍数。
可信估值位于 `frontend/src/features/portfolio/PortfolioValuationPanel.jsx`，必须展示基准币种、覆盖率、自动/手工方法、价格/NAV、汇率、来源日期、有效期和阻断原因。`null` 金额不得渲染为 0，手工金额不得渲染为自动估值，`trade_amount_eligible` 不得渲染为交易授权。
Agent 工作台位于 `frontend/src/tabs/AgentTab.jsx`，只通过 `/api/v1/agent/...` 读取 Batch、
Run、Evidence 和 Audit，不直接调用底层基金接口拼装“Agent 结果”。Batch 只负责原子创建、
排队、进度聚合、跨基金披露持仓重合下界和组合级资金分配复核；每只基金仍拥有独立 Run 与完整审计链。
批次预算是整批唯一总额，前端不得把它渲染为任一子 Run 的计划投入。分配快照生成后，页面必须同时
展示已分配金额、保持未投入金额、逐只容量、联合门禁和不可变快照状态。申购执行前复核必须位于
组合分配之后，逐只显示平台报价事实、费用、限额、确认日、失效时间和阻断原因，不得把录入表单
伪装成订单入口。
`AISynthesisView.jsx` 将模型合成与确定性门禁明确分层，展示提供商、模型、调用时延、Evidence
引用和私有组合是否进入模型上下文；运行历史默认折叠，避免治理信息抢占主决策路径。

## 数据原则

- 不展示伪造行情、基金净值、持仓或投资结论。
- 实时或历史源失败时，接口必须返回明确的失败原因；页面显示该错误，不能用示例数据代替。
- 专业热门榜与公开网页降级源必须明确分层；未配置、授权不足、熔断、陈旧缓存、混合计算和公开降级不得显示为“专业实时”。零额度状态读取不得回传 Key 或主动访问供应商；真实探测只能由显式 POST 触发、限制频率并禁用公开源。
- 机会扫描不得把预设、自选或热门榜称为交易所全量；每次结果必须保存实际候选来源、截断数、失败来源和未解决限制。
- 股票因子缺失不得静默重分配权重；同市场候选池分位不得包装为行业中性或全市场分位，综合分也不得覆盖数据、风险或基本面硬门槛。
- 纸面组合只能从冻结后的真实行情追加观察，不得回填冻结前表现、重写基准、推断真实成交或把行情失败股票的权重分配给成功股票。目标收益必须按冻结后的精确第 N 个交易日重建，不能把延迟补跑日冒充目标窗口。
- 同一或重叠起点的重复扫描不得增加收益验证样本数；资金资格必须使用历史全部已冻结策略版本的多重检验校正，归档失败版本不得降低校正强度。
- 收益政策只能在引擎安全底线内调整：往返成本不低于 10 bps、覆盖不低于 80%、成熟独立批次不少于 6、胜基准比例不低于 50%、批次回撤上限不高于 25%、人工试运行不高于组合 5%、最新候选不超过 30 天。通过也不构成未来收益保证或交易授权。
- 组合数字孪生不得把未披露基金权益填成精确市场或行业，不得允许 WHAT-IF 隐含杠杆、外部注资、卖空或新增未研究标的；只有非正冲击保证单调时才能输出统一反向压力倍数。运行历史的五段哈希未全部复算前不得显示完整性通过。
- 组合金额必须绑定当前持仓哈希和未过期的价格/NAV/汇率观察。刷新失败只允许仍在有效期内的不可变缓存继续服务；过期观察、绑定旧持仓、哈希失败或缺少任一持仓估值时不得开放风险门禁。
- 决策检查只有在相关来源完整时才能把缺失条件解释为风险消失；任何部分失败都必须延迟自动解决。
- 用户持仓分析仅使用用户保存或确认过的持仓记录。
- 基金换仓不得用确认净值估算替代销售平台本次赎回总额，也不得用预计赎回款垫资申购；旧报价、到账前申购报价和最终成交必须使用不同证据记录。
- 基金替换归因不得使用盘中估值、近似日期或模拟净值；实际路径和继续持有反事实必须落在同一真实确认净值日，未入账分红或拆分必须阻断归因。
- Agent 的数值 Claim 必须绑定同一 Run 内的 Evidence；来源失败时收窄结论或终止任务。
- 批次不得把单笔计划金额复制给多个子 Run；子 Run 只输出适用性与容量，最终金额只能来自绑定全部终态结果的组合级不可变分配快照。
- 组合分配缺少任一候选的真实年化波动、披露持仓覆盖、当前持仓/IPS/穿透哈希或联合权益/行业参数时必须整体阻断；未分配预算保持未投入，不为了凑满预算突破上限。
- 批量申购不得沿用基金费率页或上一版平台报价；每个已分配基金必须有本次申购页事实，并重新联合穿透申购后组合。报价、持仓、IPS 或分配变化时旧复核必须关闭。
- 生产 Worker 按 `agent`、`market-data`、`llm`、`ocr`、`scheduler` 独立队列和资源上限运行；批次大小和全局活动 Run 数分别受独立门禁控制。
- 可用性手动探测不得改变 SLO 样本；事故只能由连续观测确认，快照和事故事件不可修改或删除。未知、过期或完整性失败的健康状态不得开放新鲜动作。API 流量 SLO 与双副本冗余 SLO必须分开，不能把“仍有一个副本”写成完全冗余，也不能把“少一个副本”写成全站停机。
- 新闻、网页和 OCR 文本进入模型前一律标记为不可信外部内容，不能改变系统指令或触发工具。
- 私有组合默认不发往模型；即使部署显式开启，也只发送去标识化聚合摘要，不发送姓名、账户号或原始流水。
- 登录与 PostgreSQL 应用层用户隔离已经完成；数据库 RLS 仍是后续强化项。外部模型私人组合传输仍需独立的逐用户明示同意，未获同意时必须保持关闭。

## 迭代规则

- 新功能先归属到一个工作区，再新增对应领域接口，避免跨域堆叠在通用文件中。
- 现有 `/api/...` 路径是前后端契约；重构时应保持兼容，并先做接口回归。
- 大型页面应优先提取纯展示组件和独立数据加载钩子，不能把新的状态继续堆入单一页面文件。
