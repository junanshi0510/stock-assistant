import { useState } from 'react'
import { AlertTriangle, CheckCircle2, Database, RefreshCw, ShieldCheck } from 'lucide-react'
import { probeMarketProvider } from '../api/market'

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

function probeSummary(result) {
  if (!result) return null
  if (!result.available) return `验证失败：${result.message || '专业源不可用'}`
  const quality = result.data_quality || {}
  const count = result.counts ? Object.values(result.counts).reduce((sum, value) => sum + Number(value || 0), 0) : 0
  return `验证通过：${result.provider_label || result.provider} · ${result.as_of || '最新'} · ${count} 条榜单结果 · ${result.latency_ms}ms${quality.status ? ` · 质量 ${quality.status}` : ''}`
}

export default function MarketProviderStatus({ data, market = null, compact = false }) {
  const [probing, setProbing] = useState('')
  const [probes, setProbes] = useState({})
  if (!data?.markets?.length) return null
  const rows = market ? data.markets.filter((item) => item.market === market) : data.markets
  if (!rows.length) return null

  const runProbe = async (marketName) => {
    setProbing(marketName)
    try {
      const result = await probeMarketProvider(marketName)
      setProbes((current) => ({ ...current, [marketName]: result }))
    } catch (error) {
      setProbes((current) => ({
        ...current,
        [marketName]: { available: false, message: error.message || '验证请求失败' },
      }))
    } finally {
      setProbing('')
    }
  }

  return (
    <section className={`provider-status ${compact ? 'compact' : ''}`} aria-label="专业行情源状态">
      <div className="provider-status-head">
        <span><Database size={15} /><b>专业行情数据中台</b></span>
        <em><ShieldCheck size={13} />密钥仅在服务端 · 多源接力 · 不使用新浪</em>
      </div>
      <div className="provider-status-grid">
        {rows.map((item) => {
          const healthy = item.state === 'ready' || item.state === 'configured_unverified'
          const Icon = healthy ? CheckCircle2 : AlertTriangle
          const providers = item.providers?.length ? item.providers : [item]
          const probe = probes[item.market]
          return (
            <article key={item.market} className={`provider-status-card state-${item.state}`}>
              <div className="provider-status-title">
                <strong>{item.market}</strong>
                <span><Icon size={13} />{STATE_LABELS[item.state] || item.state}</span>
              </div>
              <b>{item.provider_label}</b>
              <small>
                {FRESHNESS_LABELS[item.expected_freshness] || item.expected_freshness}
                {' · '}{item.available_provider_count || 0}/{item.provider_count || providers.length} 条专业路线已配置
              </small>
              <div className="provider-route-list">
                {providers.map((provider) => (
                  <span
                    key={provider.provider}
                    className={`provider-route state-${provider.state}`}
                    title={provider.configuration_message || provider.runtime?.last_error || provider.required_env}
                  >
                    {provider.provider_label}
                    <i>{STATE_LABELS[provider.state] || provider.state}</i>
                  </span>
                ))}
              </div>
              {!item.configured && <code>任选一条路线配置服务端凭据或 OpenD</code>}
              {item.runtime?.last_error && <p title={item.runtime.last_error}>{item.runtime.last_error}</p>}
              <button
                type="button"
                className="provider-probe-button"
                disabled={!item.configured || probing === item.market}
                onClick={() => runProbe(item.market)}
              >
                <RefreshCw size={12} className={probing === item.market ? 'spin' : ''} />
                {probing === item.market ? '正在真实验证' : '真实连通性验证'}
              </button>
              {probe && (
                <p className={`provider-probe-result ${probe.available ? 'ok' : 'failed'}`} title={probeSummary(probe)}>
                  {probeSummary(probe)}
                </p>
              )}
            </article>
          )
        })}
      </div>
      {rows.some((item) => !item.configured) && (
        <p className="provider-status-note">免费/低成本路线可组合使用：富途 OpenD、Tushare、Massive 和 Alpha Vantage。没有专业源时，公开网页接口只作为明确标记的临时降级，不会伪造榜单。</p>
      )}
    </section>
  )
}
