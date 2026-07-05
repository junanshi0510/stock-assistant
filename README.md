# 金融投资助手 📈

抓取 **A股 / 港股 / 美股** 的历史行情,用**多因子量化模型**打分,给出看涨打分、
方向判断与**估计上涨概率**,支持**多股对比**、**批量扫描排名**和**信号回测**,并画出专业 K 线图。

架构:**React 前端 + FastAPI 后端**(Python 负责抓数据/打分/回测,React 负责界面)。

> ⚠️ **重要**:本工具给出的是基于历史价格的【量化信号与估计概率】,**不是涨跌预测,
> 也不是投资建议**。没有任何模型能准确预测股市;请用「信号回测」查看历史命中率,
> 理性参考,盈亏自负。

## 三大功能(界面顶部切换标签页)

| 标签页 | 作用 |
|--------|------|
| **单股分析** | 技术面看涨打分(0-100)+ 上涨概率 + 10 因子透明加减分 + K线/均线/布林带图,并附:<br>　• 🏦 **基本面**(PE/PB/ROE/营收净利增长 + 基本面评分)<br>　• 🤖 **AI 模型预测**(梯度提升 + 样本外准确率 + 最新上涨概率)<br>　• 📰 **新闻情绪**(近期个股新闻 + 情绪打分) |
| **多股对比** | 同一市场多只股票横向比较:归一化走势(都从100起步)、区间收益、波动率、最大回撤、技术评分、相关性矩阵,并支持导出 CSV |
| **发现** | 真实热门榜/涨跌幅榜/成交活跃榜。优先走新浪真实行情榜,东财仅作真实备选源;不使用精选池或假兜底 |
| **批量扫描** | 一次扫一批股票(或一键载入预设股票池),按看涨打分排序,点任意一行跳到详细分析 |
| **信号回测** | 用约 4 年历史检验技术信号的真实命中率(方向准确率、看涨/看跌胜率、分数分档收益) |

> 🤖 **关于 AI 预测的诚实说明**:模型用严格的「时间序列样本外验证」,并把样本外准确率与
> 基准一并显示。单股技术面模型多数情况下与抛硬币接近(50%±几个点),**别只看"上涨概率"就重仓**。
> 🏦📰 **美股的基本面与新闻情绪**需要在 `backend/config.py` 配置免费的 Alpha Vantage Key;
> A股 用 BaoStock / 东方财富,开箱即用;港股 暂无免费基本面/新闻源。

---

## 最简单的用法(推荐给非技术用户)

1. **首次安装**:双击 `setup.bat`,等它跑完(会装 Python 和前端依赖,约几分钟)。
2. **每次使用**:双击 `start.bat`,会自动打开浏览器到 `http://localhost:5173`。
3. 用完后,关掉弹出的两个黑色命令行窗口即可。

