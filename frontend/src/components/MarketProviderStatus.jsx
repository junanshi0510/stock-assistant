import { AlertTriangle, CheckCircle2, Database, ShieldCheck } from 'lucide-react'

const STATE_LABELS = {
  ready: '运行正常',
  configured_unverified: '已配置·待验证',
  configuration_required: '需要配置',
  configuration_invalid: '配置有误',
  circuit_open: '暂时熔断',
}

const FRESHNESS_LABELS = {
  latest_completed_eod: '最近完整交易日',
  end_of_day: '日终榜',
  delayed: '延时行情',
  realtime: '实时行情',
}

export default function MarketProviderStatus({ data, market = null, compact = false }) {
  if (!data?.markets?.length) return null
  const rows = market ? data.markets.filter((item) => item.market === market) : data.markets
  if (!rows.length) return null

  return (
    <section className={`provider-status ${compact ? 'compact' : ''}`} aria-label="专业行情源状态">
      <div className="provider-status-head">
        <span><Database size={15} /><b>专业行情源</b></span>
        <em><ShieldCheck size={13} />密钥仅在服务端配置 · 不使用新浪</em>
      </div>
      <div className="provider-status-grid">
        {rows.map((item) => {
          const healthy = item.state === 'ready' || item.state === 'configured_unverified'
          const Icon = healthy ? CheckCircle2 : AlertTriangle
          return (
            <article key={item.market} className={`provider-status-card state-${item.state}`}>
              <div><strong>{item.market}</strong><span><Icon size={13} />{STATE_LABELS[item.state] || item.state}</span></div>
              <b>{item.provider_label}</b>
              <small>{FRESHNESS_LABELS[item.expected_freshness] || item.expected_freshness}</small>
              {!item.configured && <code>服务端配置 {item.required_env}</code>}
              {item.runtime?.last_error && <p title={item.runtime.last_error}>{item.runtime.last_error}</p>}
            </article>
          )
        })}
      </div>
      {rows.some((item) => !item.configured) && (
        <p className="provider-status-note">未配置专业密钥时只会尝试公开降级源；公开接口被云厂商 IP 限制时，榜单会明确失败，不会伪造数据。</p>
      )}
    </section>
  )
}
