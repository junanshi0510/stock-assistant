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
  Settings2,
  WalletCards,
} from 'lucide-react'
import { saveInvestmentProfile } from '../../api/portfolio'

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
  const [form, setForm] = useState({
    risk: 'balanced',
    horizon: 'mid_long',
    monthly_budget: '',
    max_single_ratio: '35',
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
      monthly_budget: profile.monthly_budget ?? '',
      max_single_ratio: String(profile.max_single_ratio ?? 35),
    })
  }, [profile])

  function updateForm(field, value) {
    setForm((current) => ({ ...current, [field]: value }))
  }

  async function saveProfile() {
    setSaving(true)
    setSaveError('')
    try {
      await saveInvestmentProfile({
        risk: form.risk,
        horizon: form.horizon,
        monthly_budget: form.monthly_budget === '' ? null : Number(form.monthly_budget),
        max_single_ratio: Number(form.max_single_ratio),
      })
      setEditing(false)
      onRefresh()
    } catch (requestError) {
      setSaveError(requestError.message || '保存投资约束失败')
    } finally {
      setSaving(false)
    }
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
            <span className="decision-profile-label">你的投资约束</span>
            <strong>{profile?.configured ? '已保存，用于判断组合风险边界' : '尚未设置，系统不会把默认数值当成你的策略'}</strong>
          </div>
          <button className="ghost decision-icon-button" onClick={() => setEditing((value) => !value)} title={editing ? '收起策略设置' : '设置投资约束'} aria-label={editing ? '收起策略设置' : '设置投资约束'}>
            <Settings2 size={16} aria-hidden="true" />
          </button>
        </div>
        {!editing && (
          <div className="decision-profile-values">
            <span>风险偏好 <b>{RISK_OPTIONS.find((item) => item.value === profile?.risk)?.label || '-'}</b></span>
            <span>投资期限 <b>{HORIZON_OPTIONS.find((item) => item.value === profile?.horizon)?.label || '-'}</b></span>
            <span>单品上限 <b>{profile?.configured ? `${profile.max_single_ratio}%` : '未设置'}</b></span>
            <span>月度预算 <b>{profile?.configured ? formatBudget(profile.monthly_budget) : '未设置'}</b></span>
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
              <span>单品最大占比</span>
              <input type="number" min="10" max="80" step="1" value={form.max_single_ratio} onChange={(event) => updateForm('max_single_ratio', event.target.value)} />
            </label>
            <label className="field">
              <span>每月新增投入预算（可选）</span>
              <input type="number" min="0" step="100" placeholder="例如 2000" value={form.monthly_budget} onChange={(event) => updateForm('monthly_budget', event.target.value)} />
            </label>
            <div className="decision-profile-actions">
              <button onClick={saveProfile} disabled={saving}>{saving ? '保存中' : '保存约束'}</button>
              <button className="ghost" onClick={() => setEditing(false)} disabled={saving}>取消</button>
            </div>
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
