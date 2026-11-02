// Chart.jsx — график изменения валют за выбранный период, на чистом SVG.
//
// Важный дизайн-сдвиг: раньше каждая валюта рисовалась в своих рублях,
// и с приходом третьего инструмента (12 / 80 / 90 ₽) шкалы стали бы
// непригодны. Поэтому график показывает НЕ цену, а ПРОЦЕНТ ИЗМЕНЕНИЯ
// от первого значения периода — стандартный приём финансовых дашбордов:
// «кто за месяц вырос/упал сильнее» видно мгновенно, а абсолютные цены
// остались в таблице под графиком.
//
// Бонус для обучения: тут есть <title> — наведи курсор на точку, и
// браузер покажет подпись с датой и процентом (нативные тултипы SVG).

const W = 720
const H = 260
const PAD = { top: 16, right: 16, bottom: 28, left: 48 }

const COLORS = { USD: '#4c6ef5', EUR: '#20c997', CNY: '#e8590c' }

export default function Chart({ series }) {
  // Серия годна для графика, если в ней хотя бы две точки
  const codes = Object.keys(series).filter((c) => series[c].length > 1)
  if (!codes.length) return null

  // --- Ось X: общая, по датам ---
  const allDates = codes.flatMap((c) => series[c].map((p) => p.rate_date))
  const t = (d) => new Date(d).getTime()
  const t0 = Math.min(...allDates.map(t))
  const tSpan = Math.max(...allDates.map(t)) - t0 || 1
  const x = (d) => PAD.left + ((t(d) - t0) / tSpan) * (W - PAD.left - PAD.right)

  // --- Процент изменения от первой точки каждой серии; он же и общая шкала Y ---
  const pct = {}
  for (const c of codes) {
    const base = series[c][0].rate
    pct[c] = series[c].map((p) => ({
      ...p,
      change: ((p.rate - base) / base) * 100,
    }))
  }
  const values = codes.flatMap((c) => pct[c].map((p) => p.change))
  let lo = Math.min(...values, 0)              // нулевая линия обязана влезть:
  let hi = Math.max(...values, 0)              // она значит «без изменений»
  const pad = (hi - lo) * 0.08 || 1            // небольшой воздух по краям
  lo -= pad; hi += pad
  const y = (v) => PAD.top + (1 - (v - lo) / (hi - lo)) * (H - PAD.top - PAD.bottom)

  // Три горизонтальные линии: минимум, «без изменений», максимум
  const ticks = [lo, 0, hi].filter((v, i, a) => a.indexOf(v) === i)

  return (
    <div className="chart">
      <svg viewBox={`0 0 ${W} ${H}`} role="img" aria-label="График изменения курсов, %">
        {ticks.map((v, i) => (
          <g key={i}>
            <line x1={PAD.left} x2={W - PAD.right} y1={y(v)} y2={y(v)}
                  stroke={v === 0 ? '#4a4a5e' : '#2a2a38'}
                  strokeDasharray={v === 0 ? '' : '4 4'} />
            <text x={PAD.left - 6} y={y(v) + 4} textAnchor="end" fontSize="11" fill="#999">
              {v.toFixed(1)}%
            </text>
          </g>
        ))}

        {/* Даты под осью X: начало, середина, конец */}
        {[new Date(t0).toISOString().slice(0, 10),
          new Date(t0 + tSpan / 2).toISOString().slice(0, 10),
          new Date(t0 + tSpan).toISOString().slice(0, 10)].map((d, i) => (
          <text key={i} x={x(d)} y={H - 8} textAnchor={i === 0 ? 'start' : i === 2 ? 'end' : 'middle'}
                fontSize="11" fill="#77778b">
            {d.slice(5)}
          </text>
        ))}

        {codes.map((code) => (
          <g key={code}>
            <polyline
              fill="none"
              stroke={COLORS[code]}
              strokeWidth="2.5"
              points={pct[code].map((p) => `${x(p.rate_date)},${y(p.change)}`).join(' ')}
            />
            {/* Точки с нативными подписями: наведи курсор */}
            {pct[code].map((p) => (
              <circle key={p.rate_date} cx={x(p.rate_date)} cy={y(p.change)} r="3" fill={COLORS[code]}>
                <title>{`${code} ${p.rate_date}: ${p.change >= 0 ? '+' : ''}${p.change.toFixed(2)}%`}</title>
              </circle>
            ))}
          </g>
        ))}
      </svg>

      <div className="legend">
        {codes.map((c) => (
          <span key={c}><i style={{ background: COLORS[c] }} /> {c}</span>
        ))}
        <span className="legend-note">изменение к началу периода, %</span>
      </div>
    </div>
  )
}
