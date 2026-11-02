// Chart.jsx — график курсов на чистом SVG, в двух режимах.
//
//   mode='pct' — ПРОЦЕНТ изменения каждой валюты от первой точки периода.
//     Одна общая шкала, все линии сравнимы: видно, кто за период вырос или
//     упал СИЛЬНЕЕ, даже если цены различаются в разы (12/81/90 ₽).
//     Ноль на графике = «вернулась к цене начала периода», а не «бесплатно».
//
//   mode='rub' — АБСОЛЮТНЫЕ рубли. Но рисовать 12 ₽ и 90 ₽ на одной оси —
//     путь к плоской линеечке юаня, поэтому каждая валюта получает свою
//     мини-полоску со своей шкалой (приём «small multiples»: одинаковые
//     маленькие графики вместо одного большого кашевого).

const W = 720
const PAD = { top: 14, right: 16, bottom: 24, left: 56 }

// Порядок и цвета серий; неизвестные коды получают цвет из палитры по индексу
const COLORS = {
  USD: '#4c6ef5',
  USD_OTC: '#845ef7',     // фиолетовый «змей» рядом с синим фиксингом
  USD_CBR: '#4c6ef5',
  EUR: '#20c997',
  EUR_OTC: '#f06595',     // розовый — пара к биржевому евро
  EUR_CBR: '#20c997',
  CNY: '#e8590c',
  CNY_CBR: '#e8590c',
}
const PALETTE = ['#4c6ef5', '#e8590c', '#20c997', '#845ef7', '#f06595']

// Дата+время → единый «момент» для оси X; дневным значениям дописываем
// полдень, чтобы точка ММВБ/ЦБ ложилась в ряд аккуратно
const stamp = (p) => new Date(`${p.rate_date}T${p.rate_time || '12:00'}+03:00`).getTime()

// Кирпичик: одна серия на своей шкале. Возвращает элементы SVG;
// вызывающий решает, на какой холст их посадить.
function Series({ code, points, x, y, labels, dashed, unit = '₽' }) {
  const color = COLORS[code] ?? PALETTE[Object.keys(COLORS).indexOf(code) % PALETTE.length]
  return (
    <g>
      <polyline
        fill="none" stroke={color} strokeWidth="2.5"
        strokeDasharray={dashed ? '7 4' : undefined}
        points={points.map((p) => `${x(stamp(p))},${y(p.rate)}`).join(' ')}
      />
      {points.map((p) => (
        <circle key={p.rate_date + p.rate_time}
                cx={x(stamp(p))} cy={y(p.rate)}
                r={p.rate_time ? 2 : 3} fill={color}>
          <title>{`${labels[code] ?? code} ${p.rate_date}${p.rate_time ? ' ' + p.rate_time : ''}: ` +
                  `${p.rate >= 0 && unit === '%' ? '+' : ''}${p.rate.toFixed(2)} ${unit}`}</title>
        </circle>
      ))}
    </g>
  )
}

// --- Режим процентов: общая шкала, все линии вместе ---
function PctChart({ series, labels }) {
  const codes = Object.keys(series).filter((c) => series[c].length > 1)
  const allStamps = codes.flatMap((c) => series[c].map(stamp))
  const t0 = Math.min(...allStamps)
  const H = 260
  const tSpan = Math.max(...allStamps) - t0 || 1
  const x = (ts) => PAD.left + ((ts - t0) / tSpan) * (W - PAD.left - PAD.right)

  const pct = {}
  for (const c of codes) {
    const base = series[c][0].rate
    pct[c] = series[c].map((p) => ({ ...p, rate: ((p.rate - base) / base) * 100 }))
  }
  const values = codes.flatMap((c) => pct[c].map((p) => p.rate))
  let lo = Math.min(...values, 0)
  let hi = Math.max(...values, 0)
  const pad = (hi - lo) * 0.08 || 1
  lo -= pad; hi += pad
  const y = (v) => PAD.top + (1 - (v - lo) / (hi - lo)) * (H - PAD.top - PAD.bottom)
  const ticks = [lo, 0, hi].filter((v, i, a) => a.indexOf(v) === i)
  const dateMarks = [t0, t0 + tSpan / 2, t0 + tSpan]

  return (
    <>
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
        {dateMarks.map((ts, i) => (
          <text key={i} x={x(ts)} y={H - 6} textAnchor={i === 0 ? 'start' : i === 2 ? 'end' : 'middle'}
                fontSize="11" fill="#77778b">
            {new Date(ts + 3 * 3600e3).toISOString().slice(5, 10)}
          </text>
        ))}
        {codes.map((c) => (
          <Series key={c} code={c} points={pct[c]} x={x} y={y} labels={labels}
                  dashed={c.endsWith('_CBR')} unit="%" />
        ))}
      </svg>
      <div className="legend">
        {codes.map((c, i) => (
          <span key={c}>
            <i style={{ background: COLORS[c] ?? PALETTE[i % PALETTE.length] }} />
            {labels[c] ?? c}{c.endsWith('_CBR') ? ' ⸺' : ''}
          </span>
        ))}
        <span className="legend-note">изменение к началу периода, %</span>
      </div>
    </>
  )
}

