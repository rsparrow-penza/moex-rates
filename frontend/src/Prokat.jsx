// Prokat.jsx — блок «Металлопрокат» на вкладке «Металлы».
//
// Два источника, которые нельзя мешать в одну колонку, поэтому и таблицы две:
//
//   • Росстат — «Средние цены производителей на отдельные виды промышленных
//     товаров». Официальная статистика: цена завода за тонну, без НДС,
//     ежемесячно, лаг ~2 месяца. Это НЕ цена, по которой прокат покупают на
//     складе.
//   • МСиБ — мониторинг журнала «Металлоснабжение и сбыт»: опрос
//     металлотрейдеров Центрального региона (Москва), ₽/т, шаг ~2 недели.
//     Здесь есть арматура — в Росстате её нет, группировка 24.10.61 по РФ
//     закрыта прочерками.
//
// Данные: GET /api/prokat. Если ряда нет — прочерк, а не ноль.

import { useEffect, useMemo, useState } from 'react'
import Spark from './Spark.jsx'

const fmt0 = (n) => n == null ? '—' : Math.round(n).toLocaleString('ru-RU')
const pct = (n) => n == null ? '—' : `${n > 0 ? '+' : ''}${n.toFixed(1).replace('.', ',')}%`
const MON = ['', 'янв', 'фев', 'мар', 'апр', 'май', 'июн', 'июл', 'авг', 'сен', 'окт', 'ноя', 'дек']

// период в человекочитаемую дату: '2026-09' → 'сентябрь 2026',
// '2026-11-03' → '3 ноября 2026'
function periodLabel(p) {
  if (!p) return '—'
  const m = MON[+p.slice(5, 7)] || '?'
  return p.length === 7 ? `${m} ${p.slice(0, 4)}` : `${+p.slice(8, 10)} ${m} ${p.slice(0, 4)}`
}

const asDate = (p) => new Date((p.length === 7 ? p + '-01' : p) + 'T00:00:00Z').getTime()
const DAY = 864e5

// Δ к «N дней назад»: ищем ближайшую точку, а не сдвиг на k записей — ряд
// месячный у одного товара начинается в 2023-м, у другого в 2021-м, а
// дилерский ещё и с пропусками наблюдений. tolDays — насколько «год назад»
// имеет право быть не ровно годом
function delta(series, days, tolDays) {
  const n = series.length
  if (!n) return null
  const last = series[n - 1]
  const want = asDate(last.period) - days * DAY
  let best = null, diff = Infinity
  for (const p of series) {
    const d = Math.abs(asDate(p.period) - want)
    if (d < diff) { diff = d; best = p }
  }
  if (!best || best.period === last.period || diff > tolDays * DAY) return null
  return (last.value / best.value - 1) * 100
}

const COLORS = ['#fab005', '#74c0fc', '#69db7c', '#f783ac', '#ffa94d',
                '#8fd3c7', '#b197fc', '#ff8787', '#ced4da']

// общий вид строки таблицы: последняя цена, Δ к предыдущему наблюдению, Δ г/г
function prepare(list, sparkLen) {
  return (list || []).map((x, i) => {
    const s = x.series || []
    const last = s[s.length - 1]
    const prev = s[s.length - 2]
    return {
      ...x,
      price: last ? last.value : null,
      period: last ? last.period : null,
      chain: last && prev ? (last.value / prev.value - 1) * 100 : null,
      year: delta(s, 365, 47),
      since: s.length ? `с ${periodLabel(s[0].period)}` : '',
      spark: s.slice(-sparkLen).map((p) => ({ date: p.period, value: p.value })),
      color: COLORS[i % COLORS.length],
    }
  })
}

function Table({ rows, firstLabel, chainLabel, note }) {
  return (
    <table className="mt-table">
      <thead>
        <tr>
          <th>{firstLabel}</th><th>Цена, ₽/т</th><th>{chainLabel}</th>
          <th>За год</th><th>Динамика (даты: мм.гг)</th>
        </tr>
      </thead>
      <tbody>
        {rows.map((r) => (
          <tr key={r.key}>
            <td className="mt-name" title={note ? `${r.explain}\n\n${note}` : r.explain}>
              <i className="mt-dot" style={{ background: r.color }} />
              {r.label}
              <em>{r.code ? `ОКПД2 ${r.code}` : 'дилер · Москва'}</em>
            </td>
            <td className="mt-price" title={r.period ? `по состоянию на ${periodLabel(r.period)}` : 'нет данных'}>
              {fmt0(r.price)}
            </td>
            <td className={`mt-chg${r.chain > 0 ? ' up' : r.chain < 0 ? ' down' : ''}`}
                title="к предыдущему наблюдению того же источника">
              {pct(r.chain)}
            </td>
            <td className={`mt-basis${r.year > 0 ? ' up' : r.year < 0 ? ' down' : ''}`}
                title={r.year == null ? 'точек год назад нет' : `${r.since}: ${fmt0(r.price)} против ${fmt0(r.price / (1 + r.year / 100))} год назад`}>
              {pct(r.year)}
            </td>
            <td><Spark points={r.spark} color={r.color} suffix="₽/т" /></td>
          </tr>
        ))}
      </tbody>
    </table>
  )
}

