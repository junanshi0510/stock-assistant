# 金融投资助手

一个面向个人投资者的真实数据决策工作台，覆盖公募基金、A 股、港股、美股和用户真实持仓。

当前版本使用 **React + Vite** 构建前端，使用 **FastAPI + Python** 完成数据获取、基金与股票研究、组合账本和确定性计算。项目正在按照工业级投资 Agent PRD 逐步升级，但当前线上版本仍是“专业投资工作台”，不是已经完成的自主 Agent，也不具备自动交易能力。

> 风险提示：系统输出用于研究和风险复盘，不代表未来涨跌，不构成投资建议，也不承诺收益。数据源不可用、数据过期或用户持仓不完整时，系统必须明确显示缺口，不使用模拟数据补齐。

## 最近更新

### 2026-07-12：Agent 运行历史

- 新增 `GET /api/v1/agent/runs`，支持轻量摘要、状态/基金代码筛选和游标分页。
- 历史查询固定按服务端 `tenant_id + user_id` 范围读取，为后续登录和租户隔离保留协议边界。
- Agent 工作台新增最近研究任务面板，可回看完整 Run，并按需加载更早任务。
- 历史列表不返回 Evidence 原始载荷；只有打开具体 Run 和 Evidence 时才读取详细数据。
- 增加隔离、筛选、分页与接口契约测试，并完成桌面端和手机端验证。

## 当前能力

| 工作区 | 主要能力 |
|---|---|
| 投资总览 | 汇总真实持仓、投资约束、组合风险、数据缺口和市场机会日报，生成有优先级的复盘任务 |
| 投资 Agent | 创建可恢复的基金深度研究 Run，执行版本化 R0 只读工具，保存 Step、Evidence、Claim 和追加式审计链 |
| 基金中心 | 基金发现与搜索、真实净值分析、盘中估值、回撤与恢复、同类排名、替代品、分红、定期报告持仓、披露变化、多基金比较与重合度 |
| 股票与板块 | A 股/港股/美股行情、热门榜、行业与概念、个股技术面和基本面、多股比较、批量筛选、新闻情绪与历史信号回测 |
| 我的组合 | 手动、文本、CSV/XLSX 和 OCR 持仓导入，基金名称反查、组合体检、穿透暴露、交易流水、FIFO 成本、XIRR、行为复盘、快照归因和仓位纪律 |
| 自选与提醒 | 自选股票、评分刷新和提醒记录 |

### 基金研究

- 基金分类、热门榜和风险偏好机会筛选。
- 单基金净值趋势、区间收益、波动、最大回撤和恢复过程。
- 已确认净值与盘中估值分开显示，估值不会替代确认净值。
- 同类排名、同类分位和多维替代品比较。
- 最新定期报告持仓、前后披露期变化和风格变化线索。
- 多基金相关性、重仓股/行业重合以及用户持仓穿透暴露。

### 投资 Agent 第一阶段

- 当前支持固定意图 `fund_deep_research`，默认只读取公募基金确认净值并执行确定性风险计算。
- Agent Run、工具步骤、证据、结论引用和审计事件持久化到 SQLite，进程重启后可恢复未完成任务并复用已完成证据。
- 运行历史支持服务端游标分页、状态筛选、基金代码筛选和完整任务回看。
- 工具通过版本化白名单注册，当前仅开放 R0 公共只读工具；每个工具都有实际生效的执行时限。
- 请求支持幂等键、活动队列限制、运行中取消、可选盘中估值、披露变化和同类替代品研究。
- 每条数值 Claim 绑定 Evidence，Evidence 保存来源、有效时间、质量状态和 SHA-256 摘要。
- 真实来源失败时 Run 进入 `partial` 或 `failed`，不会生成替代数据。

当前阶段没有自主规划大模型、私人持仓工具、交易工具或自动下单。SQLite 和单进程 Worker 是迁移期实现，不等同于最终的 PostgreSQL + Temporal 生产架构。

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
| `AGENT_WORKER_ENABLED` | 是否启动内置持久化 Worker；默认开启 |
| `AGENT_WORKER_POLL_SECONDS` | 内置 Worker 轮询间隔；默认 `0.75` 秒 |
| `FUND_HTTP_TRUST_ENV` | 基金请求是否使用环境代理；设为 `0`/`direct` 时强制直连 |

前后端分离部署时，参考 `frontend/.env.example` 配置 `VITE_API_BASE_URL`。不要把真实 Key、服务器密码或用户数据提交到 Git。

## 项目结构

```text
backend/
  main.py                  FastAPI 启动与路由装配
  agent/
    registry.py            版本化工具白名单
    repository.py          Run、Step、Evidence、Claim 与 Audit 持久化
    workflow.py            确定性基金研究工作流、超时与取消
    worker.py              可恢复的迁移期单进程 Worker
  routers/
    agent.py               Agent Run、Evidence 和 Audit API
    market.py              股票、板块、行情和市场日报接口
    funds.py               基金发现、研究、比较和替代品接口
    portfolio.py           持仓、交易、OCR、复盘和提醒接口
  funds.py                 基金净值、风险、披露和比较领域逻辑
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
