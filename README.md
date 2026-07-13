# 金融投资助手

一个面向个人投资者的真实数据决策工作台，覆盖公募基金、A 股、港股、美股和用户真实持仓。

当前版本使用 **React + Vite** 构建前端，使用 **FastAPI + Python** 完成数据获取、基金与股票研究、组合账本、确定性计算和证据约束的大模型合成。项目正在按照工业级投资 Agent PRD 逐步升级；当前已经具备可恢复 Run、真实工具、Evidence、风险门禁和可选 LLM 研判，但仍不具备自动交易能力，也不把模型文本当作确定性计算结果。

> 风险提示：系统输出用于研究和风险复盘，不代表未来涨跌，不构成投资建议，也不承诺收益。数据源不可用、数据过期或用户持仓不完整时，系统必须明确显示缺口，不使用模拟数据补齐。

## 最近更新

### 2026-07-13：五阶段决策闭环与四工作区重构

- 首页从功能集合改为固定决策顺序：持仓事实、投资政策、持有纪律、交易账本、组合报告。每个阶段只使用真实数据或用户确认版本，并明确显示已完成、待完成、等待前序和暂不可用。
- 后端新增 `investment_decision_workflow.v1`。只有持仓金额、已激活投资政策、全部持仓纪律、真实交易收益口径和当前有效组合报告依次通过后，才把闭环标记为可用于 Agent 研判。
- 首页只展示一个当前下一步和按风险排序的行动清单；投资政策完整编辑器迁入“我的资产”，不再把大型表单、市场榜单和组合信息同时堆在首页。
- 顶层导航收敛为“今日决策、我的资产、研究中心、投资 Agent”。基金、股票与板块、策略验证统一归入研究中心，持仓、政策、账本和观察清单统一归入我的资产。
- 投资政策编辑器按目标与承受能力、组合风险边界、市场范围与确认分组，继续保留不可变版本、适当性校验、激活确认和哈希审计，不把页面初始值当成用户政策。
- 完成桌面端和 390px 手机端验证，修复手机端主导航截断；后端全量回归 214 项通过，前端生产构建通过。

### 2026-07-13：版本化持有逻辑与退出纪律

- 每只基金或股票可在持仓详情中保存组合角色、买入与持有逻辑、计划持有月数、复核日期、最大可接受持仓亏损、最大可接受标的回撤、允许新增的前提和退出条件。
- 每次保存都创建不可变版本，记录内容 SHA-256、前一版本 ID 与前一版本哈希；SQLite 触发器禁止修改或删除历史版本。
- 持有逻辑绑定具体持仓记录 ID。删除后重新添加同代码资产时，旧计划不会被误用。
- 组合行动报告升级到 `portfolio_action_report.v2`，使用用户确认的持仓收益率和基金真实净值回撤检查预设边界，区分计划缺失、复核到期、纪律边界触发和计划内持有。
- 自由文本加仓与退出条件始终标记为人工核对，不冒充机器已验证信号，也不会自动下单。逻辑版本变化会立即使旧行动报告失效。
- 新增 `GET/POST /api/portfolio/theses` 和单资产版本历史接口；桌面端及 390px 手机端无横向溢出。
- 本次后端全量回归 212 项通过，前端生产构建和浏览器控制台检查通过。

### 2026-07-13：DeepSeek 模型接入

- LLM Gateway 新增一等供应商 `deepseek`，支持专用 `DEEPSEEK_API_KEY`、官方 `https://api.deepseek.com` 端点和 Chat Completions JSON Output。
- 部署默认推荐 `deepseek-v4-flash`；需要更深推理时可选择 `deepseek-v4-pro`。模型 ID 必须显式配置，不会静默切换模型。
- DeepSeek 默认关闭思考模式以控制批量基金任务的延迟，可通过 `LLM_THINKING_MODE=enabled` 开启，并用 `LLM_REASONING_EFFORT=high|max` 控制推理强度。
- 网关会确保提示中包含 JSON 输出要求，并对 HTTP 429/5xx、连接异常、非 JSON 响应和成功但空内容做限定次数重试；失败后明确返回不可用，不生成兜底研判。
- API Key 只从服务器环境读取，模型状态、Run 结果、Evidence 和审计事件均不返回密钥。个人组合上下文仍默认禁止发送给外部模型。
- 本次后端全量回归 205 项通过，前端生产构建通过。

