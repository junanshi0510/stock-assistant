import { useEffect, useMemo, useState } from 'react'
import { Activity, AlertTriangle, CalendarClock, RefreshCw, ShieldCheck, TrendingUp, WalletCards } from 'lucide-react'
import { fetchOpportunityPaperBasket, observeOpportunityPaperBasket } from '../../api/opportunities'

function number(value, digits = 2, suffix = '') {
  const parsed = Number(value)
  return Number.isFinite(parsed) ? `${parsed.toFixed(digits)}${suffix}` : '—'
}

function signed(value, digits = 2) {
  const parsed = Number(value)
  return Number.isFinite(parsed) ? `${parsed > 0 ? '+' : ''}${parsed.toFixed(digits)}%` : '—'
}

function dateTime(value) {
  if (!value) return '尚未观察'
  return new Date(value).toLocaleString('zh-CN', { hour12: false })
}

export default function PaperTracker({ baskets, selectedId, onSelect, onRefresh }) {
  const [detail, setDetail] = useState(null)
  const [loading, setLoading] = useState(false)
  const [observing, setObserving] = useState(false)
  const [error, setError] = useState('')
  const activeId = selectedId || baskets[0]?.id

  useEffect(() => {
    if (!activeId) {
      setDetail(null)
      return
    }
    let live = true
    setLoading(true); setError('')
    fetchOpportunityPaperBasket(activeId)
      .then((result) => { if (live) setDetail(result) })
      .catch((requestError) => { if (live) setError(requestError.message) })
      .finally(() => { if (live) setLoading(false) })
    return () => { live = false }
  }, [activeId])

  const latest = detail?.latest_observation?.payload
  const positions = latest?.positions || detail?.snapshot?.positions || []
  const history = useMemo(() => [...(detail?.observations || [])].reverse(), [detail])

  async function observe() {
    if (!activeId) return
    setObserving(true); setError('')
    try {
      await observeOpportunityPaperBasket(activeId)
      const refreshed = await fetchOpportunityPaperBasket(activeId)
      setDetail(refreshed)
      onRefresh?.()
    } catch (requestError) {
      setError(requestError.message)
    } finally {
      setObserving(false)
    }
  }

  if (!baskets.length) return <section className="opp-paper-empty"><WalletCards size={30} /><h3>还没有纸面组合</h3><p>先运行机会策略，确认淘汰原因和组合约束，再从冻结结果启动纸面跟踪。系统不会把回测最优参数直接当成前瞻成绩。</p></section>

  return (
    <div className="opp-paper-workspace">
      <aside className="opp-paper-list">
        <div><span className="eyebrow">纸面组合</span><b>{baskets.length} 个冻结批次</b></div>
        {baskets.map((basket) => {
          const observation = basket.latest_observation?.payload
          return <button key={basket.id} className={basket.id === activeId ? 'active' : ''} onClick={() => onSelect(basket.id)}>
            <span><b>{basket.snapshot?.strategy?.name || '机会组合'}</b><small>{String(basket.id).slice(-8)} · {basket.snapshot?.positions?.length || 0} 只</small></span>
            <em className={Number(observation?.weighted_return_pct) >= 0 ? 'positive' : 'negative'}>{observation ? signed(observation.weighted_return_pct) : '待观察'}</em>
          </button>
        })}
      </aside>

      <section className="opp-paper-detail">
        {loading && <div className="page-loading"><span className="spinner" />正在读取冻结组合</div>}
        {error && <div className="error">{error}</div>}
        {!loading && detail && <>
          <div className="opp-paper-head">
            <div><span className="eyebrow">前瞻纸面跟踪 · 非真实持仓</span><h2>{detail.snapshot.strategy?.name || '机会组合'}</h2><p>冻结于 {dateTime(detail.snapshot.frozen_at)} · 策略 v{detail.snapshot.strategy?.version_no}</p></div>
            <button onClick={observe} disabled={observing}>{observing ? <><span className="spinner" />读取行情</> : <><RefreshCw size={15} />更新真实收盘表现</>}</button>
          </div>
          <div className="opp-paper-kpis">
            <div><Activity size={17} /><span>成本后纸面收益<small>{latest?.schema_version === 'opportunity_paper_observation.v2' ? `已扣 ${number(latest.round_trip_cost_scenario_bps, 0, ' bps')} 往返成本情景` : '旧观察尚未纳入成本'}</small></span><b className={Number(latest?.net_return_after_cost_pct ?? latest?.weighted_return_pct) >= 0 ? 'positive' : 'negative'}>{latest ? signed(latest.net_return_after_cost_pct ?? latest.weighted_return_pct) : '—'}</b></div>
            <div><TrendingUp size={17} /><span>成本后净超额<small>按 A/H/美股市场基准同日起算</small></span><b className={Number(latest?.net_excess_return_pct) >= 0 ? 'positive' : 'negative'}>{latest?.net_excess_return_pct == null ? '—' : signed(latest.net_excess_return_pct)}</b></div>
            <div><ShieldCheck size={17} /><span>已覆盖仓位<small>失败股票权重不会被重新分配</small></span><b>{latest ? number(latest.covered_position_weight_pct, 1, '%') : '—'}</b></div>
            <div><WalletCards size={17} /><span>冻结现金<small>纸面现金收益固定按 0</small></span><b>{number(detail.snapshot.cash_pct, 1, '%')}</b></div>
            <div><CalendarClock size={17} /><span>最新观察<small>{detail.observations.length} 个不可变观察点</small></span><b>{latest ? dateTime(latest.observed_at) : '尚未开始'}</b></div>
          </div>
          <div className="opp-table-scroll">
            <table className="opp-paper-table"><thead><tr><th>股票</th><th>冻结权重</th><th>冻结日/价格</th><th>观察日/价格</th><th>本币收益</th><th>市场基准</th><th>组合贡献</th><th>行情源</th></tr></thead><tbody>{positions.map((position) => <tr key={`${position.market}:${position.symbol}`}>
              <td><b>{position.name || position.symbol}</b><small>{position.market} · {position.symbol}</small></td>
              <td>{number(position.weight_pct, 1, '%')}</td>
              <td>{position.entry_date || '—'}<small>{number(position.entry_price, 3)}</small></td>
              <td>{position.current_date || '待观察'}<small>{number(position.current_price, 3)}</small></td>
              <td className={Number(position.return_pct) >= 0 ? 'delta-pos' : 'delta-neg'}>{position.status === 'unavailable' ? '数据失败' : signed(position.return_pct)}</td>
              <td className={Number(position.benchmark?.return_pct) >= 0 ? 'delta-pos' : 'delta-neg'}>{position.benchmark?.status === 'available' ? signed(position.benchmark.return_pct) : '基准失败'}<small>{position.benchmark?.symbol || position.benchmark?.error || '—'}</small></td>
              <td className={Number(position.contribution_pct) >= 0 ? 'delta-pos' : 'delta-neg'}>{signed(position.contribution_pct)}</td>
              <td>{position.source || position.price_source || '—'}{position.error && <small>{position.error}</small>}</td>
            </tr>)}</tbody></table>
          </div>
          {history.length > 0 && <div className="opp-paper-history"><div><span className="eyebrow">观察历史</span><b>自动日终采集 · 相同行情截面幂等去重</b></div><div>{history.map((observation) => <span key={observation.id}><small>{dateTime(observation.observed_at)} · {observation.payload.observed_trading_days_min ?? 0} 日</small><b className={Number(observation.payload.net_return_after_cost_pct ?? observation.payload.weighted_return_pct) >= 0 ? 'positive' : 'negative'}>{signed(observation.payload.net_return_after_cost_pct ?? observation.payload.weighted_return_pct)}</b><em>超额 {signed(observation.payload.net_excess_return_pct)} · {number(observation.payload.covered_position_weight_pct, 0, '%')} 覆盖</em></span>)}</div></div>}
          <div className="opp-warning-list">{(latest?.limitations || detail.snapshot.limitations || []).map((item) => <div key={item}><AlertTriangle size={14} /><span>{item}</span></div>)}</div>
        </>}
      </section>
    </div>
  )
}
