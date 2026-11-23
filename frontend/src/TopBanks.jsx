// TopBanks.jsx — блок «Топ-10 банков России».
//
// Данные живут в бэкенде (таблица top_banks), блок обновляемый:
// GET /api/top-banks отдаёт текущий срез, POST /api/top-banks с токеном
// его заменяет — без пересборки фронта. Источник рейтинга (указатель ЦБ)
// открытого API не имеет, поэтому обновляют вручную раз в квартал;
// когда ЦБ даст JSON — останется научить бэкенд его опрашивать.

import { useEffect, useState } from 'react'

const fmtInt = (n) => Math.round(n).toLocaleString('ru-RU')

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

  return (
    <section className="top-banks" aria-label="Топ-10 российских банков">
      <h2>🏦 Топ-10 банков России</h2>
      <p className="cf-sub">
        Активы, капитал и прибыль · указатель ЦБ на {data.as_of}
        {' · '}обновлено {new Date(data.updated_at).toLocaleDateString('ru-RU')}
      </p>

      <table className="tb-table">
        <thead>
          <tr>
            <th>#</th>
            <th>Банк</th>
            <th>Активы, трлн ₽</th>
            <th>N1.0, %</th>
            <th>Прибыль за год, млрд ₽</th>
            <th>СИ</th>
          </tr>
        </thead>
        <tbody>
          {data.banks.map((b) => (
            <tr key={b.rank}>
              <td className="tb-num">{b.rank}</td>
              <td>{b.name}</td>
              <td className="tb-assets">{b.assets.toFixed(1).replace('.', ',')}</td>
              {/* N1.0 подсвечиваем по запасу над минимумом 8% */}
              <td className={b.n1 >= 12 ? 'tb-n1 tb-n1-strong' : 'tb-n1'}>
                {b.n1.toFixed(1).replace('.', ',')}
              </td>
              <td>{fmtInt(b.profit)}</td>
              <td className="tb-siber">{b.siber ? '✓' : ''}</td>
            </tr>
          ))}
        </tbody>
      </table>

      <p className="hint">
        Норматив N1.0 — достаточность общего капитала (минимум ЦБ 8%). СИ — системно
        значимые банки по перечню регулятора. Значения округлены; прибыль — МСФО/РСБУ
        за последний отчётный год. Рейтинг обновляется через /api/top-banks (POST с токеном).
      </p>
    </section>
  )
}
