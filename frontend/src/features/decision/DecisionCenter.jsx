import { useState } from 'react'
import {
  AlertTriangle,
  ArrowUpRight,
  BadgeCheck,
  ChartNoAxesCombined,
  ChevronDown,
  ChevronUp,
  CircleAlert,
  DatabaseZap,
  Layers3,
  ReceiptText,
  RefreshCw,
  Settings2,
  ShieldCheck,
  WalletCards,
} from 'lucide-react'

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
  if (Number.isNaN(parsed.getTime())) return String(value).replace('T', ' ')
  return parsed.toLocaleString('zh-CN', { hour12: false, month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit' })
}

export default function DecisionCenter({ data, loading, error, onRefresh, onNavigate }) {
  const [showAll, setShowAll] = useState(false)
  const profile = data?.profile
  const summary = data?.summary || {}
  const actions = data?.actions || []
  const unavailable = data?.unavailable || []
  const visibleActions = showAll ? actions : actions.slice(0, 5)

  return (
    <section className="decision-center" aria-label="今日行动清单">
      <div className="decision-center-head">
        <div>
          <span className="eyebrow">今日行动清单</span>
          <h3>风险控制优先，研究机会随后</h3>
          <p>{data?.policy || '正在根据真实持仓与真实市场数据生成行动顺序。'}</p>
        </div>
        <button className="ghost decision-refresh" onClick={onRefresh} disabled={loading} title="刷新真实数据">
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

      <div className={`decision-policy-gate ${profile?.configured ? 'active' : 'inactive'}`}>
        <ShieldCheck size={19} aria-hidden="true" />
        <div>
          <span>投资政策门禁</span>
          <b>{profile?.configured ? `V${profile.version_no} 已激活` : '尚未激活，仓位动作保持受限'}</b>
          <small>{profile?.configured
            ? `单品 ${profile.max_single_ratio}% · 权益 ${profile.max_equity_ratio}% · 最大回撤 ${profile.max_drawdown_pct}%`
            : '完整设置已移动到“我的资产 → 投资政策”，首页不再堆叠表单。'}</small>
        </div>
        <button className="ghost" onClick={() => onNavigate('profile')}><Settings2 size={15} aria-hidden="true" />{profile?.configured ? '查看政策' : '建立政策'}</button>
      </div>

      {error && <div className="error">{error}</div>}
      {!error && loading && !data && <div className="decision-loading"><span className="spinner" />正在汇总真实持仓与市场数据</div>}

      {!loading && visibleActions.length > 0 && (
        <div className="decision-actions">
          {visibleActions.map((action) => {
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
                <button className="ghost decision-open" onClick={() => onNavigate(action.target)} title={action.action_label} aria-label={action.action_label}>
                  <ArrowUpRight size={17} aria-hidden="true" />
                </button>
              </article>
            )
          })}
        </div>
      )}

      {actions.length > 5 && (
        <button type="button" className="ghost decision-expand" onClick={() => setShowAll((value) => !value)}>
          {showAll ? <ChevronUp size={15} aria-hidden="true" /> : <ChevronDown size={15} aria-hidden="true" />}
          {showAll ? '收起次要事项' : `查看其余 ${actions.length - 5} 项`}
        </button>
      )}

      {unavailable.length > 0 && (
        <details className="decision-unavailable">
          <summary>未参与结论的数据源 · {unavailable.length}</summary>
          <div>{unavailable.slice(0, 6).map((item, index) => <span key={`${item.scope}-${index}`}>{item.scope}: {item.error || '暂不可用'}</span>)}</div>
        </details>
      )}

      <div className="decision-footnote">更新于 {formatTime(data?.generated_at)}。行动顺序随持仓、政策、纪律或真实市场证据变化而失效。</div>
    </section>
  )
}
