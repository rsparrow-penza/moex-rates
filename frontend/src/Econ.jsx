// Econ.jsx — блок «Экономика»: шесть практических срезовmacroэкономики РФ.
//
// 1. Ставки в экономике (ЦБ, месячные): кредиты юрлицам/физлицам, вклады.
//    Смотрим не на ключевую, а на то, дошла ли она до бизнеса и людей.
// 2. Вклады топ-10 (ЦБ, месячные): сколько десятки дают за короткие деньги.
// 3. Кривая ОФЗ (ММВБ, дневная): доходности к погашению по корзинам сроков
//    — рынок денег длинного конца; плюс наклон (длин минус короткий).
// 4. Реальный сектор (Росстат, месячные/квартальные): ИПП, безработица,
//    номинальная зарплата, реальные располагаемые доходы.
// 5. Розничная торговля — ряд ручной правки (пост стабильной выгрузки нет).
// 6. Федеральный бюджет — срез ручной правки (Минфин режет автоматические
//    запросы), заливается scripts/update_budget.py.
//
// Компактный формат: чипы-фильтры для ставок, таблица для ОФЗ, плитка
// «значение + г/г + спарклайн» для реального сектора, таблица бюджета.

import { useEffect, useMemo, useRef, useState } from 'react'

const W = 720
const PAD = { top: 12, right: 14, bottom: 20, left: 46 }

// период источника — 'YYYY-MM' (месяц/квартал) или 'YYYY-MM-DD' (ОФЗ);
// к моменту на оси X приводим к дате первого числа/дня
const stamp = (p) => {
  const iso = p.length === 7 ? `${p}-01` : p
  return new Date(`${iso}T12:00+03:00`).getTime()
}

const fmt1 = (n) => (Math.abs(n) < 10 ? n.toFixed(1) : Math.round(n).toString()).replace('.', ',')
const fmt2 = (n) => n.toFixed(2).replace('.', ',')

// г/г по рядам: последний период против того же месяца год назад
function yoy(points, months = 12) {
  if (!points || points.length <= months) return null
  const last = points[points.length - 1]
  const prev = points[points.length - 1 - months]
  if (!prev || !prev.value) return null
  return (last.value / prev.value - 1) * 100
}

// Спарклайн: маленькая линия без осей, для плитки реального сектора
function Spark({ points, width = 120, height = 30, caption = '' }) {
  if (!points || points.length < 2) return null
  const vals = points.map((p) => p.value)
  const lo = Math.min(...vals), hi = Math.max(...vals)
  // подсказка наведения: чем дышал ряд на показанном окне
  const info = `${caption} · ${points[0].period}—${points[points.length - 1].period}: `
    + `мин ${fmt1(lo)}, макс ${fmt1(hi)}, сейчас ${fmt1(vals[vals.length - 1])}`
  const x = (i) => (i / (points.length - 1)) * (width - 2) + 1
  const y = (v) => height - 2 - ((v - lo) / (hi - lo || 1)) * (height - 4)
  return (
    <svg width={width} height={height} className="ec-spark">
      <title>{info}</title>
      <polyline points={points.map((p, i) => `${x(i)},${y(p.value)}`).join(' ')} />
    </svg>
  )
}

