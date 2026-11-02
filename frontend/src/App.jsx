// App.jsx — курсы валют: ММВБ (фиксинги/спот) + внебиржевой USD (Yahoo OTC).
//
// Список валют теперь приходит от бэкенда (GET /api/currencies) —
// чтобы добавить четвёртую валюту, достаточно изменить SOURCES в Python.
//
// Данные делятся на два"use":
//   • таблица — дневные значения ММВБ (source=MOEX), строка = дата;
//   • график — всё подряд, включая внутридневные точки Yahoo (15-минутки),
//     поэтому USD на графике будет «змеей», а USD в таблице — точкой в день.

import { useCallback, useEffect, useMemo, useState } from 'react'
import Chart from './Chart.jsx'
import Clocks from './Clocks.jsx'
import './App.css'

// Дефолтный диапазон: последний месяц — через toISOString,
// чтобы формат был YYYY-MM-DD независимо от локали браузера.
function dateMinus(days) {
  const d = new Date()
  d.setDate(d.getDate() - days)
  return d.toISOString().slice(0, 10)
}

// Короткие подписи для чипов и заголовков (длинное название — в title)
const LABELS = {
  USD: 'USD', USD_OTC: 'USD·OTC', USD_CBR: 'USD·ЦБ',
  EUR: 'EUR', EUR_OTC: 'EUR·OTC', EUR_CBR: 'EUR·ЦБ',
  CNY: 'CNY', CNY_CBR: 'CNY·ЦБ',
}

export default function App() {
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
      .then((list) => {
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
    return byCode
  }, [rates])

  // --- Таблица: дневные ряды (ММВБ и ЦБ). Внутридневные OTC строки не имеют ---
  const tableCodes = useMemo(
    () => meta.filter((m) => !m.code.endsWith('_OTC') && picked?.includes(m.code)).map((m) => m.code),
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

  if (!meta.length) return <div className="app"><h1>Загрузка…</h1></div>

  return (
    <div className="app">
      <h1>📊 Курсы валют</h1>
      <p className="hint">ММВБ — биржевые фиксинги и спот; ·OTC — межбанк Yahoo, весь день;
        ·ЦБ — официальный курс ЦБ РФ</p>

      {/* Панель фильтров: диапазон дат, валюты, режим графика */}
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

      {error && <div className="error">{error}</div>}

      {/* Мировое время: Токио → Гонконг → Москва → Нью-Йорк */}
      <Clocks />

      {Object.values(series).some((s) => s.length > 1) &&
        <Chart series={series} labels={LABELS} mode={mode} />}

      <table className="table">
        <thead>
          <tr>
            <th>Дата</th>
            {tableCodes.map((c) => <th key={c}>{LABELS[c] ?? c}, ₽</th>)}
          </tr>
        </thead>
        <tbody>
          {byDate.map(([day, byCur]) => (
            <tr key={day}>
              <td>{day}</td>
              {tableCodes.map((c) => (
                <td key={c}>{byCur[c] != null ? byCur[c].toFixed(2) : '—'}</td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}
