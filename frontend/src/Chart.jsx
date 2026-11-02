// Chart.jsx — график изменения валют за выбранный период, на чистом SVG.
//
// Показывает НЕ рубли, а ПРОЦЕНТ ИЗМЕНЕНИЯ от первого значения периода —
// стандарт финансовых дашбордов: ряды 12/81/90 ₽ сравнимы на одной шкале,
// абсолютные цены — в таблице.
//
// С приходом Yahoo OTC ось X стала датой СО ВРЕМЕНЕМ: '2026-11-02' + ''
// (дневное значение) или '2026-11-02' + '14:15' (внутридневная точка).
// Точка наведи-подсказка (<title>) теперь показывает и время.

const W = 720
const H = 260
const PAD = { top: 16, right: 16, bottom: 28, left: 48 }

// Порядок и цвета серий; неизвестные коды получают цвет из палитры по индексу
const COLORS = {
  USD: '#4c6ef5',
  USD_OTC: '#845ef7',     // фиолетовый «змей» рядом с синим фиксингом
  EUR: '#20c997',
  CNY: '#e8590c',
}
const PALETTE = ['#4c6ef5', '#e8590c', '#20c997', '#845ef7', '#f59f00']

// Дата+время → единый «момент» для оси X; дневным значениям дописываем
// полдень, чтобы точка ММВБ (публикуется ~13:00) ложилась в ряд аккуратно
const stamp = (p) => new Date(`${p.rate_date}T${p.rate_time || '12:00'}+03:00`).getTime()

export default function Chart({ series, labels = {} }) {
  const codes = Object.keys(series).filter((c) => series[c].length > 1)
  if (!codes.length) return null

  // --- Ось X: общая, по моментам ---
  const allStamps = codes.flatMap((c) => series[c].map(stamp))
  const t0 = Math.min(...allStamps)
  const tSpan = Math.max(...allStamps) - t0 || 1
  const x = (ts) => PAD.left + ((ts - t0) / tSpan) * (W - PAD.left - PAD.right)

  // --- Процент изменения от первой точки серии; общая шкала Y ---
  const pct = {}
  for (const c of codes) {
    const base = series[c][0].rate
    pct[c] = series[c].map((p) => ({ ...p, ts: stamp(p), change: ((p.rate - base) / base) * 100 }))
  }
  const values = codes.flatMap((c) => pct[c].map((p) => p.change))
  let lo = Math.min(...values, 0)              // нулевая линия («без изменений») обязана влезть
  let hi = Math.max(...values, 0)
  const pad = (hi - lo) * 0.08 || 1
  lo -= pad; hi += pad
  const y = (v) => PAD.top + (1 - (v - lo) / (hi - lo)) * (H - PAD.top - PAD.bottom)

  const ticks = [lo, 0, hi].filter((v, i, a) => a.indexOf(v) === i)

  // Подписи дат: начало / середина / конец оси — дни без времени
  const dateMarks = [t0, t0 + tSpan / 2, t0 + tSpan].map((ts) =>
    new Date(ts + 3 * 3600e3).toISOString().slice(0, 10))   // +3ч → московская дата

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

        {dateMarks.map((d, i) => (
          <text key={i} x={x([t0, t0 + tSpan / 2, t0 + tSpan][i])} y={H - 8}
                textAnchor={i === 0 ? 'start' : i === 2 ? 'end' : 'middle'}
                fontSize="11" fill="#77778b">
            {d.slice(5)}
          </text>
        ))}

        {codes.map((code, idx) => {
          const color = COLORS[code] ?? PALETTE[idx % PALETTE.length]
          return (
            <g key={code}>
              <polyline
                fill="none" stroke={color} strokeWidth="2.5"
                opacity={code.endsWith('_OTC') ? 0.9 : 1}
                points={pct[code].map((p) => `${x(p.ts)},${y(p.change)}`).join(' ')}
              />
              {pct[code].map((p) => (
                <circle key={p.rate_date + p.rate_time}
                        cx={x(p.ts)} cy={y(p.change)}
                        r={p.rate_time ? 2 : 3}      // внутридневные точки мельче
                        fill={color}>
                  <title>{`${labels[code] ?? code} ${p.rate_date}${p.rate_time ? ' ' + p.rate_time : ''}: ` +
                          `${p.change >= 0 ? '+' : ''}${p.change.toFixed(2)}%`}</title>
                </circle>
              ))}
            </g>
          )
        })}
      </svg>

      <div className="legend">
        {codes.map((c, i) => (
          <span key={c}>
            <i style={{ background: COLORS[c] ?? PALETTE[i % PALETTE.length] }} />
            {labels[c] ?? c}
          </span>
        ))}
        <span className="legend-note">изменение к началу периода, %</span>
      </div>
    </div>
  )
}