### 2026-07-13：投资 Agent 批量基金研究

- Agent 新增父级 Batch，单次支持 2-6 只不同基金；批次和全部子 Run 在同一个 SQLite 事务内创建，重复提交由 Batch 级 `Idempotency-Key` 去重。
- 新增 `POST/GET /api/v1/agent/batches`、`GET /api/v1/agent/batches/{batch_id}` 和批次取消接口。每只基金继续使用原有 Run、Step、Evidence、Claim 与追加式审计链，不复制或弱化单基金证据契约。
- 内置 Worker 支持受控并发，默认 2 路、最多 4 路；2 核服务器可让多个真实数据任务并行推进，同时保留全局活动任务上限和单批大小门禁。
- 批次结果逐行展示基金状态、市场、风险、研究动作、近一年收益、当前回撤、新闻与模型覆盖；点击任一行进入该基金完整 Evidence、Audit 和 Outcome 详情。
- 跨基金重合度只使用本批次成功获取的前 N 大真实披露持仓，按共同持仓较小净值占比求和并标记为“重合下界”；未披露、未覆盖和披露日期差异不推断。
- 批次不会把同一笔计划金额复制给多只基金，也不会把缺失模型结果用模板补齐。金额仍由每只基金的个人组合风险门禁单独决定。
- 本次后端全量回归为 200 项；真实批次 `013403 + 014089` 两条 Worker 通道同时启动，市场情报覆盖 2/2，桌面端和 390px 手机端无横向溢出。

### 2026-07-13：证据约束的大模型基金研判

- 新增提供商中立的 LLM Gateway，支持 OpenAI Responses API、阿里云百炼 DashScope OpenAI 兼容接口及其他经批准的 OpenAI-compatible 服务；提供商、模型和 API Key 必须显式配置。
- 新增真实基金情报工具 `fund.intelligence.get@1.0.0`，使用基金最新可得定期披露穿透底层持仓，并聚合腾讯证券单股行情、A/H 股真实新闻发布机构、美股 Alpha Vantage 新闻和内地行业/概念数据。
- 新增 R1 工具 `llm.fund_decision.synthesize@1.0.0`。模型只读取已持久化 Evidence 的结构化摘要，不直接调用行情工具，不重算净值、仓位或收益率，也不能绕过确定性动作门禁。
- 模型输出必须通过 Pydantic JSON Schema、Evidence ID、动作一致性、利润承诺、精确金融数字和提示注入检查；任何一项失败都返回 `unavailable`，不使用模板文本冒充模型结果。
- 私有组合默认不发送给模型。只有服务器设置 `LLM_PRIVATE_CONTEXT_ENABLED=true` 且用户在任务中勾选组合上下文时，才发送去标识化的聚合摘要；不发送姓名、账户号或原始持仓流水。
- Agent 结果升级为 `fund_deep_research.v5`，历史 v4 结果继续兼容策略 Shadow Outcome。前端按“决策问题 → 模型状态 → AI 研判 → 确定性风险门禁 → Evidence/Audit”组织，并折叠历史任务。
- 未配置真实模型时，任务明确保存 `model_not_configured` Evidence，页面显示“本轮没有生成大模型研判”；确定性研究仍可查看，但不存在兜底 AI 文本。
- 本次后端全量回归为 195 项；真实基金 `013403` 验证为港股 QDII，底层持仓行情与新闻情报可用，桌面端和 390px 手机端无横向溢出。

### 2026-07-13：Agent 跨市场基金识别与风险门禁

