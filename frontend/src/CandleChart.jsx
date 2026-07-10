import { useEffect, useRef } from 'react'
import { createChart } from 'lightweight-charts'

// K线 + 均线(MA5/MA20/MA60)+ 布林带 + 成交量。颜色遵循 A股习惯:涨=红,跌=绿。
export default function CandleChart({ candles }) {
  const containerRef = useRef(null)

  useEffect(() => {
    if (!containerRef.current || !candles || candles.length === 0) return

    const chart = createChart(containerRef.current, {
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

    const line = (key, color, width = 1) => {
      const s = chart.addLineSeries({ color, lineWidth: width, priceLineVisible: false, lastValueVisible: false })
      s.setData(candles.filter((c) => c[key] != null).map((c) => ({ time: c.date, value: c[key] })))
    }

    // 布林带(淡色,先画在底层)
    line('boll_up', 'rgba(110,86,180,0.42)')
    line('boll_low', 'rgba(110,86,180,0.42)')

    const candleSeries = chart.addCandlestickSeries({
      upColor: '#c63b4a', downColor: '#087f70',
      borderUpColor: '#c63b4a', borderDownColor: '#087f70',
      wickUpColor: '#c63b4a', wickDownColor: '#087f70',
    })
    candleSeries.setData(
      candles.map((c) => ({ time: c.date, open: c.open, high: c.high, low: c.low, close: c.close }))
    )

    line('ma5', '#aa7200')
    line('ma20', '#176f9c')
    line('ma60', '#7256b4')

    const vol = chart.addHistogramSeries({ priceFormat: { type: 'volume' }, priceScaleId: 'vol' })
    chart.priceScale('vol').applyOptions({ scaleMargins: { top: 0.82, bottom: 0 } })
    vol.setData(
      candles.map((c) => ({
        time: c.date, value: c.volume,
        color: c.close >= c.open ? 'rgba(198,59,74,0.30)' : 'rgba(8,127,112,0.30)',
      }))
    )

    chart.timeScale().fitContent()
    return () => chart.remove()
  }, [candles])

  return <div ref={containerRef} className="chart" />
}