// Линейный график % для ставок: общая вертикальная шкала.
// При наведении — вертикальная линия на ближайший месяц и окошко со
// значением каждого включённого ряда (как на основном графике курсов).
function LineChart({ series, labels = {} }) {
  const svgRef = useRef(null)
  const [tip, setTip] = useState(null)
  const all = series.flatMap((s) => s.points)
  if (all.length < 2) return null
  const t0 = Math.min(...all.map((p) => stamp(p.period)))
  const t1 = Math.max(...all.map((p) => stamp(p.period)))
  const vals = all.map((p) => p.value)
  const lo = Math.min(...vals), hi = Math.max(...vals)
  const yLo = lo - (hi - lo) * 0.1 - 0.5
  const yHi = hi + (hi - lo) * 0.1 + 0.5
  const x = (t) => PAD.left + ((t - t0) / (t1 - t0 || 1)) * (W - PAD.left - PAD.right)
  const y = (v) => PAD.top + (1 - (v - yLo) / (yHi - yLo)) * (220 - PAD.top - PAD.bottom)

  // все месяцы, которые есть хотя бы у одной серии, — по ним и щёлкает наведение
  const months = [...new Set(all.map((p) => p.period))].sort()
  const valueAt = new Map(series.map((s) => [s.key, new Map(s.points.map((p) => [p.period, p.value]))]))

  const onMove = (e) => {
    const rect = svgRef.current.getBoundingClientRect()
    const px = ((e.clientX - rect.left) / rect.width) * W
    const ratio = (px - PAD.left) / (W - PAD.left - PAD.right)
    if (ratio < -0.02 || ratio > 1.02) { setTip(null); return }
    const ts = t0 + ratio * (t1 - t0)
    // ближайший месяц по оси времени
    let best = months[0]
    for (const m of months)
      if (Math.abs(stamp(m) - ts) < Math.abs(stamp(best) - ts)) best = m
    // рядом с текущим значением — насколько оно выше/ниже того же месяца
    // прошлого года (в процентных пунктах): «18,11% · +2,30 п.п. г/г»
    const prevYear = (m) => `${+m.slice(0, 4) - 1}${m.slice(4)}`
    const rows = series
      .map((s) => {
        const map = valueAt.get(s.key)
        const value = map.get(best)
        const then = map.get(prevYear(best))
        return { key: s.key, color: s.color, label: labels[s.key] ?? s.label, value, yoy: then != null ? value - then : null }
      })
      .filter((r) => r.value != null)
      .sort((a, b) => b.value - a.value)
    if (!rows.length) { setTip(null); return }
    const cssX = (x(stamp(best)) / W) * rect.width   // px уже занят: координата viewBox
    const flip = cssX > rect.width * 0.55
    // Правая половина графика — ставим окно ЛЕВЕЕ курсора (иначе оно
    // залезает за край карточки и обрезатья): translateX(-100%) от точки.
    setTip({
      month: best, rows, lineX: x(stamp(best)), flip,
      cx: flip ? cssX - 12 : cssX + 12,
      cy: Math.max(8, Math.min(e.clientY - rect.top + 14,
                               rect.height - 30 - 26 * rows.length)),
    })
  }

  // подписи годовщин: каждый январь
  const years = []
  const seen = new Set()
  months.forEach((m) => {
    if (m.slice(5, 7) === '01' && !seen.has(m.slice(0, 4))) { seen.add(m.slice(0, 4)); years.push(m) }
  })

  const H = 220
  const MONTS_RU = ['янв', 'фев', 'мар', 'апр', 'май', 'июн', 'июл', 'авг', 'сен', 'окт', 'ноя', 'дек']
  const monthLabel = (m) => `${MONTS_RU[+m.slice(5, 7) - 1]} ${m.slice(0, 4)}`

  return (
    <div className="ec-chartwrap">
      <svg ref={svgRef} viewBox={`0 0 ${W} ${H}`} className="ec-chart"
           onMouseMove={onMove} onMouseLeave={() => setTip(null)}>
        {[yHi - (yHi - yLo) * 0.1, (yLo + yHi) / 2, lo].map((v, i) => (
          <g key={i}>
            <line x1={PAD.left} x2={W - PAD.right} y1={y(v)} y2={y(v)} className="ec-guide" />
            <text x={PAD.left - 6} y={y(v) + 3} textAnchor="end" className="ec-label">{fmt1(v)}</text>
          </g>
        ))}
        {years.map((m) => (
          <g key={m}>
            <line x1={x(stamp(m))} x2={x(stamp(m))} y1={PAD.top} y2={H - PAD.bottom} className="ec-guide" />
            <text x={x(stamp(m))} y={H - 6} textAnchor="middle" className="ec-label">{m.slice(0, 4)}</text>
          </g>
        ))}
        {series.map((s) => (
          <g key={s.key}>
            <polyline points={s.points.map((p) => `${x(stamp(p.period))},${y(p.value)}`).join(' ')}
                      stroke={s.color} />
            {s.points.length > 0 && (
              <circle cx={x(stamp(s.points[s.points.length - 1].period))}
                      cy={y(s.points[s.points.length - 1].value)} r={3} fill={s.color} />
            )}
          </g>
        ))}
        {/* точки всех серий в выбранном месяце — чтобы было видно, что сравниваем */}
        {tip && tip.rows.map((r) => {
          const s = series.find((q) => q.key === r.key)
          const p = s && s.points.find((q) => q.period === tip.month)
          return p ? <circle key={r.key} cx={x(stamp(p.period))} cy={y(p.value)} r={3.5}
                             fill={r.color} className="ec-hover-dot" /> : null
        })}
        {tip && <line className="chart-cross" x1={tip.lineX} x2={tip.lineX}
                     y1={PAD.top} y2={H - PAD.bottom} />}
      </svg>
      {tip && (
        <div className="chart-tip" style={{
          left: tip.cx, top: tip.cy,
          transform: tip.flip ? 'translateX(-100%)' : undefined,
        }}>
          <b className="tip-day">{monthLabel(tip.month)}</b>
          {tip.rows.map((r) => (
            <div key={r.key} className="tip-row">
              <i style={{ background: r.color }} />
              <span>{r.label}</span>
              <b>
                {fmt2(r.value)}%
                {r.yoy != null && (
                  <em className={r.yoy >= 0 ? 'ec-up' : 'ec-down'}
                      title={`год назад: ${fmt2(r.value - r.yoy)}%`}>
                    {' '}{r.yoy >= 0 ? '▲' : '▼'} {fmt2(Math.abs(r.yoy))} п.п.
                  </em>
                )}
              </b>
            </div>
          ))}
        </div>
      )}
    </div>
  )
}