- 新增版本化市场画像 `fund_market_profile@1.0.0` 和 R0 工具 `fund.market_profile.get@1.0.0`，使用东方财富基金代码库的真实基金类型、基金名称和详情页累计收益比较序列识别投资市场；页面比较序列不冒充基金合同业绩基准。
- 当前可区分中国内地、港股、美国、全球/其他海外和跨市场基金，并显式标记 QDII、汇率风险、海外交易时差与确认净值发布滞后。
- QDII 只能确认“跨境”但无法确认具体投向时，状态为 `insufficient`，Agent 必须输出“等待确认基金投资市场”，不得猜测市场或生成金额。
- 投资档案新增允许投资的基金市场和汇率风险确认。历史档案迁移后默认只允许内地市场，不会自动替用户开放港股、美股或全球基金。
- 个人决策新增“投资市场识别、市场投资权限、汇率风险”三道强制门禁；跨境基金只有在市场已识别、用户明确允许对应市场并接受汇率风险后，才可能继续进入原有风险、期限、仓位和历史条件门禁。
- 基金盘中估值继续与确认净值隔离。跨境基金的盘中估值只作参考，不进入个人金额计算；Agent 展示确认净值滞后政策和详情页比较序列。
- Agent 结果升级为 `fund_deep_research.v3`，市场画像拥有独立 Evidence，并和基金分析、持仓上下文一起成为个人决策 Evidence 的输入引用。
- 真实验证：`013403` 识别为港股 QDII，`040046` 识别为美国市场 QDII，`110022` 识别为内地股票基金。持仓披露慢源不作为同步硬依赖，避免阻塞核心决策 Run。

### 2026-07-13：持仓感知的基金投资决策 Agent

- 新增版本化策略 `personalized_fund_decision@1.0.0`，把基金真实净值研究结果与用户已确认持仓、风险偏好、投资期限、月度预算和单品仓位上限合并评估。
- 新增 R1 只读工具 `portfolio.context.get@1.0.0` 和确定性工具 `fund.personalized_decision.evaluate@1.0.0`；运行时使用完整上下文，Step 只保存最小输入引用，输出继续形成 Evidence、Claim 和哈希审计链。
- 决策顺序固定为：资料完整性、组合金额、风险适配、期限适配、单品仓位上限、历史条件优势。任一关键门禁不通过，不生成加仓金额。
- 输出动作包括：先补资料、等待条件改善、持有复核、不新增投入、降低集中度、仅保留研究候选和可考虑小额分批；不会因为当前亏损自动建议补仓。
- 金额使用确定性公式：先计算投入后仓位不超过用户上限的最大新增空间，再取计划投入额或月度预算与该空间的较小值；满足全部门禁后才拆分首批观察金额。
- Agent 工作台增加“应用真实持仓与约束”和计划投入金额，并展示当前仓位、个人上限、历史样本、全部门禁、缺失资料及决策 Evidence。
- 当前线上持仓仍使用单用户迁移账本。对外开放多用户前必须完成登录、授权和数据隔离；本功能不自动下单，也不承诺收益。

### 2026-07-13：实时价位与基金估值历史到达

- 根据用户澄清，撤下上一版“滚动收益率上一次出现”面板，恢复原有滚动收益统计；新功能回答的是当前实时价位或盘中估算净值上一次何时在历史中到达。
- 新增版本化指标 `asset_level_recurrence@1.0.0`。股票以腾讯证券实时成交价为目标，使用专业源优先的未复权历史日线比较，不再使用新浪单股行情。
- 股票只有在当前价落入某个更早交易日的真实最高价与最低价区间时才标记“历史曾到达”；排除实时报价当天，并披露当日区间、收盘价、历史源和覆盖期。
- 基金以东方财富盘中估算净值为目标：若历史确认净值同值则报告具体日期；若只在两个相邻确认净值之间穿越，则如实报告日期区间，不伪造精确盘中时刻。
- 覆盖期内未到达时只显示历史最近值和差额；实时价、盘中估值或真实历史源不可用时明确显示不可用，不用昨日收盘、确认净值或模拟数据替代。
- 基金中心的盘中估值面板、股票行情快照和勾选了“盘中估值核验”的 Agent Run 使用同一确定性指标；Agent 结果绑定估值 Evidence 和审计链。
- 新增 `GET /api/quote/level-history`，并增加当天排除、未复权日线、日内区间命中、基金精确同值、基金跨越区间、无命中拒绝和 Evidence 绑定测试。
- 真实验证：基金 `001480` 的估算净值 `8.4923` 上一次在 `2026-07-08` 至 `2026-07-09` 的确认净值间向下穿越；贵州茅台 `600519` 当前价 `1204.98` 上一次由 `2026-07-06` 的 `1180.00–1215.00` 日内区间覆盖。

