import { CircleAlert, CircleCheck, Database, ShieldAlert, WalletCards } from 'lucide-react'

function money(value) {
  if (value == null || Number.isNaN(Number(value))) return '-'
  return `¥${Number(value).toLocaleString('zh-CN', { maximumFractionDigits: 2 })}`
}

function pct(value) {
  if (value == null || Number.isNaN(Number(value))) return '-'
  return `${Number(value).toFixed(2)}%`
}

const ACTION_TONE = {
  consider_tranche: 'positive',
  hold_review: 'mixed',
  research_only: 'mixed',
  wait: 'negative',
  do_not_add: 'negative',
  reduce_exposure: 'negative',
  hold_no_add: 'negative',
  setup_required: 'unavailable',
  budget_required: 'unavailable',
  market_data_required: 'unavailable',
  exposure_data_required: 'unavailable',
}

const MARKET_LABELS = {
  mainland: '内地市场',
  hong_kong: '港股市场',
  united_states: '美国市场',
  global: '全球及其他海外',
}

export default function PersonalizedDecisionView({ decision, onOpenEvidence }) {
  if (!decision) return null
  const action = decision.decision || {}
  const portfolio = decision.portfolio || {}
  const budget = decision.budget || {}
  const history = decision.historical_context || {}
  const market = decision.market_context || {}
  const exposure = decision.portfolio_exposure || {}
  const tone = ACTION_TONE[action.action] || 'mixed'

  return (
    <section className="agent-personal-decision" aria-label="个人基金决策">
      <div className="agent-section-head">
        <div>
          <span className="eyebrow">Personal Decision Policy</span>
          <h3>持仓感知的投资决策</h3>
          <small>{decision.strategy_id}@{decision.strategy_version}</small>
        </div>
        {decision.evidence_id && (
          <button className="ghost" onClick={() => onOpenEvidence(decision.evidence_id)}>
            <Database size={14} aria-hidden="true" />查看决策 Evidence
          </button>
        )}
      </div>

      <div className={`agent-personal-action ${tone}`}>
        <div><span>本轮动作</span><b>{action.label || '-'}</b></div>
        <p>{action.rationale}</p>
      </div>

      <div className="agent-personal-metrics">
        <div><span>当前仓位</span><b>{pct(portfolio.current_ratio)}</b><small>{portfolio.target_exists ? money(portfolio.target_amount) : '-'}</small></div>
        <div><span>你的单品上限</span><b>{pct(portfolio.max_single_ratio)}</b><small>按确认组合金额计算</small></div>
        <div><span>上限内可用总额</span><b>{money(budget.allowed_full_amount)}</b><small>不等于必须投入</small></div>
        <div>
          <span>{action.action === 'reduce_exposure' ? '建议复核减仓额' : '首批观察金额'}</span>
          <b>{money(action.action === 'reduce_exposure' ? budget.suggested_reduction_amount : budget.first_tranche_amount)}</b>
          <small>{budget.tranche_count ? `计划拆为 ${budget.tranche_count} 批` : '触发门禁时不生成金额'}</small>
        </div>
      </div>

      <div className="agent-personal-evidence-row">
        <WalletCards size={15} aria-hidden="true" />
        <span>
          历史主窗口 {history.primary_horizon || '-'} · 正收益比例 {pct(history.positive_rate)} · 中位收益 {pct(history.median_return)} · 最差 {pct(history.worst_return)}
        </span>
      </div>

      <div className="agent-personal-market">
        <div>
          <span>组合穿透快照</span>
          <b>{exposure.integrity_verified ? '完整性已验证' : '不可用于决策'}</b>
          <small>{exposure.snapshot_id || '未生成不可变快照'}</small>
        </div>
        <div>
          <span>权益暴露 / IPS 上限</span>
          <b>{pct(exposure.equity?.current_lower_ratio)} - {pct(exposure.equity?.current_upper_ratio)} / {pct(exposure.equity?.limit_ratio)}</b>
          <small>未披露权益进入最坏上界</small>
        </div>
        <div>
          <span>行业集中 / IPS 上限</span>
          <b>{pct(exposure.industry?.current_max_lower_ratio)} - {pct(exposure.industry?.current_max_upper_ratio)} / {pct(exposure.industry?.limit_ratio)}</b>
          <small>未知行业可能集中于任一行业</small>
        </div>
      </div>

      <div className="agent-personal-market">
        <div>
          <span>基金投资市场</span>
          <b>{market.label || '待确认'}{market.is_qdii ? ' · QDII' : ''}</b>
          <small>{market.confirmed_nav_lag || '以确认净值日期为准'}</small>
        </div>
        <div>
          <span>你的市场权限</span>
          <b>{(market.allowed_markets || []).map((item) => MARKET_LABELS[item] || item).join('、') || '-'}</b>
          <small>{market.currency_risk ? (market.accept_fx_risk ? '已确认汇率风险' : '尚未确认汇率风险') : '无需额外汇率确认'}</small>
        </div>
        <div>
          <span>详情页比较序列</span>
          <b>{(market.benchmark_names || []).slice(0, 3).join('、') || '-'}</b>
          <small>{market.estimate_policy || '盘中估值不替代确认净值'}</small>
        </div>
      </div>

      <div className="agent-personal-gates">
        {(decision.gates || []).map((gate) => {
          const Icon = gate.status === 'pass' ? CircleCheck : gate.status === 'block' ? ShieldAlert : CircleAlert
          return (
            <div className={gate.status} key={gate.code}>
              <Icon size={15} aria-hidden="true" />
              <span><b>{gate.label}</b><small>{gate.detail}</small></span>
            </div>
          )
        })}
      </div>

      {(decision.missing_requirements || []).length > 0 && (
        <div className="agent-personal-missing">
          缺少：{decision.missing_requirements.map((item) => ({
            investment_profile: '投资约束',
            confirmed_holding_amounts: '完整持仓金额',
            planned_or_monthly_budget: '计划投入或月度预算',
            fund_market_identification: '基金投资市场证据',
            portfolio_exposure_snapshot: '完整且绑定一致的组合穿透快照',
          }[item] || item)).join('、')}
        </div>
      )}
      <p className="agent-personal-policy">{decision.policy}</p>
    </section>
  )
}
