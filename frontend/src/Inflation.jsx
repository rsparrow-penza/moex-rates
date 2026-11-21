// Inflation.jsx — блок «Официальная инфляция в России» с двумя режимами:
//
//   'year' — годовая (ИПЦ, % к предыдущему году) по данным Росстата
//     (агрегация World Bank API), ряд доступен с 1993 года;
//   'cum'  — накопительная: во сколько раз выросли цены с первого года
//     ряда. 1990–1992 у источника нет (данных СССР-периода в серии нет),
//     поэтому точка отсчёта — 1993, и блок об этом прямо говорит.
//
// Шкала ЛОГАРИФМИЧЕСКАЯ в обоих режимах: иначе гиперинфляционный 1993-й
// (874%) и ×сотни накопительной сплющивают всё остальное в полоску у нуля.

import { useEffect, useMemo, useState } from 'react'

const W = 720
const H = 190
const PAD = { top: 14, right: 10, bottom: 24, left: 40 }

const fmt = (n) => n.toFixed(1).replace('.', ',')
// индекс роста цен: ×9,7 / ×238 / ×2 385
const fmtMult = (v) => v >= 1000
  ? `×${Math.round(v).toLocaleString('ru-RU')}`
  : v >= 100
    ? `×${Math.round(v)}`
    : `×${v.toFixed(1).replace('.', ',')}`

export default function Inflation() {
  const [years, setYears] = useState([])
  const [error, setError] = useState('')
  const [mode, setMode] = useState('year')      // 'year' | 'cum'

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

  // Накопительный индекс: цепочное произведение (1 + r/100)
  const cum = useMemo(() => {
    let idx = 1
    return years.map((y) => {
      idx *= 1 + y.rate / 100
      return { year: y.year, index: idx }
    })
  }, [years])

  // Единый «ряд» для текущего режима: значение и подпись на столбике
  const series = useMemo(() => (mode === 'year'
    ? years.map((y) => ({ year: y.year, v: y.rate, label: `${fmt(y.rate)}%` }))
    : cum.map((c) => ({ year: c.year, v: c.index, label: fmtMult(c.index) }))),
    [years, cum, mode])

  if (error) return <section className="inflation"><div className="error">{error}</div></section>
  if (!series.length || series.length < 2) {
    return <section className="inflation"><p className="hint">Загрузка инфляции…</p></section>
  }

  const first = series[0]
  const last = series[series.length - 1]
  const peak = years.reduce((a, b) => (b.rate > a.rate ? b : a))
  const plotW = W - PAD.left - PAD.right
  const plotH = H - PAD.top - PAD.bottom
  const colW = plotW / series.length
  const bw = colW * 0.72
  const x = (i) => PAD.left + (i + 0.5) * colW

  // лог-шкала по значениям ряда; минимум не ниже 1 (1% или ×1)
  const lo = Math.log10(Math.max(Math.min(...series.map((s) => s.v)), 1))
  const hi = Math.log10(Math.max(...series.map((s) => s.v)))
  const y = (v) => PAD.top + (1 - (Math.log10(Math.max(v, 1)) - lo) / (hi - lo || 1)) * plotH

  const ticks = mode === 'year'
    ? [4, 10, 100, 1000].filter((v) => v >= Math.pow(10, lo) && v <= Math.pow(10, hi))
    : [2, 10, 100, 1000].filter((v) => v >= Math.pow(10, lo) && v <= Math.pow(10, hi))
  const tickLabel = (v) => (mode === 'year' ? `${v}%` : fmtMult(v))
  const xLabels = series.filter((s) => Number(s.year) % 5 === 0 || s === last)

  return (
    <section className="inflation" aria-label="Официальная инфляция в России">
      <div className="inf-head">
        <h2>📈 Инфляция в России</h2>
        <div className="inf-now">
          <div className="seg" role="radiogroup" aria-label="Режим инфляции">
            <label className={mode === 'year' ? 'seg-on' : ''}>
              <input type="radio" name="inflation-mode" value="year"
                     checked={mode === 'year'} onChange={(e) => setMode(e.target.value)} />
              по годам
            </label>
            <label className={mode === 'cum' ? 'seg-on' : ''}>
              <input type="radio" name="inflation-mode" value="cum"
                     checked={mode === 'cum'} onChange={(e) => setMode(e.target.value)} />
              накопительная
            </label>
          </div>
          {mode === 'year'
            ? <>
                <span className="inf-peak">пик: {peak.year} — {fmt(peak.rate)}%</span>
                <b>{fmt(last.v)}%</b>
                <span>{last.year} год</span>
              </>
            : <>
                <b>{fmtMult(last.v)}</b>
                <span>с {first.year} года</span>
              </>}
        </div>
      </div>

      <svg viewBox={`0 0 ${W} ${H}`} className="inf-chart" role="img"
           aria-label={mode === 'year' ? 'Годовая инфляция по годам' : 'Накопительный рост цен с 1993 года'}>
        {ticks.map((v) => (
          <g key={v}>
            <line x1={PAD.left} x2={W - PAD.right} y1={y(v)} y2={y(v)} className="inf-grid" />
            <text x={PAD.left - 5} y={y(v) + 3} textAnchor="end" className="inf-tick">{tickLabel(v)}</text>
          </g>
        ))}
        {mode === 'year' &&
          <line x1={PAD.left} x2={W - PAD.right} y1={y(4)} y2={y(4)} className="inf-target" />}
        {series.map((s, i) => (
          <rect key={s.year}
                x={x(i) - bw / 2} width={bw}
                y={y(s.v)} height={Math.max(H - PAD.bottom - y(s.v), 1)}
                className={i === series.length - 1 ? 'inf-bar inf-bar-last' : 'inf-bar'}
                rx={1.5}>
            <title>{mode === 'year'
              ? `${s.year}: ${s.label}`
              : `${s.year}: ${s.label} цен с ${first.year}`}</title>
          </rect>
        ))}
        {xLabels.map((s) => (
          <text key={`l${s.year}`} x={x(series.indexOf(s))} y={H - 6}
                textAnchor="middle" className="inf-year">{s.year}</text>
        ))}
      </svg>

      <p className="hint">
        {mode === 'year'
          ? 'Росстат (годовой ИПЦ), шкала логарифмическая — иначе 1993-й сплющил бы всё остальное. 2026: прогноз ЦБ — 7,1–7,6% (по итогам года ещё не опубликован).'
          : 'Цепочное произведение годовых ИПЦ: во сколько раз выросли цены с 1993 года — первого года доступного ряда (данных за 1990–1992 у источника нет).'}
      </p>
    </section>
  )
}
