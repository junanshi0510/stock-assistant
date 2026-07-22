import { useEffect, useMemo, useState } from 'react'
import { Check, Database, Layers3, ShieldCheck, SlidersHorizontal, X } from 'lucide-react'
import { createOpportunityStrategy, createOpportunityStrategyVersion } from '../../api/opportunities'

const MARKET_OPTIONS = ['A股', '港股', '美股']
const FACTOR_FIELDS = [
  ['momentum', '趋势动量'], ['value', '估值'], ['quality', '盈利质量'], ['growth', '成长'], ['risk', '风险韧性'],
]
const STATUS_SOURCES = [
  ['active', '成交活跃榜'], ['gainers', '涨幅榜'], ['losers', '跌幅榜'],
]

function clone(value) {
  return JSON.parse(JSON.stringify(value))
}

function parseSymbols(text, markets) {
  if (!text.trim()) return []
  return text.split(/\r?\n/).map((line, index) => {
    const parts = line.split(/[,，\t]/).map((item) => item.trim())
    if (parts.length < 2) throw new Error(`手工股票第 ${index + 1} 行需要“市场,代码,名称”`)
    if (!markets.includes(parts[0])) throw new Error(`手工股票第 ${index + 1} 行的市场未勾选`)
    return { market: parts[0], symbol: parts[1], name: parts.slice(2).join(' ') }
  })
}

function symbolsText(items = []) {
  return items.map((item) => [item.market, item.symbol, item.name].filter(Boolean).join(',')).join('\n')
}

