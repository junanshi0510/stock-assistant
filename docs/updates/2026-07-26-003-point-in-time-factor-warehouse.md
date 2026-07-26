# A 股时点因子数据仓库、配额调度与零调用研究回放

发布日期：2026-07-26

## 1. 为什么这是基础设施级功能

此前量化选股虽然已经能严格按信号日计算因子，但财务/估值数据仍在一次研究运行中直接请求 Tushare。低额度账号会遇到三个根本问题：

1. 同一段历史被每次回测重复下载，API 额度与研究次数线性增长；
2. 请求失败会让一次长回测中途终止，供应商可用性被错误地耦合到研究可重复性；
3. 没有独立、不可变的采集时间与内容哈希，难以证明后来修订的数据没有偷偷改变旧结果。

本次建立独立数据平面：

```text
Tushare 供应商
  → 每次仅一个额度目标
  → 清洗与内容寻址
  → 不可变时点观察
  → 冲突排除与快照 SHA-256
  → 多次量化研究零供应商调用回放
```

它解决的是“历史数据能否持续积累、可追溯和重复研究”，不是输出必涨股票。系统仍不连接券商、不生成订单、不自动下单，也不承诺收益。

## 2. 数据契约

### 2.1 每日估值截面

Tushare [`daily_basic`](https://tushare.pro/document/2?doc_id=32) 支持按单个 `trade_date` 读取全市场截面，官方单次最多返回 6000 行。因此仓库不再按股票请求 12 份历史，而是每个目标日期只调用一次并保存：

| 字段 | 仓库口径 |
| --- | --- |
| `trade_date` | 数据可见日；研究只允许 `trade_date <= signal_date` |
| `pe_ttm` | 滚动市盈率；亏损导致的空值保持为空 |
| `pb` | 市净率 |
| `dv_ttm` | 滚动股息率 |
| `total_mv / circ_mv` | Tushare 原始市值口径，原始载荷同步保留 |
| `turnover_rate_f` | 自由流通股换手率 |

返回行必须与目标交易日一致，代码必须是标准 A 股 `SH/SZ/BJ` 后缀；异常行计入 `malformed_rows`，不会静默修正后进入研究。

### 2.2 公告日财务质量

Tushare [`fina_indicator`](https://tushare.pro/document/2?doc_id=79) 当前按单只股票采集：

| 字段 | 仓库口径 |
| --- | --- |
| `ann_date` | 真正可见日；研究只允许 `announcement_date <= signal_date` |
| `end_date` | 报告期，只用于选择最新已披露报告，不能替代公告日 |
| `roe` | ROE |
| `grossprofit_margin` | 毛利率 |
| `ocf_to_or` | 经营现金流/营业收入 |
| `debt_to_assets` | 资产负债率 |
| `update_flag` | 供应商修订标记，连同原始载荷保存 |

任何 `report_end_date > announcement_date` 的记录都视为畸形并排除。

### 2.3 交易日与节假日

增量调度只生成周一至周五候选；Tushare 返回空截面时，批次以 `no_data=true` 成功留痕，因此节假日不会被无限重试。需要更精确的交易所日历时，可以使用官方 [`trade_cal`](https://tushare.pro/document/2?doc_id=26) 升级，但当前低额度账号不为避免少量节假日空调用而额外消耗一份日历接口额度。

## 3. 五张生产表

| 表 | 作用 | 可变性 |
| --- | --- | --- |
| `quant_factor_backfill_plans` | 数据集、日期范围、股票范围、冻结政策和进度 | 仅允许暂停、继续、完成、取消 |
| `quant_factor_sync_runs` | 一个供应商目标的请求、租约、尝试次数、结果与错误 | 请求不可变；完成结果不可重写 |
| `quant_factor_sync_events` | queued/running/succeeded/failed/requeued/lease_expired 事件 | 只追加、前序哈希链 |
| `quant_factor_daily_observations` | A 股每日全市场估值观察 | 不允许 UPDATE/DELETE |
| `quant_factor_financial_observations` | 按公告日可见的财务观察 | 不允许 UPDATE/DELETE |

观察 ID 由“数据集 + 供应商 + 股票 + 可见日期 + 原始载荷 SHA-256”确定。完全相同的重复响应幂等复用；相同业务键出现不同载荷哈希时，两份都保留，但研究会把整个冲突组排除。

## 4. 配额安全调度

默认参数：

```text
QUANT_FACTOR_SYNC_INTERVAL_SECONDS=3900
QUANT_FACTOR_RETRY_COOLDOWN_SECONDS=3900
QUANT_FACTOR_QUEUE_REDISPATCH_SECONDS=600
QUANT_FACTOR_SYNC_LEASE_SECONDS=600
QUANT_FACTOR_MAX_BACKFILL_DAYS=1830
QUANT_FACTOR_DAILY_RESET_GRACE_MINUTES=15
QUANT_FACTOR_RATE_LIMIT_MAX_ATTEMPTS=30
```

调度规则：

1. 每次 Beat 先回收过期租约；
2. 系统存在 running 同步时不再创建第二个；queued 任务超过 10 分钟仍未被领取时，会用同一 `run_id` 安全重派；
3. 优先补最近 10 个工作日的全市场每日估值；
4. 最近截面完整后，再选择最早创建的活动回填计划；
5. 每次调度最多调用一个日期或一只股票；
6. 普通失败至少冷却 65 分钟、最多尝试 3 次；供应商明确返回日配额限制时，冷却到下一个上海自然日 00:15，最多保留 30 个跨日恢复机会；
7. 失败、空数据和计划取消都永久留痕，不提供删除接口。

任务软/硬时限为 `300/330` 秒，租约默认 `600` 秒。仓库写入还会在同一事务中核验运行状态、尝试序号与租约到期时间；调度器已经回收的旧 Worker 即使迟到返回，也不能把响应写进仓库。

## 5. 研究运行如何使用仓库

A 股财务/估值因子新增两种显式模式：

```text
warehouse_only   默认；只读本地仓库，研究期供应商调用数为 0
provider_direct  显式高额度模式；研究运行直接请求供应商
```

`warehouse_only` 会为每次研究保存：

- 仓库版本和内容寻址快照 SHA-256；
- 请求/加载成功/失败股票数；
- 每只股票估值与财务的首末日期、行数和冲突数；
- `live_incremental` 与 `historical_backfill` 采集模式；
- 研究期供应商调用数；
- 公告日、交易日和报告期不可替代可见日的规则。

启用财务或估值因子后，至少 4 只候选必须具有所需历史，否则 Run 明确失败并保留错误；不会退回网页数据、今天的财报或中性假值。

## 6. API 与权限

| 方法 | 路径 | 权限 |
| --- | --- | --- |
| GET | `/api/v1/quant-factors/overview` | 登录用户；普通用户读取脱敏摘要 |
| GET | `/api/v1/quant-factors/sync-runs/{run_id}` | 管理员 |
| POST | `/api/v1/quant-factors/backfill-plans` | 管理员 |
| POST | `/api/v1/quant-factors/backfill-plans/{plan_id}/actions` | 管理员 |
| POST | `/api/v1/quant-factors/sync-runs` | 管理员 |
| POST | `/api/v1/quant-factors/schedule` | 管理员 |

完整请求只保存在 PostgreSQL，Celery 消息只携带 `run_id`。Token 只从服务端环境读取，不写入队列、事件、响应或日志。

## 7. 前端

“研究中心 → 量化选股”新增：

- 仓库状态、总行数、股票数、日期范围和最新截面宽度；
- 默认 12 股研究池的估值/财务覆盖；
- 内容冲突数；
- 1–5 年回填计划创建、进度、暂停/继续/取消；
- 最近同步批次、插入行数、空交易日和错误状态；
- 每次一目标、65 分钟间隔的额度说明；
- 研究表单的仓库只读/供应商直连模式；
- 结果页的仓库快照、行数和研究期供应商调用数证据。

普通用户不会看到管理按钮；前端隐藏不是授权边界，后端仍执行管理员与 CSRF 检查。

## 8. 本地验收

- 新仓库专项：`13 tests` 通过；
- 新模块与路由/任务协议关键回归：`43 tests` 通过；
- 后端全量：`617 tests` 通过；
- 前端 Vite 生产构建：`1857 modules transformed`；
- `npm audit --omit=dev`：`0 vulnerabilities`；
- OpenAPI：`188 paths / 218 operations`；
- `git diff --check`：通过；
- 真实浏览器：桌面端和 `390px` 手机宽度均无页面级横向溢出，控制台错误/告警 `0`。

浏览器验收没有创建回填计划或调用本地供应商；页面正确显示“Token 未配置、仓库只读、等待首次采集”。

## 9. 生产发布记录

最终 Git SHA、双副本 release、真实首批全市场行数、PostgreSQL 表/迁移数量、Worker/Beat 状态、权限验收和 OSS 恢复证据将在本次云端发布完成后写入本节。

## 10. 风险边界

- 仓库消除的是重复请求与数据可见日歧义，不消除股票池幸存者偏差；
- PE/PB、ROE 和历史收益是事实特征，不是未来收益概率；
- 数据供应商可能修订历史；系统会保留并排除冲突，而不是替用户猜哪份正确；
- 工作日候选不是完整交易所日历，节假日可能产生一次有审计记录的空调用；
- 当前 `fina_indicator` 权限不足时，财务质量仓库不会伪造数据；
- 任何策略仍须通过样本外、成本、容量、回撤、前向独立批次和投资政策门禁；
- 所有输出仅供研究和人工决策辅助，不构成投资建议，不保证盈利。
