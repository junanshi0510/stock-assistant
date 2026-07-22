# 2026-07-22 更新 004：专业行情源路由与热门榜故障隔离

## 1. 问题不是“多重试几次”

云端机会扫描曾同时返回 9 条热门榜错误：A 股、港股的东方财富连接被远端关闭，美股 Yahoo 预定义筛选器返回 403。旧实现按 3 个市场 × 涨幅/跌幅/活跃 3 种榜单逐项请求，因此一个供应商级故障被放大成 9 次网络调用和 9 条重复告警。

这不是评分模型故障，也不能靠无限重试解决：公开网页接口没有生产 SLA，云服务器出口 IP、代理策略、反爬规则或接口变更都可能使其整体不可用。继续增加 User-Agent、切换到另一个网页抓取端点或回退新浪，只会把不稳定性隐藏起来。

本轮把热门榜改造成独立的专业供应商路由层：

```text
机会工厂 / 市场日报 / 发现股票
            |
            v
  单市场榜单 bundle（一次请求三种榜）
            |
      专业源优先 + 配置检查
            |
       成功 ---------> 来源/时效/截止日/方法
            |
       失败或未配置
            v
   可选公开降级源（明确 degraded）
            |
       仍失败 -------> 单市场可操作错误 + 熔断
```

## 2. 供应商策略

| 市场 | 专业优先源 | 榜单口径 | 公开降级源 | 服务端变量 |
| --- | --- | --- | --- | --- |
| A 股 | Tushare Pro | 最近完整交易日全市场 `daily` 快照，本地统一排序 | 东方财富公开榜单 | `TUSHARE_TOKEN` |
| 港股 | Tushare Pro | 最近完整交易日全市场 `hk_daily` 快照，本地统一排序 | 东方财富公开榜单 | `TUSHARE_TOKEN` |
| 美股 | Alpha Vantage | 一次 `TOP_GAINERS_LOSERS` 响应中的 gainers、losers、most active | Yahoo 公开预定义筛选器 | `ALPHAVANTAGE_API_KEY` |

官方口径与权限应以供应商文档为准：

