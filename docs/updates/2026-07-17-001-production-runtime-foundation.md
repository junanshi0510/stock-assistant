# 生产运行时基础设施升级

## 目标

把单实例 SQLite 与进程内线程升级为可审计、可恢复、可备份的生产运行时，同时保持现有 API 和前端行为兼容。该升级不引入模拟数据，也不扩大模型或自动交易权限。

## 已完成范围

### PostgreSQL

- `DATABASE_URL` 配置后 PostgreSQL 成为唯一事实源，连接失败不回退 SQLite。
- SQLite 保留为本地测试与首次迁移输入。
- 迁移器反射并迁移 45 张既有业务表、索引和外键。
- 迁移在单一 PostgreSQL 事务中执行，目标库非空时拒绝覆盖。
- 每张表比较源/目标行数与规范化内容 SHA-256。
- SQLite 快照前后及迁移完成后比较主文件/WAL 指纹，检测并发写入。
- PostgreSQL 安装对象资产、持久任务、平台迁移表及审计不可变触发器。

### Redis 与任务队列

- Redis 仅用于 Celery 传输，不保存权威任务载荷或结果。
- 消息体仅包含 `run_id` 或 `job_id`。
- Agent、真实数据、LLM、OCR 和 scheduler 使用独立队列。
- 任务输入、结果、重试预算、幂等键、Worker、心跳和租约保存在 PostgreSQL。
- Worker 丢失租约后不能提交结果；scheduler 恢复过期租约并重新派发。
- 生产 Redis/Worker 不可用时明确失败，不回退进程内执行。

### 数据抓取、OCR 与 LLM

- 基金、股票、板块、新闻、行情、批量扫描和多股比较统一通过 market-data Worker 调用真实数据源。
- Agent 的公开数据工具与 LLM 合成分别路由到 market-data/llm Worker。
- OCR 上传改为 `202 Accepted` 异步任务，前端轮询持久任务状态。
- OCR 图片在上传前解码、EXIF 纠正、像素限制、缩放和元数据剥离。

### 对象存储

- OCR 原图写入专用阿里云私有 OSS，数据库仅保存对象元数据、SHA-256 与审计事件。
- 对象 Key 使用用户 ID 的 HMAC 摘要，不包含账号或原文件名。
- 上传后校验对象长度和服务端加密状态。
- 初始化命令强制 private ACL、公共访问阻断及生命周期规则。
- 没有 OSS 配置或 OSS 不可用时拒绝上传，不保存到本地目录。

### 可观测性与备份

- API/Worker 输出单行 JSON 日志，并传播 request/task/run/job 关联 ID。
- 日志隐藏数据库、Redis、HTTP URL 凭据以及 API Key、AccessKey、Token 和密码。
- Prometheus 指标覆盖 HTTP 延迟/状态、并发、队列深度和任务结果。
- readiness 同时检查 PostgreSQL、Redis、五个队列 Worker 和 OSS。
- systemd 每两分钟执行完整 readiness，失败写入 journald。
- 每日 `pg_dump` 后执行归档读取和 SHA-256 校验，再上传加密 OSS。
- 每周把最新备份恢复到临时数据库，核对表数和平台迁移记录后删除临时库。

## 关键不变量

1. PostgreSQL 是生产唯一事实源，SQLite/PostgreSQL 不长期双写。
2. Redis 消息不含持仓、Prompt、OCR 文本、模型输入或第三方密钥。
3. API 进程不执行生产 Agent 长任务和外部数据抓取。
4. 对象存储不可用时不写本地临时文件作为业务兜底。
5. 数据源失败仍返回真实失败或 partial，不生成示例数据。
6. LLM 仍不拥有交易权限，也不能覆盖确定性金额和风险门禁。

## 已验证

- 后端完整回归测试。
- 前端生产构建。
- PostgreSQL 16：45 张业务表迁移和逐表 SHA-256 一致。
- Redis/Celery：真实基金 `013403` market-data 任务一次完成，输入/结果哈希与事件链通过。
- 备份恢复：PostgreSQL 自定义格式备份恢复到隔离库，核对 50 张表和 2 条平台迁移记录。

## 未完成的工业化差距

- PostgreSQL Row-Level Security 尚未启用，当前继续由 Repository 强制用户范围。
- 单机 PostgreSQL、Redis 和 OSS 访问仍不具备跨可用区高可用。
- 尚未接入外部 Prometheus/告警通知和集中日志平台。
- Agent 编排仍是 Celery 固定工作流，不是 Temporal 级长事务或开放式动态规划。
- 尚未完成 MFA、集中身份服务和自动化密钥轮换。
