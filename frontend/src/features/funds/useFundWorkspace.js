import { useEffect, useState } from 'react'
import {
  analyzeFund,
  analyzeFundOverlap,
  compareFunds,
  fetchFundAlternatives,
  fetchFundCategories,
  fetchFundDividends,
  fetchFundOpportunities,
  fetchFundPeers,
  fetchFundPortfolio,
  fetchHotFunds,
  searchFunds,
} from '../../api/funds'

function parseCodes(text) {
  return String(text || '').split(/[\s,，、;；]+/).map((item) => item.trim()).filter(Boolean)
}

/**
 * Owns fund workspace state and real-data requests. The tab component only
 * renders the selected view, which keeps data fetching separate from UI.
 */
export function useFundWorkspace() {
  const [fundView, setFundView] = useState('discover')
  const [researchLayer, setResearchLayer] = useState('decision')
  const [category, setCategory] = useState('all')
  const [sort, setSort] = useState('1y')
  const [limit, setLimit] = useState(30)
  const [months, setMonths] = useState(36)
  const [code, setCode] = useState('')
  const [hot, setHot] = useState(null)
  const [categories, setCategories] = useState([])
  const [categoryError, setCategoryError] = useState('')
  const [fund, setFund] = useState(null)
  const [portfolio, setPortfolio] = useState(null)
  const [portfolioError, setPortfolioError] = useState('')
  const [peers, setPeers] = useState(null)
  const [peerSort, setPeerSort] = useState('1y')
  const [dividends, setDividends] = useState(null)
  const [searchKeyword, setSearchKeyword] = useState('')
  const [searchResults, setSearchResults] = useState([])
  const [compareInput, setCompareInput] = useState('110022 001480 006502')
  const [compareData, setCompareData] = useState(null)
  const [overlapData, setOverlapData] = useState(null)
  const [opportunityRisk, setOpportunityRisk] = useState('balanced')
  const [opportunities, setOpportunities] = useState(null)
  const [alternatives, setAlternatives] = useState(null)
  const [loadingHot, setLoadingHot] = useState(false)
  const [loadingFund, setLoadingFund] = useState(false)
  const [loadingPortfolio, setLoadingPortfolio] = useState(false)
  const [loadingPeers, setLoadingPeers] = useState(false)
  const [loadingDividends, setLoadingDividends] = useState(false)
  const [loadingSearch, setLoadingSearch] = useState(false)
  const [loadingCompare, setLoadingCompare] = useState(false)
  const [loadingOverlap, setLoadingOverlap] = useState(false)
  const [loadingOpportunities, setLoadingOpportunities] = useState(false)
  const [loadingAlternatives, setLoadingAlternatives] = useState(false)
  const [error, setError] = useState('')

  async function loadHot(nextCategory = category, nextSort = sort) {
    setLoadingHot(true)
    setError('')
    try {
      const data = await fetchHotFunds(nextCategory, limit, nextSort)
      setHot(data)
      const first = data.items?.[0]
      if (first && !code) setCode(first.code)
    } catch (requestError) {
      setError(requestError.message)
    } finally {
      setLoadingHot(false)
    }
  }

  async function loadFund(nextCode = code, nextMonths = months) {
    const clean = String(nextCode || '').trim()
    if (!/^\d{6}$/.test(clean)) {
      setError('请输入 6 位基金代码')
      return
    }
    setFundView('research')
    setResearchLayer('decision')
    setLoadingFund(true)
    setError('')
    setPortfolio(null)
    setPortfolioError('')
    setPeers(null)
    setDividends(null)
    setAlternatives(null)
    try {
      const data = await analyzeFund(clean, nextMonths)
      setFund(data)
      setCode(clean)
    } catch (requestError) {
      setError(requestError.message)
    } finally {
      setLoadingFund(false)
    }
  }

  async function loadPortfolio(nextCode = code) {
    const clean = String(nextCode || '').trim()
    if (!/^\d{6}$/.test(clean)) return
    setLoadingPortfolio(true)
    setPortfolioError('')
    try {
      setPortfolio(await fetchFundPortfolio(clean))
    } catch (requestError) {
      setPortfolioError(requestError.message)
    } finally {
      setLoadingPortfolio(false)
    }
  }

  async function loadPeers(nextCode = code, nextSort = peerSort) {
    const clean = String(nextCode || '').trim()
    if (!/^\d{6}$/.test(clean)) return
    setLoadingPeers(true)
    try {
      setPeers(await fetchFundPeers(clean, nextSort, 1000))
    } catch (requestError) {
      setPeers({ error: requestError.message })
    } finally {
      setLoadingPeers(false)
    }
  }

  async function loadDividends(nextCode = code) {
    const clean = String(nextCode || '').trim()
    if (!/^\d{6}$/.test(clean)) return
    setLoadingDividends(true)
    try {
      setDividends(await fetchFundDividends(clean))
    } catch (requestError) {
      setDividends({ error: requestError.message })
    } finally {
      setLoadingDividends(false)
    }
  }

  async function runSearch() {
    const keyword = searchKeyword.trim()
    if (!keyword) return
    setLoadingSearch(true)
    setError('')
    try {
      const data = await searchFunds(keyword, 12)
      setSearchResults(data.items || [])
    } catch (requestError) {
      setError(requestError.message)
    } finally {
      setLoadingSearch(false)
    }
  }

  async function runCompare() {
    const codes = parseCodes(compareInput)
    if (codes.length < 2) {
      setError('至少输入 2 只基金代码进行对比')
      return
    }
    setLoadingCompare(true)
    setError('')
    try {
      setCompareData(await compareFunds(codes, months))
    } catch (requestError) {
      setError(requestError.message)
    } finally {
      setLoadingCompare(false)
    }
  }

  async function runOverlap() {
    const codes = parseCodes(compareInput)
    if (codes.length < 2) {
      setError('至少输入 2 只基金代码进行持仓重合度分析')
      return
    }
    setLoadingOverlap(true)
    setError('')
    try {
      setOverlapData(await analyzeFundOverlap(codes))
    } catch (requestError) {
      setError(requestError.message)
    } finally {
      setLoadingOverlap(false)
    }
  }

  async function loadOpportunities(nextRisk = opportunityRisk) {
    setLoadingOpportunities(true)
    setError('')
    try {
      setOpportunities(await fetchFundOpportunities(nextRisk, 5))
    } catch (requestError) {
      setError(requestError.message)
    } finally {
      setLoadingOpportunities(false)
    }
  }

  async function loadAlternatives(nextCode = code, nextSort = peerSort) {
    const clean = String(nextCode || '').trim()
    if (!/^\d{6}$/.test(clean)) {
      setError('请输入 6 位基金代码')
      return
    }
    setLoadingAlternatives(true)
    setError('')
    try {
      setAlternatives(await fetchFundAlternatives(clean, nextSort, 5, months))
    } catch (requestError) {
      setError(requestError.message)
    } finally {
      setLoadingAlternatives(false)
    }
  }

  async function loadCategories() {
    try {
      const data = await fetchFundCategories()
      setCategories(data.items || [])
      setCategoryError('')
    } catch (requestError) {
      setCategories([])
      setCategoryError(requestError.message)
    }
  }

  useEffect(() => {
    loadHot()
    loadCategories()
    loadOpportunities('balanced')
  }, [])

  useEffect(() => {
    if (fundView !== 'research' || researchLayer !== 'evidence' || !fund?.code) return
    loadPortfolio(fund.code)
    loadPeers(fund.code, peerSort)
    loadDividends(fund.code)
  }, [fundView, researchLayer, fund?.code])

  return {
    fundView, setFundView, researchLayer, setResearchLayer,
    category, setCategory, sort, setSort, limit, setLimit, months, setMonths, code, setCode,
    hot, categories, categoryError, fund, portfolio, portfolioError, peers, peerSort, setPeerSort,
    dividends, searchKeyword, setSearchKeyword, searchResults, compareInput, setCompareInput,
    compareData, overlapData, opportunityRisk, setOpportunityRisk, opportunities, alternatives,
    loadingHot, loadingFund, loadingPortfolio, loadingPeers, loadingDividends, loadingSearch,
    loadingCompare, loadingOverlap, loadingOpportunities, loadingAlternatives, error,
    loadHot, loadFund, loadPeers, loadAlternatives, loadOpportunities, runSearch, runCompare, runOverlap,
  }
}