- [Tushare A 股日线 `daily`](https://tushare.pro/document/1?doc_id=27)
- [Tushare 港股数据说明](https://tushare.pro/document/2?doc_id=190)
- [Tushare A 股交易日历](https://tushare.pro/document/2?doc_id=26)
- [Alpha Vantage `TOP_GAINERS_LOSERS`](https://www.alphavantage.co/documentation/)

Tushare Token 已配置不等于 A 股和港股接口权限都已开通；程序会把真实权限错误作为专业源失败记录。Alpha Vantage `ALPHAVANTAGE_MARKET_DATA_ENTITLEMENT` 留空时按日终榜披露，只有订阅明确允许时才能设为 `delayed` 或 `realtime`。

公开源继续存在只是为了平滑迁移，返回时固定包含：

- `provider_tier=public_fallback`；
- `degraded=true`；
- 覆盖范围和稳定性不受保证的警告；
- 专业源未生效的原因；
- 真实公开供应商名称。

系统不会把公开源包装为专业源，不会在公开源失败后生成榜单，也不会回退新浪。

## 3. 一次市场级读取替代九次独立读取

新增 `get_hot_stock_bundle(market, types, limit)`。机会工厂按市场调用一次，并从同一 bundle 读取所需榜单：

- Tushare 只读取一份完整日快照；涨幅、跌幅和活跃榜在服务端按同一份数据排序；
- Alpha Vantage 官方接口本身一次返回三榜，只发送一次请求；
- A 股市场日报的涨幅和跌幅榜也复用同一 bundle；
- 公开降级源若第一种榜单已出现系统性连接失败，整个市场立即失败，不继续制造同类告警。

机会工厂的失败来源从 `A股:hot_active`、`A股:hot_losers` 等九种条目收敛为：

```text
A股:hot_provider
港股:hot_provider
美股:hot_provider
```

热门榜失败不会删除手工候选、自选或内置候选，也不会把扫描直接伪装为完整成功；Run 保持 `partial` 并记录每市场缺口。

## 4. 时效、来源和缓存语义

热门榜响应新增：

- `provider`：机器可读供应商 ID；
- `provider_tier`：`professional` 或 `public_fallback`；
- `data_freshness`：`latest_completed_eod`、`end_of_day`、`delayed`、`realtime` 或 `intraday_best_effort`；
- `as_of`：供应商披露的数据截止时间；
- `retrieved_at`：本服务实际获取时间；
- `degraded` / `stale`：公开降级与陈旧缓存分别标记；
- `provider_attempts`：脱敏后的逐供应商结果；
- `provider_policy_version=hot_stock_provider_router@1.0.0`。

“获取时间”不再冒充“行情截止时间”。Tushare 榜单是最近完整交易日日线，Alpha Vantage 默认是日终榜；只有供应商授权和响应口径支持时才显示延时或实时。

已有成功缓存仍可在供应商临时失败时返回，但固定同时设置 `stale=true`、`degraded=true`，并提示不得按陈旧榜单直接交易。没有成功缓存时直接失败，不生成样本数据。

## 5. 熔断、脱敏和状态接口

每个市场/供应商维护进程内运行状态：连续失败数、最近成功、最近失败、最后脱敏错误和熔断截止时间。默认连续失败 2 次后熔断 300 秒，避免 Worker 持续撞击同一被封端点。配置缺失不计为网络失败，也不消耗熔断次数。

异常在进入响应或运行状态前会：

- 移除 Tushare、Alpha Vantage、Polygon 的实际凭据；
- 脱敏 URL 中的 `apikey`、`api_key` 和 `token` 参数；
- 把连接关闭、代理不可达和 HTTP 拒绝收敛成有限长度消息；
- 保留用户真正可操作的环境变量名称。

新增只读接口：

```http
GET /api/market/providers
```

它通过既有 market-data Worker 白名单执行，只报告每市场的推荐专业源、所需环境变量、是否配置、预期时效、熔断和最近运行摘要。接口固定返回 `secrets_exposed=false`、`active_probe=false`，既不回传 Key，也不会为了显示状态额外消耗供应商额度。

“发现股票”和“机会工厂”都新增专业行情源状态卡。前端不提供 Key 输入框；凭据只能进入服务器权限为 `600` 的环境文件。

## 6. 生产配置

最小配置写入 `/etc/stock-assistant/stock-assistant.env`：

```ini
TUSHARE_TOKEN=
ALPHAVANTAGE_API_KEY=
ALPHAVANTAGE_MARKET_DATA_ENTITLEMENT=
HOT_STOCK_PUBLIC_FALLBACK_ENABLED=true
HOT_STOCK_PROVIDER_FAILURE_THRESHOLD=2
HOT_STOCK_PROVIDER_CIRCUIT_SECONDS=300
```

修改后必须重启 `stock-assistant-market-worker`。专业源真实验收完成后，可以把 `HOT_STOCK_PUBLIC_FALLBACK_ENABLED=false`，让生产严格拒绝公开网页榜单。

状态卡显示“已配置·待验证”只说明 Worker 读到了非空配置，并不证明账号权限、额度、IP 白名单或订阅时效已经通过。最终验收必须分别读取 A 股、港股、美股榜单，并核对：

1. `provider_tier=professional`；
2. `degraded=false`；
3. `as_of` 与 `data_freshness` 符合订阅；
4. 三种榜单字段和排序合理；
5. 日志、响应和状态接口均不含密钥。

## 7. 验证覆盖

自动测试覆盖：

- Tushare 一份日快照生成三榜及代码/名称规整；
- Alpha Vantage 一次 HTTP 调用生成三榜；
- 未配置专业源时公开结果必须标记降级；
- 专业源和公开源同时失败时只返回一个市场级错误；
- 凭据和 URL 参数不会进入异常或状态 JSON；
- 连续失败达到阈值后不再调用上游；
- 过期成功缓存只能作为明确陈旧结果返回；
- 机会工厂三市场最多产生三条热门源警告；
- 市场日报复用市场级 bundle；
- 新状态路由保持在 Worker 操作白名单内；
- 前端生产构建通过。

本地全量结果为 `443 passed`、`4 subtests passed`。真实页面验收中，A 股公开端点失败后只显示一条脱敏、可操作且带熔断状态的市场错误；同一台本机在 VPN 环境下可以读取 Yahoo 美股榜，但页面仍正确显示 `public_fallback`、`degraded=true` 和专业 Key 未配置警告。桌面端三张状态卡完整显示；`390×844` 手机端状态卡单列显示，热门榜与量化体检宽表只在自身容器内滚动，整页没有横向溢出，浏览器控制台无错误。

没有真实专业 Key 的环境只能验证“未配置—公开降级—公开失败—熔断”边界和模拟官方契约，不能声称专业源已经生产可用。上线记录必须把代码发布成功与供应商授权验收成功分开报告。

## 8. 投资边界

热门榜只能说明某一时点的涨跌或成交关注度，不是未来收益预测。进入机会工厂后仍需经过历史完整度、数据新鲜度、技术、波动、回撤、基本面、因子覆盖、综合分和组合相关性门槛。即使专业源可用、榜单靠前或纸面组合后续上涨，也不构成收益保证、买入指令或自动交易授权。
