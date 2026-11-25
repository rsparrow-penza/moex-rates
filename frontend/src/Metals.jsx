// Metals.jsx — блок «Металлы на бирже»: ближайшие контракты ФОРТС и мир.
//
// Данные: GET /api/metals — дневные цены в $ за тройскую унцию с двух
// площадок: ММВБ ФОРТС (GOLD/SILV/PLT/PLD, ряд закрывается по московскому
// дню) и COMEX/NYMEX через Yahoo. Разница между ними — базис: сколько
// российский контракт стоит относительно мирового.
//
// Контракт квартальный: когда ближайший истекает, бэкенд сам подбирает
// следующий (metal_contract), и ряд продолжается новым контрактом — в месте
// переклейки возможен скачок на величину контанго, это отмечено в hint.

import { useEffect, useMemo, useState } from 'react'

const fmt2 = (n) => n == null ? '—' : n.toFixed(2).replace('.', ',')
const fmt1 = (n) => n == null ? '—' : (Math.abs(n) >= 100
  ? Math.round(n).toLocaleString('ru-RU')
  : n.toFixed(2).replace('.', ','))

// Тройская унция — 31,1034768 г; перевод $/унция → ₽/грамм идёт по курсу ЦБ
const OZ_IN_G = 31.1034768

const COLORS = { AU: '#fab005', AG: '#ced4da', PT: '#74c0fc', PD: '#f783ac' }

// Спарклайн: линия цены за окно + последняя точка
function Spark({ points, color = '#fab005', width = 132, height = 30 }) {
  if (!points || points.length < 2) return <span className="mt-empty">—</span>
  const vals = points.map((p) => p.value)
  const lo = Math.min(...vals), hi = Math.max(...vals)
  const x = (i) => (i / (points.length - 1)) * (width - 2) + 1
  const y = (v) => height - 2 - ((v - lo) / (hi - lo || 1)) * (height - 4)
  const last = points[points.length - 1]
  return (
    <svg width={width} height={height} className="mt-spark">
      <title>{`${points[0].date}—${last.date}: мин ${fmt1(lo)}, макс ${fmt1(hi)}, сейчас ${fmt1(last.value)} $/унция`}</title>
      <polyline points={points.map((p, i) => `${x(i)},${y(p.value)}`).join(' ')} stroke={color} />
      <circle cx={x(points.length - 1)} cy={y(last.value)} r={2.5} fill={color} />
    </svg>
  )
}

