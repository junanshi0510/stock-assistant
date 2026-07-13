import { CircleAlert, Database, Globe2 } from 'lucide-react'

export default function FundMarketProfileView({ profile, onOpenEvidence }) {
  if (!profile) return null
  const fund = profile.fund || {}
  const market = profile.market || {}
  const valuation = profile.valuation || {}

  return (
    <section className="agent-market-profile" aria-label="基金跨市场画像">
      <div className="agent-section-head">
        <div>
          <span className="eyebrow">Cross-market Evidence</span>
          <h3>基金投资市场画像</h3>
          <small>{profile.strategy_id}@{profile.strategy_version}</small>
        </div>
        {profile.evidence_id && (
          <button className="ghost" onClick={() => onOpenEvidence(profile.evidence_id)}>
            <Database size={14} aria-hidden="true" />查看市场 Evidence
          </button>
        )}
      </div>
      <div className="agent-market-summary">
        <Globe2 size={19} aria-hidden="true" />
        <div><span>主要投资市场</span><b>{market.label || '待确认'}</b></div>
        <div><span>基金类型</span><b>{fund.fund_type || '-'}</b></div>
        <div><span>跨境属性</span><b>{fund.is_qdii ? 'QDII' : market.cross_border ? '跨境' : '境内'}</b></div>
        <div><span>净值确认</span><b>{valuation.confirmed_nav_lag || '-'}</b></div>
      </div>
      {(profile.benchmark_names || []).length > 0 && (
        <div className="agent-market-benchmarks">
          <span>详情页累计收益比较序列</span>
          <b>{profile.benchmark_names.slice(0, 5).join('、')}</b>
        </div>
      )}
      <p className="agent-market-policy"><CircleAlert size={13} aria-hidden="true" />{valuation.intraday_estimate_policy || profile.policy}</p>
      <p className="agent-market-policy"><CircleAlert size={13} aria-hidden="true" />页面比较序列不等于基金合同业绩比较基准，不能据此判断跟踪误差。</p>
    </section>
  )
}
