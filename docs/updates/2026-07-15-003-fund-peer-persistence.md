# 持仓基金同类持续性诊断与替代审查

## 1. 更新信息

- 更新编号：`2026-07-15-003`
- 诊断：`fund_peer_relative_persistence@1.0.0`
- Agent 工具：`fund.peer_persistence.get@1.0.0`
- API：`GET /api/funds/peer-persistence?code={code}`
- Agent 结果：`fund_deep_research.v6`
- 数据：东方财富基金详情页 `Data_grandTotal` 中的基金累计收益与明确标记的“同类平均”；东方财富基金阶段涨幅接口中的近 3/6/12 月基金与同类平均

## 2. 决策问题

持仓基金亏损不能直接推出基金自身变差：同类基金可能同时处于风格逆风。反过来，基金上涨也不能证明选基有效，因为它可能持续跑输同类。本功能回答：

1. 基金近 3、6、12 个月相对同类平均是领先还是落后。
2. 最近两个互不重叠的三个月窗口是否连续跑输。
3. 是否只需要继续观察，还是已经满足“开始比较替代品”的研究门禁。
4. 哪些换仓前置证据仍然缺失。

Investor.gov 提醒投资者，基金过去表现不能预测未来，而且单看过去表现不足以选择基金；费用会直接降低投资回报。因此，本功能只生成相对诊断和研究顺序，不生成买卖指令：[Mutual Funds](https://www.investor.gov/introduction-investing/investing-basics/investment-products/mutual-funds-and-exchange-traded-funds-etfs/mutual-funds)、[Mutual Fund and ETF Fees and Expenses](https://www.investor.gov/introduction-investing/general-resources/news-alerts/alerts-bulletins/investor-bulletins/mutual-fund-and-etf-fees-and-expenses-investor-bulletin)。

## 3. 数据与对齐

```text
基金代码
  -> 基金详情页原生累计收益比较序列
  -> 唯一匹配基金正式名称
  -> 只接受名称精确为“同类平均”的序列
  -> 基金与同类日期取交集
  -> 3/6 个月共同端点和两个非重叠季度
  -> 阶段接口 3/6 月双窗口交叉校验
  -> 校验通过后接纳同源近 12 月阶段值
  -> 相对收益与非重叠季度
  -> 替代审查门禁
```

- 基金序列必须与详情页正式基金名称唯一匹配。
- 同类序列必须由数据源明确命名为“同类平均”。
- 两条序列只在完全相同的日期比较，不把基金日期与同类的附近日期拼接。
- 观察窗口目标日遇到周末或休市时，只允许选择目标日前最多 10 天内的共同日期；超过 10 天则该窗口覆盖不足。
- 累计收益不能直接相减得到区间收益，系统先还原累计收益指数，再计算端点比例。
- `Data_grandTotal` 实际覆盖不足 12 个月时，系统读取同源“阶段涨幅”接口；只有该接口的基金和同类平均在近 3 月、近 6 月均与共同日期计算结果相差不超过 `0.08` 个百分点，才接纳其近 12 月值。
- 阶段接口交叉校验失败、缺项或不可用时，近 12 月仍标记为覆盖不足，不拼接附近日期、不推算同类收益。

## 4. 计算口径

对于累计收益率 `R(t)`，先构造指数：

```text
I(t) = 1 + R(t) / 100
```

区间收益：

```text
period_return = I(end) / I(start) - 1
```

相对差异：

```text
excess_return_pp = fund_period_return - peer_period_return
```

页面分别展示本基金收益、同类平均收益和百分点差异，百分比与百分点不得混用。由阶段接口提供的近 12 月值标记为“来源阶段口径”，不伪造一个精确历史起点。

## 5. 持续性与替代审查门禁

### 5.1 非重叠窗口

- `latest_3m`：从近三个月共同端点到最新共同日期。
- `previous_3m`：从近六个月共同端点到近三个月共同端点。
- 两个窗口不重叠，避免用同一段行情重复证明“连续”。

### 5.2 版本化触发条件

只有同时满足以下条件，`replacement_review.triggered` 才为 `true`：

1. 最近三个月相对同类小于 0。
2. 此前三个月相对同类小于 0。
3. 近 12 个月相对同类不高于 `-3.00` 个百分点。

`-3.00` 个百分点是产品版本 `1.0.0` 的审查阈值，用于过滤很小的年度差异；它不是统计上已证明最优的交易参数。任何修改都必须升级诊断版本并重新做样本外检验。

### 5.3 输出状态

- `relative_strength`：多数可比窗口和最近三个月相对同类为正。
- `mixed`：不同窗口方向分化。
- `underperformance_watch`：多个窗口偏弱或连续两个季度偏弱，但完整门禁未通过。
- `replacement_review`：完整门禁通过，只允许继续比较真实替代品。
- `insufficient_data`：共同日期或窗口覆盖不足。
- `unavailable`：真实来源或计算不可用。

## 6. 换仓前仍需通过的门禁

即使替代审查被触发，系统仍固定返回：

- `automatic_redemption_allowed=false`
- 可投资替代品尚待核验。
- 申购费、赎回费、税费、份额类别和机会成本尚待核验。
- 替代品与现有组合的持仓重合、行业暴露尚待核验。
- 基金经理、投资合同和跟踪目标变化尚待核验。

SEC/Investor.gov 的基金说明指出，费用较高的基金需要取得更高表现才能带来相同净回报，因此未完成费用比较时不得把历史领先候选称为“更优替代品”：[Mutual Fund Investing: Look at More Than a Fund's Past Performance](https://www.sec.gov/about/reports-publications/investorpubsmfperformhtm)。

## 7. 前端流程与性能

1. 持仓列表仍优先加载用户确认金额、收益和批量估值回溯。
2. 用户点击单只基金后，才请求同类持续性诊断。
3. 只有用户继续点击“核验替代候选”，才执行原有同类榜单和候选净值分析。
4. 同一组件也展示在 Agent 结果中，避免持仓页与 Agent 使用不同口径。
5. 手机端把 3 个窗口、2 个季度和候选列表改为单列，不产生横向滚动。

## 8. Agent 证据边界

- `fund.peer_persistence.get@1.0.0` 为只读 R0 工具。
- 工具输出独立持久化为 Evidence，并进入模型的结构化上下文。
- 原始逐日序列不发送给模型，只发送已计算窗口、门禁、覆盖和限制。
- 模型可以解释相对表现，但不能改变 `automatic_redemption_allowed=false`。
- 该 Evidence 不进入 `personalized_fund_decision` 的确定性金额计算。
- 新结果协议为 `fund_deep_research.v6`；策略 Shadow Outcome 同时接受 v4、v5 和 v6。

## 9. 验收条件

- 绝对亏损但相对同类占优时，不得触发替代审查。
- 两个季度连续跑输但缺少 12 个月窗口时，只能进入观察。
- 同源近 12 月阶段值只有通过近 3/6 月双窗口交叉校验后才能参与门禁；任一窗口超过 `0.08` 个百分点即拒绝。
- 基金与同类没有共同日期时，不得用附近的单边日期补齐。
- 真实来源失败时不得调用指数或其他分类代理。
- 可用窗口必须同时展示基金收益、同类收益、相对百分点和实际起止日期。
- Agent Evidence 必须包含精确工具版本、数据日期、载荷哈希和质量状态。
- 基础持仓列表不得因该功能增加同步外部请求。
- 后端全量测试、前端生产构建、桌面端和 390px 手机端验证全部通过后才允许部署。
