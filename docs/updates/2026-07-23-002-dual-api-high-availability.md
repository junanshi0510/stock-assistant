# 2026-07-23 更新 002：API 双副本流量层与零停机滚动发布

## 1. 这次解决什么

上一版高可用控制面已经能发现故障、保存事故证据并关闭不安全能力，但 HTTP 流量仍只有一个 FastAPI 进程：

1. API 进程崩溃后，systemd 重启完成前全部动态请求中断；
2. 每次发布都整体重启 API，实测会出现短暂连接失败；
3. Nginx、systemd 健康检查和运维命令都把 `127.0.0.1:8000` 当成唯一真相；
4. 运行进程没有稳定的副本/release 身份，无法证明故障切换后是谁承接请求；
5. 控制面能看到数据库、队列和供应商，却看不到 API 副本丢失或版本漂移。

本次把流量层升级为：

```text
Browser
   |
   v
Nginx :80/:443
   |  least_conn + passive failure detection
   |  safe next-upstream policy (no non_idempotent replay)
   +-----------------------+
   |                       |
   v                       v
api-8001                api-8002
release SHA A           release SHA A
   |                       |
   +-----------+-----------+
               |
               v
      PostgreSQL / Redis / OSS

Celery scheduler
   -> probe api-8001 + api-8002
   -> immutable availability snapshot
   -> traffic quorum SLO + redundancy SLO
```

它解决的是单 API 进程和发布重启的可用性，不解决整台主机、Nginx、单 PostgreSQL 或单 Redis 故障。

## 2. 设计依据

