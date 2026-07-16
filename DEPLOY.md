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
     -> FastAPI :8000
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
  /internal/metrics (localhost only)
  /health/ready + two-minute systemd health check
  daily PostgreSQL dump -> encrypted OSS
  weekly isolated restore drill
```

PostgreSQL 保存所有输入、结果、租约、幂等键和不可变事件链。Redis 不是事实源，Redis 丢失后由 scheduler 从 PostgreSQL 重新派发尚未完成的任务。

## 2. 服务器要求

- Ubuntu 24.04 LTS。
- 2 核 4G 是当前最低生产配置，建议增加 2G swap；用户量或并发批次增加后升级到 4 核 8G。
- 根分区至少保留 15GB 可用空间。
- 安全组只开放 `22`、`80`、`443`。不要开放 `5432`、`6379`、`8000`。
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

AUTH_AUDIT_PEPPER=至少32字节随机值
AUTH_COOKIE_SECURE=false

OSS_REGION=cn-hangzhou
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
  stock-assistant-celery-beat \
  stock-assistant-api

sudo systemctl enable --now \
  stock-assistant-healthcheck.timer \
  stock-assistant-backup.timer \
  stock-assistant-backup-verify.timer
```

所有 Worker 使用同一代码版本和环境文件，但使用独立队列、并发和内存上限。API 单元中禁止进程内 Agent 与定时 Worker。

## 9. Nginx 与前端

```bash
cd /opt/stock-assistant/frontend
npm ci
npm run build
sudo install -d -m 0755 /var/www/stock-assistant
sudo find /var/www/stock-assistant -mindepth 1 -maxdepth 1 -exec rm -rf -- {} +
sudo cp -a dist/. /var/www/stock-assistant/

sudo cp /opt/stock-assistant/deploy/nginx-stock-assistant.conf \
  /etc/nginx/sites-available/stock-assistant
sudo ln -sfn /etc/nginx/sites-available/stock-assistant \
  /etc/nginx/sites-enabled/stock-assistant
sudo nginx -t
sudo systemctl reload nginx
```

修改模板中的 `server_name`。`/internal/metrics` 只能由 `127.0.0.1` 和 `::1` 访问。

## 10. 验收

```bash
curl -fsS http://127.0.0.1:8000/health/live
curl -fsS http://127.0.0.1:8000/health/ready
curl -fsS http://127.0.0.1:8000/internal/metrics/ | head

sudo systemctl --no-pager --failed
sudo systemctl status 'stock-assistant-*' --no-pager
sudo -u postgres psql stock_assistant -Atqc \
  "select count(*) from platform_schema_migrations"
```

`/health/ready` 只有在数据库、Redis、OSS 和五个队列 Worker 全部可用时才返回 `200`。公网未登录访问业务 API 必须返回 `401`。

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
sudo journalctl -u stock-assistant-api -f -o cat
sudo journalctl -u stock-assistant-market-worker -f -o cat
sudo journalctl -u stock-assistant-healthcheck.service --since today --no-pager
```

Prometheus 指标位于 `http://127.0.0.1:8000/internal/metrics/`，包括 HTTP 延迟、状态码、并发请求、队列深度和 Celery 任务结果。systemd 每两分钟执行一次完整 readiness 检查，失败记录可由阿里云云监控采集并告警。

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
/opt/stock-assistant/venv/bin/pip install -r backend/requirements.txt

cd frontend
npm ci
npm run build
sudo find /var/www/stock-assistant -mindepth 1 -maxdepth 1 -exec rm -rf -- {} +
sudo cp -a dist/. /var/www/stock-assistant/

sudo systemctl restart \
  stock-assistant-agent-worker \
  stock-assistant-market-worker \
  stock-assistant-llm-worker \
  stock-assistant-ocr-worker \
  stock-assistant-scheduler-worker \
  stock-assistant-celery-beat \
  stock-assistant-api
sudo nginx -t && sudo systemctl reload nginx
curl -fsS http://127.0.0.1:8000/health/ready
```

数据库结构升级必须先备份并执行对应迁移，不能依赖应用启动时自动建表。

## 14. 回滚

代码回滚与数据回滚分开处理：

1. 停止 API、Beat 和所有 Worker，防止继续写入。
2. 代码回滚到已验证提交。
3. 若 PostgreSQL 数据有效，只回滚代码，不恢复旧 SQLite。
4. 只有明确决定放弃切换后的全部 PostgreSQL 写入时，才移除 `DATABASE_URL/REDIS_URL` 并恢复最终 SQLite 备份。
5. 恢复前再次备份当前 PostgreSQL，保留迁移报告和服务日志。

SQLite 回滚会丢失切换后在 PostgreSQL 产生的用户、持仓、交易和 Agent 任务，必须由管理员明确批准，不能自动执行。
