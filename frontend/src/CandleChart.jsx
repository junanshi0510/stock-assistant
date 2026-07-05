import { useEffect, useRef } from 'react'
import { createChart } from 'lightweight-charts'

// K线 + 均线(MA5/MA20/MA60)+ 布林带 + 成交量。颜色遵循 A股习惯:涨=红,跌=绿。
export default function CandleChart({ candles }) {
  const containerRef = useRef(null)

  useEffect(() => {
    if (!containerRef.current || !candles || candles.length === 0) return

    const chart = createChart(containerRef.current, {
      layout: { background: { color: 'transparent' }, textColor: '#8896a8', fontSize: 11 },
      grid: {
        vertLines: { color: 'rgba(255,255,255,0.04)' },
        horzLines: { color: 'rgba(255,255,255,0.05)' },
      },
      rightPriceScale: { borderColor: 'rgba(255,255,255,0.08)' },
      timeScale: { borderColor: 'rgba(255,255,255,0.08)' },
      crosshair: { mode: 1 },
      autoSize: true,
    })

    const line = (key, color, width = 1) => {
      const s = chart.addLineSeries({ color, lineWidth: width, priceLineVisible: false, lastValueVisible: false })
      s.setData(candles.filter((c) => c[key] != null).map((c) => ({ time: c.date, value: c[key] })))
    }

    // 布林带(淡色,先画在底层)
    line('boll_up', 'rgba(157,107,255,0.45)')
    line('boll_low', 'rgba(157,107,255,0.45)')

    const candleSeries = chart.addCandlestickSeries({
      upColor: '#ff4d5e', downColor: '#1fd286',
      borderUpColor: '#ff4d5e', borderDownColor: '#1fd286',
      wickUpColor: '#ff4d5e', wickDownColor: '#1fd286',
    })
    candleSeries.setData(
      candles.map((c) => ({ time: c.date, open: c.open, high: c.high, low: c.low, close: c.close }))
    )

    line('ma5', '#f5b942')
    line('ma20', '#5b8cff')
    line('ma60', '#9d6bff')

    const vol = chart.addHistogramSeries({ priceFormat: { type: 'volume' }, priceScaleId: 'vol' })
    chart.priceScale('vol').applyOptions({ scaleMargins: { top: 0.82, bottom: 0 } })
    vol.setData(
      candles.map((c) => ({
        time: c.date, value: c.volume,
        color: c.close >= c.open ? 'rgba(255,77,94,0.35)' : 'rgba(31,210,134,0.35)',
      }))
    )

    chart.timeScale().fitContent()
    return () => chart.remove()
  }, [candles])

  return <div ref={containerRef} className="chart" />
}
