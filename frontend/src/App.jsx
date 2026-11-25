// App.jsx — курсы валют: межбанк ·OTC (Yahoo) и официальные ·ЦБ.
//
// Список валют приходит от бэкенда (GET /api/currencies), но биржевые коды
// ММВБ (EXCHANGE_CODES) в интерфейсе скрыты — сбор в бэкенде не трогали.
//
// Данные делятся на два «use»:
//   • график — все видимые серии: дневные ЦБ и внутридневные OTC
//     (15-минутки, поэтому OTC на графике «змея»);
//   • таблица — дневные значения (source=CBR), строка = дата.

import { useCallback, useEffect, useMemo, useState } from 'react'
import Chart, { localExtrema } from './Chart.jsx'
import Changelog from './Changelog.jsx'
import TopBanks from './TopBanks.jsx'
import Econ from './Econ.jsx'
import CbrForecast from './CbrForecast.jsx'
import KeyRate from './KeyRate.jsx'
import Inflation from './Inflation.jsx'
import CbrCalendar from './CbrCalendar.jsx'
import './App.css'

// Дефолтный диапазон: последний месяц — через toISOString,
// чтобы формат был YYYY-MM-DD независимо от локали браузера.
function dateMinus(days) {
  const d = new Date()
  d.setDate(d.getDate() - days)
  return d.toISOString().slice(0, 10)
}

// Пояснение по каждому тикеру — что это за инструмент и чем он дышит.
// Живёт под фильтрами; приглушается, когда тикер выключен чипом.
const EXPLAIN = {
  USD_OTC: 'межбанковский USD/RUB (Yahoo, RUB=X): внутридневные точки каждые ~15 минут, почти круглосуточно — тот же курс, что в приложении «Акции»',
  EUR_OTC: 'межбанковский EUR/RUB (Yahoo, EURRUB=X): та же площадка и тот же 15-минутный шаг, что и у доллара',
  USD_CBR: 'официальный курс ЦБ РФ по доллару: одно значение в день, действует со следующего (cbr.ru)',
  EUR_CBR: 'официальный курс ЦБ РФ по евро: обновление раз в день',
  CNY_CBR: 'официальный курс ЦБ РФ по юаню: обновление раз в день',
  BTC: 'биткоин к доллару (CoinGecko): крипторынок работает 24/7, точка раз в час — непрерывная «змея»',
  BRENT: 'нефть Brent, фьючерс ICE (BZ=F): котировка в $ за баррель, дневные закрытия плюс внутридневные точки',
  GOLD: 'золото, фьючерс COMEX (GC=F): мировая цена в $ за тройскую унцию, торги почти круглосуточно',
}

// Короткие подписи для чипов и заголовков (длинное название — в title)
const LABELS = {
  USD: 'USD', USD_OTC: 'USD·OTC', USD_CBR: 'USD·ЦБ',
  EUR: 'EUR', EUR_OTC: 'EUR·OTC', EUR_CBR: 'EUR·ЦБ',
  CNY: 'CNY', CNY_CBR: 'CNY·ЦБ',
  BTC: 'Bitcoin $', BRENT: 'Brent $', GOLD: 'Gold $',
}

// Биржевые серии ММВБ (фиксинги USD/EUR и спот CNY) из интерфейса убраны
// полностью: нет чипов в фильтрах, линий на графике и колонок в таблице.
// Бэкенд их продолжает собирать — история лежит в базе, вернуть отображение
// можно одной строкой.
const EXCHANGE_CODES = ['USD', 'EUR', 'CNY']

