# A 股可运行量化基准与最低权限研究层

发布日期：2026-07-26

## 1. 问题与目标

量化研究计划上线后的生产探针发现，原有 A 股默认路径存在两个实际阻断：

1. 沪深 300 基准使用 `510300` ETF 代理代码，但通用股票行情链在当前云端不能稳定取得 ETF 历史；
2. 第一个默认预设同时需要 Tushare `index_weight` 与 `fina_indicator`，而当前账号只具备 `daily_basic` 与 `index_daily` 能力，用户点击默认入口会必然失败。

本次不是放宽研究门槛，而是把产品分成诚实的三层：

```text
立即可运行研究层
  = 冻结当前名单
  + 真实股票日线
  + 真实指数基准
  → 可以研究
  → 因幸存者偏差固定 research_only

时点估值研究层
  = 上述价格研究
  + daily_basic 历史 PE/PB
  + 足够批量额度或已预热的本地历史缓存
  → 当前每小时 1 次的账号不能即时运行

专业历史验证层
  = index_weight 历史股票池
  + fina_indicator 公告日财务
  + daily_basic 时点估值
  + 专业复权/未复权双价格
  → 才可能通过全部门槛并进入纸面前向验证
```

系统仍不预测“必涨/必跌”，不连接券商，不生成股数，不自动下单，也不承诺盈利。

## 2. A 股指数专用行情链

支持三个研究基准：

| 指数 | 标准代码 | Tushare | BaoStock 降级代码 |
| --- | --- | --- | --- |
| 沪深 300 | `000300.SH` | `index_daily` | `sh.000300` |
| 中证 500 | `000905.SH` | `index_daily` | `sh.000905` |
| 中证 1000 | `000852.SH` | `index_daily` | `sh.000852` |

`data_fetch.get_history()` 会识别上述代码并切换到指数专用源：

```text
Tushare index_daily
  → 失败时 BaoStock 指数日线
  → 两者都失败才返回聚合错误
```

指数基准不经过 `pro_bar + adj_factor` 股票复权链，也不读取“未复权指数”。在量化证据中它被标为：

```json
{
  "raw_source": "benchmark_index_level_not_applicable",
  "raw_requested": false
}
```

原因是指数只衡量相对收益，不是候选股票，不参与成交执行或容量证明。候选股票的专业双价格覆盖率分母保持不变，不会被基准指数抬高。

通用纸面组合观察的 A 股基准也同步改为 `000300.SH`。旧数据中的 `510300`、`510500`、`512100` 名称映射仍保留，因此历史 mandate 可以继续读取。

## 3. 默认基础权限预设

新增并设为首个预设：

```text
A股价格多因子研究池（可直接运行）
```

冻结样本共 12 只：

```text
600519  300750  601318  600036
000858  000333  002594  600900
601899  600276  000651  601088
```

固定因子权重：

| 因子 | 权重 |
| --- | ---: |
| 中期动量 | 35% |
| 趋势质量 | 25% |
| 低波动 | 25% |
| 流动性 | 15% |
| 披露日财务质量 | 0% |
| 时点估值 | 0% |

