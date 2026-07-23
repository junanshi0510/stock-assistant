# 金融投资助手生产部署

本文档描述当前生产架构。生产环境不再使用 SQLite 作为运行时数据库，也不允许 API 在 Redis、Worker 或 OSS 不可用时回退到进程内任务或本地文件。

## 目录

1. [生产架构](#1-生产架构)
2. [服务器要求](#2-服务器要求)
3. [安装运行依赖](#3-安装运行依赖)
4. [生产环境变量](#4-生产环境变量)
5. [初始化 PostgreSQL 与 Redis](#5-初始化-postgresql-与-redis)
6. [初始化私有 OSS](#6-初始化私有-oss)
7. [SQLite 首次迁移](#7-sqlite-首次迁移)
8. [安装服务](#8-安装服务)
9. [Nginx 与前端](#9-nginx-与前端)
10. [验收](#10-验收)
11. [备份与恢复演练](#11-备份与恢复演练)
12. [日志与监控](#12-日志与监控)
13. [日常发布](#13-日常发布)
14. [回滚](#14-回滚)

## 1. 生产架构

```text
Browser
  -> Nginx :80/:443
     -> React static files
     -> FastAPI api-8001 + api-8002
        (least_conn, passive failover, release identity)
        -> PostgreSQL (authoritative state, audit chains, jobs)
        -> Redis (task transport only; messages contain IDs)

Celery queues
  agent       -> Agent orchestration worker
  market-data -> fund/stock/sector/news data worker
  llm         -> DeepSeek synthesis worker
  ocr         -> Alibaba Cloud OCR worker + private OSS
  scheduler   -> durable schedules and stale-lease recovery

Operations
  journald JSON logs
  /internal/metrics (localhost only, each API replica)
  /health/ready (authoritative read traffic)
  /health/full + two-minute two-replica strict systemd health check
  five-minute persisted availability probe + incident/SLO control plane
  content-addressed release + atomic per-replica rollout/rollback
  daily PostgreSQL dump -> encrypted OSS
  weekly isolated restore drill
```

PostgreSQL 保存所有输入、结果、租约、幂等键和不可变事件链。Redis 不是事实源，Redis 丢失后由 scheduler 从 PostgreSQL 重新派发尚未完成的任务。

当前拓扑是同机双 API 副本：单个 API 进程崩溃或逐副本发布时，Nginx 可把流量交给另一副本；两个副本使用共享 PostgreSQL Session/CSRF/幂等事实，不需要粘性会话。主机、Nginx、PostgreSQL 或 Redis 整体故障仍会中断。需要主机或可用区级容灾时，应增加跨主机/跨区 API 副本、云负载均衡、托管 PostgreSQL 主备/跨区备份、Redis 高可用和独立监控执行点。

## 2. 服务器要求

- Ubuntu 24.04 LTS。
- 2 核 4G 是当前最低生产配置，建议增加 2G swap；用户量或并发批次增加后升级到 4 核 8G。
- 根分区至少保留 15GB 可用空间。
- 安全组只开放 `22`、`80`、`443`。不要开放 `5432`、`6379`、`8000`、`8001` 或 `8002`。
- PostgreSQL、Redis、FastAPI 只监听本机地址。
- 需要独立的阿里云 RAM 身份，最小授权 OCR 与指定 OSS Bucket，不使用主账号 AccessKey。

## 3. 安装运行依赖

```bash
sudo apt update
sudo apt install -y \
  python3 python3-venv python3-pip \
  postgresql postgresql-client redis-server \
  nginx git sqlite3 openssl curl

sudo useradd --system --home-dir /var/lib/stock-assistant \
  --shell /usr/sbin/nologin stockassistant 2>/dev/null || true
sudo install -d -o stockassistant -g stockassistant -m 0700 /var/lib/stock-assistant
sudo install -d -m 0750 /etc/stock-assistant
sudo install -d -m 0700 /var/backups/stock-assistant/postgresql
```

安装项目：

```bash
cd /opt/stock-assistant
python3 -m venv venv
/opt/stock-assistant/venv/bin/pip install -r backend/requirements.txt
```

## 4. 生产环境变量

唯一环境文件为 `/etc/stock-assistant/stock-assistant.env`，必须由 root 持有且权限为 `600`：

```bash
sudo cp deploy/stock-assistant.env.example /etc/stock-assistant/stock-assistant.env
sudo chown root:root /etc/stock-assistant/stock-assistant.env
sudo chmod 600 /etc/stock-assistant/stock-assistant.env
```

至少配置：

```ini
DATABASE_URL=postgresql://stockassistant_app:URL编码密码@127.0.0.1:5432/stock_assistant
POSTGRES_ADMIN_URL=postgresql://stockassistant_backup:URL编码密码@127.0.0.1:5432/postgres
REDIS_URL=redis://:URL编码密码@127.0.0.1:6379/0
TASK_QUEUE_MODE=celery

# 固定探针、连续确认、积压阈值与内部 SLO（不是对外 SLA）。
AVAILABILITY_PROBE_INTERVAL_SECONDS=300
AVAILABILITY_STALE_SECONDS=900
AVAILABILITY_FAILURE_THRESHOLD=2
AVAILABILITY_RECOVERY_THRESHOLD=2
AVAILABILITY_QUEUE_WARN_DEPTH=100
AVAILABILITY_QUEUE_OUTAGE_DEPTH=1000
AVAILABILITY_SLO_CORE=99.9
AVAILABILITY_SLO_BACKGROUND=99.0
AVAILABILITY_SLO_PRIVATE_ASSET=99.0
AVAILABILITY_SLO_MARKET=95.0
API_REPLICA_ENDPOINTS=api-8001=http://127.0.0.1:8001,api-8002=http://127.0.0.1:8002
AVAILABILITY_SLO_API_TRAFFIC=99.9
AVAILABILITY_SLO_API_REDUNDANCY=99.0

# 生产热门榜支持多专业源接力。Key 只写入本文件；不要写入 Git、前端或 systemd unit。
TUSHARE_TOKEN=服务端Token
MASSIVE_API_KEY=服务端Key
MASSIVE_API_BASE_URL=https://api.massive.com
# 旧 Polygon Key 仍兼容；新部署优先使用 MASSIVE_API_KEY。
POLYGON_API_KEY=
ALPHAVANTAGE_API_KEY=服务端Key
# 留空为日终；只有订阅明确授权时才设置 delayed 或 realtime。
ALPHAVANTAGE_MARKET_DATA_ENTITLEMENT=

# 可选富途实时源：只有同机或内网 OpenD 已安全运行并登录后才填写 HOST。
FUTU_OPEND_HOST=
FUTU_OPEND_PORT=11111
FUTU_OPEND_MARKETS=A,H,US
FUTU_SNAPSHOT_BATCH_SIZE=400
HOT_STOCK_US_MIN_PRICE=1
HOT_STOCK_US_MIN_VOLUME=10000
HOT_STOCK_PUBLIC_FALLBACK_ENABLED=true
HOT_STOCK_PROVIDER_FAILURE_THRESHOLD=2
HOT_STOCK_PROVIDER_CIRCUIT_SECONDS=300

AUTH_AUDIT_PEPPER=至少32字节随机值
AUTH_COOKIE_SECURE=false

# The internal endpoint region must match the server; production is cn-wuhan-lr.
OSS_REGION=cn-wuhan-lr
OSS_BUCKET=全局唯一私有Bucket名
OSS_USE_INTERNAL_ENDPOINT=true
OSS_SSE_MODE=AES256
OBJECT_KEY_PEPPER=至少32字节随机值
REQUIRE_OBJECT_STORAGE=true

ALIBABA_CLOUD_ACCESS_KEY_ID=RAM用户AccessKeyId
ALIBABA_CLOUD_ACCESS_KEY_SECRET=RAM用户AccessKeySecret
ALIYUN_OCR_ENDPOINT=ocr-api.cn-hangzhou.aliyuncs.com

LLM_PROVIDER=deepseek
LLM_MODEL=deepseek-chat
DEEPSEEK_API_KEY=服务端Key
```

纯 IP HTTP 阶段使用 `AUTH_COOKIE_SECURE=false`。配置域名和 HTTPS 后改为 `true` 并重启 API。任何 Key 都不能写入 Git、前端变量或命令输出。

`TUSHARE_TOKEN` 必须实际拥有所用 A 股日线权限；港股日线/基础资料可能需要单独开通。Massive 免费档提供最近完整日终全市场聚合，不能标记为盘中实时；默认价格/成交量门槛用于避免极低流动性标的污染榜首。Alpha Vantage 留空 entitlement 时按日终榜使用，不能把免费或日终权限标记为实时。富途路线只有在 FutuOpenD 常驻、登录有效、行情权限与 `FUTU_OPEND_MARKETS` 一致时才算配置完成；OpenD 端口只允许本机或受控内网访问，不能直接暴露公网。公开降级默认开启只用于迁移期；专业源验收稳定后可设 `HOT_STOCK_PUBLIC_FALLBACK_ENABLED=false`。修改这些变量后至少重启 `stock-assistant-market-worker`。

## 5. 初始化 PostgreSQL 与 Redis

推荐角色：

- `stockassistant_app`：数据库 owner，仅供应用连接。
- `stockassistant_backup`：仅具备 `LOGIN, CREATEDB`，用于隔离恢复演练，不保存 PostgreSQL 超级用户密码。

创建角色和空数据库后，确认：

```bash
psql "$DATABASE_URL" -Atqc 'select current_database(), current_user'
```

Redis 至少配置：

```conf
bind 127.0.0.1 ::1
protected-mode yes
appendonly yes
appendfsync everysec
maxmemory 256mb
maxmemory-policy noeviction
requirepass <随机密码>
```

重启并检查：

```bash
sudo systemctl restart postgresql redis-server
redis-cli -u "$REDIS_URL" ping
```

必须返回 `PONG`。禁止使用 `allkeys-lru`，否则积压任务可能被静默淘汰。

## 6. 初始化私有 OSS

配置环境变量后执行幂等初始化：

```bash
cd /opt/stock-assistant/backend
sudo bash -c '
  set -a
  source /etc/stock-assistant/stock-assistant.env
  set +a
  exec runuser -u stockassistant --preserve-environment -- \
    /opt/stock-assistant/venv/bin/python -m provision_object_storage
'
```

该命令会创建或校验专用 Bucket，并强制：

- Bucket ACL 为 `private`。
- Bucket 级公共访问阻断开启。
- OCR 原图 `private/holding-ocr/` 两天后删除。
- PostgreSQL 异地备份保留 180 天。
- 未完成分片上传七天后清理。
- 所有应用上传对象使用 OSS 服务端加密。

权限、区域、ACL 或生命周期校验失败时命令非零退出，不会改用本地文件。

## 7. SQLite 首次迁移

仅首次升级执行。目标 PostgreSQL 必须是空数据库。

```bash
cd /opt/stock-assistant
sudo bash -c '
  set -a
  source /etc/stock-assistant/stock-assistant.env
  set +a
  TARGET_DATABASE_URL="$DATABASE_URL" \
    SOURCE_SQLITE=/var/lib/stock-assistant/stock_assistant.db \
    /opt/stock-assistant/deploy/scripts/cutover-sqlite-to-postgres.sh
'
```

脚本执行顺序：

1. 在旧 API 运行期间创建一份 SQLite 在线备份并执行 `integrity_check`。
2. 停止 API 和 Worker。
3. 创建最终 SQLite 一致性备份及 SHA-256。
4. 迁移全部业务表、索引和外键。
5. 在同一个 PostgreSQL 事务内安装平台表和审计触发器。
6. 逐表比较源/目标行数和规范化内容 SHA-256。
7. 检测迁移期间 SQLite 主文件/WAL 是否发生写入。

任一检查失败，PostgreSQL 事务回滚并尝试恢复旧 SQLite API。成功后 SQLite 文件和两份备份仍保留，不自动删除。

## 8. 安装服务

```bash
cd /opt/stock-assistant
sudo cp deploy/stock-assistant-*.service /etc/systemd/system/
sudo cp deploy/stock-assistant-*.timer /etc/systemd/system/
sudo chmod 0755 deploy/scripts/*.sh
sudo systemctl daemon-reload

sudo systemctl enable --now \
  stock-assistant-agent-worker \
  stock-assistant-market-worker \
  stock-assistant-llm-worker \
  stock-assistant-ocr-worker \
  stock-assistant-scheduler-worker \
  stock-assistant-celery-beat

sudo systemctl enable --now \
  stock-assistant-healthcheck.timer \
  stock-assistant-backup.timer \
  stock-assistant-backup-verify.timer

# 从当前已提交 SHA 创建只读 release 与独立 .venv，依次启动并验证 8001/8002。
# 首次安装时 Nginx 还未指向双上游，所以让发布器用 8001 做最终直连冒烟。
sudo env PUBLIC_HEALTH_URL=http://127.0.0.1:8001/health/ready \
  /opt/stock-assistant/deploy/scripts/rollout-api-release.sh HEAD
```

所有 Worker 使用同一代码版本和环境文件，但使用独立队列、并发和内存上限。两个 API 实例使用同一个 `stock-assistant-api@.service` 模板、不同固定槽位和端口；每个 release 拥有独立 Python `.venv`，回退不复用已升级依赖。API 单元中禁止进程内 Agent 与定时 Worker。发布器拒绝脏工作区、非 Git commit、并发发布和非符号链接槽位。

## 9. Nginx 与前端

```bash
sudo cp /opt/stock-assistant/deploy/nginx-stock-assistant.conf \
  /etc/nginx/sites-available/stock-assistant
sudo cp /opt/stock-assistant/deploy/stock-assistant-api-upstreams.conf \
  /etc/nginx/stock-assistant-api-upstreams.conf
sudo ln -sfn /etc/nginx/sites-available/stock-assistant \
  /etc/nginx/sites-enabled/stock-assistant
sudo nginx -t
sudo systemctl reload nginx
```

修改模板中的 `server_name`。Nginx 使用 `127.0.0.1:8001/8002` 两个上游，独立 include `/etc/nginx/stock-assistant-api-upstreams.conf` 由发布器原子维护；滚动更新会先把目标副本标记为 `down`、reload 并等待排空，再重启该副本。前端根目录为发布器原子维护的 `/var/www/stock-assistant-current`。公网只开放拓扑脱敏的 `/health/edge`；详细 `/health/ready`、`/health/full` 与 `/internal/metrics` 只能由 `127.0.0.1` 和 `::1` 访问。配置没有启用 `non_idempotent`，因此不会为了切换上游重放可能已提交的写请求。

从旧单实例无中断迁移时，先保持 `stock-assistant-api.service :8000` 运行，执行上一节发布器并确认两个新副本 ready，再复制和 reload 新 Nginx 配置；公网验证通过后才执行：

```bash
sudo systemctl disable --now stock-assistant-api.service
```

不要在双副本 ready 之前让 Nginx 指向新上游。

## 10. 验收

```bash
for port in 8001 8002; do
  curl -fsS "http://127.0.0.1:${port}/health/live"
  curl -fsS "http://127.0.0.1:${port}/health/ready"
  curl -fsS "http://127.0.0.1:${port}/health/full"
  curl -fsS "http://127.0.0.1:${port}/internal/metrics/" | head
done

curl -fsS -D - http://127.0.0.1/health/ready -o /dev/null \
  | grep -Ei 'X-Stock-Assistant-(Replica|Release)'

sudo systemctl --no-pager --failed
sudo systemctl status 'stock-assistant-*' --no-pager
sudo -u postgres psql stock_assistant -Atqc \
  "select count(*) from platform_schema_migrations"
```

`/health/ready` 在 PostgreSQL 和全部生产 Schema 可安全提供权威事实时返回 `200`；`/health/full` 还要求 Redis、私有 OSS 和五个队列 Worker 全部可用。每份健康响应必须包含正确的 `api_replica` 和完整 release SHA。systemd 严格监控要求两个副本 ready 且至少一个报告 full；Nginx 在只剩一个副本时继续服务。公网未登录访问业务 API 必须返回 `401`，包括 `/api/platform/availability`、`/api/admin/availability` 和主动探测接口。

首次迁移并启动全部 Worker 后，以部署身份执行一次标准探测，确认控制面形成首份快照：

```bash
cd /opt/stock-assistant/backend
sudo bash -lc '
  set -a
  source /etc/stock-assistant/stock-assistant.env
  set +a
  exec runuser -u stockassistant --preserve-environment -- \
    /opt/stock-assistant/venv/bin/python -c \
    "import availability_service; print(availability_service.run_probe(trigger_type=\"deployment\", actor_id=\"system:deployment\")[\"id\"])"
'
```

管理员页面应显示 18 个生产组件和两个 API 副本卡片；普通用户只能读取脱敏能力矩阵。连续两次失败才开启事故，连续两次成功才恢复；不要用手工探测补齐 SLO，24 小时/7 天/30 天窗口只统计 Celery Beat 的 `scheduled` 样本。API 流量 SLO 是“任一副本成功”，API 冗余 SLO 是“全部副本成功”，两者不能混用。

受控故障注入必须在低流量窗口执行，并始终一次只停一个副本：

```bash
sudo systemctl stop stock-assistant-api@8001.service
for attempt in $(seq 1 100); do
  curl -fsS --max-time 3 http://127.0.0.1/health/ready >/dev/null || exit 1
done
sudo systemctl start stock-assistant-api@8001.service
curl -fsS http://127.0.0.1:8001/health/ready >/dev/null

sudo systemctl stop stock-assistant-api@8002.service
for attempt in $(seq 1 100); do
  curl -fsS --max-time 3 http://127.0.0.1/health/ready >/dev/null || exit 1
done
sudo systemctl start stock-assistant-api@8002.service
curl -fsS http://127.0.0.1:8002/health/ready >/dev/null
```

任一步失败立即停止演练并恢复被停副本。两个副本恢复后，`stock-assistant-healthcheck.service` 必须重新返回 success。

登录后还应在“机会工厂”或“发现股票”检查专业行情数据中台。`GET /api/market/providers` 只返回每市场全部候选路线的配置状态，不会泄露 Key，也不会主动消耗供应商额度。点击“真实连通性验证”会调用 `POST /api/market/providers/probe`，仅尝试专业源并在 30 秒内复用探测结果，不会偷偷回退公开网页。随后分别执行 A 股、港股、美股三榜冒烟；必须确认 `provider_tier=professional`、`degraded=false`、`as_of`/`data_freshness` 和 `data_quality` 符合订阅。美股 7/30 日榜还应确认 `full_market_multiday=true`，否则只是明确标记的活跃候选池降级计算。

## 11. 备份与恢复演练

立即执行一次备份：

```bash
sudo systemctl start stock-assistant-backup.service
sudo journalctl -u stock-assistant-backup.service -n 100 --no-pager
```

备份成功条件：

- `pg_dump` 自定义压缩格式成功。
- `pg_restore --list` 可读取。
- 本地 SHA-256 已生成。
- 备份和校验文件已上传到私有 OSS 且确认服务端加密。
- 只有 OSS 上传成功后才清理超过保留期的本地备份。

立即执行隔离恢复演练：

```bash
sudo systemctl start stock-assistant-backup-verify.service
sudo journalctl -u stock-assistant-backup-verify.service -n 100 --no-pager
```

恢复演练创建临时数据库、完整恢复、核对表数和迁移标记，随后删除临时数据库。默认每日备份、每周恢复演练。

## 12. 日志与监控

服务输出单行 JSON 到 journald，自动隐藏 API Key、AccessKey、密码、Authorization 和带认证信息的数据库/Redis URL。

```bash
sudo journalctl -u 'stock-assistant-api@*' -f -o cat
sudo journalctl -u stock-assistant-market-worker -f -o cat
sudo journalctl -u stock-assistant-healthcheck.service --since today --no-pager
```

Prometheus 应分别抓取 `http://127.0.0.1:8001/internal/metrics/` 与 `:8002/internal/metrics/`，并使用 replica 标签区分进程；指标包括 HTTP 延迟、状态码、并发请求、队列深度、Celery 任务结果、探针总数、组件当前状态和事故转换数。systemd 每两分钟要求两个副本 ready；Celery Beat 每五分钟把副本 quorum、release 一致性和其他组件快照写入 PostgreSQL，后台失败不会依赖 journald 临时日志才能追溯。

建议配置持久 journald：

```ini
# /etc/systemd/journald.conf.d/stock-assistant.conf
[Journal]
Storage=persistent
SystemMaxUse=1G
MaxRetentionSec=30day
Compress=yes
```

修改后执行 `sudo systemctl restart systemd-journald`。

## 13. 日常发布

```bash
cd /opt/stock-assistant
git pull --ff-only

# 首次发布包含数据库结构升级的版本时，必须先完成一份 PostgreSQL + OSS 备份，
# 再由 root 只把环境变量注入迁移进程；命令不会打印数据库凭据。
sudo systemctl start stock-assistant-backup.service
sudo bash -lc '
  set -a
  source /etc/stock-assistant/stock-assistant.env
  set +a
  cd /opt/stock-assistant/backend
  /opt/stock-assistant/venv/bin/python -m migrations.opportunity_factory_v1
  /opt/stock-assistant/venv/bin/python -m migrations.portfolio_decision_twin_v1
  /opt/stock-assistant/venv/bin/python -m migrations.portfolio_valuation_v1
  /opt/stock-assistant/venv/bin/python -m migrations.availability_control_v1
'

# Worker 消息只有持久任务 ID，可逐个重启；不要一次停止全部服务。
for service in \
  stock-assistant-agent-worker \
  stock-assistant-market-worker \
  stock-assistant-llm-worker \
  stock-assistant-ocr-worker \
  stock-assistant-scheduler-worker \
  stock-assistant-celery-beat; do
  sudo systemctl restart "$service"
  sudo systemctl is-active --quiet "$service" || exit 1
done

# 创建含独立依赖的内容寻址 release、依次切换两个 API、原子切换静态资源；失败自动恢复旧槽位。
sudo /opt/stock-assistant/deploy/scripts/rollout-api-release.sh HEAD

curl -fsS http://127.0.0.1:8001/health/full
curl -fsS http://127.0.0.1:8002/health/full
curl -fsS http://127.0.0.1/health/ready
```

`opportunity-factory.v1` 会在单个 PostgreSQL 事务和 advisory lock 内建立 6 张机会工厂表、不可变触发器和迁移标记；`portfolio-decision-twin.v1` 会建立用户隔离的 `portfolio_twin_runs` 表；`portfolio-valuation.v1` 会建立共享公开行情观察与用户隔离估值快照两张表；`availability-control.v1` 会建立不可变探针与事故事件两张表及哈希链所需索引。失败会整体回滚，首次成功后无需在无数据库变更的日常发布中重复执行。数据库结构升级必须先备份并执行对应迁移，不能依赖应用启动时自动建表；readiness 必须同时返回 `opportunity_schema=true`、`portfolio_twin_schema=true`、`portfolio_valuation_schema=true` 和 `availability_schema=true` 才能接流量。

## 14. 回滚

代码回滚与数据回滚分开处理。API 与前端可以滚动回到仍存在于 Git 的已验证 commit：

```bash
sudo /opt/stock-assistant/deploy/scripts/rollout-api-release.sh <known-good-full-sha>
```

该命令仍逐副本验证并在失败时恢复执行前槽位。完整回滚还需要让 Worker 工作区切回同一兼容版本并逐个重启。数据库规则：

1. 日常迁移必须 expand/contract，同时兼容当前和上一 release；无法兼容时必须维护窗口，不能使用滚动发布。
2. 若 PostgreSQL 数据有效，只回滚代码，不恢复旧 SQLite。
3. 只有明确决定放弃切换后的全部 PostgreSQL 写入时，才停止 API、Beat 和所有 Worker，并恢复数据库备份。
4. 恢复前再次备份当前 PostgreSQL，保留迁移报告、release ID 和服务日志。

SQLite 回滚会丢失切换后在 PostgreSQL 产生的用户、持仓、交易和 Agent 任务，必须由管理员明确批准，不能自动执行。