export default function Prokat() {
  const [data, setData] = useState(null)
  const [error, setError] = useState('')

  useEffect(() => {
    const load = () => fetch('/api/prokat')
      .then((r) => { if (!r.ok) throw new Error(`сервер ответил ${r.status}`); return r.json() })
      .then(setData)
      .catch((e) => setError(`Не удалось загрузить цены на металлопрокат: ${e.message}`))
    load()
    // ряды обновляются раз в месяц (Росстат) и раз в две недели (МСиБ):
    // час — с запасом, чтобы поймать дневную загрузку после рестарта сервиса
    const id = setInterval(load, 60 * 60 * 1000)
    return () => clearInterval(id)
  }, [])

  const rosstat = useMemo(() => prepare(data && data.rosstat, 42), [data])
  // сводный ряд МСиБ ставим первой строкой: он и есть «прокат в целом»
  const dealer = useMemo(() => {
    const rows = prepare(data && data.dealer, 60)
    return rows.filter((r) => r.key === 'dk_index').concat(rows.filter((r) => r.key !== 'dk_index'))
  }, [data])

  if (error) return <section className="metals"><div className="error">{error}</div></section>
  if (!data) return <section className="metals"><p className="hint">Загрузка цен на металлопрокат…</p></section>

  const meta = data.meta || {}
  const live = dealer.filter((r) => r.price != null)

  return (
    <section className="metals" aria-label="Цены на металлопрокат">
      <h2>🏗 Металлопрокат, ₽ за тонна</h2>
      <p className="cf-sub">
        Два разных ряда: цена завода (Росстат) и цена дилера в Москве (мониторинг
        «Металлоснабжения и сбыта») — различаются и методикой, и сроком, поэтому
        они в отдельных таблицах
      </p>

      <h3 className="pk-h3"
          title="Средние цены производителей на отдельные виды промышленных товаров: наблюдение за ценами заводов-изготовителей, без НДС. Публикуются ежемесячно с лагом ~2 месяца">
        Росстат · цены производителей
        {meta.rosstat_asof ? ` · ${periodLabel(meta.rosstat_asof)}` : ''}
        {meta.rosstat_file ? ` · файл ${meta.rosstat_file}` : ''}
      </h3>
      <Table rows={rosstat.filter((r) => r.price != null)} firstLabel="Позиция (ОКПД2)"
             chainLabel="За месяц"
             note="средняя цена производителя по Российской Федерации, без НДС" />
      <p className="hint">
        Это цена изготовителя: на складе с доставкой и нарезкой будет дороже,
        обычно на 10–25 %. Месячный лаг — статистика выходит через ~2 месяца
        после периода. Отдельной строки арматуры здесь нет и быть не может: она
        входит в группировку 24.10.61, а она по России закрыта прочерками —
        арматура есть ниже, в дилерском мониторинге. Ряд начинается с 2021 года:
        в более старых листах Росстата годовые средние, в один график с
        месячными их не кладут.
      </p>

      <h3 className="pk-h3"
          title="Опрос металлотрейдеров Центрального региона (Москва), шаг наблюдения ~2 недели. Справочные цены продажи, не котировки биржи и не статистика">
        МСиБ · дилерские цены, Центральный регион
        {meta.dealer_asof ? ` · наблюдение от ${periodLabel(meta.dealer_asof)}` : ''}
      </h3>
      {live.length ? (
        <Table rows={live} firstLabel="Сортопозиция" chainLabel="К предыдущему"
               note="цена дилера в Москве; сводный ряд — среднее по всем позициям мониторинга" />
      ) : (
        <p className="hint">
          Рядов МСиБ в базе пока нет: сайт мониторинга отдаёт страницу без
          блока графика (проверка повторяется раз в сутки). Это не цена равна
          нулю — это нет данных, поэтому таблица не показывается.
        </p>
      )}
      <p className="hint">
        Дилерские цены — опрос металлотрейдеров, а не биржевые котировки: это
        справочный уровень «почем тонна на складе в Москве», со своим лагом и
        округлением. Частота наблюдения — примерно раз в две недели, поэтому
        «к предыдущему» здесь не «за день». Сводный ряд (среднее по 18
        позициям) считаем сами, исходный индекс сайта не перепечатываем.
      </p>
    </section>
  )
}