该预设不请求 Tushare [`daily_basic`](https://tushare.pro/document/2?doc_id=32)、[`fina_indicator`](https://tushare.pro/document/2?doc_id=79) 或 `index_weight`，因此不受当前账号财务接口频次阻断。

股票池是今天冻结的名单，因此回看过去存在幸存者偏差。即使收益、成本和回撤看起来很好，系统也不会允许它自动冻结 shadow mandate。这是基础权限下“能跑但不冒充专业历史验证”的明确边界。

### 3.1 配额安全价格路径

首次云端整链探针发现，12 只股票并发尝试 `pro_bar` 时，低额度 `adj_factor` 会反复拒绝请求，并连带消耗同窗口的 `daily` 额度。之后虽然 `daily_basic` 本身有权限，也会因为前置价格请求形成的调用风暴而无法加载足够股票。

冻结当前名单本来就不能通过无幸存者偏差门槛，因此先消耗专业复权额度没有晋级价值。基础预设现在使用独立源配置：

```text
source_profile = a_share_research

BaoStock 前复权
  → 腾讯证券前复权
  → 东方财富降级
```

该路径不先调用 Tushare 股票 `pro_bar/adj_factor`。`source_profile` 会进入逐资产数据证据，不能被误解为专业双价格覆盖，也不会让无需财务因子的默认研究消耗 Tushare 额度。

专业历史股票池仍使用默认的专业源优先路径，因为它具备晋级可能性，必须如实验证专业复权与独立未复权数据。

### 3.2 可选时点估值预设

`A股时点估值研究池（需批量额度）` 保留同一 12 股冻结样本，并启用 20% value 因子。它只接受严格 `trade_date <= signal_date` 的历史 PE/PB，但要求：

```text
足够的 daily_basic 调用频次
或
已经按限额逐步预热完成的本地历史因子缓存
```

Tushare 官方文档说明单次最多返回 6000 行，可按交易日循环提取全部历史；接口支持按单只 `ts_code` 查询一段历史，或按单个 `trade_date` 查询全市场。它不提供“一次请求多只代码的完整多年历史”。当前云端实测频次为每分钟/每小时 1 次，因此按股票加载 12 份历史至少需要低速预热，不能作为点击后即时执行的默认入口。

## 4. 预设能力透明化

每张预设卡片新增：

- `data_requirements`：真正需要的接口或行情能力；
- `known_limitations`：权限不足、当前名单偏差或专业覆盖门槛。

专业质量价值预设明确要求：

```text
Tushare index_weight
+ Tushare fina_indicator
+ Tushare daily_basic
+ 专业复权与独立未复权日线
```

历史指数价格预设不启用财务因子，但仍要求 `index_weight`。权限不足时运行会失败并留痕，不会偷偷改成今天的指数成分。

## 5. 当前生产数据能力审计

2026-07-26 在现有云端配置上得到：

| 能力 | 结果 | 产品影响 |
| --- | --- | --- |
| Tushare `daily_basic` | 单次有权限，但仅每分钟/每小时 1 次 | 不能即时加载 12 只历史；估值预设要求批量额度或预热缓存 |
| Tushare `index_daily` | 可用 | 沪深 300 真实指数基准可用 |
| BaoStock `sh.000300` | 可用 | 指数具有独立降级源 |
| Tushare `fina_indicator` | 当前账号无权限 | 财务质量因子拒绝运行 |
| Tushare `index_weight` | 当前账号无权限 | 历史指数成分模式拒绝运行 |
| Tushare `adj_factor` | 当前额度约束明显 | 股票复权日线会降级到 BaoStock，不能通过专业双源门槛 |

本地真实探针通过 BaoStock 取得沪深 300：

```text
246 rows
2025-07-21 → 2026-07-24
```

云端最终发布探针会在发布后补录到第 7 节。

## 6. 自动化验收

- 新增 A 股指数专用路由测试；
- 验证小写后缀会标准化为 `000300.SH`；
- 验证指数基准不会请求股票未复权接口；
- 验证默认预设不启用任何 Tushare 财务因子；
- 验证估值预设仍启用 value 并明确要求批量额度或历史缓存；
- 验证通用纸面组合 A 股基准已切换为沪深 300 指数；
- 量化研究计划专项：`15 tests` 通过；
- 量化、前向验证和资本学习相关回归：`45 tests` 通过；
- 后端全量：`602 tests` 通过；
- 前端 Vite 生产构建：`1857 modules transformed`；
- `npm audit --omit=dev`：`0 vulnerabilities`；
- `git diff --check`：通过。

全量测试日志中的既有监控线程信息、FastAPI 弃用提示和 Pillow 超大图保护提示不构成测试失败。

## 7. 生产发布记录

### 7.1 Release

- 提交：`1f98549`（指数与预设）、`435567c`（配额安全价格链）、`3e59e1f`（立即可运行默认因子）；
- 最终功能 SHA：`3e59e1f9d6f3b2fc867f69b99a5707eecd18a653`；
- GitHub：`main` 已包含全部提交；
- 云端：`8001/8002` 双副本与前端均原子切换到最终 release；
- 六个 Worker/Beat 全部 active，scheduler 任务按 `900` 秒注册；
- 两副本均 `full_service_ready=true`；
- OpenAPI：`182 paths`、`212 operations`；
- 失败 systemd 单元：`0`；
- 最终发布窗口 error 日志：`0`。

### 7.2 真实数据整链

最终验收没有写入生产业务表，直接使用发布包和云端真实数据运行首个预设：

```json
{
  "preset_id": "a_frozen_price_research",
  "benchmark": "000300.SH",
  "benchmark_source": "BaoStock 指数日线",
  "candidate_requested": 12,
  "candidate_loaded": 12,
  "candidate_failures": [],
  "candidate_price_sources": {
    "BaoStock": 12
  },
  "equity_day_count": 488,
  "signal_count": 24,
  "fundamental_factor_requested": false,
  "promotion_status": "research_only",
  "paper_shadow_eligible": false
}
```

这证明默认链可以在当前权限下完成真实指数、真实股票日线、横截面因子、逐日撮合、成本压力、样本外信号和研究门禁。它没有因为“终于能跑”就绕过幸存者偏差或专业双源门槛。

生产探针同时验证：

- 旧默认估值方案会被当前 `daily_basic` 每分钟/每小时 1 次限额阻断；
- 调整后默认方案不请求财务因子；
- 可选估值预设仍存在，并在认证 API 中明确显示额度/缓存要求；
- 普通用户总览返回 `200`，匿名返回 `401`；
- 临时账户已停用，活跃会话 `0`，临时量化 Run `0`；
- 认证审计链 `138` 个事件完整。

### 7.3 PostgreSQL 与备份

- `81` 张表、`15` 个迁移标记；
- 量化研究计划：4 张表、5 个不可变触发器、6 个外键；
- 云端发布包专项：`15 tests` 通过；
- 最终私有 OSS AES256 备份对象：
  `backups/postgresql/2026/07/stock-assistant-iZn4ai1fm0tr284w21h4kmZ-20260726T040236Z.dump`；
- 大小：`2,423,218` 字节；
- SHA-256：`1b7f3554df0586271bae5e098db5c1c0065e620c3234efb405d296424a1d408c`；
- 隔离恢复：`81 tables / 15 migrations`。

## 8. 风险边界

- 基础预设“可运行”只表示数据链和确定性研究链可完成，不表示策略有效；
- 冻结当前股票名单不能消除退市、被剔除和历史成分变化造成的幸存者偏差；
- BaoStock 是真实降级行情，但不计入 Tushare/Massive/Polygon 专业双价格覆盖；
- 指数收益不含 ETF 跟踪误差、费用、申赎和成交冲击，不能当作可直接成交回报；
- 获得 `fina_indicator` 与 `index_weight` 权限后，仍需通过样本外、成本、容量、覆盖、回撤和前向独立批次门槛；
- 所有结果用于研究和决策辅助，不构成投资建议，不保证收益。
