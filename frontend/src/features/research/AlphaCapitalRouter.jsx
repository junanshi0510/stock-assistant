import { useCallback, useEffect, useState } from 'react'
import {
  AlertTriangle,
  Ban,
  CheckCircle2,
  ChevronDown,
  ChevronUp,
  CircleDollarSign,
  Compass,
  Database,
  Fingerprint,
  GitBranch,
  History,
  Layers3,
  LockKeyhole,
  RefreshCw,
  Scale,
  ShieldCheck,
  Target,
  WalletCards,
} from 'lucide-react'
import {
  fetchAlphaCapitalMandate,
  fetchAlphaCapitalMandates,
  fetchAlphaCapitalRoute,
  freezeAlphaCapitalRoute,
} from '../../api/alphaForecasts'

const STATUS = {
  blocked: ['政策阻断', 'danger'],
  collecting: ['积累前瞻证据', 'waiting'],
  abstained: ['现金 / 否决路线', 'warning'],
  paper_ready: ['研究路线可冻结', 'verified'],
}

function number(value) {
  const parsed = Number(value)
  return Number.isFinite(parsed) ? parsed : null
}

function pct(value, digits = 1) {
  const parsed = number(value)
  return parsed == null ? '—' : `${parsed.toFixed(digits)}%`
}

function probabilityEdge(value) {
  const parsed = number(value)
  return parsed == null ? '—' : `${parsed >= 0 ? '+' : ''}${(parsed * 100).toFixed(1)}pp`
}

function shortHash(value) {
  return value ? `${String(value).slice(0, 12)}…` : '—'
}

function dateTime(value) {
  if (!value) return '—'
  const parsed = new Date(value)
  return Number.isNaN(parsed.getTime())
    ? String(value)
    : parsed.toLocaleString('zh-CN', { hour12: false })
}

function RouteBadge({ status }) {
  const [label, tone] = STATUS[status] || [status || '未知', 'waiting']
  return (
    <span className={`alpha-route-badge ${tone}`}>
      {status === 'paper_ready' ? <CheckCircle2 size={13} /> : <Compass size={13} />}
      {label}
    </span>
  )
}

function SleeveBar({ route }) {
  const sleeves = route?.sleeves || {}
  const core = number(sleeves.core_allocated_pct) || 0
  const satellite = number(sleeves.satellite_allocated_pct) || 0
  const cash = Math.max(0, 100 - core - satellite)
  return (
    <section className="alpha-router-sleeves">
      <header>
        <div>
          <span className="eyebrow">Core / satellite construction</span>
          <h3><Layers3 size={18} />核心-卫星与现金路线</h3>
        </div>
        <small>政策目标 核心 {pct(sleeves.core_target_pct, 0)} · 卫星 {pct(sleeves.satellite_target_pct, 0)}</small>
      </header>
      <div className="alpha-router-bar" aria-label={`核心 ${core}%，卫星 ${satellite}%，现金 ${cash}%`}>
        <span className="core" style={{ width: `${core}%` }} />
        <span className="satellite" style={{ width: `${satellite}%` }} />
        <span className="cash" style={{ width: `${cash}%` }} />
      </div>
      <div className="alpha-router-legend">
        <span><i className="core" />核心 <b>{pct(core)}</b></span>
        <span><i className="satellite" />卫星 <b>{pct(satellite)}</b></span>
        <span><i className="cash" />保留现金 <b>{pct(cash)}</b></span>
      </div>
    </section>
  )
}