> 前提:电脑已安装 [Python 3.10+](https://www.python.org/) 和
> [Node.js 18+](https://nodejs.org/)。本机已确认安装(Python 3.13 / Node 24)。

---

## 怎么用界面

**单股分析**:选市场 → 输代码 → 拖动回溯时间 → 点「开始分析」。
- A股:6 位数字,如 `600519`(贵州茅台);港股:5 位,如 `00700`(腾讯);
  美股:字母,如 `AAPL`(苹果),忘了代码可用搜索框。

**批量扫描**:选市场 → 点「一键载入预设股票池」或自己粘贴一串代码 → 点「批量扫描」,
结果按打分从高到低排序,点行可跳转详细分析。

**多股对比**:选市场 → 粘贴 2-12 只股票代码 → 点「开始对比」。
它会把所有股票放在同一张归一化走势图上,并输出收益、波动、回撤、评分和相关性矩阵。

**信号回测**:输代码 + 选前瞻天数 → 点「开始回测」,看这套信号过去准不准。
> 重点:若方向准确率接近或低于「基准随机上涨率」,说明信号对该股该周期预测力有限 —— 这很正常,别盲信。

---

## 手动启动(给开发者)

需要开两个终端:

```powershell
# 终端 1 —— 后端 API
.\venv\Scripts\Activate.ps1
cd backend
uvicorn main:app --reload --port 8000

# 终端 2 —— 前端界面
cd frontend
npm run dev
```

浏览器打开 `http://localhost:5173`。前端通过 vite 代理把 `/api` 转发到后端 8000 端口。

---

## 项目结构

```
backend/                 后端(Python / FastAPI)
  main.py                API 接口:analyze / scan / backtest / presets / search_us
  data_fetch.py          抓取三个市场行情(多专业源 + 自动降级 + 缓存)
  analysis.py            技术指标 + 多因子打分模型 + 上涨概率
  backtest.py            信号历史准确率回测
  compare.py             单股 vs 大盘指数对比
  multi_compare.py       多股横向对比(收益/波动/回撤/相关性)
  fundamentals.py        基本面数据 + 评分(A股 BaoStock / 美股 AlphaVantage)
  ml_model.py            机器学习预测(sklearn 梯度提升 + 样本外验证)
  sentiment.py           新闻舆情情绪(A股 东财新闻 / 美股 AlphaVantage)
  config.py              ★ 数据源 API Key 配置(在这里粘 token/key)
  requirements.txt       后端依赖
frontend/                前端(React / Vite)
  src/App.jsx            主界面 + 标签页切换
  src/tabs/AnalyzeTab.jsx   单股分析页
  src/tabs/InsightSections.jsx 基本面/AI/新闻 三个板块
  src/tabs/MultiCompareTab.jsx 多股对比页
  src/tabs/ScanTab.jsx      批量扫描页
  src/tabs/BacktestTab.jsx  信号回测页
  src/CandleChart.jsx    K线图(lightweight-charts,含布林带)
  src/ScoreRing.jsx      圆环评分仪表盘
  src/api.js / helpers.js   接口封装 / 颜色辅助
  vite.config.js         开发服务器 + /api 代理配置
setup.bat                一键安装
start.bat                一键启动
```

---

## 数据源(专业多源 + 自动降级)

每个市场按优先级依次尝试,任一成功即返回;前面的源没配 Key 会自动跳过:

| 市场 | 优先级顺序 |
|------|-----------|
| A股  | Tushare → **BaoStock(免费免Key,默认可用)** → 东方财富 → 新浪 |
| 港股 | Tushare → 东方财富 → 新浪 |
| 美股 | Polygon → Alpha Vantage → 东方财富 → 新浪 |

**开箱即用**:A股 默认走 BaoStock(免费、无需任何配置)。

**想接更专业的源**:去下面网站免费注册,把拿到的 token / Key 粘进 `backend/config.py`
对应的引号里(留空则跳过该源):

| 数据源 | 用途 | 注册地址 |
|--------|------|---------|
| Tushare Pro | A股 / 港股 | https://tushare.pro/register (注册送积分) |
| Polygon.io | 美股 | https://polygon.io/ (有免费档) |
| Alpha Vantage | 美股 | https://www.alphavantage.co/support/#api-key |

> 配置后**重启后端**(关掉黑窗口、重新 `start.bat`)才生效。
> Alpha Vantage 免费档为不复权日线、每天 25 次调用限制;Polygon 免费档限速 5 次/分钟。

### 关于代理(重要)

国内数据源(东方财富/Tushare/新浪)代码里已设置**绕过系统代理直连**;
海外源(Polygon/Alpha Vantage)仍走系统代理(在国内访问海外源通常正需要代理)。
如果你用 Clash 等工具且 A股 仍报连接错误,可在其规则里把 `*.eastmoney.com`、
`tushare.pro` 设为直连,或临时关闭系统代理。

---

## 打分模型说明(多因子加权)

以 50 分为中性基准,综合 **10 类技术因子**加减分,最终 0-100:

1. **均线排列** — 多头/空头排列(价、MA5、MA20、MA60 的关系),权重最高
2. **趋势力度** — MA20 斜率
3. **ADX 强度** — 趋势是否够强,并按方向加减
4. **MACD** — 动量方向
5. **RSI** — 超买/超卖
6. **KDJ** — 金叉/死叉、超买超卖
7. **布林带** — 价格在带内位置(%B)
8. **多周期动量** — 5 日 + 20 日涨跌幅
9. **量价** — 量比 + OBV 资金流向
10. **52 周位置** — 高位追高风险 / 低位低吸机会

≥65 分 → 看涨;≤35 分 → 看跌;中间 → 中性/震荡。打分再经 logistic 映射成
**估计上涨概率**。每项加减分都在界面透明列出。

> ⚠️ 这套信号是否有效,**因股、因周期而异**,务必用「信号回测」实测,不要盲目依赖。

### 指标计算位置
- `analysis.py` — `add_indicators()` 算指标,`_evaluate()` 单日打分,`score()` 综合输出
- `backtest.py` — 复用 `_evaluate()` 对历史逐日打分,统计命中率

---

## API 接口

| 接口 | 说明 |
|------|------|
| `GET /api/markets` | 支持的市场列表 |
| `GET /api/presets` | 预设股票池(批量扫描用) |
| `GET /api/analyze?market=A股&symbol=600519&months=12` | 抓数据 + 多因子打分 + 概率 + K线序列 |
| `GET /api/compare?market=A股&symbol=600519&months=12` | 单股 vs 大盘指数对比 |
| `POST /api/multi_compare` body: `{market, symbols:[...], months}` | 多股横向对比(归一化走势/收益/波动/回撤/相关性) |
| `GET /api/backtest?market=A股&symbol=600519&horizon=20` | 信号历史准确率回测 |
| `POST /api/scan`  body: `{market, symbols:[...], months}` | 批量扫描并按打分排序 |
| `GET /api/fundamentals?market=A股&symbol=600519` | 基本面数据 + 评分 |
| `GET /api/ml?market=A股&symbol=600519&horizon=10` | AI 模型预测 + 样本外准确率 |
| `GET /api/news?market=A股&symbol=600519` | 近期新闻 + 情绪打分 |
| `GET /api/search_us?keyword=AAPL` | 美股代码查找 |
