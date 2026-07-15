# 替代候选持续胜率与追涨门禁

## 1. 更新信息

- 更新编号：`2026-07-15-004`
- 诊断：`fund_alternative_durability@1.0.0`
- 所属工具：`fund.alternatives.get@1.0.0`
- 数据：东方财富同类基金排行、东方财富基金详情页每日收益、天天基金历史净值接口每日收益
- 入口：持仓基金详情的“继续核验替代候选”和 Agent 基金深度研究

## 2. 决策问题

同类榜单只能说明候选基金在一个固定区间内排名靠前，不能证明它在不同市场阶段都稳定优于当前基金。Investor.gov 明确提醒，某一年的冠军基金下一年未必继续领先，基金份额类别还可能因为费用结构不同而产生不同表现。因此，本诊断回答：

1. 候选在过去多个滚动 6/12 个月窗口中，有多少比例跑赢当前基金。
2. 典型窗口的中位超额是否为正，而不是只靠少数暴涨窗口抬高总收益。
3. 当前基金亏损时，候选是否多数时间表现得更好。
4. 候选的历史最大回撤是否比当前基金更深。
5. 候选最新 6 个月收益是否处在自身历史极热区，存在追涨风险。
6. 候选是否只值得继续费用和持仓尽调，而不是直接换入。

参考：[Investor.gov - Mutual Funds, Past Performance](https://www.investor.gov/introduction-investing/investing-basics/glossary/mutual-funds-past-performance)、[Investor.gov - How to Read a Mutual Fund or ETF Shareholder Report](https://www.investor.gov/introduction-investing/general-resources/news-alerts/alerts-bulletins/investor-bulletins/updated-investor-bulletin-how-read-mutual-fund-or-etf-shareholder-report)。

## 3. 候选池去重

```text
同类真实榜单
  -> 识别基金名称
  -> 归并名称末尾 A/B/C/E/I/R/Y 等份额类别
  -> 同一策略只保留榜单位置最靠前的一项
  -> 读取不同策略候选的真实历史数据
```

- 去重只影响候选研究队列，不修改用户持仓。
- 被排除代码、名称、归并键和 `same_strategy_share_class` 原因写入响应及 Evidence。
- A/C 份额费用并不相同，因此保留份额费用核验门禁；去重不等于认定某个份额类别更优。

## 4. 总回报口径

系统不使用单位净值直接计算长期候选胜率。基金分红可能造成单位净值跳变，直接比较会把分红错误解释为亏损。

对数据源披露的每日收益率 `r(t)` 复利：

```text
I(0) = 1
I(t) = I(t-1) * (1 + r(t) / 100)
```

区间收益：

```text
period_return = I(end) / I(start) - 1
```

- 每条日收益必须来自真实来源。
- 除第一条可作为指数起点外，序列中任何日收益缺失都会停止该候选诊断。
- 重复日期、无效收益或复利指数非正时停止诊断。
- 当前基金与候选基金只在完全相同的日期比较，不使用附近日期拼接。

## 5. 滚动窗口

1. 对共同日期按自然月取该月最后一个共同观察日。
2. 每个终点分别查找准确相隔 6 个月和 12 个月的月度端点。
3. 计算候选收益减当前基金收益，单位为百分点。
4. 至少需要 25 个月度端点、18 个六个月窗口和 12 个十二个月窗口。

页面展示：滚动胜率、中位超额、下四分位与最差超额、当前基金亏损窗口保护率、最新窗口和当前候选收益历史分位。

滚动窗口彼此重叠，因此样本不独立。系统明确披露该限制，滚动胜率不能写成未来上涨概率。

## 6. 版本化门禁

进入后续尽调必须同时满足：

1. 滚动 6 个月胜率不低于 `60%`。
2. 滚动 12 个月胜率不低于 `60%`。
3. 两个窗口的中位超额都大于 0。
4. 候选最大回撤不能比当前基金深 `5` 个百分点以上。
5. 最新 6 个月和 12 个月都相对领先。
6. 最新 6 个月收益不能同时位于自身历史 `90%` 以上分位且高于历史中位数至少 `2` 个百分点。

这些数值是 `1.0.0` 产品研究门槛，不是经样本外验证的最优交易参数。修改门槛必须升级诊断版本并重新评测。

## 7. 输出状态

- `durable_advantage`：持续性和回撤门禁通过，可继续费用与持仓尽调。
- `advantage_but_hot`：持续占优，但处于追涨区，暂不进入换仓尽调。
- `recent_leader_only`：最新窗口领先，长期胜率或中位超额不足。
- `mixed_evidence`：持续性、近期优势或回撤证据分化。
- `insufficient_data`：真实日收益或共同日期不足。
- 顶层 `unavailable`：当前基金总回报序列不可用，整轮停止。

任何状态均固定返回：

```text
automatic_purchase_allowed = false
automatic_redemption_allowed = false
```

## 8. 费用与下一步

页面展示数据源当前披露的页面申购费，但用户真实持有天数对应的赎回费、销售服务费、税费、平台折扣、最新持仓重合、风格漂移、基金经理和合同变化仍为待核验门禁。

SEC/Investor.gov 指出，交易费用和持续费用都会降低实际回报，高成本基金必须获得更高表现才能产生相同净回报。因此持续性通过后仍不能跳过成本核验：[How Fees and Expenses Affect Your Investment Portfolio](https://www.investor.gov/introduction-investing/general-resources/news-alerts/alerts-bulletins/investor-bulletins/updated)、[SEC - Look at More Than a Fund's Past Performance](https://www.sec.gov/about/reports-publications/investorpubsmfperformhtm)。

## 9. Agent 边界

- 原始日收益不发送给模型。
- 模型只接收窗口样本数、胜率、中位超额、近期窗口、回撤、追涨状态和门禁。
- `eligible_for_due_diligence=false` 时，模型不得把候选称为可换入机会。
- `eligible_for_due_diligence=true` 只代表进入费用和持仓重合尽调。
- 确定性代码决定门禁，大模型不得修改。

## 10. 验收条件

- 同策略 A/C 份额不得同时进入候选分析。
- 单一近期暴涨候选不得因为最新收益高而通过持续性门禁。
- 日收益中间缺失时不得用单位净值补齐。
- 没有共同日期时不得使用附近日期匹配。
- 最大回撤显著更深时不得进入后续尽调。
- 追涨区候选必须显示暂缓状态。
- 持仓列表基础加载不增加外部请求；仅在用户核验替代候选时运行。
- Agent Evidence 必须保留来源、工具版本、质量状态和载荷哈希。