### 2026-07-13：基金当前条件历史前瞻策略

- 新增版本化策略 `fund_conditioned_forward_return@1.0.0`，使用基金真实确认净值计算，不访问模拟行情。
- 策略按每个自然月最后一个净值样本，匹配与当前“60 日趋势 + 回撤区间”相同的历史时点，统计随后 3/6/12 个月的正收益比例、中位收益、四分位区间和历史最差结果。
- 每个窗口同时显示该基金全部历史月末样本的无条件基准，避免只展示相似条件结果而缺少参照。
- 策略输出方向与置信度分离；相似样本少于 6 个时拒绝给方向，最高只标记中等置信度，并明确前瞻窗口重叠、基金经理或合同变化等局限。
- 新建 Agent 基金研究默认使用 60 个月净值窗口，提高长窗口策略的样本覆盖；仍可按研究需要选择 12/24/36/60 个月。
- Agent 研究结果升级为 `fund_deep_research.v2`，策略主窗口的历史正收益比例和中位收益保存为 Evidence 可追溯 Claim。
- Agent 工作台新增策略证据面板，展示当前条件、三个前瞻窗口、基准差、失效条件和个人适用性缺口；重跑对比同步追踪策略判断、方向、置信度和主窗口变化。
- 增加策略版本、分布统计、防未来泄漏、样本不足拒绝和 Agent Evidence 绑定测试，并使用真实基金 `001480` 的 60 个月净值完成验证。

### 2026-07-12：Agent 重跑结果对比

- 新增 `GET /api/v1/agent/runs/{run_id}/comparison`，可比较重跑任务与其来源 Run 的数据日期、关键指标和研究结论。
- 对比只读取两个 Run 已保存的结果快照，不重新请求数据源，也不生成模拟数据。
- 比较前同时校验父子 Run 的 Evidence 载荷 SHA-256、`evidence.created` 审计记录和完整哈希链；任一校验失败即拒绝输出差异。
- Agent 工作台新增“与来源任务对比”面板，展示旧值、本次值、变化量、结论变化和 Evidence 核验数量，并适配桌面端与手机端。
- 增加真实差异、结果稳定、Evidence 篡改拒绝和公开 API 契约测试。

### 2026-07-12：Agent 历史筛选

- Agent 运行历史新增基金代码和任务状态筛选，支持 Enter 或按钮提交。
- 基金代码在前端校验为 6 位数字，无效条件不会发送请求。
- 刷新、任务完成后的自动刷新和“加载更早任务”都会保持已应用的筛选条件。
- 提供一键清除筛选和空结果状态，桌面端与手机端使用响应式布局。

### 2026-07-12：Agent 按原配置重跑

- 新增 `POST /api/v1/agent/runs/{run_id}/rerun`，终态任务可以按原参数创建新的研究 Run。
- 新 Run 保存 `parent_run_id`，`run.created` 审计事件同步记录来源任务；旧任务和原 Evidence 保持不变。
- 重跑请求支持 `Idempotency-Key`，重复点击不会重复创建任务；运行中的任务拒绝重跑。
- Agent 工作台新增“按原配置重跑”按钮、来源 Run 展示和历史任务“重跑”标识。
- 增加父子关系、幂等和运行状态门禁测试。

