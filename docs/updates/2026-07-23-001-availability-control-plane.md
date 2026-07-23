# 2026-07-23 更新 001：高可用控制面与安全降级

## 1. 这次解决什么

平台此前已经有 PostgreSQL、Redis/Celery、五类 Worker、私有 OSS、供应商接力、任务租约和 `/health/ready`，但这些能力仍是分散的：

1. 健康状态只存在于瞬时接口和 journald，没有可查询的历史；
2. 单个异步依赖失败会让“能否继续读取已保存事实”和“能否刷新新数据”混在一起；
3. 供应商运行状态是进程内状态，管理员看不到事故何时开启、何时恢复；
4. 没有固定采样口径的 SLI、错误预算和手动探测防刷机制；
5. 用户不知道当前哪些能力安全可用，容易把旧数据或旧健康状态当成现在可用。

本次建立的是应用级可用性控制面，不是又一个股票评分：

```text
Celery Beat（固定 5 分钟时间桶）
               |
               v
 scheduler availability task（幂等、late ack、重试）
               |
       +-------+-------------------------------+
       |       |       |        |              |
       v       v       v        v              v
 PostgreSQL  Redis   5 Worker  5 Queue        OSS
                                               |
                                               v
                               A/H/US 专业行情路线状态
               |
               v
  不可变探针快照 -> 连续确认状态机 -> 哈希事故事件链
               |
       +-------+----------------+
       |                        |
       v                        v
 用户能力门禁              管理员 SLO / 事故中心
```

它的目标是让依赖故障可发现、可追溯、可恢复，并让平台在不安全时收窄功能。它不预测收益，也不能消除单机宕机风险。

## 2. 设计依据