- [Nginx HTTP load balancing](https://nginx.org/en/docs/http/load_balancing.html) 说明开源 Nginx 可用被动健康检查、`max_fails` 和 `fail_timeout` 暂时避开失败上游；本项目使用两个 loopback 副本和最少连接分配。
- [Nginx upstream module](https://nginx.org/en/docs/http/ngx_http_upstream_module.html) 定义上游组、失败计数和 keepalive；本项目把两个副本放入一个共享 zone。
- [Nginx proxy module](https://nginx.org/en/docs/http/ngx_http_proxy_module.html) 定义 `proxy_next_upstream` 和尝试次数。配置没有加入 `non_idempotent`，避免把可能已经提交的 POST/PATCH/DELETE 为了容灾重放到第二副本。
- systemd 模板实例使用 `%i` 将同一受限服务定义实例化到 `8001/8002`，两个进程分别拥有独立 cgroup、内存上限、日志身份和自动重启策略。

这些机制只形成同机进程级冗余；没有把被动上游检测包装成 Nginx Plus 主动健康检查，也没有宣称跨可用区。

## 3. 无状态与共享状态前提

双副本成立依赖以下事实：

- Session、CSRF 哈希、角色和账号状态在 PostgreSQL；
- 业务事实、幂等键、后台任务信封、租约和结果在 PostgreSQL；
- Redis 只传递任务 ID，不保存唯一业务事实；
- OCR 原件在私有 OSS，不在 API 本地目录；
- 生产 `TASK_QUEUE_MODE=celery`，API 不启动进程内 Agent、行情抓取或定时 Worker；
- 两个副本使用同一认证 Pepper、Cookie 配置、数据库和 Redis。

因此请求不需要粘性会话。若未来增加进程内 Session、临时上传文件或本地任务队列，必须先迁回共享存储，否则不能继续宣称副本可互换。

## 4. 副本与 release 身份

每个响应新增：

- `X-Stock-Assistant-Replica`：例如 `api-8001`；
- `X-Stock-Assistant-Release`：完整 Git commit SHA；
- `/health/live`、`/health/ready`、`/health/full` 的 `api_replica` 载荷，包含 schema、replica、release 和进程启动时间。

身份只允许字母、数字、点、下划线、冒号和连字符，不包含主机名、目录、PID 或凭据。release 默认读取发布目录根部的 `RELEASE_ID`；本地开发没有该文件时明确返回 `development`。

## 5. Nginx 故障切换边界

上游固定为：

```nginx
upstream stock_assistant_api {
    zone stock_assistant_api 64k;
    least_conn;
    include /etc/nginx/stock-assistant-api-upstreams.conf;
    keepalive 32;
}
```

正常 include 同时包含 `8001/8002`。滚动发布某个副本前，发布器原子改写 include，把目标副本标记为 `down`，执行 `nginx -t`、reload 并等待三秒排空，再停止或重启进程；目标副本通过身份与 readiness 后才恢复完整 include。这样部署期间的新写请求不会被送往正在重启的副本，也不需要开启非幂等请求重放。

连接错误、超时、无效响应头和 `502/503/504` 可以尝试另一个上游，最多两次、总切换等待最多五秒。没有启用 `non_idempotent`；写请求一旦已经发送，不由 Nginx 自动重放。应用自身的写操作仍必须使用 CSRF、事务、唯一约束和业务幂等键。

双上游模板继续在 Nginx 层统一发送 CSP、禁止 framing、MIME 嗅探保护、Referrer Policy 和 Permissions Policy；切换流量层不能以丢失原有浏览器安全头为代价。

公网健康检查只使用 `edge_readiness.v1` 的 `/health/edge`，响应仅含 ready、状态和脱敏 replica/release 身份。包含 PostgreSQL 目标、OSS Bucket、Worker 主机名、队列和 Schema 明细的 `/health/ready`、`/health/full` 由 Nginx 限制为 loopback，避免把内部依赖拓扑随高可用改造一起暴露。

这是被动故障检测：只有真实请求失败后 Nginx 才会暂时标记上游不可用。独立 systemd 检查与五分钟持久探针负责主动发现冗余丢失。

## 6. 内容寻址 release 与原子切换

`deploy/scripts/rollout-api-release.sh [git-ref]` 执行：

1. 获取目标完整 Git SHA，拒绝脏工作区和并发发布；
2. 用 `git archive` 在 `/opt/stock-assistant-releases/<SHA>` 创建隔离 release；
3. 在 release 内创建独立 `.venv` 并安装 Python 依赖，再执行 `npm ci` 和 Vite 构建；旧 release 不共享可变 site-packages；
4. release 由 root 持有并移除组/其他用户写权限，应用用户只读；
5. 在 upstream include 主动排空 8001，等待旧请求完成，再原子切换 `/opt/stock-assistant-api/8001`，重启并等待目标 replica/release readiness；
6. 恢复 8001 流量；只有它成功后才用同样流程切换 8002；
7. 原子切换 `/var/www/stock-assistant-current` 到同一 release 的前端构建；
8. `nginx -t`、reload 和 Nginx 路径 readiness 通过后，原子记录当前 release 状态。

API 进程的当前工作目录和 Python 解释器都在启动时解析到具体 release；后续切换槽位符号链接不会改变仍在运行进程的代码或依赖。前端不再先清空线上目录再复制，因此不会产生 JS/CSS 文件暂时不存在的窗口。

## 7. 自动回退与数据库约束

脚本在改变任何槽位后安装 EXIT 回退：

- 新副本在截止时间内未返回目标 replica/release，恢复该槽位旧目标；
- 第二副本失败时，先恢复第二副本，再恢复第一副本；
- 静态链接或 Nginx 验收失败时恢复旧静态 release、原 upstream include，并按反向顺序恢复 API；
- 首次引导没有旧槽位时，失败副本会被停用并移除槽位，不留下半配置服务；
- 文件锁保证同一时间只有一个滚动发布器。

自动代码回退不能自动撤销破坏性数据库迁移。滚动发布要求迁移采用 expand/contract：先增加新结构并同时兼容旧/新代码，等旧 release 不再可能回退后再单独清理旧结构。无法向后兼容的迁移必须进入明确维护窗口，不能假装零停机。

## 8. 控制面、能力与 SLO

生产标准探针从 16 个组件增加到 18 个：新增 `api_replica:api-8001` 和 `api_replica:api-8002`。

状态口径：

| 场景 | 副本状态 | API 流量能力 |
|---|---|---|
| 两副本在线且 release 相同 | 两个 operational | `redundant` |
| 一个在线、一个失联 | 在线 operational、失联 degraded | `reduced_redundancy`，仍可服务 |
| 两副本在线但 release 不同 | 两个 degraded | `reduced_redundancy` |
| 两副本都失联 | 两个 outage | `unavailable` |

端点配置只接受无凭据的 loopback HTTP origin，避免把运维环境变量变成 SSRF 通道。不可变探针只保存副本名、release、延迟、状态和错误类型，不保存 URL、主机名或系统路径。

SLO 分开计算：

- `API 流量可达`：任一副本 operational 即为好样本，默认目标 99.9%；
- `API 双副本冗余`：全部副本 operational 才是好样本，默认目标 99.0%。

这样单副本故障不会被错误统计为全站停机，但会消耗冗余错误预算。仍然只统计 scheduled 探针，发布和管理员手工探测不能刷可用率。

## 9. 验收计划

- 副本身份与 release 健康契约；
- 非 loopback、带凭据副本端点拒绝；
- 单副本丢失仍保留流量能力；
- API 流量 `any` 与双副本冗余 `all` 两种 SLO；
- Nginx 必须包含两个上游、失败窗口和两次尝试，且禁止 `non_idempotent`；
- systemd 模板必须使用独立槽位和副本身份；
- Bash 脚本语法、原子链接和反向恢复契约；
- 桌面与手机端副本卡片、宽表和控制台；
- 生产逐副本停止故障注入、持续公网请求、严格健康检查和自动滚动发布。

本地验收结果：后端全量 `499 passed`、`4 subtests passed`，前端生产构建与 `npm audit --omit=dev` 通过；桌面 `1440×1000` 和手机 `390×844` 已验证双副本卡片、18 个组件、SLO 与旧快照元数据回算，无页面级横向溢出或控制台错误。生产迁移、故障注入、自动回退和恢复验证结果见第 11 节。

## 10. 明确边界

- 两个副本共享同一云主机、内核、网络、Nginx、PostgreSQL、Redis 和电源故障域；主机宕机仍会全站中断。
- Nginx 开源版这里使用被动检测，不是独立外部主动探针；云端还需要从另一主机或云监控发起探测。
- Worker 仍按队列各一个进程，任务由 PostgreSQL 租约和 Redis 持久队列恢复，但不是每队列双活。
- 数据库和 Redis 仍是单实例；真正跨主机高可用需要托管主备/PITR、Redis Sentinel 或托管版、云负载均衡和跨可用区 API 副本。
- 高可用减少技术中断和错误动作，不产生投资收益，也不改变系统禁止自动交易的边界。

## 11. 生产发布结果

2026-07-23 已在 `8.148.67.79` 完成真实迁移与验收：

- 生产从旧 `:8000` 单实例无中断切到 `api-8001/api-8002`，旧服务已 disable，固定槽位、静态 current 链接和发布状态均指向同一 release；
- 第二次滚动发布对现网 `/health/ready` 持续采样 `866` 次，0 次非 200；每次先主动排空目标副本，再重启、核对身份并恢复 upstream；
- 分别强制停止 `8001`、`8002`，每个场景连续请求 `100` 次，0 次失败且响应全部来自仍存活的另一副本；
- 以不可达最终健康地址故意制造发布失败，脚本自动恢复 upstream、两个 API 槽位和静态 release；回退期间持续采样 `463` 次，0 次非 200；
- 公网 `/health/edge` 为 200 且只含 `api_replica,ready,schema_version,status`；公网 `/health/ready`、`/health/full`、`/internal/metrics/` 均为 403，管理员接口未登录为 401，CSP 与 framing 防护响应头保留；
- Celery Beat 已连续写入两份 18 组件 scheduled 快照，API 副本 `2/2` ready、release 一致、流量能力 `redundant`；严格 systemd 健康检查通过，部署后 API 与六个后台服务的 error 日志计数均为 0；
- 发布前后 PostgreSQL 备份均已加密上传私有 OSS；最新备份隔离恢复核对 `62` 张表和 `6` 个迁移标记；最终可用内存约 `2.0 GiB`、磁盘余量 `38 GiB`、swap 未使用。

行情能力仍按既有事实降级：Worker 重启后供应商运行态先显示“等待真实调用验证”，已确认的港股数据源事故继续保持 open，因此整体决策模式为只读降级；本次高可用发布没有把行情授权缺口伪装成正常，也没有改变该事故。