### 2026-07-12：Agent 运行历史

- 新增 `GET /api/v1/agent/runs`，支持轻量摘要、状态/基金代码筛选和游标分页。
- 历史查询固定按服务端 `tenant_id + user_id` 范围读取，为后续登录和租户隔离保留协议边界。
- Agent 工作台新增最近研究任务面板，可回看完整 Run，并按需加载更早任务。
- 历史列表不返回 Evidence 原始载荷；只有打开具体 Run 和 Evidence 时才读取详细数据。
- 增加隔离、筛选、分页与接口契约测试，并完成桌面端和手机端验证。

## 当前能力

| 工作区 | 主要能力 |
|---|---|
| 今日决策 | 按“持仓事实 → 投资政策 → 持有纪律 → 交易账本 → 组合报告”展示证据闭环，只给出一个当前下一步和有优先级的复盘任务 |
| 我的资产 | 管理真实持仓与逐项纪律、版本化投资政策、交易账本、FIFO/XIRR 复盘和观察清单 |
| 研究中心 | 在一个入口内组织基金筛选与比较、股票与板块研究、多股分析、批量筛选和历史策略验证 |
| 投资 Agent | 创建可恢复的单基金 Run 或 2-6 只基金 Batch，识别内地/港股/美股/全球基金，编排真实市场/持仓/新闻工具、跨基金披露持仓重合、确定性风险门禁和可选 LLM 证据合成，保存 Step、Evidence、Claim、模型调用摘要和追加式审计链 |

### 基金研究

- 基金分类、热门榜和风险偏好机会筛选。
- 单基金净值趋势、区间收益、波动、最大回撤和恢复过程。
- 盘中估算净值上一次同值或穿越的确认净值日期区间，并保留估值与确认净值的口径差异。
- 已确认净值与盘中估值分开显示，估值不会替代确认净值。
- 同类排名、同类分位和多维替代品比较。
- 最新定期报告持仓、前后披露期变化和风格变化线索。
- 多基金相关性、重仓股/行业重合以及用户持仓穿透暴露。

### 投资 Agent 当前阶段

- 当前支持固定意图 `fund_deep_research`，读取公募基金真实数据，并可选择把用户已确认组合和投资约束纳入确定性风险门禁。
- Agent Run、工具步骤、证据、结论引用和审计事件持久化到 SQLite，进程重启后可恢复未完成任务并复用已完成证据。
- 运行历史支持服务端游标分页、状态筛选、基金代码筛选和完整任务回看。
- 工具通过版本化白名单注册，当前开放 R0 公共只读工具和 R1 个人数据只读/确定性计算工具；每个工具都有实际生效的执行时限。
- 请求支持幂等键、活动队列限制、运行中取消、可选盘中估值、披露变化和同类替代品研究。
- 已完成任务可以按原配置创建新的 Run，并保留可审计的父子任务关系。
- 重跑完成后可以比较父子 Run 已持久化的指标和结论；只有双方 Evidence 与审计链均完整时才输出差异。
- 基金研究包含版本化历史条件策略：以真实净值匹配当前趋势/回撤状态，展示后续 3/6/12 个月历史分布、无条件基准和样本置信度。
- 持仓感知决策只有在投资档案、完整组合金额、风险、期限、仓位和历史条件全部通过时才计算分批研究金额；缺少关键数据时主动弃权。
- 跨境基金还必须通过市场识别、用户市场权限和汇率风险确认；QDII 估值不替代确认净值，也不直接驱动金额。
- 基金底层情报按投资市场路由：内地基金读取 A 股行业/概念，港股和美股基金使用披露行业及底层持仓行情；没有获准的海外板块源时明确标记不可用，不把 A 股板块套用到跨境基金。
- 可选大模型在所有工具完成后执行，只做证据解释、反证整理、未知项识别和复核计划；确定性代码负责精确数字、组合暴露、预算和最终允许动作。
- 模型调用记录提供商、模型、提示模板版本、输入/输出哈希、Token 使用、时延和质量门禁，但不保存 API Key 或完整原始 Prompt。
- 每条数值 Claim 绑定 Evidence，Evidence 保存来源、有效时间、质量状态和 SHA-256 摘要。
- 真实来源失败时 Run 进入 `partial` 或 `failed`，不会生成替代数据。

