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
//
// При наведении курсора — вертикальная линия-хайр и всплывающее окошко
// со значением каждой серии в ближайший к курсору момент времени.
// Экстремумы курсов (localExtrema) подсвечивает не график, а таблица — App.jsx.

import { useMemo, useRef, useState } from 'react'

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
const colorFor = (code, i) => COLORS[code] ?? PALETTE[i % PALETTE.length]

// Дата+время → единый «момент» для оси X; дневным значениям дописываем
// полдень, чтобы точка ММВБ/ЦБ ложилась в ряд аккуратно
const stamp = (p) => new Date(`${p.rate_date}T${p.rate_time || '12:00'}+03:00`).getTime()

// Индекс ближайшей к моменту ts точки в отсортированной серии
function nearestIdx(pts, ts) {
  let best = 0
  for (let i = 1; i < pts.length; i++)
    if (Math.abs(stamp(pts[i]) - ts) < Math.abs(stamp(pts[best]) - ts)) best = i
  return best
}

// Момент → «2026-11-04» по МСК
const dayOf = (ts) => new Date(ts + 3 * 3600e3).toISOString().slice(0, 10)

// Локальные экстремумы серии: точка — максимум, если она не ниже всех
// соседей в окне ±K и при этом возвышается над «дном» окна минимум на
// половину размаха окна (иначе это плечо, а не вершина). Порог от размаха
// делает метод независимым от масштаба — он одинаково работает и в %, и в ₽.
// Подряд идущие одноимённые отметки сливаются в одну (самую выразительную).
export function localExtrema(vals) {
  const n = vals.length
  const K = Math.min(12, Math.max(3, Math.round(n / 15)))
  const out = []
  for (let i = K; i < n - K; i++) {
    const v = vals[i]
    let hi = -Infinity, lo = Infinity, isMax = true, isMin = true
    for (let j = i - K; j <= i + K; j++) {
      const w = vals[j]
      if (j !== i) {
        if (w > v) isMax = false
        if (w < v) isMin = false
      }
      if (w > hi) hi = w
      if (w < lo) lo = w
    }
    const span = hi - lo
    if (span <= 0) continue
    if (isMax && v - lo >= 0.5 * span) out.push({ i, type: 'max' })
    else if (isMin && hi - v >= 0.5 * span) out.push({ i, type: 'min' })
  }
  const res = []
  for (const e of out) {
    const p = res[res.length - 1]
    if (p && p.type === e.type) {
      if (e.type === 'max' ? vals[e.i] > vals[p.i] : vals[e.i] < vals[p.i]) res[res.length - 1] = e
    } else res.push(e)
  }
  return res
}

// Всплывающее окошко: стрелка курсора → строки «серия = значение».
// Живёт в .chart (position: relative), координаты — в CSS-пикселях.
function TipBox({ tip }) {
  if (!tip) return null
  return (
    <div className="chart-tip" style={{ left: tip.x, top: tip.y }}>
      <b className="tip-day">{tip.day}</b>
      {tip.rows.map((r) => (
        <div key={r.code} className="tip-row">
          <i style={{ background: r.color }} />
          <span>{r.label}{r.when ? ` ${r.when}` : ''}</span>
          <b>{r.text}</b>
        </div>
      ))}
    </div>
  )
}

// Общая логика наведения: курсор → момент времени по оси X.
// rowsBuild(ts) делает содержимое окошка.
function useHover(svgRef, { t0, tSpan, x, rowsBuild }) {
  const [tip, setTip] = useState(null)
  function onMove(e) {
    const rect = svgRef.current.getBoundingClientRect()
    const px = ((e.clientX - rect.left) / rect.width) * W
    const ratio = (px - PAD.left) / (W - PAD.left - PAD.right)
    if (ratio < -0.01 || ratio > 1.01) { setTip(null); return }
    const ts = t0 + ratio * tSpan
    const lineX = x(ts)
    const cssX = (lineX / W) * rect.width
    const rows = rowsBuild(ts)
    if (!rows.length) { setTip(null); return }
    setTip({
      rows, day: dayOf(ts), lineX,
      // окошко не должно вылезать за правый/нижний край графика
      x: Math.min(cssX + 14, rect.width - 190),
      y: Math.min(e.clientY - rect.top + 14, rect.height - 150),
    })
  }
  return { tip, handlers: { onMouseMove: onMove, onMouseLeave: () => setTip(null) } }
}