function RatesApp() {
  const [meta, setMeta] = useState([])              // справочник с сервера
  const [from, setFrom] = useState(dateMinus(30))
  const [to, setTo] = useState(dateMinus(0))
  const [picked, setPicked] = useState(null)        // null = все валюты
  const [rates, setRates] = useState([])
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')
  // Режим графика: 'pct' — сравнение темпами (все на одной шкале %),
  //             'rub' — абсолютные рубли (каждая валюта на своей полоске)
  const [mode, setMode] = useState('pct')

  // Справочник валют грузим один раз
  useEffect(() => {
    fetch('/api/currencies')
      .then((r) => r.json())
      .then((all) => {
        const list = all.filter((m) => !EXCHANGE_CODES.includes(m.code))
        setMeta(list)
        setPicked(list.map((m) => m.code))
      })
      .catch(() => setError('Бэкенд недоступен. Запустите: python3 app.py'))
  }, [])

  const load = useCallback(async () => {
    if (!picked) return                 // справочник ещё не приехал
    setLoading(true)
    setError('')
    try {
      const url = `/api/rates?from=${from}&to=${to}&currencies=${picked.join(',')}`
      const res = await fetch(url)
      if (!res.ok) throw new Error(`сервер ответил ${res.status}`)
      setRates(await res.json())
    } catch (e) {
      setError(`Не удалось загрузить курсы: ${e.message}.`)
    } finally {
      setLoading(false)
    }
  }, [from, to, picked])

  // Грузим сами и каждые 5 минут: планировщик дописывает точки (OTC — каждые
  // ~15 минут на стороне источника), страница должна «жить» без перезагрузки.
  useEffect(() => {
    if (!picked) return
    load()
    const timer = setInterval(load, 5 * 60 * 1000)
    return () => clearInterval(timer)
  }, [load, picked])

  function toggleCurrency(code) {
    setPicked((prev) => {
      const next = prev.includes(code) ? prev.filter((c) => c !== code) : [...prev, code]
      return next.length ? next : prev          // полностью пустой список не разрешаем
    })
  }

  // --- График: все точки, серии по кодам, сортировка по дате+времени ---
  const series = useMemo(() => {
    const sorted = [...rates].sort((a, b) =>
      (a.rate_date + a.rate_time).localeCompare(b.rate_date + b.rate_time))
    const byCode = {}
    for (const r of sorted) (byCode[r.currency] ||= []).push(r)
    // Дневную закрытку убираем из дня, где есть внутридневные тики:
    // дневная свеча Yahoo датирована началом сессии (вечер ET = наши утро/ночь),
    // и вперемешку с 15-минутками она рисовала «пилу» ±3% на границах суток.
    // В старом диапазоне, где минут нет, закрытия остаются единственной точкой.
    for (const code of Object.keys(byCode)) {
      const rows = byCode[code]
      const timedDays = new Set(rows.filter((r) => r.rate_time).map((r) => r.rate_date))
      if (timedDays.size)
        byCode[code] = rows.filter((r) => r.rate_time || !timedDays.has(r.rate_date))
    }
    return byCode
  }, [rates])

  // Биржевые линии на графике не рисуем в принципе (см. EXCHANGE_CODES):
  // остаются только ·OTC (межбанк) и ·ЦБ (официальный)
  const chartSeries = useMemo(() => {
    const out = {}
    for (const [code, pts] of Object.entries(series))
      if (!EXCHANGE_CODES.includes(code)) out[code] = pts
    return out
  }, [series])

  // --- Таблица: дневные ряды провайдеров moex/cbr. Внутридневные OTC-строки, ---
  // биткоин и нефть (у них своя природа и своя валюта) в неё не попадают
  const tableCodes = useMemo(
    () => meta.filter((m) => ['moex', 'cbr'].includes(m.provider) && picked?.includes(m.code)).map((m) => m.code),
    [meta, picked],
  )
  const byDate = useMemo(() => {
    const map = new Map()
    for (const r of rates) {
      if (!['MOEX', 'CBR'].includes(r.source) || !tableCodes.includes(r.currency)) continue
      if (!map.has(r.rate_date)) map.set(r.rate_date, {})
      map.get(r.rate_date)[r.currency] = r.rate
    }
    return [...map.entries()].sort((a, b) => b[0].localeCompare(a[0]))   // свежие наверху
  }, [rates, tableCodes])

  // Подсветка локальных экстремумов в таблице: свой проход localExtrema
  // для каждой колонки (значения идут по возрастанию даты, как на графике)
  const extCells = useMemo(() => {
    const asc = [...byDate].reverse()
    const out = {}
    for (const code of tableCodes) {
      const pts = asc
        .map(([day, byCur]) => ({ day, v: byCur[code] }))
        .filter((p) => p.v != null)
      const flags = {}
      for (const { i, type } of localExtrema(pts.map((p) => p.v))) flags[pts[i].day] = type
      out[code] = flags
    }
    return out
  }, [byDate, tableCodes])

  if (!meta.length) return <div className="app"><h1>Загрузка…</h1></div>

  return (
    <div className="app">
      <div className="title-row">
        <h1>📊 Курсы валют, ставка ЦБ и прочее</h1>
        <a className="nav-link" href="#/changelog">История изменений →</a>
      </div>
      <p className="hint">·OTC — межбанк Yahoo, весь день; ·ЦБ — официальный курс ЦБ РФ;
        Bitcoin, Brent и Gold — активы, только график</p>

      {error && <div className="error">{error}</div>}

      {/* Панель фильтров и график курсов — на самом верху: рабочий инструмент
          страницы, справочные блоки ЦБ ушли под них */}
      <div className="filters">
        <label>
          с <input type="date" value={from} max={to} onChange={(e) => setFrom(e.target.value)} />
        </label>
        <label>
          по <input type="date" value={to} min={from} onChange={(e) => setTo(e.target.value)} />
        </label>
        <div className="currencies">
          {meta.map(({ code, title }) => (
            <label key={code} title={title}
                   className={picked.includes(code) ? 'chip chip-on' : 'chip'}>
              <input type="checkbox" checked={picked.includes(code)}
                     onChange={() => toggleCurrency(code)} />
              {LABELS[code] ?? code}
            </label>
          ))}
        </div>
        {/* Переключатель режима — обычные radio, просто оформлены как сегменты */}
        <div className="seg" role="radiogroup" aria-label="Режим графика">
          <label className={mode === 'pct' ? 'seg-on' : ''}>
            <input type="radio" name="mode" value="pct"
                   checked={mode === 'pct'} onChange={(e) => setMode(e.target.value)} />
            %
          </label>
          <label className={mode === 'rub' ? 'seg-on' : ''}>
            <input type="radio" name="mode" value="rub"
                   checked={mode === 'rub'} onChange={(e) => setMode(e.target.value)} />
            ₽
          </label>
        </div>
        <button onClick={load} disabled={loading}>{loading ? 'Загрузка…' : 'Обновить'}</button>
      </div>

      {/* Расшифровка тикеров: строка на каждый, выключенные — приглушены */}
      <div className="tick-notes" aria-label="Пояснения по тикерам">
        {meta.map((m) => (
          <div key={m.code} className={picked.includes(m.code) ? 'tn-row' : 'tn-row tn-off'}>
            <b>{LABELS[m.code] ?? m.code}</b>
            <span>{EXPLAIN[m.code] ?? m.title}</span>
          </div>
        ))}
      </div>

      {Object.values(chartSeries).some((s) => s.length > 1) &&
        <Chart series={chartSeries} labels={LABELS} mode={mode} />}

      {/* Ключевая ставка — отдельным блоком: свои данные, своя шкала (%) */}
      <KeyRate />

      {/* Инфляция — естественный сосед ставки: именно она диктует решения ЦБ */}
      <Inflation />

      {/* Справочно: даты заседаний ЦБ по ключевой ставке на текущий год */}
      <CbrCalendar />

      {/* Справочно: траектория ставки по сценариям среднесрочного прогноза ЦБ */}
      <CbrForecast />

      {/* Справочно: топ-10 банков — активы, капитал, прибыль (указатель ЦБ) */}
      <Econ />
      <TopBanks />

      <table className="table">
        <thead>
          <tr>
            <th>Дата</th>
            {tableCodes.map((c) => <th key={c}>{LABELS[c] ?? c}, ₽</th>)}
          </tr>
        </thead>
        <tbody>
          {byDate.map(([day, byCur]) => (
            <tr key={day} className={day.endsWith('-01') ? 'row-1st' : undefined}>
              <td>{day}</td>
              {tableCodes.map((c) => {
                const ex = extCells[c]?.[day]
                const v = byCur[c]
                return (
                  <td key={c} className={ex === 'max' ? 'cell-max' : ex === 'min' ? 'cell-min' : undefined}
                      title={ex ? `локальный ${ex === 'max' ? 'максимум' : 'минимум'}` : undefined}>
                    {v != null ? `${ex === 'max' ? '▲ ' : ex === 'min' ? '▼ ' : ''}${v.toFixed(2)}` : '—'}
                  </td>
                )
              })}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

// --- Мини-роутинг по хешу: #/changelog — страница истории изменений ---
// Библиотека не нужна: адресов два, достаточно слушать hashchange.

export default function Router() {
  const [page, setPage] = useState(() => window.location.hash)
  useEffect(() => {
    const onHash = () => {
      setPage(window.location.hash)
      window.scrollTo(0, 0)
    }
    window.addEventListener('hashchange', onHash)
    return () => window.removeEventListener('hashchange', onHash)
  }, [])
  if (page === '#/changelog') return <Changelog />
  return <RatesApp />
}