当前阶段没有开放式自主规划、交易工具或自动下单。LLM 仅是受约束的最终合成节点，不拥有工具权限。R1 私人持仓工具仍处于单用户迁移阶段，真实登录、多用户授权和数据隔离尚未完成；SQLite 和单进程 Worker 也不等同于最终的 PostgreSQL + Temporal 生产架构。

### 组合与交易复盘

- OCR 截图、粘贴文本、CSV/XLSX 文件和手动持仓录入。
- 解析结果先预览，只有用户确认的数据才写入持仓。
- 交易流水批量预览、原子导入和重复文件保护。
- FIFO 剩余成本、已实现收益、费用和份额对账。
- XIRR 资金加权收益率；现金流不完整时拒绝展示完整收益率。
- 组合快照、区间归因、交易行为和用户单品上限复盘。

### 股票与市场研究

- A 股、港股、美股历史行情和当前行情。
- 技术指标、多因子评分、基本面、新闻情绪和模型信号。
- 单股与基准比较、多股收益/波动/回撤/相关性比较。
- 批量股票筛选、热门股、涨跌榜和成交活跃榜。
- A 股行业与概念热度、板块热门股以及盈利/概念驱动线索。
- 市场机会日报和风险提示。

## 数据原则

1. 不展示伪造行情、净值、财务数据、持仓或投资结论。
2. 可以在已接入的真实来源之间切换，但必须保留实际来源、数据时间和失败原因。
3. 来源全部失败时返回 `partial`、`unavailable` 或明确错误，不用示例值兜底。
4. 用户组合分析只使用用户保存或确认过的持仓和交易。
5. 盘中估值、确认净值、日终行情和定期报告披露不得混为同一种“最新数据”。
6. 收益率、回撤、FIFO、XIRR、相关性和组合集中度由确定性代码计算，不由大模型自行编造。
7. 港股、美股、全球和 QDII 基金必须披露市场识别依据、净值时差与汇率风险；无法确认具体市场时停止金额决策。

## 快速开始

### 环境要求

- Python 3.10+
- Node.js 18+
- Windows 本地运行，或 Ubuntu 22.04/24.04 服务器

### Windows 一键运行

首次使用：

```text
双击 setup.bat
```

以后启动：

```text
双击 start.bat
```

