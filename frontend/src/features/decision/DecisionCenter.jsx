import { useEffect, useState } from 'react'
import {
  AlertTriangle,
  ArrowUpRight,
  BadgeCheck,
  ChartNoAxesCombined,
  CircleAlert,
  DatabaseZap,
  Layers3,
  ReceiptText,
  RefreshCw,
  ShieldCheck,
  Settings2,
  WalletCards,
} from 'lucide-react'
import {
  activateInvestmentProfileVersion,
  createInvestmentProfileDraft,
  fetchInvestmentProfileAudit,
  fetchInvestmentProfileVersions,
} from '../../api/portfolio'

const RISK_OPTIONS = [
  { value: 'stable', label: '稳健' },
  { value: 'balanced', label: '均衡' },
  { value: 'aggressive', label: '进取' },
]

const HORIZON_OPTIONS = [
  { value: 'short', label: '短期（1 年内）' },
  { value: 'mid_long', label: '中长期（1-5 年）' },
  { value: 'long', label: '长期（5 年以上）' },
]

const EXPERIENCE_OPTIONS = [
  { value: 'beginner', label: '初学（不足 2 年）' },
  { value: 'intermediate', label: '有经验（2-5 年）' },
  { value: 'experienced', label: '经验丰富（5 年以上）' },
]

const OBJECTIVE_OPTIONS = [
  { value: 'capital_preservation', label: '本金稳定优先' },
  { value: 'balanced_growth', label: '风险与增长平衡' },
  { value: 'long_term_growth', label: '长期增长优先' },
]

const FUND_MARKET_OPTIONS = [
  { value: 'mainland', label: '内地市场' },
  { value: 'hong_kong', label: '港股市场' },
  { value: 'united_states', label: '美国市场' },
  { value: 'global', label: '全球及其他海外' },
]

const PRIORITY_META = {
  high: { label: '优先处理', icon: AlertTriangle },
  medium: { label: '需要复盘', icon: CircleAlert },
  normal: { label: '研究队列', icon: BadgeCheck },
}

function actionIcon(category) {
  if (category.includes('组合') || category.includes('收益')) return WalletCards
  if (category.includes('成本') || category.includes('仓位')) return ReceiptText
  if (category.includes('基金')) return Layers3
  if (category.includes('市场')) return ChartNoAxesCombined
  if (category.includes('数据')) return DatabaseZap
  return CircleAlert
}

