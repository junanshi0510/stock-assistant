# 2026-07-22 更新 005：专业行情数据中台 v2 与低成本全市场榜

## 1. 这次解决什么

上一版已经把 9 次重复热门榜请求收敛为每市场一次 bundle，并建立了来源披露、熔断和脱敏边界，但每个市场仍只有一个专业源：A/H 股依赖 Tushare，美股依赖 Alpha Vantage。一旦账号未配置、港股权限未购买或美股榜单额度不适合全市场扫描，系统只能落到不稳定的公开网页源。

本次把热门榜升级成真正的多专业源数据中台：

```text
A 股：富途 OpenD 实时快照 -> Tushare A 股日线 -> 东方财富公开降级
港股：富途 OpenD 实时快照 -> Tushare 港股日线 -> 东方财富公开降级
美股：富途 OpenD 实时快照 -> Massive 全市场日终 -> Alpha Vantage 三榜 -> Yahoo 公开降级
```

箭头表示有序接力，不表示把多个来源拼成一份不一致行情。某次请求只会选中一条成功路线，响应保留此前每条尝试的状态。

## 2. Massive 免费全市场美股榜

Massive（原 Polygon.io）Basic 免费档的 grouped daily endpoint 可以一次返回指定交易日全部美股 OHLCV。系统读取最近两个有数据的完整交易日，在本地完成：

- 涨幅榜：`current_close / previous_close - 1` 降序；
- 跌幅榜：同一收益率升序；
- 活跃榜：`volume * VWAP` 估算成交额降序；
- 默认排除 OTC；
- 默认排除价格低于 1 美元或截止日成交量低于 10,000 股的标的；
- 使用复权聚合，减少拆股造成的伪涨跌。

过滤门槛不是盈利规则，只是榜单可交易性卫生线，可通过 `HOT_STOCK_US_MIN_PRICE` 和 `HOT_STOCK_US_MIN_VOLUME` 调整。每次结果返回 `data_quality`，包括两期原始行数、昨收匹配率、合格行数、过滤行数、基准日和实际过滤条件，不能只看榜首而忽略数据覆盖。

官方接口：

