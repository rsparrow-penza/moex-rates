// Inflation.jsx — блок «Официальная инфляция в России» с 1991 года.
//
// Данные: GET /api/inflation — годовая инфляция (ИПЦ, % к предыдущему
// году), источник — агрегированные данные Росстата (World Bank API).
// Шкала ЛОГАРИФМИЧЕСКАЯ: иначе гиперинфляционный 1993-й (874%) оставляет
// от всех остальных лет тонкую полоску у нуля, а лог-шкала честно
// показывает порядок величины каждый год.

import { useEffect, useMemo, useRef, useState } from 'react'

const W = 720
const H = 190
const PAD = { top: 14, right: 10, bottom: 24, left: 36 }

const fmt = (n) => n.toFixed(1).replace('.', ',')

export default function Inflation() {
  const [years, setYears] = useState([])
  const [error, setError] = useState('')
  // всплывающее значение под курсором при наведении на столбик
  const [tip, setTip] = useState(null)
  const boxRef = useRef(null)

  useEffect(() => {
    const load = () => fetch('/api/inflation')
      .then((r) => { if (!r.ok) throw new Error(`сервер ответил ${r.status}`); return r.json() })
      .then(setYears)
      .catch((e) => setError(`Не удалось загрузить инфляцию: ${e.message}`))
    load()
    // ряд меняется раз в год — перечислять раз в час достаточно
    const id = setInterval(load, 60 * 60 * 1000)
    return () => clearInterval(id)
  }, [])

  // лог-шкала: границы — от минимального (не ниже 1%) до максимального
  const scale = useMemo(() => {
    const vals = years.map((y) => y.rate).filter((v) => v > 0)
    if (vals.length < 2) return null
    const lo = Math.log10(Math.max(Math.min(...vals), 1))
    const hi = Math.log10(Math.max(...vals))
    return { lo, hi }
  }, [years])

  if (error) return <section className="inflation"><div className="error">{error}</div></section>
  if (!years.length) return <section className="inflation"><p className="hint">Загрузка инфляции…</p></section>

  const last = years[years.length - 1]
  const peak = years.reduce((a, b) => (b.rate > a.rate ? b : a))
  const plotW = W - PAD.left - PAD.right
  const plotH = H - PAD.top - PAD.bottom
  const bw = (plotW / years.length) * 0.72          // ширина столбика
  const x = (i) => PAD.left + (i + 0.5) * (plotW / years.length)
  const y = (v) => {
    const lv = Math.log10(Math.max(v, 1))
    return PAD.top + (1 - (lv - scale.lo) / (scale.hi - scale.lo)) * plotH
  }
  // decade-ориентиры: 4 (цель ЦБ), 10, 100, 1000 — по лог-шкале ровно деления
  const ticks = [4, 10, 100, 1000].filter((v) => v >= Math.pow(10, scale.lo) && v <= Math.pow(10, scale.hi))
  // подписи лет: каждый пятый год и последний
  const xLabels = years.filter((yr, i) => (Number(yr.year) % 5 === 0) || yr === last)

  // тултип показываем над верхушкой столбика: viewBox-координаты пересчитываем
  // в пиксели по масштабу svg, координаты svg — в координаты карточки
  function showTip(e, yr, i) {
    const svg = e.currentTarget.ownerSVGElement
    const sr = svg.getBoundingClientRect()
    const cr = boxRef.current.getBoundingClientRect()
    const k = sr.width / W
    const topPx = sr.top - cr.top + y(yr.rate) * k
    const xPx = sr.left - cr.left + x(i) * k
    setTip({
      year: yr.year, rate: yr.rate,
      left: Math.min(Math.max(xPx - 44, 4), cr.width - 96),
      top: Math.max(topPx - 34, 2),
    })
  }

  return (
    <section className="inflation" aria-label="Официальная инфляция в России" ref={boxRef}>
      <div className="inf-head">
        <h2>📈 Инфляция в России, % к предыдущему году</h2>
        <div className="inf-now">
          <span className="inf-peak" title={`пик за всю наблюдаемую историю`}>
            пик: {peak.year} — {fmt(peak.rate)}%
          </span>
          <b>{fmt(last.rate)}%</b>
          <span>{last.year} год</span>
        </div>
      </div>

      <svg viewBox={`0 0 ${W} ${H}`} className="inf-chart" role="img"
           aria-label="Столбики годовой инфляции по годам">
        {ticks.map((v) => (
          <g key={v}>
            <line x1={PAD.left} x2={W - PAD.right} y1={y(v)} y2={y(v)} className="inf-grid" />
            <text x={PAD.left - 5} y={y(v) + 3} textAnchor="end" className="inf-tick">{v}%</text>
          </g>
        ))}
        {/* линия цели ЦБ — 4% */}
        <line x1={PAD.left} x2={W - PAD.right} y1={y(4)} y2={y(4)} className="inf-target" />
        {years.map((yr, i) => (
          <rect key={yr.year}
                x={x(i) - bw / 2} width={bw}
                y={y(yr.rate)} height={Math.max(H - PAD.bottom - y(yr.rate), 1)}
                className={yr === last ? 'inf-bar inf-bar-last' : 'inf-bar'}
                rx={1.5}
                aria-label={`${yr.year}: ${fmt(yr.rate)}%`}
                onMouseMove={(e) => showTip(e, yr, i)}
                onMouseLeave={() => setTip(null)} />
        ))}
        {xLabels.map((yr) => (
          <text key={`l${yr.year}`} x={x(years.indexOf(yr))} y={H - 6}
                textAnchor="middle" className="inf-year">{yr.year}</text>
        ))}
      </svg>
      {tip && (
        <div className="inf-tip" style={{ left: tip.left, top: tip.top }}>
          <b>{tip.year}</b> · {fmt(tip.rate)}%
        </div>
      )}

      <p className="hint">
        Росстат (годовой ИПЦ), шкала логарифмическая — иначе 1993-й сплющил бы всё остальное.
        2026: прогноз ЦБ — 7,1–7,6% (по итогам года ещё не опубликован).
      </p>
    </section>
  )
}