function formatTime(value) {
  if (!value) return '刚刚生成'
  const parsed = new Date(value)
  if (Number.isNaN(parsed.getTime())) return value.replace('T', ' ')
  return parsed.toLocaleString('zh-CN', { hour12: false, month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit' })
}

function formatBudget(value) {
  if (value == null || value === '') return '未设置'
  return `¥${Number(value).toLocaleString('zh-CN', { maximumFractionDigits: 0 })}/月`
}

export default function DecisionCenter({ data, loading, error, onRefresh, onNavigate }) {
  const [editing, setEditing] = useState(false)
  const [saving, setSaving] = useState(false)
  const [saveError, setSaveError] = useState('')
  const [draft, setDraft] = useState(null)
  const [validation, setValidation] = useState(null)
  const [acknowledged, setAcknowledged] = useState(false)
  const [versions, setVersions] = useState([])
  const [audit, setAudit] = useState(null)
  const [form, setForm] = useState({
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
  })

  const profile = data?.profile
  const summary = data?.summary || {}
  const actions = data?.actions || []
  const unavailable = data?.unavailable || []

  useEffect(() => {
    if (!profile) return
    setForm({
      risk: profile.risk || 'balanced',
      horizon: profile.horizon || 'mid_long',
      experience_level: profile.experience_level || 'beginner',
      primary_objective: profile.primary_objective || 'balanced_growth',
      monthly_budget: String(profile.monthly_budget ?? 0),
      max_single_ratio: String(profile.max_single_ratio ?? 30),
      max_equity_ratio: String(profile.max_equity_ratio ?? 70),
      max_industry_ratio: String(profile.max_industry_ratio ?? 25),
      max_drawdown_pct: String(profile.max_drawdown_pct ?? 25),
      liquidity_reserve_months: String(profile.liquidity_reserve_months ?? 3),
      allowed_fund_markets: profile.allowed_fund_markets?.length ? profile.allowed_fund_markets : ['mainland'],
      accept_fx_risk: Boolean(profile.accept_fx_risk),
      emergency_fund_confirmed: Boolean(profile.emergency_fund_confirmed),
      review_cycle_months: String(profile.review_cycle_months ?? 6),
    })
  }, [profile])

  useEffect(() => {
    if (editing) loadPolicyGovernance()
  }, [editing])

  function updateForm(field, value) {
    setForm((current) => ({ ...current, [field]: value }))
    setDraft(null)
    setValidation(null)
    setAcknowledged(false)
  }

  function toggleFundMarket(value) {
    setForm((current) => {
      const selected = new Set(current.allowed_fund_markets)
      if (selected.has(value)) selected.delete(value)
      else selected.add(value)
      return { ...current, allowed_fund_markets: [...selected] }
    })
    setDraft(null)
    setValidation(null)
    setAcknowledged(false)
  }

  function profilePayload() {
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

  async function loadPolicyGovernance() {
    try {
      const [history, auditData] = await Promise.all([
        fetchInvestmentProfileVersions(8),
        fetchInvestmentProfileAudit(),
      ])
      setVersions(history.items || [])
      setAudit(auditData)
    } catch (requestError) {
      setSaveError(requestError.message || '投资政策历史获取失败')
    }
  }

  async function createDraft() {
    setSaving(true)
    setSaveError('')
    try {
      const result = await createInvestmentProfileDraft(profilePayload())
      setDraft(result.draft)
      setValidation(result.validation)
      setAcknowledged(false)
      await loadPolicyGovernance()
    } catch (requestError) {
      setSaveError(requestError.message || '投资政策草稿创建失败')
    } finally {
      setSaving(false)
    }
  }

  async function activateDraft() {
    if (!draft || !validation?.valid || !acknowledged) return
    setSaving(true)
    setSaveError('')
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
      await onRefresh()
    } catch (requestError) {
      setSaveError(requestError.message || '投资政策激活失败，请重新获取当前版本')
    } finally {
      setSaving(false)
    }
  }

  function toggleEditing() {
    setEditing((value) => !value)
    setDraft(null)
    setValidation(null)
    setAcknowledged(false)
    setSaveError('')
  }

  function openAction(action) {
    if (action.target === 'profile') {
      setEditing(true)
      return
    }
    onNavigate(action.target)
  }

  return (
    <section className="decision-center" aria-label="今日决策中心">
      <div className="decision-center-head">
        <div>
          <span className="eyebrow">今日决策中心</span>
          <h3>先处理真实风险，再研究市场机会</h3>
          <p>{data?.policy || '正在根据真实持仓与真实市场数据生成行动清单。'}</p>
        </div>
        <button className="ghost decision-refresh" onClick={onRefresh} disabled={loading} title="刷新真实数据" aria-label="刷新真实数据">
          <RefreshCw size={16} className={loading ? 'spin-icon' : ''} aria-hidden="true" />
          <span>{loading ? '刷新中' : '刷新'}</span>
        </button>
      </div>

      <div className="decision-summary" aria-live="polite">
        <div className="decision-summary-item high"><span>优先处理</span><b>{summary.high_count ?? '-'}</b></div>
        <div className="decision-summary-item medium"><span>需要复盘</span><b>{summary.medium_count ?? '-'}</b></div>
        <div className="decision-summary-item normal"><span>研究队列</span><b>{summary.normal_count ?? '-'}</b></div>
        <div className="decision-summary-item unavailable"><span>不可用来源</span><b>{summary.unavailable_count ?? '-'}</b></div>
      </div>

      <div className="decision-profile">
        <div className="decision-profile-head">
          <div>
            <span className="decision-profile-label">版本化投资政策书（IPS）</span>
            <strong>
              {profile?.configured
                ? `V${profile.version_no} 已激活 · 下次复核 ${formatTime(profile.review_due_at)}`
                : profile?.review_required
                  ? '现有版本已到复核期，Agent 已停止使用'
                  : '尚无已确认版本，默认数值不会进入 Agent 决策'}
            </strong>
          </div>
          <button className="ghost decision-icon-button" onClick={toggleEditing} title={editing ? '收起投资政策书' : '设置投资政策书'} aria-label={editing ? '收起投资政策书' : '设置投资政策书'}>
            <Settings2 size={16} aria-hidden="true" />
          </button>
        </div>
        {!editing && (
          <div className="decision-profile-values">
            <span>风险偏好 <b>{RISK_OPTIONS.find((item) => item.value === profile?.risk)?.label || '-'}</b></span>
            <span>投资期限 <b>{HORIZON_OPTIONS.find((item) => item.value === profile?.horizon)?.label || '-'}</b></span>
            <span>投资经验 <b>{EXPERIENCE_OPTIONS.find((item) => item.value === profile?.experience_level)?.label || '-'}</b></span>
            <span>主要目标 <b>{OBJECTIVE_OPTIONS.find((item) => item.value === profile?.primary_objective)?.label || '-'}</b></span>
            <span>单品上限 <b>{profile?.configured ? `${profile.max_single_ratio}%` : '未设置'}</b></span>
            <span>权益上限 <b>{profile?.configured ? `${profile.max_equity_ratio}%` : '未设置'}</b></span>
            <span>最大可承受回撤 <b>{profile?.configured ? `${profile.max_drawdown_pct}%` : '未设置'}</b></span>
            <span>月度预算 <b>{profile?.configured ? formatBudget(profile.monthly_budget) : '未设置'}</b></span>
            <span>基金市场 <b>{profile?.configured ? (profile.allowed_fund_markets || []).map((value) => FUND_MARKET_OPTIONS.find((item) => item.value === value)?.label || value).join('、') : '未设置'}</b></span>
            <span>汇率风险 <b>{profile?.configured ? (profile.accept_fx_risk ? '已确认接受' : '未接受') : '未设置'}</b></span>
            <span>版本完整性 <b>{profile?.configured ? (profile.integrity_verified ? '哈希通过' : '校验失败') : '-'}</b></span>
            <span>审计链 <b>{audit?.verification?.verified ? `${audit.count} 个事件通过` : audit ? '校验失败' : '打开设置后检查'}</b></span>
          </div>
        )}
        {editing && (
          <div className="decision-profile-form">
            <label className="field">
              <span>风险偏好</span>
              <select value={form.risk} onChange={(event) => updateForm('risk', event.target.value)}>
                {RISK_OPTIONS.map((item) => <option key={item.value} value={item.value}>{item.label}</option>)}
              </select>
            </label>
            <label className="field">
              <span>投资期限</span>
              <select value={form.horizon} onChange={(event) => updateForm('horizon', event.target.value)}>
                {HORIZON_OPTIONS.map((item) => <option key={item.value} value={item.value}>{item.label}</option>)}
              </select>
            </label>
            <label className="field">
              <span>真实投资经验</span>
              <select value={form.experience_level} onChange={(event) => updateForm('experience_level', event.target.value)}>
                {EXPERIENCE_OPTIONS.map((item) => <option key={item.value} value={item.value}>{item.label}</option>)}
              </select>
            </label>
            <label className="field">
              <span>主要投资目标</span>
              <select value={form.primary_objective} onChange={(event) => updateForm('primary_objective', event.target.value)}>
                {OBJECTIVE_OPTIONS.map((item) => <option key={item.value} value={item.value}>{item.label}</option>)}
              </select>
            </label>
            <label className="field">
              <span>单品最大占比</span>
              <input type="number" min="5" max="60" step="1" value={form.max_single_ratio} onChange={(event) => updateForm('max_single_ratio', event.target.value)} />
            </label>
            <label className="field">
              <span>权益资产总上限</span>
              <input type="number" min="0" max="100" step="1" value={form.max_equity_ratio} onChange={(event) => updateForm('max_equity_ratio', event.target.value)} />
            </label>
            <label className="field">
              <span>单行业最大占比</span>
              <input type="number" min="5" max="50" step="1" value={form.max_industry_ratio} onChange={(event) => updateForm('max_industry_ratio', event.target.value)} />
            </label>
            <label className="field">
              <span>最大可承受回撤</span>
              <input type="number" min="5" max="50" step="1" value={form.max_drawdown_pct} onChange={(event) => updateForm('max_drawdown_pct', event.target.value)} />
            </label>
            <label className="field">
              <span>每月新增投入预算</span>
              <input type="number" min="0" max="10000000" step="100" value={form.monthly_budget} onChange={(event) => updateForm('monthly_budget', event.target.value)} />
            </label>
            <label className="field">
              <span>流动性储备（月）</span>
              <input type="number" min="0" max="36" step="1" value={form.liquidity_reserve_months} onChange={(event) => updateForm('liquidity_reserve_months', event.target.value)} />
            </label>
            <label className="field">
              <span>政策复核周期</span>
              <select value={form.review_cycle_months} onChange={(event) => updateForm('review_cycle_months', event.target.value)}>
                <option value="6">每 6 个月</option>
                <option value="12">每 12 个月</option>
              </select>
            </label>
            <fieldset className="decision-market-field">
              <legend>允许投资的基金市场</legend>
              <div className="decision-market-options">
                {FUND_MARKET_OPTIONS.map((item) => (
                  <label key={item.value}>
                    <input
                      type="checkbox"
                      checked={form.allowed_fund_markets.includes(item.value)}
                      onChange={() => toggleFundMarket(item.value)}
                    />
                    <span>{item.label}</span>
                  </label>
                ))}
              </div>
            </fieldset>
            <label className="decision-fx-toggle">
              <input
                type="checkbox"
                checked={form.accept_fx_risk}
                onChange={(event) => updateForm('accept_fx_risk', event.target.checked)}
              />
              <span>我接受跨境基金的汇率波动、海外休市和净值确认滞后风险</span>
            </label>
            <label className="decision-fx-toggle">
              <input
                type="checkbox"
                checked={form.emergency_fund_confirmed}
                onChange={(event) => updateForm('emergency_fund_confirmed', event.target.checked)}
              />
              <span>我确认上述投资预算不占用日常生活、负债偿还和应急资金</span>
            </label>
            <div className="decision-profile-actions">
              <button onClick={createDraft} disabled={saving || form.allowed_fund_markets.length === 0}>{saving ? '校验中' : '校验并生成草稿'}</button>
              <button className="ghost" onClick={toggleEditing} disabled={saving}>取消</button>
            </div>
            {validation && (
              <div className={`decision-policy-validation ${validation.valid ? 'valid' : 'invalid'}`}>
                <div className="decision-policy-validation-head">
                  <ShieldCheck size={17} aria-hidden="true" />
                  <div>
                    <b>{validation.valid ? '适当性校验通过，等待你确认激活' : '草稿未通过，不会进入 Agent 决策'}</b>
                    <small>{draft ? `V${draft.version_no} · ${draft.payload_sha256}` : '-'}</small>
                  </div>
                </div>
                {validation.errors?.length > 0 && (
                  <div className="decision-policy-findings errors">
                    {validation.errors.map((item) => <p key={`${item.field}-${item.code}`}><b>{item.field}</b>{item.message}</p>)}
                  </div>
                )}
                {validation.warnings?.length > 0 && (
                  <div className="decision-policy-findings warnings">
                    {validation.warnings.map((item) => <p key={`${item.field}-${item.code}`}><b>{item.field}</b>{item.message}</p>)}
                  </div>
                )}
                {validation.valid && (
                  <>
                    <label className="decision-policy-consent">
                      <input type="checkbox" checked={acknowledged} onChange={(event) => setAcknowledged(event.target.checked)} />
                      <span>{validation.consent.text}</span>
                    </label>
                    <div className="decision-profile-actions">
                      <button onClick={activateDraft} disabled={saving || !acknowledged}>{saving ? '激活中' : '确认并激活此版本'}</button>
                    </div>
                  </>
                )}
              </div>
            )}
            {versions.length > 0 && (
              <div className="decision-policy-history">
                <div className="decision-policy-history-head">
                  <b>版本历史</b>
                  <span>{audit?.verification?.verified ? `审计链通过 · ${audit.count} 个事件` : '审计链待检查'}</span>
                </div>
                {versions.map((item) => (
                  <div key={item.id}>
                    <span>V{item.version_no}</span>
                    <b>{item.status}</b>
                    <small>{formatTime(item.activated_at || item.created_at)}</small>
                    <code>{item.payload_sha256?.slice(0, 12)}</code>
                  </div>
                ))}
              </div>
            )}
            {saveError && <div className="error">{saveError}</div>}
          </div>
        )}
      </div>

      {error && <div className="error">{error}</div>}
      {!error && loading && !data && <div className="decision-loading"><span className="spinner" />正在汇总真实持仓与市场数据</div>}

      {!loading && actions.length > 0 && (
        <div className="decision-actions">
          {actions.map((action) => {
            const priority = PRIORITY_META[action.priority] || PRIORITY_META.normal
            const PriorityIcon = priority.icon
            const CategoryIcon = actionIcon(action.category || '')
            return (
              <article className={`decision-action ${action.priority}`} key={action.id}>
                <div className="decision-action-icon"><CategoryIcon size={18} strokeWidth={2} aria-hidden="true" /></div>
                <div className="decision-action-content">
                  <div className="decision-action-meta"><span className={`decision-priority ${action.priority}`}><PriorityIcon size={13} aria-hidden="true" />{priority.label}</span><span>{action.category}</span></div>
                  <h4>{action.title}</h4>
                  <p>{action.detail}</p>
                  {action.evidence?.length > 0 && <div className="decision-evidence">{action.evidence.map((item, index) => <span key={`${action.id}-${index}`}>{item}</span>)}</div>}
                  <small>来源：{action.source}</small>
                </div>
                <button className="ghost decision-open" onClick={() => openAction(action)} title={action.action_label} aria-label={action.action_label}>
                  <ArrowUpRight size={17} aria-hidden="true" />
                </button>
              </article>
            )
          })}
        </div>
      )}

      {unavailable.length > 0 && (
        <div className="decision-unavailable">
          <strong>未参与结论的数据源</strong>
          <div>{unavailable.slice(0, 4).map((item, index) => <span key={`${item.scope}-${index}`}>{item.scope}: {item.error || '暂不可用'}</span>)}</div>
        </div>
      )}

      <div className="decision-footnote">更新于 {formatTime(data?.generated_at)}。市场线索仅进入研究队列，需结合持仓、估值和风险承受能力自行决策。</div>
    </section>
  )
}
