export default function FundMetricCard({ label, value, cls = '' }) {
  return (
    <div className="bt-card">
      <div className="k">{label}</div>
      <div className={`v ${cls}`}>{value}</div>
    </div>
  )
}
