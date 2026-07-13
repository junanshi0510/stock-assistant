# 用户登录、RBAC 与个人数据隔离

## 目标

本次迭代把原有单用户投资工作台升级为可上线的多账户边界，完成以下能力：

- 服务端认证，不在浏览器保存 Bearer Token。
- `admin` 与 `user` 两级 RBAC。
- 个人投资数据按服务端会话隔离。
- 管理员账户治理、会话吊销和认证审计。
- 旧版单用户数据无损归属初始管理员。
- 生产环境失败关闭和离线恢复。

不在本次范围内：公开注册、短信/邮件找回、MFA、第三方 OAuth、组织/租户层级、自动交易和管理员任意读取用户第三方密钥。

## 权限矩阵

| 能力 | 未登录 | 普通用户 | 管理员 |
|---|---:|---:|---:|
| 登录、读取当前会话 | 是 | 是 | 是 |
| 查看和修改自己的投资数据 | 否 | 是 | 是 |
| 查看自己的 Agent Run/Batch/Evidence | 否 | 是 | 是 |
| 按 ID 审计其他用户 Agent Run | 否 | 否，返回 404 | 是 |
| 创建、启停、升降级账户 | 否 | 否 | 是 |
| 重置用户临时密码 | 否 | 否 | 是 |
| 查看全局账户、会话和 Run 统计 | 否 | 否 | 是 |
| 查看并校验认证审计链 | 否 | 否 | 是 |
| 读取密码、会话 Token、OCR/LLM API Key | 否 | 否 | 否 |
| 自动下单或绕过风险门禁 | 否 | 否 | 否 |

管理员不能停用或降级自己的当前账户，系统也不允许停用或降级最后一个启用管理员。角色、状态和密码发生安全变化时，相关活动会话立即吊销。

## 认证协议

1. 密码使用 Argon2id，参数为 19 MiB、2 次迭代、1 lane；数据库不保存明文或可逆密文。
2. 登录成功生成高熵随机 Session Token 和 CSRF Token。
3. 浏览器 Session Token 只存在 `HttpOnly`、`SameSite=Lax` Cookie 中；数据库只保存 SHA-256 哈希。
4. CSRF Token 只保存在前端模块内存中，写请求通过 `X-CSRF-Token` 提交，不写入 `localStorage`。
5. 会话同时受绝对有效期和空闲超时约束；过期、停用、改密或角色变更后拒绝访问。
6. 登录失败按用户名哈希或客户端标识联合限流，错误信息不区分用户不存在、密码错误或账户停用。
7. 临时密码首次登录后只能访问会话、退出和修改密码接口；修改成功后所有设备重新登录。

生产环境缺少 `AUTH_AUDIT_PEPPER` 或初始管理员时，业务 API 返回 `503`，不会回退到匿名模式。

## 数据归属

下列数据全部使用认证表中的不可变 `subject_id` 作为所有者：

- 自选和提醒。
- 持仓、投资政策及其版本审计。
- 交易流水、导入批次和组合快照。
- 持有逻辑版本、组合行动报告和穿透快照。
- Agent Run、Batch、Outcome Schedule 和 Strategy Shadow Enrollment。

前端请求没有 `user_id` 参数，路由只读取 `request.state.principal.subject_id`。按资源 ID 查询和删除时，SQL 或路由所有权检查同时校验当前用户。普通用户探测他人 Agent Run 时统一返回 404，避免泄露资源是否存在。

管理员的高权限只在服务端 `require_admin` 和 Agent 所有权判断中实现；隐藏前端菜单不是权限控制。

## 管理员能力

新增“系统管理”工作区：

- 创建管理员或普通用户，临时密码不在响应中回显。
- 修改角色和启停账户。
- 重置临时密码并吊销该用户全部会话。
- 查看启用管理员、启用用户、停用账户、活动会话和 Agent Run 数量。
- 查看认证事件并验证完整哈希链。

公网不提供注册和管理员离线恢复接口。服务器 SSH 运维命令：

```bash
python backend/manage_auth.py bootstrap-admin --username admin
python backend/manage_auth.py recover-admin --username admin
python backend/manage_auth.py verify-audit
```

