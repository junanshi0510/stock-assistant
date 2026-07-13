import { useEffect, useState } from 'react'
import { CheckCircle2, History, Save, ShieldCheck, SlidersHorizontal } from 'lucide-react'
import {
  activateInvestmentProfileVersion,
  createInvestmentProfileDraft,
  fetchInvestmentProfile,
  fetchInvestmentProfileAudit,
  fetchInvestmentProfileVersions,
} from '../../api/portfolio'

const RISK_OPTIONS = [
  ['stable', '稳健'],
  ['balanced', '均衡'],
  ['aggressive', '进取'],
]
const HORIZON_OPTIONS = [
  ['short', '短期（1 年内）'],
  ['mid_long', '中长期（1-5 年）'],
  ['long', '长期（5 年以上）'],
]
const EXPERIENCE_OPTIONS = [
  ['beginner', '初学（不足 2 年）'],
  ['intermediate', '有经验（2-5 年）'],
  ['experienced', '经验丰富（5 年以上）'],
]
const OBJECTIVE_OPTIONS = [
  ['capital_preservation', '本金稳定优先'],
  ['balanced_growth', '风险与增长平衡'],
  ['long_term_growth', '长期增长优先'],
]
const FUND_MARKETS = [
  ['mainland', '内地市场'],
  ['hong_kong', '港股市场'],
  ['united_states', '美国市场'],
  ['global', '全球及其他海外'],
]

const EMPTY_FORM = {
  risk: 'balanced',
  horizon: 'mid_long',
  experience_level: 'beginner',
  primary_objective: 'balanced_growth',
  monthly_budget: '0',
  max_single_ratio: '30',
  max_equity_ratio: '70',
  max_industry_ratio: '25',
  max_drawdown_pct: '25',
  liquidity_reserve_months: '3',
  allowed_fund_markets: ['mainland'],
  accept_fx_risk: false,
  emergency_fund_confirmed: false,
  review_cycle_months: '6',
}

function optionLabel(options, value) {
  return options.find(([key]) => key === value)?.[1] || '-'
}

function dateText(value) {
  if (!value) return '-'
  const parsed = new Date(value)
  return Number.isNaN(parsed.getTime()) ? String(value).replace('T', ' ') : parsed.toLocaleString('zh-CN', { hour12: false })
}

