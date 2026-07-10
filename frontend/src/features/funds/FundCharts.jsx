import { useEffect, useRef } from 'react'
import { createChart } from 'lightweight-charts'

const COLORS = ['#176f9c', '#087f70', '#c63b4a', '#9a6800', '#7256b4', '#287f9f', '#a45a1e', '#4f8a4c']

function createBaseChart(container) {
  return createChart(container, {
    layout: { background: { color: 'transparent' }, textColor: '#667784', fontSize: 11 },
    grid: {
      vertLines: { color: 'rgba(28,42,53,0.06)' },
      horzLines: { color: 'rgba(28,42,53,0.08)' },
    },
    rightPriceScale: { borderColor: 'rgba(28,42,53,0.14)' },
    timeScale: { borderColor: 'rgba(28,42,53,0.14)' },
    crosshair: { mode: 1 },
    autoSize: true,
  })
}

export function FundLineChart({ data }) {
  const ref = useRef(null)

  useEffect(() => {
    if (!ref.current || !data?.length) return undefined
    const chart = createBaseChart(ref.current)
    const navSeries = chart.addLineSeries({
      color: '#176f9c',
      lineWidth: 2,
      priceLineVisible: false,
      title: '单位净值',
    })
    navSeries.setData(data.map((row) => ({ time: row.date, value: Number(row.unit_nav) })))
    chart.timeScale().fitContent()
    return () => chart.remove()
  }, [data])

  return <div ref={ref} className="chart small" />
}

export function FundCompareChart({ data }) {
  const ref = useRef(null)

  useEffect(() => {
    if (!ref.current || !data?.rebased?.length) return undefined
    const chart = createBaseChart(ref.current)
    data.codes.forEach((code, index) => {
      const series = chart.addLineSeries({
        color: COLORS[index % COLORS.length],
        lineWidth: 2,
        priceLineVisible: false,
        title: code,
      })
      series.setData(data.rebased
        .filter((row) => row[code] != null)
        .map((row) => ({ time: row.date, value: Number(row[code]) })))
    })
    chart.timeScale().fitContent()
    return () => chart.remove()
  }, [data])

  return <div ref={ref} className="chart small" />
}