- [Google SRE：Service Level Objectives](https://sre.google/sre-book/service-level-objectives/) 用于区分 SLI、内部目标和用户可感知结果；本项目的百分比是内部工程目标，不是客户 SLA。
- [Kubernetes：Liveness、Readiness 与 Startup Probes](https://kubernetes.io/docs/concepts/workloads/pods/probes/) 用于区分“进程还活着”和“当前可接流量”。本项目额外增加 `/health/full` 表达全部异步能力可用。
- [Celery：Tasks](https://docs.celeryq.dev/en/stable/userguide/tasks.html) 与 [Configuration](https://docs.celeryq.dev/en/latest/userguide/configuration.html) 用于约束探针任务幂等、late acknowledgement、Worker 丢失重派发、超时和固定调度。

这些资料提供运行原则，具体阈值仍由本平台根据单机资源和业务风险显式配置。

## 3. 三层健康语义

| 接口 | 含义 | 失败条件 | 用途 |
|---|---|---|---|
| `/health/live` | API 进程存活 | 进程无法响应 | 进程重启判断 |
| `/health/ready` | 能安全提供 PostgreSQL 权威事实 | 数据库或任一生产 Schema 不可用 | 流量接入、只读事实服务 |
| `/health/full` | 全部生产能力可用 | ready 失败，或 Redis、OSS、任一必需 Worker 不可用 | systemd 严格监控、发布验收 |

`/health/ready` 不再因一个异步 Worker 暂时离线而把全部已保存事实流量摘除。`/health/full` 保留严格检查，因此运维仍能立即发现功能不完整。专业行情路线由持久控制面单独观测，不进入 API 进程的瞬时 full 检查，避免供应商网络波动让本地服务被反复重启。

## 4. 16 个组件与主动探测

标准探针记录：

- 1 个 PostgreSQL/生产 Schema；
- 1 个 Redis 消息总线；
- `agent`、`market-data`、`llm`、`ocr`、`scheduler` 五类消费者；
- 上述五条 Redis 队列的积压深度；
- 1 个私有 OSS 链路；
- A 股、港股、美股三条专业行情路由。

标准探针读取供应商最近运行状态，不消耗三市场外部额度。管理员“深度探测”才通过 `market-data` Worker 并发调用三个市场的专业路线，而且禁止把公开网页降级源伪装成专业源成功。

队列默认在深度达到 `100` 时标记 degraded，达到 `1000` 时标记 outage。阈值是运行参数，不是写死的业务承诺。

## 5. 幂等、抖动抑制和事故生命周期

- scheduled 探针 ID 由“固定时间桶 + 探针间隔”计算，同一时间桶重复投递只返回原快照；
- 去重结果不重复增加 Prometheus 探针计数或事故计数；
- 一个组件连续两次 degraded/outage 才开启事故；
- 已开启事故连续两次 operational 才恢复；
- degraded 与 outage 之间的确认状态变化写入 `severity_changed`；
- unknown 保留为 unknown，既不能打开新鲜能力，也不能被总体状态误算为 operational；
- 用户可见状态取“本次原始观测”和“连续确认状态”中更严重者：未知观测仍会阻止旧绿色状态延续，但也不能掩盖尚未恢复的 degraded/outage 事故；两个状态继续分别保留，便于管理员判断是新观测还是确认事故；
- 用户功能门禁使用本次真实观测，事故状态机只负责告警去抖，因此一次明确失败可以立即关闭高风险新动作，同时不会立刻制造事故噪音。

事故事件只有 `incident_opened`、`severity_changed`、`incident_resolved` 三类。每个 incident 独立使用 `previous_hash -> event_hash` 链接，便于验证完整生命周期。

## 6. 不可变数据与脱敏

新增：

- `availability_probe_runs`：保存 schema/method 版本、触发类型、执行人、原始/确认总体状态、起止时间、完整载荷和 SHA-256；
- `availability_incident_events`：保存事故、序号、组件、类别、转换前后状态、探针绑定和哈希链。

SQLite 测试库和 PostgreSQL 生产库都用触发器拒绝 UPDATE/DELETE。生产迁移 `availability-control.v1` 在 advisory lock 和单事务内建立两张表、索引、触发器和迁移标记；应用启动不会偷偷补生产表。

组件详情只允许白名单运行字段进入载荷。递归脱敏同时检查值和键名，`token`、`password`、`secret`、`authorization`、`api_key`、`access_key` 及 URL 凭据不会写入不可变历史。普通用户接口不返回组件内部详情、执行人、探针载荷或事故事件。

## 7. 用户能力门禁

控制面生成六类能力：

| 能力 | 正常条件 | 降级行为 |
|---|---|---|
| 已保存事实读取 | PostgreSQL 和生产 Schema 正常 | 数据库失败时整体不可用 |
| 市场数据刷新 | Redis、market-data Worker/队列和至少一个目标市场专业路线可用 | 按市场 partial 或关闭刷新 |
| 组合估值刷新 | 与市场刷新相同，并继续受估值自身时效/覆盖门禁约束 | 只读最近可信结果，不生成伪当前金额 |
| 投资 Agent | Agent 与 market-data 链路可用 | LLM 单独失败时进入确定性模式；底层队列失败时关闭 |
| 私有 OCR 导入 | Redis、OCR Worker/队列和私有 OSS 可用 | 关闭上传，不落本地文件兜底 |
| 持久调度 | Redis、scheduler Worker/队列可用 | 暂停新的可靠调度，保留 PostgreSQL 状态 |

总体决策模式为：

- `normal`：三市场刷新链路完整；
- `read_only_degraded`：权威事实仍可读，但一个或多个新鲜动作不可安全执行；
- `unavailable`：权威数据库不可用，不能安全提供投资事实。

能力矩阵只是平台基础设施门禁；具体投资动作仍必须通过数据时效、证据、持仓、策略和风险门禁。基础设施绿色不等于可以买入。

## 8. SLI、内部 SLO 与错误预算

控制面按 24 小时、7 天、30 天窗口计算四组目标：

| 组 | 默认目标 | 组件 |
|---|---:|---|
| 权威事实读取 | 99.9% | PostgreSQL |
| 持久后台处理 | 99.0% | Redis、Agent/market-data/scheduler Worker |
| 私有文件链路 | 99.0% | OSS、OCR Worker |
| 三市场专业行情 | 95.0% | A/H/US 路由 |

只有 `scheduled` 探针进入 SLI；`manual`、`manual_deep` 和 `deployment` 不进入分子或分母，管理员不能通过重复点击刷新可用率。未知样本单独计数，不假装成功或失败。少于 12 个已知固定样本时页面只显示“样本积累中”。

错误预算展示可用率、坏样本、burn rate 和剩余比例。当前是离散固定间隔探针 SLI，并非请求级精确停机时长；后续接入集中式指标系统后可增加请求成功率和分位延迟 SLI。

## 9. API、前端与指标

| 方法 | 路径 | 权限 | 作用 |
|---|---|---|---|
| `GET` | `/api/platform/availability` | 登录用户 | 脱敏总体状态、能力矩阵和开放事故数 |
| `GET` | `/api/admin/availability` | 管理员 | 完整组件、历史、事故、SLO 和完整性校验 |
| `POST` | `/api/admin/availability/probes` | 管理员 + CSRF | 标准或三市场深度探测 |

顶栏每 60 秒刷新脱敏摘要；快照超过默认 15 分钟时强制显示“监测中”，不会延续旧绿色状态。管理员页显示 16 个组件的观测/确认状态、连续失败/恢复、能力矩阵、SLO 和事故恢复时间线。

Prometheus 新增：

- `stock_assistant_availability_probes_total`；
- `stock_assistant_availability_component_state`；
- `stock_assistant_availability_incident_events_total`。

## 10. 本地验收

自动化覆盖：

- 连续失败/恢复去抖和严重度转换；
- 探针与事故表不可修改/删除；
- 固定 ID 去重及重复指标保护；
- 混合 unknown 状态不会误报正常；
- 队列 outage 只关闭受影响能力；
- 嵌套凭据在持久化前脱敏；
- 过期监控快照强制进入 unknown；
- 手动探测不能污染 scheduled SLO；
- Celery 路由、Beat 周期和任务过期时间；
- 新 API 的 OpenAPI 契约以及 readiness/full-service 语义。

本地后端全量回归为 `493 tests` 与 `4 subtests` 通过；高可用、健康检查、任务协议和路由契约定向回归为 `25 tests` 与 `4 subtests` 通过。前端 `npm audit` 返回 `0 vulnerabilities`；Vite `8.1.5` 生产构建成功并转换 `1849` 个模块。当前 OpenAPI 为 `164` 个操作。

本地浏览器以开发管理员真实执行标准探针，页面从无快照进入 16 组件只读降级，三市场专业路线不可用时没有显示绿色正常；手动探测结果会立即同步顶栏，不再等待下一轮 60 秒轮询。桌面端和 `390×844` 手机端均无页面级横向溢出，两个宽表只在自身容器滚动，浏览器控制台无 warning/error。

## 11. 明确边界与下一层高可用

- 当前生产仍是一台云主机；主机、机房、单 PostgreSQL 或单 Redis 的整体故障不能由应用内控制面自行接管。
- 探针是离散采样，不代表两次采样之间的每一秒都可用，也不构成外部 SLA。
- 供应商“路线可用”只说明最近状态或主动探测成功，不保证每只证券、每个端点和用户订阅权限全部覆盖。
- read-only degraded 允许读取已保存且自身仍通过时效/完整性校验的事实，不允许把过期事实改名为最新事实。
- 本功能不会自动交易、自动切换券商、自动购买数据订阅或承诺赚钱。

基础设施层下一阶段应按优先级推进：负载均衡后的无状态 API 多副本；托管 PostgreSQL 主备与时间点恢复；Redis Sentinel/托管高可用；独立外部探针和告警通知；跨可用区恢复演练。完成这些之前，项目应明确称为“应用级安全降级”，不能宣称跨可用区高可用。

## 12. 生产发布结果

- 代码已推送 GitHub `main` 并发布到 `http://8.148.67.79/`；部署前保留完整代码和静态站点回滚副本。发布前 PostgreSQL 备份对象为 `backups/postgresql/2026/07/stock-assistant-iZn4ai1fm0tr284w21h4kmZ-20260723T002745Z.dump`，SHA-256 为 `f4130f20109489201ff75802d353314c1d9b7e1bbd509727948d10cd7642b32a`。
- `availability-control.v1` 迁移成功，生产库共 `62` 张表、`6` 个迁移标记；探针和事故事件表的 PostgreSQL UPDATE/DELETE 拒绝触发器均存在。服务器定向回归 `24 tests` 通过，临时 SQLite 隔离库随后删除。
- API、Celery Beat、五类 Worker 和 Nginx 均为 active，systemd 无 failed unit。`/health/full` 返回 `ready=true`、`traffic_ready=true`、`full_service_ready=true`；PostgreSQL、Redis、五条队列、五类消费者和私有 OSS 全部通过。公网首页及版本化 JS/CSS 返回 `200`，匿名访问用户摘要、管理员控制面和主动探测均返回 `401`。
- 临时管理员真实登录后可读取用户摘要和管理员控制面；无 CSRF 调用主动探测返回 `403`，携带 CSRF 后返回 `200`。临时普通用户可读取脱敏摘要，访问管理员控制面及主动探测均返回 `403`。两账户随后均已 disabled，活动会话为 `0`；认证审计链 `26` 个事件校验完整。
- 首次部署探针形成 `16` 个组件。三市场真实深度探测确认 A 股 `tushare_pro_a` 可用、数据日为 `2026-07-22`；美股由 Alpha Vantage 接力成功、数据时间为 `2026-07-21 16:15:56 US/Eastern`。港股 `tushare_pro_hk` 返回 `hk_daily` 当前套餐 `1 次/分钟` 限频，FutuOpenD 未配置，且策略禁止公开源伪装为专业源，因此保留 `market:港股` 开放事故并进入 `read_only_degraded`。这是明确披露的运行缺口，不是发布成功后改写成绿色。
- Celery Beat 已自动形成新的 `scheduled` 快照；同一五分钟时间桶重复投递后快照数不增加，时间桶幂等通过。最新探针 SHA-256、逐事故事件链和认证审计链均通过完整性校验；SLO 已开始积累固定样本，未达到 `12` 个样本前明确显示“样本积累中”。
- 发布后备份对象为 `backups/postgresql/2026/07/stock-assistant-iZn4ai1fm0tr284w21h4kmZ-20260723T004423Z.dump`，SHA-256 为 `a389b2574cfb3608381f0cb3616cd8c184959880f280bc7432427d226fe79e51`，私有 OSS 服务端加密为 AES256。恢复验证服务完成校验并在隔离数据库恢复出 `62` 张表和 `6` 个迁移标记。
- 当前验收结论是“单机应用级控制面可用、已安全降级”，不是跨可用区高可用。港股稳定新鲜行情要么增加 Tushare 权限/频次，要么部署 FutuOpenD；主机级容灾仍需 API 多副本、托管 PostgreSQL 主备、Redis 高可用和外部负载均衡。