浏览器会打开 [http://localhost:5173](http://localhost:5173)。后端 API 默认运行在 `http://127.0.0.1:8000`。

### 手动启动

后端终端：

```powershell
Set-Location C:\Project
.\venv\Scripts\Activate.ps1
Set-Location backend
python -m uvicorn main:app --reload --port 8000
```

前端终端：

```powershell
Set-Location C:\Project\frontend
npm install
npm run dev
```

开发环境中，Vite 会把 `/api` 请求代理到后端 `8000` 端口。FastAPI 接口文档位于 [http://127.0.0.1:8000/docs](http://127.0.0.1:8000/docs)。

## 配置

后端优先从环境变量读取第三方服务配置：

| 环境变量 | 用途 |
|---|---|
| `TUSHARE_TOKEN` | A 股/港股数据源 |
| `POLYGON_API_KEY` | 美股数据源 |
| `ALPHAVANTAGE_API_KEY` | 美股基本面、行情或新闻数据 |
| `ALIBABA_CLOUD_ACCESS_KEY_ID` | 阿里云 OCR |
| `ALIBABA_CLOUD_ACCESS_KEY_SECRET` | 阿里云 OCR |
| `ALIYUN_OCR_ENDPOINT` | 阿里云 OCR Endpoint |
| `ALLOWED_ORIGINS` | FastAPI CORS 允许来源 |
| `AGENT_DB_PATH` | Agent 迁移期 SQLite 文件路径；默认复用 `backend/stock_assistant.db` |
| `AGENT_MAX_PENDING_RUNS` | Agent 排队和运行任务总上限；默认 `20` |
| `AGENT_MAX_BATCH_SIZE` | 单个基金研究批次上限；默认 `6`，代码硬上限 `8` |
| `AGENT_WORKER_ENABLED` | 是否启动内置持久化 Worker；默认开启 |
| `AGENT_WORKER_POLL_SECONDS` | 内置 Worker 轮询间隔；默认 `0.75` 秒 |
| `AGENT_WORKER_CONCURRENCY` | 单进程 Run Worker 并发数；默认 `2`，代码硬上限 `4` |
| `FUND_HTTP_TRUST_ENV` | 基金请求是否使用环境代理；设为 `0`/`direct` 时强制直连 |
| `LLM_PROVIDER` | `openai`、`dashscope`、`deepseek` 或 `openai_compatible`；不配置时禁用模型合成 |
| `LLM_MODEL` | 明确批准的模型 ID，不提供隐式默认值 |
| `LLM_API_KEY` | 通用模型密钥；内置供应商优先使用下面的专用变量 |
| `OPENAI_API_KEY` | `LLM_PROVIDER=openai` 时的 OpenAI API Key |
| `DASHSCOPE_API_KEY` | `LLM_PROVIDER=dashscope` 时的阿里云百炼 API Key |
| `DEEPSEEK_API_KEY` | `LLM_PROVIDER=deepseek` 时的 DeepSeek API Key |
| `LLM_BASE_URL` | 可选自定义 HTTPS Base URL；OpenAI、DashScope 和 DeepSeek 有公开默认值 |
| `LLM_API_STYLE` | `responses` 或 `chat_completions`；OpenAI 默认 Responses，其余默认 Chat Completions |
| `LLM_THINKING_MODE` | DeepSeek 思考模式：`disabled`（默认）或 `enabled` |
| `LLM_REASONING_EFFORT` | DeepSeek 开启思考模式后的推理强度：`high` 或 `max`，可不配置 |
| `LLM_PRIVATE_CONTEXT_ENABLED` | 是否允许去标识化的聚合组合摘要离开本服务；默认 `false` |
| `LLM_DATA_REGION` | 记录模型处理地域，例如 `cn-beijing` 或 `ap-southeast-1` |
| `LLM_TIMEOUT_SECONDS` | 模型请求时限，默认 75 秒 |

前后端分离部署时，参考 `frontend/.env.example` 配置 `VITE_API_BASE_URL`。不要把真实 Key、服务器密码或用户数据提交到 Git。

DeepSeek 最小配置如下。当前项目不使用即将停用的旧模型别名：

```dotenv
LLM_PROVIDER=deepseek
LLM_MODEL=deepseek-v4-flash
DEEPSEEK_API_KEY=只写入服务器环境文件
LLM_THINKING_MODE=disabled
LLM_DATA_REGION=cn
LLM_PRIVATE_CONTEXT_ENABLED=false
```

## 项目结构

```text
backend/
  main.py                  FastAPI 启动与路由装配
  agent/
    batches.py             批次状态、逐只决策矩阵与披露持仓重合下界
    comparison.py          基于持久化结果和 Evidence 门禁的父子 Run 对比
    llm_gateway.py         提供商中立模型网关、重试与调用元数据
    portfolio_context.py   用户已确认持仓与投资约束的最小只读上下文
    registry.py            版本化工具白名单
    repository.py          Run、Step、Evidence、Claim 与 Audit 持久化
    synthesis.py           证据上下文、结构化模型协议与质量门禁
    workflow.py            确定性基金研究工作流、超时与取消
    worker.py              可恢复、受控并发的迁移期单进程 Worker
  holding_thesis.py        持有逻辑版本、计划边界与真实证据复核
  strategies/
    asset_level_recurrence.py   股票实时价/基金估值历史到达指标 1.0.0
    fund_conditioned_forward.py  基金当前条件历史前瞻策略 1.0.0
    fund_market_profile.py        基金跨市场画像与 QDII 风险口径 1.0.0
    personalized_fund_decision.py 持仓感知的个人风险门禁与金额策略 1.0.0
  routers/
    agent.py               Agent Batch/Run、重跑对比、Evidence 和 Audit API
    market.py              股票、板块、行情和市场日报接口
    funds.py               基金发现、研究、比较和替代品接口
    portfolio.py           持仓、交易、OCR、复盘和提醒接口
  funds.py                 基金净值、风险、披露和比较领域逻辑
  fund_intelligence.py     跨市场持仓、行情、板块和新闻聚合
  holdings.py              持仓解析、OCR 和组合分析
  portfolio_review.py      FIFO、XIRR、行为、快照和归因
  decision_center.py       持仓感知的规则化决策任务
  data_fetch.py            A 股/港股/美股历史数据
  market_daily.py          市场机会日报
  sectors.py               行业、概念和热门股分析
  storage.py               当前 SQLite 持久化
  tests/                   后端回归与契约测试
frontend/
  src/App.jsx              顶层工作区导航
  src/components/PersonalizedDecisionView.jsx  Agent 个人决策与门禁面板
  src/components/AISynthesisView.jsx           模型研判、反证、未知项与审计摘要
  src/components/FundMarketProfileView.jsx     基金跨市场 Evidence 面板
  src/tabs/                总览、基金、市场、组合和研究页面
  src/features/funds/      基金研究组件和状态管理
  src/features/decision/   决策中心组件
  src/api/                 按领域拆分的 API 客户端
deploy/                    systemd 与 Nginx 配置模板
docs/
  industrial-agent-prd.md  工业级 Agent 升级 PRD
ARCHITECTURE.md             当前代码架构约定
DEPLOY.md                   云服务器部署说明
```

## 测试与构建

后端：

```powershell
Set-Location C:\Project\backend
..\venv\Scripts\python.exe -m unittest discover -s tests -v
```

前端：

```powershell
Set-Location C:\Project\frontend
npm run build
```

## 部署

生产环境推荐使用 Nginx 托管前端构建产物，并把 `/api` 反向代理到只监听 `127.0.0.1:8000` 的 FastAPI 服务。完整步骤见 [DEPLOY.md](DEPLOY.md)。

## 工业级 Agent 升级

项目已经完成工业级 Agent 的产品和架构设计，实施范围包括：

- 登录、多租户、PostgreSQL 和行级数据隔离。
- 可恢复的 Agent Run 状态机和异步工作流。
- 统一工具协议、Provider Gateway 和真实数据治理。
- Evidence 证据账本、Claim 引用和追加式审计链。
- 策略注册、模型评测、Prompt Injection 防护和灰度发布。
- 今日投资任务、基金购买前审查、替代品、组合风险和板块解释 Agent。

完整设计、需求编号、数据模型、API、SLO、评测门槛、迁移阶段和验收场景见：

**[金融投资助手工业级 Agent 升级方案设计书（PRD）](docs/industrial-agent-prd.md)**

第一条“基金深度研究”垂直链路已经落地持久化 Run、工具协议、Evidence、Claim 和审计链。当前实现与目标架构之间仍有明确差距：尚未提供真实登录、多用户隔离、PostgreSQL、Temporal 分布式工作流、动态规划与模型治理，也不提供自动交易。README 和 UI 中不得把规划中的能力描述为已经上线。

## 相关文档

- [当前架构约定](ARCHITECTURE.md)
- [云服务器部署说明](DEPLOY.md)
- [工业级 Agent PRD](docs/industrial-agent-prd.md)
