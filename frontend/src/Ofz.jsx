// Ofz.jsx — закладка «ОФЗ»: кривая доходностей и состав корзины.
//
// Данные: GET /api/econ/bonds — снимок кривой (последний дневной, ММВБ борт
// TQOB) плюс история YIELDCLOSE по каждому выпуску за год. Таблица отвечает
// на вопрос «сколько просят сегодня», график — «как менялись ожидания»:
// длинные выпуски уползают вверх раньше ключевой ставки, короткие почти
// повторяют её.

import { useEffect, useMemo, useState } from 'react'
import { LineChart } from './Econ.jsx'

const fmt2 = (n) => n.toFixed(2).replace('.', ',')
const fmt1 = (n) => (Math.abs(n) >= 100 ? Math.round(n).toLocaleString('ru-RU') : n.toFixed(1)).replace('.', ',')

// Цвет привязан к выпуску (порядок в кривой = по сроку погашения), иначе
// чип и линия разъезжаются, стоит убрать или добавить серию на графике
const PALETTE = ['#4c6ef5', '#845ef7', '#f06595', '#e8590c', '#fab005', '#20c997', '#adb5bd']

// Сколько выпусков показывать на графике по умолчанию (кратчайший, пара
// средних, длинный) — 12 линий читать невозможно
const DEFAULT_PICK = ['SU26207RMFS9', 'SU26242RMFS6', 'SU26249RMFS1', 'SU26247RMFS5']

export default function Ofz() {
  const [data, setData] = useState(null)
  const [error, setError] = useState('')
  const [picked, setPicked] = useState(DEFAULT_PICK)

  useEffect(() => {
    const load = () => fetch('/api/econ/bonds?from=' + new Date(Date.now() - 400 * 864e5).toISOString().slice(0, 10))
      .then((r) => { if (!r.ok) throw new Error(`сервер ответил ${r.status}`); return r.json() })
      .then(setData)
      .catch((e) => setError(`Не удалось загрузить ОФЗ: ${e.message}`))
    load()
    // биржевой снимок обновляется планировщиком раз в 12 часов
    const id = setInterval(load, 30 * 60 * 1000)
    return () => clearInterval(id)
  }, [])

  const curve = useMemo(() => (data?.curve || []).filter((c) => c.yield_prc != null), [data])

  // стабильный цвет на выпуск: индекс в отсортированной по погашению корзине
  const colorOf = useMemo(
    () => Object.fromEntries(curve.map((c, i) => [c.secid, PALETTE[i % PALETTE.length]])),
    [curve])

  // линии графика: история доходности каждого выбранного выпуска
  const series = useMemo(() => {
    if (!data) return []
    return curve
      .filter((c) => picked.includes(c.secid))
      .map((c) => ({
        key: c.secid,
        label: `${c.shortname} (${c.matdate.slice(0, 4)})`,
        color: colorOf[c.secid],
        points: (data.series[c.secid] || []).map((p) => ({ period: p.date, value: p.yield })),
      }))
      .filter((s) => s.points.length > 1)
  }, [data, curve, picked, colorOf])

  if (error) return <section className="ofz"><div className="error">{error}</div></section>
  if (!data) return <section className="ofz"><p className="hint">Загрузка кривой ОФЗ…</p></section>
  if (!curve.length) return null

  const slope = curve.length > 1
    ? curve[curve.length - 1].yield_prc - curve[0].yield_prc
    : null

  return (
    <section className="ofz" aria-label="Рынок ОФЗ">
      <h2>
        🧱 ОФЗ
        {slope != null && (
          <span className="ec-slope"
                title={`${curve[curve.length - 1].shortname} минус ${curve[0].shortname}: сколько просят за длинные деньги сверх коротких`}>
            наклон {slope >= 0 ? '+' : ''}{fmt1(slope)} п.п.
          </span>
        )}
      </h2>
      <p className="cf-sub">
        Доходность к погашению, ММВБ борт «Т+: Гособлигации» · снимок {data.snap_date}
        {' · '}корзина {curve.length} выпусков
      </p>

      {/* ——— кривая сегодня ——— */}
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
                } млн ₽`}
                onClick={() => setPicked((p) =>
                  p.includes(c.secid) ? p.filter((k) => k !== c.secid) : [...p, c.secid])}
                className={picked.includes(c.secid) ? 'row-on' : undefined}>
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

      {/* ——— чем дольше держишь, тем сильнее ожидания ——— */}
      <h3 className="ec-title">Как менялись доходности</h3>
      <div className="ec-chips">
        {curve.map((c, i) => (
          <button key={c.secid} type="button"
                  className={`ec-chip${picked.includes(c.secid) ? ' on' : ''}`}
                  style={{ '--c': colorOf[c.secid] }}
                  title={`погашение ${c.matdate}, купон ${c.coupon_prc != null ? fmt2(c.coupon_prc) + '%' : '—'}`}
                  onClick={() => setPicked((p) =>
                    p.includes(c.secid) ? p.filter((k) => k !== c.secid) : [...p, c.secid])}>
            {c.shortname}
          </button>
        ))}
      </div>
      {series.length > 0
        ? <LineChart series={series} />
        : <p className="hint">Выбери хотя бы два выпуска в таблице или в чипах — и на графике появится сравнение.</p>}

      <p className="hint">
        Цена — последняя сделка в процентах от номинала (1000 ₽); в подсказке
        строки — НКД, объём выпуска и оборот дня. Клик по строке таблицы
        добавляет или убирает выпуск на график. Наклон кривой — сколько рынок
        просит сверх коротких ставок за длинные деньги: пологая или перевёрнутая
        кривая — ожидание быстрого смягчения ЦБ, крутая — что низких ставок
        в обозримом будущем не будет.
      </p>
    </section>
  )
}
