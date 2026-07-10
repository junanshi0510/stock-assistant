import { scoreColor } from './helpers'

// 圆环式评分仪表盘(0-100)
export default function ScoreRing({ score }) {
  const r = 62
  const c = 2 * Math.PI * r
  const pct = Math.max(0, Math.min(100, score)) / 100
  const color = scoreColor(score)
  return (
    <div className="ring-wrap">
      <div className="ring">
        <svg width="150" height="150">
          <circle cx="75" cy="75" r={r} fill="none" stroke="#dfe7ea" strokeWidth="11" />
          <circle
            cx="75" cy="75" r={r} fill="none" stroke={color} strokeWidth="11"
            strokeLinecap="round" strokeDasharray={c}
            strokeDashoffset={c * (1 - pct)}
            style={{ transition: 'stroke-dashoffset 0.7s cubic-bezier(.2,.8,.2,1)' }}
          />
        </svg>
        <div className="score-num">
          <b style={{ color }}>{score}</b>
          <span>看涨打分</span>
        </div>
      </div>
    </div>
  )
}