const RATE_CHIPS = [
  { key: 'rate_corp_long', label: 'Кредиты бизнесу, длинные', color: '#4c6ef5' },
  { key: 'rate_corp_short', label: 'Кредиты бизнесу, короткие', color: '#845ef7' },
  { key: 'rate_retail_all', label: 'Кредиты людям', color: '#e8590c' },
  { key: 'rate_dep_fin_short', label: 'Вклады людей, короткие', color: '#20c997' },
  { key: 'dep10_91_180', label: 'Топ-10, 3–6 мес', color: '#f06595' },
]

export default function Econ({ rangeFrom = '2024-01' }) {
  const [meta, setMeta] = useState([])
  const [series, setSeries] = useState({})          // key -> points
  const [bonds, setBonds] = useState(null)
  const [budget, setBudget] = useState(null)
  const [keyRate, setKeyRate] = useState([])
  const [picked, setPicked] = useState(RATE_CHIPS.map((c) => c.key))
  const [error, setError] = useState('')

  useEffect(() => {
    const j = (u) => fetch(u).then((r) => { if (!r.ok) throw new Error(`${u} → ${r.status}`); return r.json() })
    const keys = [...RATE_CHIPS.map((c) => c.key), 'ippe_idx', 'unemp_rate', 'wage_nom',
                  'rddi_yoy', 'retail_turnover']

    const loadSeries = () => Promise.all(
      keys.map((k) => j(`/api/econ/series?key=${k}&from=${rangeFrom}`).catch(() => [])))
      .then((got) => {
        const filled = Object.fromEntries(keys.map((k, i) => [k, got[i]]))
        setSeries(filled)
        return got.some((g) => g.length > 1)
      })

    let retry = null
    const load = async () => {
      try {
        const [m, b, bg, kr] = await Promise.all([
          j('/api/econ/meta'), j('/api/econ/bonds'), j('/api/budget'), j('/api/key-rate'),
        ])
        setMeta(m); setBonds(b); setBudget(bg); setKeyRate(kr)
        const any = await loadSeries()
        // попали на стартовый seed бэкенда (ряды ещё не наполнены) —
        // одна повторная попытка через 20 секунд, без мученического ожидания
        if (!any) retry = setTimeout(loadSeries, 20000)
      } catch (e) {
        setError(`Не удалось загрузить экономику: ${e.message}`)
      }
    }
    load()
    // Обновление: ОФЗ — планировщик биржи раз в 12 часов, месячные ряды —
    // ЦБ/Росстат в окна публикаций. Перезапрашиваем каждые полчаса; в том
    // числе поэтому блок «догоняет» сам, если попал на стартовый seed
    // бэкенда (пока тот качает History ОФЗ, рядов ещё нет).
    const id = setInterval(() => {
      j('/api/econ/bonds').then(setBonds).catch(() => {})
      j('/api/budget').then(setBudget).catch(() => {})
      loadSeries().catch(() => {})
    }, 30 * 60 * 1000)
    return () => { clearInterval(id); clearTimeout(retry) }
  }, [rangeFrom])

  const rateSeries = useMemo(
    () => RATE_CHIPS.filter((c) => picked.includes(c.key))
      .map((c) => ({ ...c, points: series[c.key] || [] }))
      .filter((s) => s.points.length > 1),
    [picked, series])

  if (error) return <section className="econ"><div className="error">{error}</div></section>
  if (!meta.length) return <section className="econ"><p className="hint">Загрузка экономики…</p></section>

  const lastOf = (k) => {
    const p = series[k]
    return p && p.length ? p[p.length - 1] : null
  }

  // ——— ОФЗ: спред длинного конца к короткому и наклон ———
  const curve = (bonds?.curve || []).filter((c) => c.yield_prc != null)
  const shortEnd = curve.length ? curve[0] : null
  const longEnd = curve.length ? curve[curve.length - 1] : null
  const slope = shortEnd && longEnd ? longEnd.yield_prc - shortEnd.yield_prc : null

  // ——— реальный сектор: плитка ———
  const realTiles = [
    { key: 'ippe_idx', label: 'Промышленность', note: 'Индекс производства, 2023 = 100',
      unit: 'инд.', fmt: (v) => fmt1(v), yoy: yoy(series.ippe_idx) },
    { key: 'unemp_rate', label: 'Безработица', note: 'уровень, МОМ-методология',
      unit: '%', fmt: (v) => fmt2(v), yoy: null },
    { key: 'wage_nom', label: 'Зарплата', note: 'номинальная, ₽/мес',
      unit: '₽', fmt: (v) => Math.round(v).toLocaleString('ru-RU'), yoy: yoy(series.wage_nom) },
    { key: 'rddi_yoy', label: 'Реальные доходы', note: 'РРДД, % к тому же кварталу год назад',
      unit: '% г/г', fmt: (v) => fmt1(v - 100), yoy: null },
    { key: 'retail_turnover', label: 'Розница', note: 'оборот, млрд ₽ (ручной ввод)',
      unit: 'млрд', fmt: (v) => fmt1(v), yoy: yoy(series.retail_turnover) },
  ]

  // ——— бюджет ———
  const budgetRows = (budget?.rows || []).slice(-6).reverse()

  return (
    <section className="econ" aria-label="Экономика России">
      <h2>📈 Экономика</h2>
      <p className="cf-sub">
        Ставки для бизнеса и людей · кривая ОФЗ · реальный сектор · бюджет
        {' · '}данные ЦБ, ММВБ, Росстата
      </p>

      {/* ——— 1-2. ставки ——— */}
      <h3 className="ec-title">💹 Куда пошли ставки: кредиты и вклады</h3>
      <div className="ec-chips">
        {RATE_CHIPS.map((c) => (
          <button key={c.key} type="button"
                  className={`ec-chip${picked.includes(c.key) ? ' on' : ''}`}
                  style={{ '--c': c.color }}
                  title={(meta.find((m) => m.key === c.key) || {}).explain}
                  onClick={() => setPicked((p) =>
                    p.includes(c.key) ? p.filter((k) => k !== c.key) : [...p, c.key])}>
            {c.label}
          </button>
        ))}
      </div>
      {rateSeries.length > 0 && (
        <>
          <LineChart series={rateSeries} />
          <p className="ec-mini-hint">
            Наведи курсор на график — покажем ставку каждого включённого ряда
            за ближайший месяц и изменение к тому же месяцу год назад.
          </p>
        </>
      )}
      <p className="ec-mini-note">
        {lastOf('rate_corp_long') && (
          <>Кредит бизнесу на год+: <b>{fmt2(lastOf('rate_corp_long').value)}%</b>{' · '}</>
        )}
        {lastOf('rate_retail_all') && (<>кредит людям: <b>{fmt2(lastOf('rate_retail_all').value)}%</b>{' · '}</>)}
        {lastOf('rate_dep_fin_short') && (<>вклад людям: <b>{fmt2(lastOf('rate_dep_fin_short').value)}%</b>{' · '}</>)}
        {keyRate.length > 0 && (<>ключевая: <b>{fmt2(keyRate[keyRate.length - 1].rate)}%</b></>)}
      </p>

      {/* ——— 3. ОФЗ ——— */}
      {curve.length > 0 && (
        <>
          <h3 className="ec-title">
            🧱 Крива ОФЗ
            {slope != null && (
              <span className="ec-slope" title="доходность длинного выпуска минус короткого">
                наклон {slope >= 0 ? '+' : ''}{fmt1(slope)} п.п.
              </span>
            )}
          </h3>
          <table className="ec-table">
            <thead>
              <tr><th>Выпуск</th><th>Дата выпуска</th><th>Погашение</th><th>Купон, %</th>
                  <th>Цена, % ном.</th><th>Δ дня</th><th>Доходность</th></tr>
            </thead>
            <tbody>
              {curve.map((c) => (
                <tr key={c.secid}
                    title={`Номинал ${fmt1(c.face_rur ?? 1000)} ₽ · НКД ${
                      c.accrued != null ? fmt2(c.accrued) : '—'
                    } ₽ · объём выпуска ${
                      c.issue_size_mlrd != null ? fmt1(c.issue_size_mlrd) : '—'
                    } млрд ₽ · оборот сегодня ${
                      c.valtoday_rur != null ? fmt1(c.valtoday_rur / 1e6) : '0'
                    } млн ₽`}>
                  <td>{c.shortname}</td>
                  <td className="ec-mat">{c.issuedate ?? '—'}</td>
                  <td className="ec-mat">{c.matdate}</td>
                  <td className="ec-cpn">{c.coupon_prc != null ? `${fmt2(c.coupon_prc)}%` : '—'}</td>
                  <td className="ec-price">{c.price_prc != null ? fmt2(c.price_prc) : '—'}</td>
                  <td className={`ec-chg${(c.price_chg_prc ?? 0) > 0 ? ' up' : (c.price_chg_prc ?? 0) < 0 ? ' down' : ''}`}>
                    {c.price_chg_prc != null ? `${c.price_chg_prc > 0 ? '+' : ''}${fmt2(c.price_chg_prc)}` : '—'}
                  </td>
                  <td className="ec-yld">{fmt2(c.yield_prc)}%</td>
                </tr>
              ))}
            </tbody>
          </table>
          <p className="hint">
            Доходность к погашению по закрытию ММВБ (борт «Т+: Гособлигации»),
            снимок {bonds.snap_date}. Цена — последняя сделка в процентах от
            номинала (1000 ₽); в подсказке строки — НКД, объём выпуска и
            оборот дня. Наклон кривой — сколько рынок просит
            сверх коротких ставок за длинные деньги: пологая или перевёрнутая
            кривая — ожидание быстрого смягчения ЦБ, крутая — что низких
            ставок в обозримом будущем не будет.
          </p>
        </>
      )}

      {/* ——— 4-5. реальный сектор + розница ——— */}
      <h3 className="ec-title">🏭 Реальный сектор</h3>
      <div className="ec-tiles">
        {realTiles.map((t) => {
          const p = lastOf(t.key)
          if (!p) return (
            <div className="ec-tile" key={t.key}>
              <span className="ec-tile-label">{t.label}</span>
              <span className="ec-tile-empty">нет данных</span>
              <em className="ec-tile-note">{t.note}</em>
            </div>
          )
          const shown = p.value
          return (
            <div className="ec-tile" key={t.key}>
              <span className="ec-tile-label">{t.label}</span>
              <span className="ec-tile-value">
                {t.fmt(shown)}{t.unit && <i>{' '}{t.unit}</i>}
                {t.yoy != null && (
                  <b className={t.yoy >= 0 ? 'ec-up' : 'ec-down'} title="к тому же периоду год назад">
                    {' '}{t.yoy >= 0 ? '▲' : '▼'} {fmt1(Math.abs(t.yoy))}%
                  </b>
                )}
              </span>
              <Spark points={series[t.key].slice(-36)} caption={t.label} />
              <em className="ec-tile-note">{p.period} · {t.note}</em>
            </div>
          )
        })}
      </div>

      {/* ——— 6. бюджет ——— */}
      {budgetRows.length > 0 ? (
        <>
          <h3 className="ec-title">🏛️ Федеральный бюджет</h3>
          <table className="ec-table">
            <thead>
              <tr><th>Накоплено с начала года</th><th>Доходы</th><th>Нефтегаз</th>
                  <th>Расходы</th><th>Дефицит</th></tr>
            </thead>
            <tbody>
              {budgetRows.map((r) => (
                <tr key={r.period}>
                  <td>{r.period}</td>
                  <td>{r.revenue != null ? fmt1(r.revenue) : '—'}</td>
                  <td>{r.oil_gas != null ? fmt1(r.oil_gas) : '—'}</td>
                  <td>{r.expenditure != null ? fmt1(r.expenditure) : '—'}</td>
                  <td className={(r.deficit ?? 0) < 0 ? 'ec-down' : 'ec-up'}>
                    {r.deficit != null ? fmt1(r.deficit) : '—'}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
          <p className="hint">млрд ₽; {budget.as_of} · срез ручной:{' '}
            <code>TOP_BANKS_SECRET=… python3 backend/update_budget.py budget.json</code></p>
        </>
      ) : (
        <p className="hint">
          🏛️ Бюджетный срез пока не загружен: Минфин отдаёт выгрузку только
          браузеру. Залить цифры можно скриптом backend/update_budget.py
          (файл budget.json, тот же секрет, что у рейтинга банков).
        </p>
      )}

      <p className="hint">
        Ряды обновляются планировщиком: ставки ЦБ — 7–16 числа за прошлый
        месяц, ОФЗ — каждые 12 часов, Росстат — в течение месяца по мере
        публикации. Всё по отчётности регуляторов и биржи, без оценок «от себя».
      </p>
    </section>
  )
}
