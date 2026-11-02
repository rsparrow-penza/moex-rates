// Chart.jsx — линейный график прямо на SVG, без библиотек.
//
// Зачем с нуля: во-первых, учебный проект (видно каждую координату),
// во-вторых — ноль зависимостей и крохотный бандл.
//
// Идея: дата → положение по X, цена → положение по Y. Шкала цен у каждой
// валюты СВОЯ: USD ~80 ₽ и CNY ~12 ₽ на одной линейке превратили бы юань
// в плоскую линию у нуля. Левая ось — первая валюта, правая — вторая,
// подписаны их цветами.

const W = 720          // «виртуальная» ширина холста; CSS растянет по экрану
const H = 260
const PAD = { top: 16, right: 52, bottom: 28, left: 52 }

const COLORS = { USD: '#4c6ef5', CNY: '#e8590c' }

export default function Chart({ series }) {
  const codes = Object.keys(series).filter((c) => series[c].length > 1)
  if (!codes.length) return null

  // --- Ось X: общая для всех (одинаковый диапазон дат) ---
  const allDates = codes.flatMap((c) => series[c].map((p) => p.rate_date))
  const t = (d) => new Date(d).getTime()
  const t0 = Math.min(...allDates.map(t))
  const tSpan = Math.max(...allDates.map(t)) - t0 || 1
  const x = (d) => PAD.left + ((t(d) - t0) / tSpan) * (W - PAD.left - PAD.right)

  // --- Ось Y: у каждой серии своя шкала ---
  const scales = {}
  for (const c of codes) {
    const vals = series[c].map((p) => p.rate)
    const lo = Math.min(...vals)
    const hi = Math.max(...vals)
    const span = hi - lo || 1
    // Y инвертирован: в SVG ось смотрит вниз, а цены должны расти вверх
    scales[c] = { lo, span, y: (v) => PAD.top + (1 - (v - lo) / span) * (H - PAD.top - PAD.bottom) }
  }

  // Горизонтальная сетка — по шкале первой валюты
  const grid = scales[codes[0]]
  const ticks = [0, 1, 2, 3, 4].map((i) => ({
    v: grid.lo + (grid.span * i) / 4,
    y: grid.y(grid.lo + (grid.span * i) / 4),
  }))

  // Подписи осей: первая валюта слева, вторая (если есть) справа
  const leftCode = codes[0]
  const rightCode = codes[1]

  return (
    <div className="chart">
      <svg viewBox={`0 0 ${W} ${H}`} role="img" aria-label="График курсов валют">
        {ticks.map(({ v, y }, i) => (
          <g key={i}>
            <line x1={PAD.left} x2={W - PAD.right} y1={y} y2={y} stroke="#2a2a38" strokeDasharray="4 4" />
            <text x={PAD.left - 6} y={y + 4} textAnchor="end" fontSize="11" fill={COLORS[leftCode]}>
              {v.toFixed(2)}
            </text>
          </g>
        ))}
        {rightCode && [0, 2, 4].map((i) => {
          const s = scales[rightCode]
          const v = s.lo + (s.span * i) / 4
          return (
            <text key={i} x={W - PAD.right + 6} y={s.y(v) + 4} fontSize="11" fill={COLORS[rightCode]}>
              {v.toFixed(2)}
            </text>
          )
        })}

        {/* Даты под осью X: начало, середина, конец */}
        {[new Date(t0).toISOString().slice(0, 10),
          new Date(t0 + tSpan / 2).toISOString().slice(0, 10),
          new Date(t0 + tSpan).toISOString().slice(0, 10)].map((d, i) => (
          <text key={i} x={x(d)} y={H - 8} textAnchor={i === 0 ? 'start' : i === 2 ? 'end' : 'middle'}
                fontSize="11" fill="#77778b">
            {d.slice(5)}
          </text>
        ))}

        {/* Ломаные линии */}
        {codes.map((code) => (
          <polyline
            key={code}
            fill="none"
            stroke={COLORS[code]}
            strokeWidth="2.5"
            points={series[code]
              .map((p) => `${x(p.rate_date)},${scales[code].y(p.rate)}`)
              .join(' ')}
          />
        ))}
      </svg>

      <div className="legend">
        {codes.map((c, i) => (
          <span key={c}>
            <i style={{ background: COLORS[c] }} /> {c}
            <em>{i === 0 ? 'левая ось' : 'правая ось'}</em>
          </span>
        ))}
      </div>
    </div>
  )
}