// Кирпичик: одна серия на своей шкале. Возвращает элементы SVG;
// вызывающий решает, на какой холст их посадить.
function Series({ code, points, x, y, labels, dashed, unit = '₽' }) {
  const color = colorFor(code, Object.keys(COLORS).indexOf(code))
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
  const codes = useMemo(() => Object.keys(series).filter((c) => series[c].length > 1), [series])
  const allStamps = codes.flatMap((c) => series[c].map(stamp))
  const t0 = Math.min(...allStamps)
  const H = 260
  const tSpan = Math.max(...allStamps) - t0 || 1
  const x = (ts) => PAD.left + ((ts - t0) / tSpan) * (W - PAD.left - PAD.right)

  // базы и проценты мемоимзим: tooltip при наведении перерисовывает график,
  // пересчитывать каждый раз заново незачем
  const pct = useMemo(() => {
    const out = {}
    for (const c of codes) {
      const base = series[c][0].rate
      out[c] = series[c].map((p) => ({ ...p, rate: ((p.rate - base) / base) * 100 }))
    }
    return out
  }, [series, codes])
  const values = codes.flatMap((c) => pct[c].map((p) => p.rate))
  let lo = Math.min(...values, 0)
  let hi = Math.max(...values, 0)
  const pad = (hi - lo) * 0.08 || 1
  lo -= pad; hi += pad
  const y = (v) => PAD.top + (1 - (v - lo) / (hi - lo)) * (H - PAD.top - PAD.bottom)
  const ticks = [lo, 0, hi].filter((v, i, a) => a.indexOf(v) === i)
  const dateMarks = [t0, t0 + tSpan / 2, t0 + tSpan]

  const svgRef = useRef(null)
  // Строки окошка: % изменения к базе периода + абсолютный ₽ рядом
  const { tip, handlers } = useHover(svgRef, {
    t0, tSpan, x,
    rowsBuild: (ts) => codes.map((c, i) => {
      const idx = nearestIdx(pct[c], ts)
      const p = pct[c][idx], raw = series[c][idx]
      return {
        code: c, color: colorFor(c, i), label: labels[c] ?? c,
        when: raw.rate_time ? raw.rate_time.slice(0, 5) : '',
        text: `${p.rate >= 0 ? '+' : ''}${p.rate.toFixed(2)} % · ${raw.rate.toFixed(2)} ₽`,
      }
    }),
  })

  return (
    <>
      <svg ref={svgRef} viewBox={`0 0 ${W} ${H}`} role="img"
           aria-label="График изменения курсов, %" {...handlers}>
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
        {codes.map((c, i) => (
          <Series key={c} code={c} points={pct[c]} x={x} y={y} labels={labels}
                  dashed={c.endsWith('_CBR')} unit="%" />
        ))}
        {tip && <line className="chart-cross" x1={tip.lineX} x2={tip.lineX}
                     y1={PAD.top} y2={H - PAD.bottom} />}
      </svg>
      <TipBox tip={tip} />
      <div className="legend">
        {codes.map((c, i) => (
          <span key={c}>
            <i style={{ background: colorFor(c, i) }} />
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
  const codes = useMemo(() => Object.keys(series).filter((c) => series[c].length > 1), [series])
  const allStamps = codes.flatMap((c) => series[c].map(stamp))
  const t0 = Math.min(...allStamps)
  const tSpan = Math.max(...allStamps) - t0 || 1
  const x = (ts) => PAD.left + ((ts - t0) / tSpan) * (W - PAD.left - PAD.right)

  const STRIP = 110            // высота одной полоски
  const H = STRIP * codes.length

  const svgRef = useRef(null)
  const { tip, handlers } = useHover(svgRef, {
    t0, tSpan, x,
    rowsBuild: (ts) => codes.map((c, i) => {
      const idx = nearestIdx(series[c], ts)
      const p = series[c][idx]
      return {
        code: c, color: colorFor(c, i), label: labels[c] ?? c,
        when: p.rate_time ? p.rate_time.slice(0, 5) : '',
        text: `${p.rate.toFixed(2)} ₽`,
      }
    }),
  })

  return (
    <>
      <svg ref={svgRef} viewBox={`0 0 ${W} ${H}`} role="img"
           aria-label="График курсов в рублях" {...handlers}>
        {codes.map((code, idx) => {
          const top = idx * STRIP
          const vals = series[code].map((p) => p.rate)
          const lo = Math.min(...vals), hi = Math.max(...vals)
          const span = hi - lo || 1
          // своя вертикальная шкала внутри своей полоски (с полями 12px)
          const y = (v) => top + 12 + (1 - (v - lo) / span) * (STRIP - 30)
          const last = series[code][series[code].length - 1]
          const color = colorFor(code, idx)
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
        {tip && <line className="chart-cross" x1={tip.lineX} x2={tip.lineX}
                     y1={0} y2={H} />}
      </svg>
      <TipBox tip={tip} />
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