生产执行时必须以 `stockassistant` Linux 用户运行并显式指定生产数据库路径，详见 `DEPLOY.md`。

## 审计链

认证审计表记录：管理员初始化、用户创建和修改、登录成功/失败、退出、用户改密、管理员重置密码和离线恢复。

每条事件包含递增序号、前一事件哈希、事件载荷和当前事件哈希。SQLite 触发器禁止 UPDATE 和 DELETE；管理员接口和离线命令均可从链首重新计算并验证。审计只保存客户端标识和用户名的加 Pepper 哈希，不保存原始 IP、登录密码或会话 Token。

## 兼容迁移

- 首个管理员固定使用 `subject_id=default`，因此旧持仓、投资政策和交易记录无需重写不可变载荷。
- 旧 `anonymous` Agent 所有权在管理员初始化事务中改为 `default`；若幂等键冲突，只清除旧记录的冲突幂等键，不删除 Run 或 Evidence。
- 旧 `watchlist` 和 `alerts` 只复制一次，迁移 ID 写入 `storage_schema_migrations`。用户删除迁移后的数据，重启不会重新写回。
- 初始化管理员、旧 Agent 归属和认证审计写入同一 SQLite 事务；失败会整体回滚。

上线前必须停止旧服务并使用 SQLite `.backup` 复制包含 WAL 的完整数据库，禁止只复制 `stock_assistant.db` 主文件。

## API

| 方法 | 路径 | 权限 |
|---|---|---|
| GET | `/api/auth/session` | 公开；只返回当前会话状态 |
| POST | `/api/auth/login` | 公开；受数据库限流 |
| POST | `/api/auth/logout` | 已登录 + CSRF |
| POST | `/api/auth/change-password` | 已登录 + CSRF |
| GET | `/api/admin/overview` | 管理员 |
| GET/POST | `/api/admin/users` | 管理员 |
| PATCH | `/api/admin/users/{user_id}` | 管理员 |
| POST | `/api/admin/users/{user_id}/reset-password` | 管理员 |
| GET | `/api/admin/auth-audit` | 管理员 |

除登录和当前会话外，所有 `/api` 业务接口均由统一认证中间件保护；所有非安全方法同时校验 CSRF。

## 生产配置

必需配置：

```dotenv
AUTH_REQUIRED=true
AUTH_AUDIT_PEPPER=<至少 32 个随机字符>
AUTH_COOKIE_SECURE=true
AUTH_TRUST_PROXY=true
STOCK_ASSISTANT_DB_PATH=/var/lib/stock-assistant/stock_assistant.db
```

当前仅使用公网 IP 和 HTTP 时，`AUTH_COOKIE_SECURE` 暂设为 `false`；域名和 HTTPS 生效后必须立即改为 `true`。

systemd 进程使用独立 `stockassistant` 用户、`StateDirectory`、`NoNewPrivileges`、只读系统目录和唯一可写数据库目录。第三方 Key 仍只存于 root 权限的 EnvironmentFile。

## 验收覆盖

自动化测试覆盖：

- Argon2id 哈希中不存在明文密码。
- Cookie 属性、服务端 Session、CSRF 成功与失败路径。
- 首次改密和改密后全会话吊销。
- 登录限流和无用户枚举错误。
- 管理员创建用户、普通用户禁止管理接口。
- 管理员跨用户 Run 审计、普通用户所有权 404。
- 自选和持仓跨用户隔离。
- 旧 Agent 所有权迁移及幂等键冲突。
- 旧自选一次性迁移且删除后不复活。
- 自锁与最后管理员保护、认证审计链完整性。

## 已知边界

- SQLite 适用于当前单机 2 核 4 GB 部署，不支持多实例并发写和数据库级行安全。
- 尚未实现管理员 MFA、风险操作二次确认和外部身份提供商。
- 管理员跨用户 Run 审计是高权限操作；后续应增加逐次访问原因和更细粒度审计。
- 用户删除、数据导出、隐私同意撤回和保留周期需要在账户生命周期迭代中补齐。
- 迁移到 PostgreSQL 后应使用数据库 RLS 作为应用层 `subject_id` 检查之外的第二道边界。