function NumberField({ label, value, onChange, min, max, step = 1, suffix, hint }) {
  return (
    <label className="opp-number-field">
      <span>{label}{hint && <small>{hint}</small>}</span>
      <span className="opp-number-input">
        <input type="number" min={min} max={max} step={step} value={value} onChange={(event) => onChange(Number(event.target.value))} />
        {suffix && <em>{suffix}</em>}
      </span>
    </label>
  )
}
export default function StrategyBuilder({ templates, strategy, onSaved, onCancel }) {
  const initial = strategy?.definition || templates?.[0]
  const [definition, setDefinition] = useState(() => clone(initial || {}))
  const [manualText, setManualText] = useState(() => symbolsText(initial?.universe?.symbols))
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState('')

  useEffect(() => {
    const next = strategy?.definition || templates?.[0]
    if (!next) return
    setDefinition(clone(next))
    setManualText(symbolsText(next.universe?.symbols))
    setError('')
  }, [strategy, templates])

  const weightTotal = useMemo(
    () => FACTOR_FIELDS.reduce((sum, [key]) => sum + Number(definition.factors?.[key] || 0), 0),
    [definition.factors],
  )

  function chooseTemplate(template) {
    const next = clone(template)
    if (strategy) next.name = strategy.definition.name
    setDefinition(next)
    setManualText(symbolsText(next.universe?.symbols))
    setError('')
  }

  function patchGroup(group, key, value) {
    setDefinition((current) => ({
      ...current,
      [group]: { ...(current[group] || {}), [key]: value },
    }))
  }

  function toggleMarket(market) {
    setDefinition((current) => {
      const selected = current.markets.includes(market)
        ? current.markets.filter((item) => item !== market)
        : [...current.markets, market]
      return { ...current, markets: selected }
    })
  }

  function toggleHot(kind) {
    const current = definition.universe.hot_lists || []
    patchGroup('universe', 'hot_lists', current.includes(kind) ? current.filter((item) => item !== kind) : [...current, kind])
  }

  async function save(event) {
    event.preventDefault()
    setError('')
    if (!definition.markets?.length) {
      setError('至少选择一个市场。')
      return
    }
    if (weightTotal <= 0) {
      setError('至少一个因子权重必须大于 0。')
      return
    }
    let symbols
    try {
      symbols = parseSymbols(manualText, definition.markets)
    } catch (parseError) {
      setError(parseError.message)
      return
    }
    const payload = {
      ...definition,
      universe: { ...definition.universe, symbols },
    }
    setSaving(true)
    try {
      const saved = strategy
        ? await createOpportunityStrategyVersion(strategy.id, payload)
        : await createOpportunityStrategy(payload)
      onSaved(saved)
    } catch (requestError) {
      setError(requestError.message)
    } finally {
      setSaving(false)
    }
  }

  if (!definition?.universe) return null

  return (
    <form className="opp-builder" onSubmit={save}>
      <div className="opp-builder-head">
        <div>
          <span className="eyebrow">{strategy ? `策略版本 ${strategy.current_version_no + 1}` : '新建机会策略'}</span>
          <h3>{strategy ? '用新版本调整策略' : '从研究模板开始'}</h3>
          <p>每次保存都会冻结完整参数；旧扫描仍绑定旧版本，不会被覆盖。</p>
        </div>
        <button type="button" className="icon-button ghost" onClick={onCancel} aria-label="关闭策略编辑"><X size={18} /></button>
      </div>

      <section className="opp-builder-section">
        <div className="opp-builder-section-title"><Layers3 size={17} /><div><b>1. 研究方法</b><span>借鉴专业筛选器的可保存模板，但每条规则都保持可见。</span></div></div>
        <div className="opp-template-grid">
          {templates.map((template) => (
            <button key={template.template_id} type="button" className={definition.template_id === template.template_id ? 'active' : ''} onClick={() => chooseTemplate(template)}>
              <b>{template.name}</b><span>{template.description}</span>
              {definition.template_id === template.template_id && <Check size={15} />}
            </button>
          ))}
        </div>
        <div className="opp-form-grid two">
          <label><span>策略名称</span><input value={definition.name || ''} maxLength={80} onChange={(event) => setDefinition((current) => ({ ...current, name: event.target.value }))} /></label>
          <NumberField label="历史观察窗口" value={definition.history_months} min={9} max={60} suffix="个月" onChange={(value) => setDefinition((current) => ({ ...current, history_months: value }))} />
          <label className="wide"><span>策略说明</span><textarea value={definition.description || ''} maxLength={300} onChange={(event) => setDefinition((current) => ({ ...current, description: event.target.value }))} /></label>
        </div>
      </section>

      <section className="opp-builder-section">
        <div className="opp-builder-section-title"><Database size={17} /><div><b>2. 候选池</b><span>来源可以合并去重；界面会始终标明这不是交易所全量。</span></div></div>
        <div className="opp-choice-line">
          {MARKET_OPTIONS.map((market) => (
            <label key={market} className={definition.markets.includes(market) ? 'selected' : ''}>
              <input type="checkbox" checked={definition.markets.includes(market)} onChange={() => toggleMarket(market)} />{market}
            </label>
          ))}
        </div>
        <div className="opp-choice-line sources">
          <label className={definition.universe.include_presets ? 'selected' : ''}><input type="checkbox" checked={definition.universe.include_presets} onChange={(event) => patchGroup('universe', 'include_presets', event.target.checked)} />内置种子池</label>
          <label className={definition.universe.include_watchlist ? 'selected' : ''}><input type="checkbox" checked={definition.universe.include_watchlist} onChange={(event) => patchGroup('universe', 'include_watchlist', event.target.checked)} />我的自选股</label>
          {STATUS_SOURCES.map(([key, label]) => <label key={key} className={definition.universe.hot_lists.includes(key) ? 'selected' : ''}><input type="checkbox" checked={definition.universe.hot_lists.includes(key)} onChange={() => toggleHot(key)} />{label}</label>)}
        </div>
        {definition.universe.hot_lists.length > 0 && <NumberField label="每个市场、每类热门池" value={definition.universe.hot_limit_per_market} min={5} max={20} suffix="只" onChange={(value) => patchGroup('universe', 'hot_limit_per_market', value)} />}
        <label className="opp-manual-symbols"><span>手工股票（可选，每行：市场,代码,名称）</span><textarea value={manualText} onChange={(event) => setManualText(event.target.value)} placeholder={'A股,600519,贵州茅台\n港股,00700,腾讯控股\n美股,AAPL,苹果'} /></label>
        <div className="opp-boundary-note"><ShieldCheck size={16} /><span>当前只能扫描这些明确候选来源。接入有授权的专业全量供应商后，才会开放“交易所全量”选项。</span></div>
      </section>

      <section className="opp-builder-section">
        <div className="opp-builder-section-title"><SlidersHorizontal size={17} /><div><b>3. 因子和淘汰门槛</b><span>因子负责排序，数据新鲜度、风险和覆盖率负责先否决。</span></div></div>
        <div className="opp-factor-grid">
          {FACTOR_FIELDS.map(([key, label]) => <NumberField key={key} label={label} value={definition.factors[key]} min={0} max={100} suffix="权重" onChange={(value) => patchGroup('factors', key, value)} />)}
          <div className={`opp-weight-total ${weightTotal > 0 ? 'ok' : ''}`}><span>总权重</span><b>{weightTotal}</b><small>系统按比例归一化，无需等于 100</small></div>
        </div>
        <details className="opp-advanced" open>
          <summary>硬性淘汰门槛</summary>
          <div className="opp-form-grid four">
            <NumberField label="最少历史" value={definition.gates.min_history_days} min={60} max={1000} suffix="交易日" onChange={(value) => patchGroup('gates', 'min_history_days', value)} />
            <NumberField label="行情最大陈旧" value={definition.gates.max_data_age_days} min={3} max={45} suffix="天" onChange={(value) => patchGroup('gates', 'max_data_age_days', value)} />
            <NumberField label="最低技术评分" value={definition.gates.min_technical_score} min={0} max={100} onChange={(value) => patchGroup('gates', 'min_technical_score', value)} />
            <NumberField label="最低三月收益" value={definition.gates.min_return_3m} min={-100} max={300} suffix="%" onChange={(value) => patchGroup('gates', 'min_return_3m', value)} />
            <NumberField label="最大年化波动" value={definition.gates.max_annual_vol} min={5} max={300} suffix="%" onChange={(value) => patchGroup('gates', 'max_annual_vol', value)} />
            <NumberField label="最大历史回撤" value={definition.gates.max_drawdown_pct} min={5} max={100} suffix="%" onChange={(value) => patchGroup('gates', 'max_drawdown_pct', value)} />
            <NumberField label="最低因子覆盖" value={Math.round(definition.gates.min_factor_coverage * 100)} min={20} max={100} suffix="%" onChange={(value) => patchGroup('gates', 'min_factor_coverage', value / 100)} />
            <NumberField label="最低综合评分" value={definition.gates.min_composite_score} min={0} max={100} onChange={(value) => patchGroup('gates', 'min_composite_score', value)} />
          </div>
          <label className="opp-toggle"><input type="checkbox" checked={definition.gates.require_fundamentals} onChange={(event) => patchGroup('gates', 'require_fundamentals', event.target.checked)} /><span><b>基本面必须可用</b><small>港股会在专业财务源接入前被明确淘汰。</small></span></label>
        </details>
      </section>

      <section className="opp-builder-section">
        <div className="opp-builder-section-title"><ShieldCheck size={17} /><div><b>4. 纸面组合约束</b><span>先控制集中度和相关性，再计算权重；未分配金额保留现金。</span></div></div>
        <div className="opp-form-grid three">
          <NumberField label="最多股票" value={definition.portfolio.max_positions} min={2} max={12} suffix="只" onChange={(value) => patchGroup('portfolio', 'max_positions', value)} />
          <NumberField label="单股上限" value={definition.portfolio.max_position_pct} min={5} max={50} suffix="%" onChange={(value) => patchGroup('portfolio', 'max_position_pct', value)} />
          <NumberField label="最低现金" value={definition.portfolio.min_cash_pct} min={0} max={60} suffix="%" onChange={(value) => patchGroup('portfolio', 'min_cash_pct', value)} />
          <NumberField label="最大两两相关" value={definition.portfolio.max_pair_correlation} min={0} max={1} step={0.05} onChange={(value) => patchGroup('portfolio', 'max_pair_correlation', value)} />
          <NumberField label="防守状态增配现金" value={definition.portfolio.defensive_cash_add_pct} min={0} max={30} suffix="%" onChange={(value) => patchGroup('portfolio', 'defensive_cash_add_pct', value)} />
          <label><span>权重方法</span><select value={definition.portfolio.weighting} onChange={(event) => patchGroup('portfolio', 'weighting', event.target.value)}><option value="score_inverse_vol">综合分 × 低波动</option><option value="inverse_vol">低波动</option><option value="equal">等权</option></select></label>
        </div>
      </section>

      {error && <div className="error">{error}</div>}
      <div className="opp-builder-actions">
        <button type="button" className="ghost" onClick={onCancel}>取消</button>
        <button type="submit" disabled={saving}>{saving ? <><span className="spinner" />保存中</> : strategy ? '保存为新版本' : '保存策略'}</button>
      </div>
    </form>
  )
}
