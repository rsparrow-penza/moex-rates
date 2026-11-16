// KeyRate.jsx — отдельный блок «Ключевая ставка ЦБ РФ».
//
// Данные: GET /api/key-rate — дневной ряд за 3 года (бэкенд сам чистит
// старое). Ставка меняется редко (раз в месяц-два), поэтому вместо тысяч
// точек-дней рисуем СТУПЕНЧАТЫЙ график: линия идёт горизонтально и падает
// вертикально в день изменения — визуально ровно те события, что важны.

import { useEffect, useMemo, useState } from 'react'
import { meetingList } from './cbrMeetings.js'

const W = 720
const H = 170
const PAD = { top: 12, right: 14, bottom: 22, left: 44 }

// 14.00 → «14,00» — по-русски запятая
const fmt = (n) => n.toFixed(2).replace('.', ',')

// День считаем с полудня по МСК: без прыжков переводов часов и границ UTC
const stamp = (iso) => new Date(`${iso}T12:00+03:00`).getTime()

const ddmm = (iso) => {
  const [y, m, d] = iso.split('-')
  return `${d}.${m}.${y}`
}

export default function KeyRate() {
  const [points, setPoints] = useState([])
  const [error, setError] = useState('')

  // Блок живёт сам по себе: обновляемся каждые 30 минут — планировщик
  // бэкенда подтягивает ставку раз в час, чаще просто нет смысла.
  useEffect(() => {
    const load = () => fetch('/api/key-rate')
      .then((r) => { if (!r.ok) throw new Error(`сервер ответил ${r.status}`); return r.json() })
      .then(setPoints)
      .catch((e) => setError(`Не удалось загрузить ставку: ${e.message}`))
    load()
    const id = setInterval(load, 30 * 60 * 1000)
    return () => clearInterval(id)
  }, [])

  // Даты, когда ставка реально менялась (шаг ступеньки), — для графика
  const changes = useMemo(() => {
    const out = []
    for (let i = 1; i < points.length; i++)
      if (points[i].rate !== points[i - 1].rate) out.push({ ...points[i], prev: points[i - 1].rate })
    return out
  }, [points])

  // Таблица — по всем заседаниям из официального календаря ЦБ (cbrMeetings.js),
  // включая те, где ставку не меняли. Решение вступает в силу в ближайшие
  // дни после заседания, поэтому берём ставку через неделю после даты M:
  // она уже отражает итог именно этого заседания (до следующего далеко)
  const meetings = useMemo(() => {
    if (points.length < 2) return []
    const first = points[0].rate_date
    const until = points[points.length - 1].rate_date
    const out = []
    for (const m of meetingList()) {
      if (m.date < first || m.date > until) continue
      const t = stamp(m.date) + 7 * 86400000
      let p = null
      for (const q of points) { if (stamp(q.rate_date) > t) break; p = q }
      if (!p) continue
      out.push({ ...m, rate: p.rate, prev: out.length ? out[out.length - 1].rate : null })
    }
    return out
  }, [points])

  if (error) return <section className="key-rate"><div className="error">{error}</div></section>
  if (!points.length) return <section className="key-rate"><p className="hint">Загрузка ставки…</p></section>

  const last = points[points.length - 1]
  const since = changes.length ? changes[changes.length - 1].rate_date : points[0].rate_date
  const lo = Math.min(...points.map((p) => p.rate))
  const hi = Math.max(...points.map((p) => p.rate))

  const t0 = stamp(points[0].rate_date)
  const t1 = stamp(last.rate_date)
  const x = (t) => PAD.left + ((t - t0) / (t1 - t0 || 1)) * (W - PAD.left - PAD.right)
  // Запас по вертикали, чтобы линия не липла к краям рамки
  const yLo = lo - (hi - lo) * 0.15 - 0.5
  const yHi = hi + (hi - lo) * 0.15 + 0.5
  const y = (v) => PAD.top + (1 - (v - yLo) / (yHi - yLo)) * (H - PAD.top - PAD.bottom)

  // Ступеньки: от каждой точки горизонтально до следующей, затем вверх/вниз
  const step = []
  points.forEach((p, i) => {
    step.push(`${x(stamp(p.rate_date))},${y(p.rate)}`)
    if (i + 1 < points.length)
      step.push(`${x(stamp(points[i + 1].rate_date))},${y(p.rate)}`)
  })

  // Границы лет на оси X (в окне 3 года их 2–3)
  const yearTicks = points
    .filter((p, i) => i > 0 && p.rate_date.slice(5, 7) === '01' && points[i - 1].rate_date.slice(0, 4) !== p.rate_date.slice(0, 4))
    .map((p) => p.rate_date)

  // Подписи ставок прямо на графике: посередине каждого горизонтального
  // участка ступеньки — это ставка, утверждённая на заседании в его начале
  const labels = changes.map((p, i) => ({
    key: p.rate_date,
    x: (x(stamp(p.rate_date)) +
        x(stamp(i + 1 < changes.length ? changes[i + 1].rate_date : last.rate_date))) / 2,
    y: y(p.rate) - 6,
    rate: p.rate,
  }))
  // Если подписи соседних ступенек сливаются (< 24 px по горизонтали),
  // вторую пропускаем — её значение видно в таблице и в тултипе точки
  .filter((l, i, arr) => i === 0 || Math.abs(l.x - arr[i - 1].x) > 24)

  return (
    <section className="key-rate" aria-label="Ключевая ставка ЦБ РФ">
      <div className="kr-head">
        <h2>Ключевая ставка ЦБ РФ</h2>
        <div className="kr-now">
          <b>{fmt(last.rate)}%</b>
          <span>действует с {ddmm(since)}</span>
        </div>
      </div>

      <div className="kr-body">
        <svg viewBox={`0 0 ${W} ${H}`} className="kr-chart">
          {/* направляющие min/max с подписями */}
          {[hi, lo].map((v) => (
            <g key={v}>
              <line x1={PAD.left} x2={W - PAD.right} y1={y(v)} y2={y(v)}
                    className="kr-guide" />
              <text x={PAD.left - 6} y={y(v) + 3} textAnchor="end"
                    className="kr-label">{fmt(v)}</text>
            </g>
          ))}
          {/* начало каждого года на оси X */}
          {yearTicks.map((iso) => (
            <g key={iso}>
              <line x1={x(stamp(iso))} x2={x(stamp(iso))} y1={PAD.top} y2={H - PAD.bottom}
                    className="kr-guide" />
              <text x={x(stamp(iso))} y={H - 6} textAnchor="middle"
                    className="kr-label">{iso.slice(0, 4)}</text>
            </g>
          ))}
          <polyline points={step.join(' ')} className="kr-line" />
          {labels.map((l) => (
            <text key={l.key} x={l.x} y={l.y} textAnchor="middle"
                  className="kr-value">{fmt(l.rate)}</text>
          ))}
          {/* точки изменений */}
          {changes.map((p) => (
            <circle key={p.rate_date} cx={x(stamp(p.rate_date))} cy={y(p.rate)} r={3.5}
                    className={p.rate > p.prev ? 'kr-up' : 'kr-down'}>
              <title>{`${ddmm(p.rate_date)}: ${fmt(p.prev)} → ${fmt(p.rate)}%`}</title>
            </circle>
          ))}
        </svg>

        <div className="kr-table-wrap">
          <table className="kr-changes">
            <thead>
              <tr><th>Заседание</th><th>Ставка</th><th>Решение</th></tr>
            </thead>
            <tbody>
              {[...meetings].reverse().map((m) => {
                const delta = m.prev == null ? null : Math.round((m.rate - m.prev) * 100)
                return (
                  <tr key={m.date}>
                    <td>{ddmm(m.date)}{m.anchor && <em className="kr-anchor">опорное</em>}</td>
                    <td>{fmt(m.rate)}%</td>
                    <td className={delta == null ? 'kr-same' : delta > 0 ? 'kr-up-text' : delta < 0 ? 'kr-down-text' : 'kr-same'}>
                      {delta == null ? 'первое в окне'
                        : delta === 0 ? '— сохранена'
                        : `${delta > 0 ? '▲' : '▼'} ${fmt(Math.abs(delta / 100))}`}
                    </td>
                  </tr>
                )
              })}
            </tbody>
          </table>
        </div>
      </div>

      <p className="hint">
        В таблице — все заседания ЦБ за 3 года, включая те, где ставку сохранили.
        Источник: cbr.ru
      </p>
    </section>
  )
}