export default function Metals() {
  const [data, setData] = useState(null)
  const [usd, setUsd] = useState(null)          // курс ЦБ, ₽ за $ — для ₽/грамм
  const [error, setError] = useState('')

  useEffect(() => {
    const load = () => Promise.all([
      fetch('/api/metals').then((r) => { if (!r.ok) throw new Error(`сервер ответил ${r.status}`); return r.json() }),
      fetch('/api/rates?currencies=USD_CBR&from=' + new Date(Date.now() - 9 * 864e5).toISOString().slice(0, 10))
        .then((r) => (r.ok ? r.json() : [])).catch(() => []),
    ])
      .then(([m, usdRows]) => {
        setData(m)
        const cbr = usdRows.filter((x) => x.source === 'CBR' && x.currency === 'USD_CBR')
        if (cbr.length) setUsd(cbr[cbr.length - 1].rate)
      })
      .catch((e) => setError(`Не удалось загрузить металлы: ${e.message}`))
    load()
    // ФОРТС обновляется в течение дня, мир — почти круглосуточно: раз в час
    const id = setInterval(load, 60 * 60 * 1000)
    return () => clearInterval(id)
  }, [])

  // по каждому металлу — последняя цена ФОРТС и мира, Δ за день, базис
  const rows = useMemo(() => {
    if (!data) return []
    const contract = Object.fromEntries((data.contracts || []).map((c) => [c.metal, c]))
    return (data.metals || []).map((m) => {
      const pts = data.series[m.metal] || []
      const withF = pts.filter((p) => p.forts != null)
      const withW = pts.filter((p) => p.world != null)
      const last = withF[withF.length - 1]
      const prev = withF[withF.length - 2]
      const world = withW[withW.length - 1]
      const worldPrev = withW[withW.length - 2]
      return {
        ...m,
        contract: contract[m.metal],
        price: last?.forts,
        dayPct: last && prev ? (last.forts / prev.forts - 1) * 100 : null,
        worldPrice: world?.world,
        worldDayPct: world && worldPrev ? (world.world / worldPrev.world - 1) * 100 : null,
        basis: last && world ? (last.forts / world.world - 1) * 100 : null,
        gramRub: last && usd ? (last.forts * usd) / OZ_IN_G : null,
        spark: withF.slice(-90).map((p) => ({ date: p.date, value: p.forts })),
      }
    }).filter((r) => r.price != null)
  }, [data, usd])

  if (error) return <section className="metals"><div className="error">{error}</div></section>
  if (!data) return <section className="metals"><p className="hint">Загрузка котировок металлов…</p></section>
  if (!rows.length) return null

  return (
    <section className="metals" aria-label="Биржевые цены на металлы">
      <h2>🪙 Металлы на бирже</h2>
      <p className="cf-sub">
        ФОРТС (MOEX) · $ за тройскую унцию · ближайшие контракты
        {usd ? ` · ₽/грамм по курсу ЦБ ${fmt2(usd)} ₽/$` : ''}
      </p>

      <table className="mt-table">
        <thead>
          <tr>
            <th>Металл</th><th>Контракт ФОРТС</th><th>Цена, $/унц</th><th>За день</th>
            <th>Мир, $/унц</th><th>Базис</th><th>₽/грамм</th><th>90 дней</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((r) => (
            <tr key={r.metal}>
              <td className="mt-name">
                <i className="mt-dot" style={{ background: COLORS[r.metal] }} />
                {r.label}
                <em>{r.world_label}</em>
              </td>
              <td className="mt-ct" title={`последняя торговля ${r.contract?.lasttrade ?? ''}`}>
                {r.contract?.shortname ?? '—'}
              </td>
              <td className="mt-price">{fmt1(r.price)}</td>
              <td className={`mt-chg${r.dayPct > 0 ? ' up' : r.dayPct < 0 ? ' down' : ''}`}>
                {r.dayPct == null ? '—' : `${r.dayPct > 0 ? '+' : ''}${fmt2(r.dayPct)}%`}
              </td>
              <td className="mt-world"
                  title={`мир: ${r.world} · ${r.worldDayPct == null ? 'Δ дня нет' : `Δ ${fmt2(r.worldDayPct)}%`}`}>
                {fmt1(r.worldPrice)}
              </td>
              <td className={`mt-basis${r.basis > 0 ? ' up' : r.basis < 0 ? ' down' : ''}`}
                  title="цена ФОРТС относительно мировой: плюс — российский контракт дороже">
                {r.basis == null ? '—' : `${r.basis > 0 ? '+' : ''}${fmt2(r.basis)}`}
              </td>
              <td className="mt-gram">{r.gramRub == null ? '—' : fmt1(r.gramRub)}</td>
              <td><Spark points={r.spark} color={COLORS[r.metal]} /></td>
            </tr>
          ))}
        </tbody>
      </table>

      <p className="hint">
        Цены фьючерсов в $ за тройскую унцию (31,103 г); ₽/грамм — по курсу ЦБ
        на доллар. ММВБ не отдаёт через публичный API спот-котировки ГЦМ
        (слитки), поэтому берутся фьючерсы ФОРТС: контракт живой, но квартальный —
        при переклейке ряда возможен скачок на величину контанго. Мировая цена —
        COMEX (золото, серебро) и NYMEX (платина, палладий); базис близок к нулю —
        значит российский рынок в теме, сильный плюс — локальный дефицит металла.
      </p>
    </section>
  )
}