// --- Режим рублей: small multiples, по полоске на валюту ---
function RubChart({ series, labels }) {
  const codes = Object.keys(series).filter((c) => series[c].length > 1)
  const allStamps = codes.flatMap((c) => series[c].map(stamp))
  const t0 = Math.min(...allStamps)
  const tSpan = Math.max(...allStamps) - t0 || 1
  const x = (ts) => PAD.left + ((ts - t0) / tSpan) * (W - PAD.left - PAD.right)

  const STRIP = 110            // высота одной полоски
  const H = STRIP * codes.length

  return (
    <>
      <svg viewBox={`0 0 ${W} ${H}`} role="img" aria-label="График курсов в рублях">
        {codes.map((code, idx) => {
          const top = idx * STRIP
          const vals = series[code].map((p) => p.rate)
          const lo = Math.min(...vals), hi = Math.max(...vals)
          const span = hi - lo || 1
          // своя вертикальная шкала внутри своей полоски (с полями 12px)
          const y = (v) => top + 12 + (1 - (v - lo) / span) * (STRIP - 30)
          const last = series[code][series[code].length - 1]
          const color = COLORS[code] ?? PALETTE[idx % PALETTE.length]
          return (
            <g key={code}>
              {idx > 0 && <line x1={0} x2={W} y1={top} y2={top} stroke="#26263a" />}
              {/* название и крайняя цена — прямо на полоске */}
              <text x={8} y={top + 18} fontSize="12" fill={color} fontWeight="600">
                {labels[code] ?? code}
              </text>
              <text x={W - 8} y={top + 18} fontSize="13" fill="#f2f2f7"
                    textAnchor="end" fontWeight="600">
                {last.rate.toFixed(2)} ₽
              </text>
              <text x={PAD.left - 6} y={y(hi) + 8} fontSize="10" fill="#666" textAnchor="end">{hi.toFixed(1)}</text>
              <text x={PAD.left - 6} y={y(lo)} fontSize="10" fill="#666" textAnchor="end">{lo.toFixed(1)}</text>
              <Series code={code} points={series[code]} x={x} y={y} labels={labels}
                      dashed={code.endsWith('_CBR')} />
            </g>
          )
        })}
        {/* даты только внизу последней полоски */}
        {[t0, t0 + tSpan / 2, t0 + tSpan].map((ts, i) => (
          <text key={i} x={x(ts)} y={H - 4} textAnchor={i === 0 ? 'start' : i === 2 ? 'end' : 'middle'}
                fontSize="11" fill="#77778b">
            {new Date(ts + 3 * 3600e3).toISOString().slice(5, 10)}
          </text>
        ))}
      </svg>
      <div className="legend">
        <span className="legend-note">абсолютный курс, ₽ (у каждой валюты своя вертикальная шкала)</span>
      </div>
    </>
  )
}

export default function Chart({ series, labels = {}, mode = 'pct' }) {
  const hasData = Object.values(series).some((s) => s.length > 1)
  if (!hasData) return null
  return (
    <div className="chart">
      {mode === 'pct'
        ? <PctChart series={series} labels={labels} />
        : <RubChart series={series} labels={labels} />}
    </div>
  )
}
