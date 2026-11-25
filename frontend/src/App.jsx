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
import Metals from './Metals.jsx'
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

// Brent и Gold — в таблицу дневными закрытиями Yahoo (rate_time = ''):
// это не «курс», а цена актива в $, но сравнивать их по дням полезно.
// Bitcoin в таблице не нужен: у него часовые точки 24/7, дневной строки нет.
const ASSET_CODES = ['BRENT', 'GOLD']
const assetFmt = (v) => v >= 1000
  ? Math.round(v).toLocaleString('ru-RU')      // золото: 3 522 — без копеек
  : v.toFixed(2)                               // нефть: 88,29
const thLabel = (c) => (ASSET_CODES.includes(c)
  ? (LABELS[c] ?? c).replace(/\s*\$\s*/, '')   // «Brent $» → «Brent» (юнит в заголовке)
  : LABELS[c] ?? c)
const thUnit = (c) => (ASSET_CODES.includes(c) ? '$' : '₽')

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
  const [rowsShown, setRowsShown] = useState(10)    // дневная таблица: 10 строк, дальше — по кнопке
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

  // новый период или новый набор инструментов — снова показываем 10 свежих
  useEffect(() => { setRowsShown(10) }, [from, to, picked])

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

  // --- Таблица: дневные ряды moex/cbr плюс дневные закрытия Brent/Gold ($).
  // Внутридневные OTC-строки и биткоин (часовые тики 24/7) в неё не попадают
  const tableCodes = useMemo(
    () => meta
      .filter((m) => (['moex', 'cbr'].includes(m.provider) || ASSET_CODES.includes(m.code))
        && picked?.includes(m.code))
      .map((m) => m.code),
    [meta, picked],
  )
  const byDate = useMemo(() => {
    const map = new Map()
    for (const r of rates) {
      if (!tableCodes.includes(r.currency)) continue
      const dailyFx = ['MOEX', 'CBR'].includes(r.source)
      const dailyAsset = ASSET_CODES.includes(r.currency) && r.source === 'YAHOO' && !r.rate_time
      if (!dailyFx && !dailyAsset) continue
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
        Brent и Gold — в таблице дневными закрытиями в $; Bitcoin — только график</p>

      {error && <div className="error">{error}</div>}

      {/* Ключевая ставка — отдельным блоком: свои данные, своя шкала (%) */}
      <KeyRate />

      {/* Даты заседаний — сразу за ставкой: это продолжение того же сюжета
          (когда ЦБ решал и что решит дальше), а не отдельная справка */}
      <CbrCalendar />

      {/* Инфляция — естественный сосед ставки: именно она диктует решения ЦБ */}
      <Inflation />

      {/* Справочно: траектория ставки по сценариям среднесрочного прогноза ЦБ */}
      <CbrForecast />

      {/* Биржевые цены на металлы: ФОРТС + мир, рядом с экономикой */}
      <Metals />

      {/* Валютный виджет (фильтры → график → дневная таблица) переехал
          сюда, к «Экономике»: справочные блоки ЦБ теперь сверху, рабочий
          инструмент — одной связкой с макроданными */}
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

      <table className="table">
        <thead>
          <tr>
            <th>Дата</th>
            {tableCodes.map((c) => <th key={c}>{thLabel(c)}, {thUnit(c)}</th>)}
          </tr>
        </thead>
        <tbody>
          {byDate.slice(0, rowsShown).map(([day, byCur]) => (
            <tr key={day} className={day.endsWith('-01') ? 'row-1st' : undefined}>
              <td>{day}</td>
              {tableCodes.map((c) => {
                const ex = extCells[c]?.[day]
                const v = byCur[c]
                return (
                  <td key={c} className={ex === 'max' ? 'cell-max' : ex === 'min' ? 'cell-min' : undefined}
                      title={ex ? `локальный ${ex === 'max' ? 'максимум' : 'минимум'}` : undefined}>
                    {v != null
                      ? `${ex === 'max' ? '▲ ' : ex === 'min' ? '▼ ' : ''}${ASSET_CODES.includes(c) ? assetFmt(v) : v.toFixed(2)}`
                      : '—'}
                  </td>
                )
              })}
            </tr>
          ))}
        </tbody>
      </table>

      {/* Таблица дневных значений длинная (300+ строк за квартал) — показываем
          первые 10, остальные раскрываются по кнопке порциями по 20 */}
      {byDate.length > rowsShown && (
        <button type="button" className="table-more" onClick={() => setRowsShown((n) => n + 20)}>
          Показать ещё {Math.min(20, byDate.length - rowsShown)} из {byDate.length - rowsShown}
        </button>
      )}
      {byDate.length > 10 && rowsShown >= byDate.length && (
        <button type="button" className="table-more table-less" onClick={() => setRowsShown(10)}>
          Свернуть до 10 строк
        </button>
      )}

      {/* Справочно: топ-10 банков — активы, капитал, прибыль (указатель ЦБ) */}
      <Econ />
      <TopBanks />

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