- [Massive Daily Market Summary](https://massive.com/docs/rest/stocks/aggregates/daily-market-summary)
- [Massive All Tickers](https://massive.com/docs/rest/stocks/tickers/all-tickers)
- [Massive Stocks Pricing](https://massive.com/pricing?product=stocks)

## 3. 真实 7/30 交易日全市场排名

旧多日榜先取当天成交活跃候选，再逐只计算收益，因此不能称为全市场榜；美股批量 Spark 还会受 Yahoo 403 影响。

Massive 可用时，新实现先读取 SPY 日线以得到真实交易日序列，精确定位 7 或 30 个交易日前的基准日，再读取截止日和基准日两份全市场 grouped daily：

- 美东 18:00 前只探测上一自然日及更早日期，避免把盘中快照误作完整日线；18:00 后会先尝试当日，避免无意义地再落后一交易日；
- 未配置 Massive/Polygon Key 时多日路径直接标记 `not_configured`，不发送空 Key 网络请求；
- 不用自然日近似交易日；
- 不需要逐只请求几千只股票；
- 7/30 日涨跌榜是通过流动性门槛后的全市场横截面；
- 多日活跃榜仍按截止日成交额排序，但涨跌列使用完整区间收益；
- 响应设置 `full_market_multiday=true`。

若 Massive 未配置或本次失败，系统才保留旧的活跃候选池计算，并设置 `provider_tier=mixed_fallback`、`degraded=true` 和明确警告，不能把近似榜伪装成全市场结果。

## 4. 富途 OpenD 实时快照路线

富途适配器使用官方 `futu-api 10.9` SDK连接常驻 FutuOpenD：

- A 股分别读取 `Market.SH` 与 `Market.SZ`；
- 港股读取 `Market.HK`；
- 美股读取 `Market.US`；
- 只请求 `SecurityType.STOCK`；
- 股票列表去重后最多 400 只一批读取市场快照；
- 过滤停牌、零价格、零昨收和无效代码；
- 美股继续执行价格/成交量卫生线；
- 连接始终在 `finally` 中关闭；
- 同进程通过锁隔离 OpenD 全市场读取，避免三个市场并发冲击快照额度；
- 港股 BMP 权限不能完成全市场快照时返回可操作的 LV1/LV2 提示，不用 20 只一批暴力调用几百次。

`FUTU_OPEND_HOST` 留空时，富途路线处于 `not_configured`，不会尝试连接，也不会影响后续 Tushare/Massive/Alpha Vantage。OpenD 登录、设备验证和行情权限属于外部运行条件，代码发布成功不等于富途生产链路已经通过。

官方接口与权限说明：

- [富途市场快照](https://openapi.futunn.com/futu-api-doc/quote/get-market-snapshot.html)
- [富途权限与额度](https://openapi.futunn.com/futu-api-doc/intro/authority.html)

## 5. 数据源状态与主动验证

`GET /api/market/providers` 继续是零额度、只读、无密钥的状态接口，但每个市场现在返回 `providers[]`：

- 是否配置；
- 配置错误或缺失项；
- 预期时效；
- 最近成功/失败；
- 连续失败与熔断；
- 官方文档；
- 当前聚合选中的路线。

新增：

```http
POST /api/market/providers/probe
Content-Type: application/json

{"market":"美股"}
```

探测只允许 A 股、港股、美股，只尝试专业路线，强制绕过普通 provider bundle 缓存，不允许公开网页降级。返回实际选中供应商、截止时间、时效、三榜数量、质量摘要、逐源尝试和耗时；30 秒内同市场重复点击复用脱敏结果，避免消耗免费额度。接口仍经过登录、CSRF 和 market-data Worker 白名单。

## 6. 新浪残留清理

此前 `_us_spot_table()` 仍调用新浪 JSONP 美股列表，与“不使用新浪”的数据治理承诺冲突。本次删除该网络调用：

- 配置 Massive/Polygon Key 时，美股搜索使用官方 `/v3/reference/tickers`；
- 无 Key 时才调用东方财富作为明确降级；
- 明显的美股 ticker 在列表源失败时仍允许按代码继续研究；
- 原 `_sina_a_symbol()` 实际只是 `sh/sz/bj` 前缀格式化，已改名为 `_a_exchange_prefixed_symbol()`；
- A 股历史源链没有新增新浪读取。

## 7. 生产配置

最省钱的日终方案：

```ini
TUSHARE_TOKEN=服务端Token
MASSIVE_API_KEY=服务端Key
MASSIVE_API_BASE_URL=https://api.massive.com
ALPHAVANTAGE_API_KEY=
FUTU_OPEND_HOST=

HOT_STOCK_US_MIN_PRICE=1
HOT_STOCK_US_MIN_VOLUME=10000
HOT_STOCK_PUBLIC_FALLBACK_ENABLED=true
```

需要富途实时行情时再增加：

```ini
FUTU_OPEND_HOST=127.0.0.1
FUTU_OPEND_PORT=11111
FUTU_OPEND_MARKETS=A,H,US
FUTU_SNAPSHOT_BATCH_SIZE=400
```

OpenD 端口不得暴露公网。Key 和 OpenD 配置只进入 root 持有、权限 `600` 的 `/etc/stock-assistant/stock-assistant.env`，修改后重启 `stock-assistant-market-worker`。

## 8. 验收门槛

自动测试至少覆盖：

- 旧 Tushare/Alpha Vantage 路由兼容；
- 新多专业源接力顺序；
- Massive 两期全市场三榜及流动性过滤；
- Massive 多日全市场路径不调用 Yahoo；
- 富途 SH/SZ 快照归一化与排序；
- 多供应商状态不暴露 Key；
- 主动探测进入 Worker 白名单；
- 熔断按供应商隔离；
- 路由契约和前端生产构建。

生产验收必须把“代码已部署”和“真实供应商已授权”分开报告。没有实际 Key/OpenD 时，只能确认配置缺失、接力、脱敏和失败边界，不能声称专业数据已经可用。

## 9. 投资边界

全市场榜解决的是覆盖和数据稳定性，不是收益预测。涨幅靠前可能包含事件冲击、低流通盘、拆股、停牌复牌和短期过热；成交活跃也不等于未来上涨。榜单进入机会工厂后仍必须经过历史完整性、趋势、波动、回撤、基本面、因子覆盖、相关性和组合上限门槛，并通过成本后回测与前瞻纸面观察。任何接口都不产生自动交易权限或收益保证。

## 10. 本地验收结果

- 后端全量回归：`449 passed`、`4 subtests passed`；
- 专业行情路由、网关、Yahoo 兼容边界和路由契约定向回归：`30 tests` 通过；
- Python `3.13.11` 下实际安装并导入 `futu-api 10.9`，`pip check` 无依赖冲突；
- 前端 Vite 生产构建通过；
- 浏览器桌面端实际打开“机会工厂”，确认三市场全部候选路线、缺失配置和禁用探测入口正确呈现；
- `390×844` 手机端三张市场卡自动单列，页面级 `scrollWidth` 与可视宽度一致；候选漏斗和宽表只在各自容器内滚动；
- 浏览器控制台无 warning/error；
- 未向本地或仓库写入任何供应商 Key，因此本轮只验证了未配置门禁、路由接力、数据归一化、脱敏和错误边界，未声称真实专业供应商已经授权。
