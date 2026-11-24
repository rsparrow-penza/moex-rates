// TopBanks.jsx — блок «Топ-10 банков России» + «Избранные банки».
//
// Данные живут в бэкенде (таблица top_banks, колонка grp различает группы),
// блок обновляемый: GET /api/top-banks отдаёт текущий срез, POST
// /api/top-banks с токеном его заменяет — без пересборки фронта. Источник
// рейтинга (указатель ЦБ) открытого API не имеет, поэтому обновляют
// вручную раз в квартал; избранные (Новиком, ББР, Хлынов, Кузнецкий) —
// карточки banki.ru.

import { useEffect, useState } from 'react'

const fmtInt = (n) => Math.round(n).toLocaleString('ru-RU')

// Активы хранятся в трлн ₽; для мелких банков правдоподобнее млрд.
const fmtAssets = (trln) => trln >= 1
  ? `${trln.toFixed(1).replace('.', ',')} трлн`
  : `${fmtInt(trln * 1000)} млрд`
// Прибыль — млрд ₽; сотни миллионов показываем в млн, знак сохраняем.
const fmtProfit = (mlrd) => Math.abs(mlrd) < 1
  ? `${fmtInt(mlrd * 1000)} млн`
  : (+mlrd.toFixed(1)).toLocaleString('ru-RU')

export default function TopBanks() {
  const [data, setData] = useState(null)      // {as_of, updated_at, banks}
  const [error, setError] = useState('')

  useEffect(() => {
    const load = () => fetch('/api/top-banks')
      .then((r) => { if (!r.ok) throw new Error(`сервер ответил ${r.status}`); return r.json() })
      .then(setData)
      .catch((e) => setError(`Не удалось загрузить рейтинг банков: ${e.message}`))
    load()
    // срез меняется раз в квартал, но перечитываем как все блоки — за час
    const id = setInterval(load, 60 * 60 * 1000)
    return () => clearInterval(id)
  }, [])

  if (error) return <section className="top-banks"><div className="error">{error}</div></section>
  if (!data) return <section className="top-banks"><p className="hint">Загрузка рейтинга банков…</p></section>

  const top = data.banks.filter((b) => b.grp !== 'extra')
  const extra = data.banks.filter((b) => b.grp === 'extra')

  return (
    <section className="top-banks" aria-label="Банки России">
      <h2>🏦 Топ-10 банков России</h2>
      <p className="cf-sub">
        Активы и прибыль · указатель ЦБ на {data.as_of}
        {' · '}обновлено {new Date(data.updated_at).toLocaleDateString('ru-RU')}
      </p>

      <table className="tb-table">
        <thead>
          <tr>
            <th>#</th>
            <th>Банк</th>
            <th>Активы</th>
            <th>N1.0, %</th>
            <th>Прибыль за год</th>
            <th>СИ</th>
          </tr>
        </thead>
        <tbody>
          {top.map((b) => (
            <tr key={b.name}>
              <td className="tb-num">{b.rank}</td>
              <td>{b.name}</td>
              <td className="tb-assets">{fmtAssets(b.assets)}</td>
              {/* N1.0 подсвечиваем по запасу над минимумом 8% */}
              <td className={b.n1 >= 12 ? 'tb-n1 tb-n1-strong' : 'tb-n1'}>
                {b.n1 > 0 ? b.n1.toFixed(1).replace('.', ',') : '—'}
              </td>
              <td>{fmtProfit(b.profit)}</td>
              <td className="tb-siber">{b.siber ? '✓' : ''}</td>
            </tr>
          ))}
        </tbody>
      </table>

      {extra.length > 0 && (
        <>
          <h3 className="tb-extra-title">📍 Избранные банки</h3>
          <table className="tb-table">
            <thead>
              <tr>
                <th>Место</th>
                <th>Банк</th>
                <th>Активы</th>
                <th>N1.0, %</th>
                <th>Прибыль за год</th>
                <th>СИ</th>
              </tr>
            </thead>
            <tbody>
              {extra.map((b) => (
                <tr key={b.name}>
                  <td className="tb-num">{b.place ?? ''}</td>
                  <td>{b.name}</td>
                  <td className="tb-assets">{fmtAssets(b.assets)}</td>
                  <td className="tb-n1">{b.n1 > 0 ? b.n1.toFixed(1).replace('.', ',') : '—'}</td>
                  <td className={b.profit < 0 ? 'tb-loss' : ''}>{fmtProfit(b.profit)}</td>
                  <td className="tb-siber">{b.siber ? '✓' : ''}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </>
      )}

      <p className="hint">
        Норматив N1.0 — достаточность общего капитала (минимум ЦБ 8%). СИ — системно
        значимые банки по перечню регулятора. Топ-10 — указатель ЦБ, избранные банки
        (место — по активам в РФ) — карточки banki.ru; значения округлены. Рейтинг
        обновляется через /api/top-banks (POST с токеном).
      </p>
    </section>
  )
}
