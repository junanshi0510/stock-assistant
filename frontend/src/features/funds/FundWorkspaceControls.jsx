const CATEGORIES = [
  ['all', '全部'],
  ['stock', '股票型'],
  ['hybrid', '混合型'],
  ['bond', '债券型'],
  ['index', '指数型'],
  ['qdii', 'QDII'],
  ['fof', 'FOF'],
]

const SORTS = [
  ['1y', '近1年'],
  ['ytd', '今年来'],
  ['6m', '近6月'],
  ['3m', '近3月'],
  ['1m', '近1月'],
]

/** Renders only the controls for the selected fund workspace. */
export default function FundWorkspaceControls({
  fundView,
  category,
  setCategory,
  sort,
  setSort,
  limit,
  setLimit,
  loadHot,
  loadingHot,
  code,
  setCode,
  months,
  setMonths,
  loadFund,
  loadingFund,
  searchKeyword,
  setSearchKeyword,
  runSearch,
  loadingSearch,
  searchResults,
  hot,
  error,
}) {
  return (
    <div className="panel">
      <div className="form-row">
        {fundView === 'discover' && <>
          <div className="field">
            <label>基金分类</label>
            <select value={category} onChange={(event) => { setCategory(event.target.value); loadHot(event.target.value, sort) }}>
              {CATEGORIES.map(([value, label]) => <option key={value} value={value}>{label}</option>)}
            </select>
          </div>
          <div className="field">
            <label>排序窗口</label>
            <select value={sort} onChange={(event) => { setSort(event.target.value); loadHot(category, event.target.value) }}>
              {SORTS.map(([value, label]) => <option key={value} value={value}>{label}</option>)}
            </select>
          </div>
          <div className="field">
            <label>榜单数量</label>
            <input type="number" min="5" max="100" value={limit} onChange={(event) => setLimit(Number(event.target.value))} />
          </div>
          <button onClick={() => loadHot()} disabled={loadingHot}>
            {loadingHot ? <><span className="spinner" /> 加载中</> : '刷新基金榜'}
          </button>
        </>}

        {fundView === 'research' && <>
          <div className="field">
            <label>基金代码</label>
            <input value={code} onChange={(event) => setCode(event.target.value)} placeholder="例如 110022" />
          </div>
          <div className="field">
            <label>净值周期(月)</label>
            <input type="number" min="6" max="120" value={months} onChange={(event) => setMonths(Number(event.target.value))} />
          </div>
          <button onClick={() => loadFund()} disabled={loadingFund}>
            {loadingFund ? <><span className="spinner" /> 分析中</> : '研究基金'}
          </button>
          <div className="field">
            <label>基金搜索</label>
            <input
              value={searchKeyword}
              onChange={(event) => setSearchKeyword(event.target.value)}
              onKeyDown={(event) => { if (event.key === 'Enter') runSearch() }}
              placeholder="代码 / 名称 / 拼音"
            />
          </div>
          <button className="ghost" onClick={runSearch} disabled={loadingSearch}>
            {loadingSearch ? <><span className="spinner" /> 搜索中</> : '搜索基金'}
          </button>
        </>}

        {fundView === 'compare' && <span className="hint">输入两只或以上基金后，比较真实净值、波动、回撤和披露持仓重合。</span>}
      </div>

      {fundView === 'research' && searchResults.length > 0 && (
        <div className="fund-search-results">
          {searchResults.map((item) => (
            <button key={item.code} className="fund-search-item" onClick={() => {
              setCode(item.code)
              loadFund(item.code, months)
            }}>
              <b>{item.code}</b>
              <span>{item.name}</span>
              <small>{item.type}</small>
            </button>
          ))}
        </div>
      )}
      {fundView === 'discover' && hot && <p className="hint" style={{ marginTop: 12 }}>数据源: {hot.source}，截至 {hot.as_of}；高收益只代表历史表现，仍需继续研究回撤和持仓。</p>}
      {error && <div className="error">{error}</div>}
    </div>
  )
}
