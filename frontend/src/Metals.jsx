// Metals.jsx — вкладка «Металлы на бирже»: ближайшие контракты ФОРТС и мир.
//
// Данные: GET /api/metals — дневные цены с двух площадок: ММВБ ФОРТС
// (GOLD/SILV/PLT/PLD в $ за тройскую унцию, COPPER/ALUM в $ за тонну) и
// мир (COMEX/NYMEX/LME через Yahoo). Бэкенд приводит мировую котировку к
// нашей единице (медь на COMEX — в $ за фунт), поэтому базис считается
// честно: сколько российский контракт стоит относительно мирового.
//
// Никель и цинк на ФОРТС есть, но не добавлены: 37–67 сделок в день —
// базис по ним был бы шумом, а не сигналом.
//
// Контракт квартальный: когда ближайший истекает, бэкенд сам подбирает
// следующий (metal_contract), и ряд продолжается новым контрактом — в месте
// переклейки возможен скачок на величину контанго, это отмечено в hint.

import { useEffect, useMemo, useState } from 'react'
import Spark from './Spark.jsx'

const fmt2 = (n) => n == null ? '—' : n.toFixed(2).replace('.', ',')
const fmt1 = (n) => n == null ? '—' : (Math.abs(n) >= 100
  ? Math.round(n).toLocaleString('ru-RU')
  : n.toFixed(2).replace('.', ','))

// Тройская унция — 31,1034768 г; цветные котируются тоннами. Перевод
// $/единица → ₽ за единицу (грамм для драгметаллов, тонна для цветных)
// идёт по курсу ЦБ на доллар
const OZ_IN_G = 31.1034768

const COLORS = { AU: '#fab005', AG: '#ced4da', PT: '#74c0fc', PD: '#f783ac' }

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
        perUnit: last && usd ? (last.forts * usd) / (m.unit === 'унция' ? OZ_IN_G : 1) : null,
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
        ФОРТС (MOEX) · драгметаллы в $ за тройскую унцию, медь и алюминий — в $ за тонну
        · ближайшие контракты{usd ? ` · ₽ по курсу ЦБ ${fmt2(usd)} ₽/$` : ''}
      </p>

      <table className="mt-table">
        <thead>
          <tr>
            <th>Металл</th><th>Контракт ФОРТС</th><th>Цена, $</th><th>За день</th>
            <th>Мир, $</th><th>Базис</th><th>₽</th><th>90 дней (даты: мм.гг)</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((r) => (
            <tr key={r.metal}>
              <td className="mt-name" title={`мир: ${r.world} (${r.world_label})`}>
                <i className="mt-dot" style={{ background: COLORS[r.metal] }} />
                {r.label}
                <em>{r.unit === 'унция' ? 'унция' : 'тонна'} · {r.world_label}</em>
              </td>
              <td className="mt-ct" title={`последняя торговля ${r.contract?.lasttrade ?? ''}`}>
                {r.contract?.shortname ?? '—'}
              </td>
              <td className="mt-price">{fmt1(r.price)}</td>
              <td className={`mt-chg${r.dayPct > 0 ? ' up' : r.dayPct < 0 ? ' down' : ''}`}>
                {r.dayPct == null ? '—' : `${r.dayPct > 0 ? '+' : ''}${fmt2(r.dayPct)}%`}
              </td>
              <td className="mt-world"
                  title={`${r.world_label} · ${r.world}${r.worldDayPct == null ? '' : ` · Δ дня ${fmt2(r.worldDayPct)}%`}${r.unit === 'тонна' && r.world === 'HG=F' ? ' (котируется в $/фунт, пересчитано в $/тонну)' : ''}`}>
                {fmt1(r.worldPrice)}
              </td>
              <td className={`mt-basis${r.basis > 0 ? ' up' : r.basis < 0 ? ' down' : ''}`}
                  title="цена ФОРТС относительно мировой: плюс — российский контракт дороже">
                {r.basis == null ? '—' : `${r.basis > 0 ? '+' : ''}${fmt2(r.basis)}`}
              </td>
              <td className="mt-gram" title={r.unit === 'унция' ? '₽ за 1 грамм' : '₽ за 1 тонну'}>
                {r.perUnit == null ? '—' : fmt1(r.perUnit)}
              </td>
              <td><Spark points={r.spark} color={COLORS[r.metal]} unit={r.unit === 'унция' ? 'унция' : 'тонну'} /></td>
            </tr>
          ))}
        </tbody>
      </table>

      <p className="hint">
        Драгметаллы — $ за тройскую унцию (31,103 г), медь и алюминий — $ за
        тонну. В колонке ₽: грамм для драгметаллов, тонна для цветных — всё по
        курсу ЦБ на доллар. Мировая цена меди котируется в $ за фунт (COMEX) и
        пересчитана в $ за тонну на входе, чтобы базис был сравним.
        ММВБ не отдаёт через публичный API спот-котировки ГЦМ (слитки), поэтому
        берутся фьючерсы ФОРТС: контракт живой, но квартальный — при переклейке
        ряда возможен скачок на величину контанго. Базис близок к нулю —
        российский рынок в теме мира; устойчивый плюс — локальный дефицит
        металла или разрыв в логистике.
      </p>
    </section>
  )
}
