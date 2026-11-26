// Ofz.jsx — закладка «ОФЗ»: кривая доходностей и состав корзины.
//
// Выпуск не исчезает из таблицы молча: когда OFZ_LINEAGE говорит, что он
// вышел (погашение наступило), бэкенд подставляет его последнюю реальную
// запись с флагом retired — строка становится серой и подписывает, чем её
// заменили. Такие строки участвуют в графике (сравнить «как было» можно), но
// не участвуют в наклоне кривой и в счётчике корзины.
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
  // вне расчёта — выведенные: иначе «наклон» и счётчик выпусков считались бы
  // по бумагам, которых на рынке уже нет
  const live = useMemo(() => curve.filter((c) => !c.retired), [curve])

  // стабильный цвет на выпуск: индекс в отсортированной по погашению корзине;
  // выведенный выпуск всегда серый — цвет здесь несёт смысл, а не украшение
  const colorOf = useMemo(() => {
    const colors = Object.fromEntries(live.map((c, i) => [c.secid, PALETTE[i % PALETTE.length]]))
    curve.filter((c) => c.retired).forEach((c) => { colors[c.secid] = '#6c757d' })
    return colors
  }, [curve, live])

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

  const slope = live.length > 1
    ? live[live.length - 1].yield_prc - live[0].yield_prc
    : null
  const ru = (d) => (d ? d.split('-').reverse().join('.') : '—')

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
        {' · '}корзина {live.length} выпусков
        {curve.length > live.length
          ? ` + ${curve.length - live.length} выведенн${curve.length - live.length === 1 ? 'ый' : 'ых'}` : ''}
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
                } млрд ₽ · оборот ${c.retired ? 'в последний день торгов ' : 'сегодня '}${
                  c.valtoday_rur != null ? fmt1(c.valtoday_rur / 1e6) : '0'
                } млн ₽${c.replaced_by_name
                  ? ` · ${c.retired ? 'погашен ' : 'выйдет '}${ru(c.exit_date)}, в корзине его место занимает ${c.replaced_by_name}` : ''}${
                  c.retired ? ` · последняя биржевая запись ${c.snap_date}` : ''}`}
                onClick={() => setPicked((p) =>
                  p.includes(c.secid) ? p.filter((k) => k !== c.secid) : [...p, c.secid])}
                className={`${picked.includes(c.secid) ? 'row-on' : ''}${c.retired ? ' ec-retired' : ''}`.trim()}
                aria-label={c.retired ? `${c.shortname}, погашен ${ru(c.exit_date)}` : c.shortname}>
              <td>
                {c.shortname}
                {c.exit_date && (
                  <em className={`ec-badge${c.retired ? ' off' : ' soon'}`}>
                    {c.retired ? `погашен ${ru(c.exit_date)}` : `выйдет ${ru(c.exit_date)}`}
                  </em>
                )}
              </td>
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
                  className={`ec-chip${picked.includes(c.secid) ? ' on' : ''}${c.retired ? ' off' : ''}`}
                  style={{ '--c': colorOf[c.secid] }}
                  title={`погашение ${c.matdate}, купон ${c.coupon_prc != null ? fmt2(c.coupon_prc) + '%' : '—'}${
                    c.retired ? ` · выведен ${ru(c.exit_date)}, замена — ${c.replaced_by_name ?? 'не выбрана'}` : ''}`}
                  onClick={() => setPicked((p) =>
                    p.includes(c.secid) ? p.filter((k) => k !== c.secid) : [...p, c.secid])}>
            {c.shortname}{c.retired ? ' †' : ''}
          </button>
        ))}
      </div>
      {series.length > 0
        ? <LineChart series={series} />
        : <p className="hint">Выбери хотя бы два выпуска в таблице или в чипах — и на графике появится сравнение.</p>}

      <details className="tb-how">
        <summary>Как выбраны выпуски в корзине</summary>
        <table className="tb-how-table">
          <tbody>
            <tr>
              <th>Только ОФЗ-ПД</th>
              <td className="tb-how-form">34 из 61</td>
              <td>На борта TQOB торгуются ещё ОФЗ-ПК (купон индексируется к
                  ставке overnight) и ОФЗ-ИН (купон привязан к инфляции) — 21 и 4
                  выпуска. Их доходность описывает ожидания по ставке и инфляции,
                  а не срок до погашения, поэтому на одной кривой с постоянным
                  купоном они были бы неправдой.</td>
            </tr>
            <tr>
              <th>Горизонт 2027–2041</th>
              <td className="tb-how-form">33 подходящих, взято 12</td>
              <td>Примерно по выпуску на каждые полтора года погашения: от
                  короткого 26207 (февраль 2027) до 26238 (май 2041). Двенадцать
                  линий на графике ещё читаются, тридцать три — уже нет.</td>
            </tr>
            <tr>
              <th>Состав зафиксирован</th>
              <td className="tb-how-form">OFZ_UNIVERSE в econ.py</td>
              <td>Корзина не пересобирается по обороту дня, иначе менялся бы сам
                  ряд: «как менялись доходности» прыгала бы при каждом пересчёте
                  ликвидности, а историю нельзя было бы сравнить с самой собой.</td>
            </tr>
            <tr>
              <th>Ликвидность</th>
              <td className="tb-how-form">оборот 30–1800 млн ₽/день</td>
              <td>Взятые выпуски ликвидны неодинаково: у 26235 (2031) и 26207
                  (2027) оборот тонкий, поэтому цена последней сделки и
                  доходность на этих участках могут отставать и прыгать —
                  изломы кривой там осторожнее читать как шум, а не как сигнал.</td>
            </tr>
            <tr>
              <th>Что не вошло</th>
              <td className="tb-how-form">26254, 26240, 26248</td>
              <td>Это ОФЗ-ПД с бо́льшим оборотом дня, чем у нескольких взятых:
                  26254 (октябрь 2040, ~1,9 млрд ₽), 26240 (июль 2036, ~1,1 млрд),
                  26248 (май 2040, ~0,9 млрд). Не брали их ради равномерности
                  сроков: на 2036–2040 уже есть точки 26245, 26243, 26253, 26247,
                  а дальше 2041 горизонт не нужен. Корзину можно пересобрать —
                  но вместе с историей рядов.</td>
            </tr>
            <tr>
              <th>Выведенные выпуски</th>
              <td className="tb-how-form">OFZ_LINEAGE в econ.py</td>
              <td>Дата погашения известна заранее, поэтому выпуск не исчезает
                  из таблицы втихаря. Пока он торгуется — с жёлтой меткой
                  «выйдет ДД.ММ.ГГГГ» и указанием преемника в подсказке; после
                  погашения строка остаётся серой, с зачёркнутой доходностью,
                  датой последней биржевой записи и подписью, чем выпуск
                  заменён. Линия такого выпуска доступна на графике — сравнить
                  «как было» можно, а вот в наклоне кривой и в счётчике корзины
                  он не участвует. Первый записанный уход: ОФЗ 26207 (погашение
                  03.02.2027) → ОФЗ 26232 (погашение 06.10.2027).</td>
            </tr>
            <tr>
              <th>Справочник выпуска</th>
              <td className="tb-how-form">карточка ММВБ, раз в месяц</td>
              <td>Дата погашения, купон, номинал и объём выпуска — из
                  <code> /iss/securities/&lt;ISIN&gt;/issue.json</code>; сама
                  доходность и цена — из сегодняшнего борта TQOB.</td>
            </tr>
          </tbody>
        </table>
        <p className="hint">
          Все цифры здесь сняты с живого борта TQOB 26.11.2026. Если состав
          эмиссии изменится, счётчики поплывут — блок это отражает, а не прячет.
        </p>
      </details>

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
