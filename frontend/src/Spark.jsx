// Spark.jsx — спарклайн для таблиц (вынесен из Metals.jsx, чтобы его же
// использовал блок металлопроката). Подпись во всплывашке обязательна: без неё
// «зубрик» без единиц и дат — украшение, а не данные.

const fmt1 = (n) => n == null ? '—' : (Math.abs(n) >= 100
  ? Math.round(n).toLocaleString('ru-RU')
  : n.toFixed(2).replace('.', ','))

// unit — «унция»/«тонна» (тогда суффикс $/унция), suffix — готовая единица
// («₽/т»), когда ряд уже приведён к рублям
export default function Spark({
  points, color = '#fab005', width = 132, height = 42, unit = 'унция', suffix = null,
}) {
  if (!points || points.length < 2) return <span className="mt-empty">—</span>
  const vals = points.map((p) => p.value)
  const lo = Math.min(...vals), hi = Math.max(...vals)
  const line = height - 14                       // снизу — место под даты
  const x = (i) => (i / (points.length - 1)) * (width - 2) + 1
  const y = (v) => 2 + (1 - (v - lo) / (hi - lo || 1)) * (line - 4)
  const last = points[points.length - 1]
  const ticks = [0, Math.floor((points.length - 1) / 2), points.length - 1]
  const mmyy = (d) => `${d.slice(5, 7)}.${d.slice(2, 4)}`
  const per = suffix || `$/${unit}`
  return (
    <svg width={width} height={height} className="mt-spark">
      <title>{`${points[0].date}—${last.date}: мин ${fmt1(lo)}, макс ${fmt1(hi)}, сейчас ${fmt1(last.value)} ${per}`}</title>
      <polyline points={points.map((p, i) => `${x(i)},${y(p.value)}`).join(' ')} stroke={color} />
      <circle cx={x(points.length - 1)} cy={y(last.value)} r={2.5} fill={color} />
      <line x1={0} x2={width} y1={line} y2={line} className="mt-axis" />
      {ticks.map((i, k) => (
        <g key={i}>
          <line x1={x(i)} x2={x(i)} y1={line} y2={line + 3} className="mt-axis" />
          <text x={x(i)} y={height - 2} textAnchor={k === 0 ? 'start' : k === 2 ? 'end' : 'middle'}
                className="mt-axis-label">{mmyy(points[i].date)}</text>
        </g>
      ))}
    </svg>
  )
}