function CandidateTable({ candidates }) {
  if (!candidates.length) {
    return (
      <div className="alpha-router-empty">
        <WalletCards size={24} />
        <div><b>本期没有获得模型权重的候选</b><small>资金保持现金，不会为了“满仓”而强制分配。</small></div>
      </div>
    )
  }
  return (
    <div className="alpha-router-table-wrap">
      <table className="alpha-router-table">
        <thead>
          <tr><th>排名 / 资产</th><th>角色</th><th>原始边际</th><th>可靠性收缩后</th><th>合格周期</th><th>模型目标</th><th>资金桥状态</th></tr>
        </thead>
        <tbody>
          {candidates.map((item) => (
            <tr key={item.key}>
              <td><span className="alpha-router-rank">{item.candidate_rank}</span><div><b>{item.name || item.symbol}</b><small>{item.market} · {item.symbol}</small></div></td>
              <td><em className={`alpha-sleeve ${item.sleeve}`}>{item.sleeve === 'core' ? '核心' : '卫星'}</em></td>
              <td><strong>{probabilityEdge(item.weighted_raw_edge)}</strong></td>
              <td><strong>{probabilityEdge(item.weighted_effective_edge)}</strong><small>可靠性 {pct((number(item.weighted_reliability) || 0) * 100, 0)}</small></td>
              <td><b>{item.eligible_horizon_count}</b><small>{(item.horizons || []).map((row) => row.horizon_sessions).join(' / ')}</small></td>
              <td><strong className="target">{pct(item.model_target_weight_pct)}</strong></td>
              <td>
                <span className={`alpha-bridge ${item.capital_bridge_state === 'new_fund_due_diligence_only' ? 'warning' : ''}`}>
                  {item.capital_bridge_state === 'held_fund_top_up_candidate'
                    ? '已持基金·可进入二次风控'
                    : item.capital_bridge_state === 'new_fund_due_diligence_only'
                      ? '新基金·仅尽调'
                      : '股票·进入二次风控'}
                </span>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

function VetoPanel({ vetoes }) {
  if (!vetoes.length) return null
  return (
    <section className="alpha-router-veto">
      <header><Ban size={17} /><div><b>新增资金否决清单</b><small>负向或长短周期冲突只阻止新增，不做空、不反向下注</small></div></header>
      <div>
        {vetoes.map((item) => (
          <article key={item.key}>
            <span><b>{item.name || item.symbol}</b><small>{item.market} · {item.symbol}</small></span>
            <em>{item.label}</em><strong>{probabilityEdge(item.weighted_effective_edge)}</strong>
          </article>
        ))}
      </div>
    </section>
  )
}

function GateStrip({ gates }) {
  return (
    <div className="alpha-router-gates">
      {(gates || []).map((gate) => (
        <span className={gate.status} title={gate.detail} key={gate.code}>
          {gate.status === 'pass' ? <CheckCircle2 size={13} /> : gate.status === 'block' ? <Ban size={13} /> : <Scale size={13} />}
          <b>{gate.label}</b><small>{gate.detail}</small>
        </span>
      ))}
    </div>
  )
}

function HistoryPanel({ history, selected, onSelect }) {
  const [open, setOpen] = useState(false)
  return (
    <section className="alpha-router-history">
      <button type="button" onClick={() => setOpen((value) => !value)}>
        <span><History size={15} /><b>不可变路线历史</b><small>{history.length} 个版本</small></span>
        {open ? <ChevronUp size={16} /> : <ChevronDown size={16} />}
      </button>
      {open && (
        <div>
          {history.length ? history.map((item) => (
            <button type="button" className={selected?.id === item.id ? 'active' : ''} onClick={() => onSelect(item.id)} key={item.id}>
              <RouteBadge status={item.status} />
              <span><b>{dateTime(item.created_at)}</b><small>{shortHash(item.evidence_sha256)}</small></span>
            </button>
          )) : <p>尚未冻结任何路线。</p>}
          {selected && (
            <article>
              <span><Fingerprint size={14} />结果 {shortHash(selected.result_sha256)}</span>
              <span><Database size={14} />证据 {shortHash(selected.evidence_sha256)}</span>
              <span><ShieldCheck size={14} />完整性 {selected.integrity?.verified ? '通过' : '失败'}</span>
            </article>
          )}
        </div>
      )}
    </section>
  )
}

export default function AlphaCapitalRouter() {
  const [route, setRoute] = useState(null)
  const [history, setHistory] = useState([])
  const [selected, setSelected] = useState(null)
  const [acknowledged, setAcknowledged] = useState(false)
  const [busy, setBusy] = useState('')
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')

  const load = useCallback(async ({ quiet = false } = {}) => {
    if (!quiet) setBusy('load')
    try {
      const [nextRoute, nextHistory] = await Promise.all([
        fetchAlphaCapitalRoute(),
        fetchAlphaCapitalMandates(20),
      ])
      setRoute(nextRoute)
      setHistory(nextHistory.items || [])
      setError('')
    } catch (requestError) {
      setError(requestError.message)
    } finally {
      if (!quiet) setBusy('')
    }
  }, [])

  useEffect(() => { load() }, [load])

  const freeze = async () => {
    if (!route?.evidence_sha256 || !acknowledged) return
    setBusy('freeze')
    setError('')
    setNotice('')
    try {
      const response = await freezeAlphaCapitalRoute(route.evidence_sha256)
      setNotice(response.created ? '当前核心-卫星研究路线已不可变冻结。' : '相同证据路线已存在，未重复创建。')
      setAcknowledged(false)
      await load({ quiet: true })
    } catch (requestError) {
      setError(requestError.message)
    } finally {
      setBusy('')
    }
  }

  const inspect = async (mandateId) => {
    setBusy(`history:${mandateId}`)
    try {
      setSelected(await fetchAlphaCapitalMandate(mandateId))
      setError('')
    } catch (requestError) {
      setError(requestError.message)
    } finally {
      setBusy('')
    }
  }

  const candidates = route?.candidates || []
  const vetoes = route?.vetoes || []
  const persistence = route?.persistence || {}
  const canFreeze = ['paper_ready', 'abstained'].includes(route?.status) && !persistence.binding_current

  return (
    <section className="alpha-router">
      <header className="alpha-router-head">
        <div>
          <span className="eyebrow">Multi-horizon Alpha · Capital routing</span>
          <h2><GitBranch size={21} />多周期 Alpha 资本路由</h2>
          <p>{route?.primary_action?.headline || '正在读取 Alpha 资本路线'}</p>
        </div>
        <div><RouteBadge status={route?.status} /><button type="button" onClick={() => load()} disabled={busy === 'load'}><RefreshCw className={busy === 'load' ? 'spin' : ''} size={14} />刷新路线</button></div>
      </header>

      {error && <div className="alpha-router-message error"><AlertTriangle size={17} /><span><b>资本路由未完成</b><small>{error}</small></span></div>}
      {notice && <div className="alpha-router-message success"><CheckCircle2 size={17} />{notice}</div>}

      <div className="alpha-router-kpis">
        <span><Target size={16} /><b>{route?.summary?.allocated_candidate_count || 0}</b><small>获得权重候选</small></span>
        <span><Ban size={16} /><b>{route?.summary?.veto_count || 0}</b><small>新增资金否决</small></span>
        <span><CircleDollarSign size={16} /><b>{pct(route?.summary?.model_invested_pct)}</b><small>Alpha 模型投入</small></span>
        <span><WalletCards size={16} /><b>{pct(route?.summary?.model_cash_pct)}</b><small>模型现金</small></span>
        <span><Scale size={16} /><b>{pct(route?.summary?.alpha_pilot_cap_pct_of_portfolio)}</b><small>全组合 Alpha 硬上限</small></span>
      </div>

      <SleeveBar route={route} />
      <section className="alpha-router-candidates">
        <header><div><span className="eyebrow">Reliability-shrunk ranking</span><h3><Target size={18} />正向候选与模型目标</h3></div><small>边际相对每个模型自己的冻结基准胜率，不与 50% 生硬比较</small></header>
        <CandidateTable candidates={candidates} />
      </section>
      <VetoPanel vetoes={vetoes} />
      <GateStrip gates={route?.gates} />

      <section className="alpha-router-freeze">
        <div><span><LockKeyhole size={18} /></span><div><b>{persistence.binding_current ? '当前路线已经冻结并与证据一致' : '冻结后才允许全组合资金引擎读取'}</b><small>{persistence.binding_current ? `指令 ${persistence.latest_mandate?.id || '—'} · ${shortHash(persistence.latest_mandate?.result_sha256)}` : '冻结只授权研究层读取；仍不授权下单、做空、杠杆或收益承诺。'}</small></div></div>
        {!persistence.binding_current && <label><input type="checkbox" checked={acknowledged} onChange={(event) => setAcknowledged(event.target.checked)} /><span>我确认这是研究资金路线，不是订单或收益承诺</span></label>}
        <button type="button" onClick={freeze} disabled={!canFreeze || !acknowledged || busy === 'freeze'}>
          {busy === 'freeze' ? <RefreshCw className="spin" size={15} /> : <Fingerprint size={15} />}
          {persistence.binding_current ? '证据绑定有效' : route?.status === 'abstained' ? '冻结现金 / 否决路线' : '冻结核心-卫星路线'}
        </button>
      </section>

      <div className="alpha-router-audit">
        <span><Fingerprint size={14} /><small>当前证据</small><b>{shortHash(route?.evidence_sha256)}</b></span>
        <span><Database size={14} /><small>证据截止</small><b>{dateTime(route?.evidence_cutoff_at)}</b></span>
        <span><ShieldCheck size={14} /><small>漂移状态</small><b>{route?.drift?.state || '—'} · 单边 {pct(route?.drift?.one_way_turnover_pct)}</b></span>
        <span><LockKeyhole size={14} /><small>执行边界</small><b>人工复核 · 不自动下单</b></span>
      </div>
      <HistoryPanel history={history} selected={selected} onSelect={inspect} />
    </section>
  )
}