export default function InvestmentPolicyPanel({ onActivated }) {
  const [profile, setProfile] = useState(null)
  const [versions, setVersions] = useState([])
  const [audit, setAudit] = useState(null)
  const [form, setForm] = useState(EMPTY_FORM)
  const [editing, setEditing] = useState(false)
  const [draft, setDraft] = useState(null)
  const [validation, setValidation] = useState(null)
  const [acknowledged, setAcknowledged] = useState(false)
  const [loading, setLoading] = useState(true)
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState('')

  async function load() {
    setLoading(true)
    setError('')
    try {
      const [current, history, auditData] = await Promise.all([
        fetchInvestmentProfile(),
        fetchInvestmentProfileVersions(12),
        fetchInvestmentProfileAudit(),
      ])
      setProfile(current)
      setVersions(history.items || [])
      setAudit(auditData)
    } catch (requestError) {
      setError(requestError.message || '投资政策读取失败')
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => { load() }, [])

  useEffect(() => {
    if (!profile) return
    setForm({
      risk: profile.risk || EMPTY_FORM.risk,
      horizon: profile.horizon || EMPTY_FORM.horizon,
      experience_level: profile.experience_level || EMPTY_FORM.experience_level,
      primary_objective: profile.primary_objective || EMPTY_FORM.primary_objective,
      monthly_budget: String(profile.monthly_budget ?? EMPTY_FORM.monthly_budget),
      max_single_ratio: String(profile.max_single_ratio ?? EMPTY_FORM.max_single_ratio),
      max_equity_ratio: String(profile.max_equity_ratio ?? EMPTY_FORM.max_equity_ratio),
      max_industry_ratio: String(profile.max_industry_ratio ?? EMPTY_FORM.max_industry_ratio),
      max_drawdown_pct: String(profile.max_drawdown_pct ?? EMPTY_FORM.max_drawdown_pct),
      liquidity_reserve_months: String(profile.liquidity_reserve_months ?? EMPTY_FORM.liquidity_reserve_months),
      allowed_fund_markets: profile.allowed_fund_markets?.length ? profile.allowed_fund_markets : EMPTY_FORM.allowed_fund_markets,
      accept_fx_risk: Boolean(profile.accept_fx_risk),
      emergency_fund_confirmed: Boolean(profile.emergency_fund_confirmed),
      review_cycle_months: String(profile.review_cycle_months ?? EMPTY_FORM.review_cycle_months),
    })
  }, [profile])

  function update(field, value) {
    setForm((current) => ({ ...current, [field]: value }))
    setDraft(null)
    setValidation(null)
    setAcknowledged(false)
  }

  function toggleMarket(value) {
    const selected = new Set(form.allowed_fund_markets)
    if (selected.has(value)) selected.delete(value)
    else selected.add(value)
    update('allowed_fund_markets', [...selected])
  }

  function payload() {
    return {
      risk: form.risk,
      horizon: form.horizon,
      experience_level: form.experience_level,
      primary_objective: form.primary_objective,
      monthly_budget: Number(form.monthly_budget),
      max_single_ratio: Number(form.max_single_ratio),
      max_equity_ratio: Number(form.max_equity_ratio),
      max_industry_ratio: Number(form.max_industry_ratio),
      max_drawdown_pct: Number(form.max_drawdown_pct),
      liquidity_reserve_months: Number(form.liquidity_reserve_months),
      allowed_fund_markets: form.allowed_fund_markets,
      accept_fx_risk: form.accept_fx_risk,
      emergency_fund_confirmed: form.emergency_fund_confirmed,
      review_cycle_months: Number(form.review_cycle_months),
    }
  }

  async function createDraft() {
    setSaving(true)
    setError('')
    try {
      const result = await createInvestmentProfileDraft(payload())
      setDraft(result.draft)
      setValidation(result.validation)
      setAcknowledged(false)
      const history = await fetchInvestmentProfileVersions(12)
      setVersions(history.items || [])
    } catch (requestError) {
      setError(requestError.message || '投资政策草稿创建失败')
    } finally {
      setSaving(false)
    }
  }

  async function activateDraft() {
    if (!draft || !validation?.valid || !acknowledged) return
    setSaving(true)
    setError('')
    try {
      await activateInvestmentProfileVersion(draft.id, {
        acknowledged: true,
        expected_payload_sha256: draft.payload_sha256,
        expected_active_version_id: profile?.profile_version_id || null,
        consent_version: validation.consent.version,
        consent_text_sha256: validation.consent.text_sha256,
      })
      setDraft(null)
      setValidation(null)
      setAcknowledged(false)
      setEditing(false)
      await load()
      await onActivated?.()
    } catch (requestError) {
      setError(requestError.message || '投资政策激活失败，请重新校验当前版本')
    } finally {
      setSaving(false)
    }
  }

  if (loading && !profile) return <div className="page-loading"><span className="spinner" />正在读取投资政策与审计链</div>

  return (
    <div className="policy-workspace">
      <section className={`policy-status ${profile?.configured ? 'active' : 'inactive'}`}>
        <div className="policy-status-icon"><ShieldCheck size={22} aria-hidden="true" /></div>
        <div>
          <span>当前决策门禁</span>
          <h3>{profile?.configured ? `V${profile.version_no} 投资政策已激活` : '尚未建立可用于决策的投资政策'}</h3>
          <p>{profile?.configured
            ? `版本哈希${profile.integrity_verified ? '已通过' : '校验失败'}，下次复核 ${dateText(profile.review_due_at)}。`
            : '页面中的初始值只是待确认草稿，不会自动进入 Agent 或组合行动报告。'}</p>
        </div>
        <button type="button" onClick={() => setEditing((value) => !value)}>
          <SlidersHorizontal size={16} aria-hidden="true" />{editing ? '收起设置' : profile?.configured ? '修订政策' : '建立政策'}
        </button>
      </section>

      {profile?.configured && !editing && (
        <section className="policy-summary" aria-label="当前投资政策摘要">
          <div><span>目标</span><b>{optionLabel(OBJECTIVE_OPTIONS, profile.primary_objective)}</b></div>
          <div><span>期限</span><b>{optionLabel(HORIZON_OPTIONS, profile.horizon)}</b></div>
          <div><span>单品上限</span><b>{profile.max_single_ratio}%</b></div>
          <div><span>权益上限</span><b>{profile.max_equity_ratio}%</b></div>
          <div><span>行业上限</span><b>{profile.max_industry_ratio}%</b></div>
          <div><span>最大回撤</span><b>{profile.max_drawdown_pct}%</b></div>
          <div><span>月度预算</span><b>¥{Number(profile.monthly_budget || 0).toLocaleString('zh-CN')}</b></div>
          <div><span>允许市场</span><b>{(profile.allowed_fund_markets || []).map((value) => optionLabel(FUND_MARKETS, value)).join('、') || '-'}</b></div>
        </section>
      )}

      {editing && (
        <div className="policy-editor">
          <section className="policy-editor-section">
            <div className="policy-editor-head"><span>01</span><div><h3>目标与承受能力</h3><p>先定义为什么投资以及能够承受什么，再设置仓位数字。</p></div></div>
            <div className="policy-form-grid">
              <label><span>风险偏好</span><select value={form.risk} onChange={(event) => update('risk', event.target.value)}>{RISK_OPTIONS.map(([value, label]) => <option value={value} key={value}>{label}</option>)}</select></label>
              <label><span>投资期限</span><select value={form.horizon} onChange={(event) => update('horizon', event.target.value)}>{HORIZON_OPTIONS.map(([value, label]) => <option value={value} key={value}>{label}</option>)}</select></label>
              <label><span>投资经验</span><select value={form.experience_level} onChange={(event) => update('experience_level', event.target.value)}>{EXPERIENCE_OPTIONS.map(([value, label]) => <option value={value} key={value}>{label}</option>)}</select></label>
              <label><span>主要目标</span><select value={form.primary_objective} onChange={(event) => update('primary_objective', event.target.value)}>{OBJECTIVE_OPTIONS.map(([value, label]) => <option value={value} key={value}>{label}</option>)}</select></label>
              <label><span>每月新增预算</span><input type="number" min="0" max="10000000" step="100" value={form.monthly_budget} onChange={(event) => update('monthly_budget', event.target.value)} /></label>
              <label><span>流动性储备（月）</span><input type="number" min="0" max="36" value={form.liquidity_reserve_months} onChange={(event) => update('liquidity_reserve_months', event.target.value)} /></label>
            </div>
          </section>

          <section className="policy-editor-section">
            <div className="policy-editor-head"><span>02</span><div><h3>组合风险边界</h3><p>这些上限会直接约束行动报告，必须由你逐项确认。</p></div></div>
            <div className="policy-form-grid four">
              <label><span>单品最大占比 %</span><input type="number" min="5" max="60" value={form.max_single_ratio} onChange={(event) => update('max_single_ratio', event.target.value)} /></label>
              <label><span>权益资产上限 %</span><input type="number" min="0" max="100" value={form.max_equity_ratio} onChange={(event) => update('max_equity_ratio', event.target.value)} /></label>
              <label><span>单行业上限 %</span><input type="number" min="5" max="50" value={form.max_industry_ratio} onChange={(event) => update('max_industry_ratio', event.target.value)} /></label>
              <label><span>最大可承受回撤 %</span><input type="number" min="5" max="50" value={form.max_drawdown_pct} onChange={(event) => update('max_drawdown_pct', event.target.value)} /></label>
            </div>
          </section>

          <section className="policy-editor-section">
            <div className="policy-editor-head"><span>03</span><div><h3>市场范围与确认</h3><p>跨境基金涉及汇率、休市和净值确认差异。</p></div></div>
            <fieldset className="policy-market-field">
              <legend>允许投资的基金市场</legend>
              <div>{FUND_MARKETS.map(([value, label]) => <label key={value}><input type="checkbox" checked={form.allowed_fund_markets.includes(value)} onChange={() => toggleMarket(value)} /><span>{label}</span></label>)}</div>
            </fieldset>
            <div className="policy-confirmations">
              <label><input type="checkbox" checked={form.accept_fx_risk} onChange={(event) => update('accept_fx_risk', event.target.checked)} /><span>我接受跨境基金的汇率波动、海外休市和净值确认滞后风险。</span></label>
              <label><input type="checkbox" checked={form.emergency_fund_confirmed} onChange={(event) => update('emergency_fund_confirmed', event.target.checked)} /><span>我确认投资预算不占用生活、偿债和应急资金。</span></label>
              <label><span>政策复核周期</span><select value={form.review_cycle_months} onChange={(event) => update('review_cycle_months', event.target.value)}><option value="6">每 6 个月</option><option value="12">每 12 个月</option></select></label>
            </div>
          </section>

          <div className="policy-editor-actions">
            <button type="button" onClick={createDraft} disabled={saving || form.allowed_fund_markets.length === 0}><Save size={16} aria-hidden="true" />{saving ? '校验中' : '校验并生成新版本'}</button>
            <button type="button" className="ghost" onClick={() => setEditing(false)} disabled={saving}>取消</button>
          </div>

          {validation && (
            <section className={`policy-validation ${validation.valid ? 'valid' : 'invalid'}`}>
              <div><CheckCircle2 size={19} aria-hidden="true" /><div><h3>{validation.valid ? '适当性校验通过，等待确认激活' : '校验未通过，不会进入决策'}</h3><code>{draft?.payload_sha256 || '-'}</code></div></div>
              {validation.errors?.map((item) => <p className="error" key={`${item.field}-${item.code}`}><b>{item.field}</b> {item.message}</p>)}
              {validation.warnings?.map((item) => <p className="warning-line" key={`${item.field}-${item.code}`}><b>{item.field}</b> {item.message}</p>)}
              {validation.valid && <><label className="policy-consent"><input type="checkbox" checked={acknowledged} onChange={(event) => setAcknowledged(event.target.checked)} /><span>{validation.consent.text}</span></label><button type="button" onClick={activateDraft} disabled={saving || !acknowledged}>确认并激活 V{draft?.version_no}</button></>}
            </section>
          )}
        </div>
      )}

      {error && <div className="error policy-error">{error}</div>}

      <section className="policy-history">
        <div className="policy-history-head"><div><History size={17} aria-hidden="true" /><div><h3>政策版本与审计</h3><p>{audit?.verification?.verified ? `${audit.count} 个审计事件校验通过` : '审计链待检查或校验失败'}</p></div></div></div>
        {versions.length > 0 ? versions.map((item) => <div className="policy-history-row" key={item.id}><b>V{item.version_no}</b><span>{item.status}</span><time>{dateText(item.activated_at || item.created_at)}</time><code>{item.payload_sha256?.slice(0, 16)}</code></div>) : <p className="policy-history-empty">尚无政策版本。</p>}
      </section>
    </div>
  )
}
