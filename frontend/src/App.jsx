// App.jsx — курсы USD / EUR / CNY с ММВБ: диапазон дат + график + таблица.
//
// Структура запроса: GET /api/rates?from=…&to=…&currencies=USD,EUR,CNY
// Ответ — «длинный список» строк {currency, rate_date, rate};
// для графика разворачиваем его в серии по валютам (useMemo, см. ниже).

import { useCallback, useEffect, useMemo, useState } from 'react'
import Chart from './Chart.jsx'
import './App.css'

// Дефолтный диапазон: последний месяц — через toISOString,
// чтобы формат был YYYY-MM-DD независимо от локали браузера.
function dateMinus(days) {
  const d = new Date()
  d.setDate(d.getDate() - days)
  return d.toISOString().slice(0, 10)
}

const ALL_CURRENCIES = ['USD', 'EUR', 'CNY']

export default function App() {
  const [from, setFrom] = useState(dateMinus(30))
  const [to, setTo] = useState(dateMinus(0))
  const [picked, setPicked] = useState(ALL_CURRENCIES)   // выбранные валюты
  const [rates, setRates] = useState([])
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')

  const load = useCallback(async () => {
    setLoading(true)
    setError('')
    try {
      const url = `/api/rates?from=${from}&to=${to}&currencies=${picked.join(',')}`
      const res = await fetch(url)
      if (!res.ok) throw new Error(`сервер ответил ${res.status}`)
      setRates(await res.json())
    } catch (e) {
      setError(`Не удалось загрузить курсы: ${e.message}. Проверьте, запущен ли бэкенд.`)
    } finally {
      setLoading(false)
    }
  }, [from, to, picked])

  // Грузим сами и каждые 5 минут: планировщик на сервере дописывает
  // строки, страница должна «жить» без перезагрузки.
  useEffect(() => {
    load()
    const timer = setInterval(load, 5 * 60 * 1000)
    return () => clearInterval(timer)
  }, [load])

  function toggleCurrency(code) {
    setPicked((prev) => {
      const next = prev.includes(code) ? prev.filter((c) => c !== code) : [...prev, code]
      return next.length ? next : prev          // полностью пустой список не разрешаем
    })
  }

  // Сортировка по дате и разбивка на серии {USD: [...], EUR: [...], CNY: [...]}
  const sorted = useMemo(
    () => [...rates].sort((a, b) => a.rate_date.localeCompare(b.rate_date)),
    [rates],
  )
  const series = useMemo(() => {
    const byCode = {}
    for (const r of sorted) (byCode[r.currency] ||= []).push(r)
    return byCode
  }, [sorted])

  // Широкая таблица: одна строка = одна дата, колонки = валюты.
  // Словарь строим один раз на рендер, а не ищем линейным перебором.
  const byDate = useMemo(() => {
    const map = new Map()
    for (const r of sorted) {
      if (!map.has(r.rate_date)) map.set(r.rate_date, {})
      map.get(r.rate_date)[r.currency] = r.rate
    }
    return [...map.entries()]                     // уже отсортировано по дате
  }, [sorted])

  return (
    <div className="app">
      <h1>📊 Курсы валют ММВБ</h1>
      <p className="hint">USD и EUR — биржевые фиксинги (обновляются раз в день после 13:00),
        CNY — спот-торги (обновляются в течение торгового дня)</p>

      {/* Панель фильтров: диапазон дат и валюты */}
      <div className="filters">
        <label>
          с <input type="date" value={from} max={to} onChange={(e) => setFrom(e.target.value)} />
        </label>
        <label>
          по <input type="date" value={to} min={from} onChange={(e) => setTo(e.target.value)} />
        </label>
        <div className="currencies">
          {ALL_CURRENCIES.map((code) => (
            <label key={code} className={picked.includes(code) ? 'chip chip-on' : 'chip'}>
              <input type="checkbox" checked={picked.includes(code)} onChange={() => toggleCurrency(code)} />
              {code}
            </label>
          ))}
        </div>
        <button onClick={load} disabled={loading}>{loading ? 'Загрузка…' : 'Обновить'}</button>
      </div>

      {error && <div className="error">{error}</div>}

      {sorted.length > 0 && <Chart series={series} />}

      <table className="table">
        <thead>
          <tr>
            <th>Дата</th>
            {ALL_CURRENCIES.filter((c) => picked.includes(c)).map((c) => (
              <th key={c}>{c}, ₽</th>
            ))}
          </tr>
        </thead>
        <tbody>
          {byDate.map(([day, byCur]) => (
            <tr key={day}>
              <td>{day}</td>
              {ALL_CURRENCIES.filter((c) => picked.includes(c)).map((c) => (
                <td key={c}>{byCur[c] != null ? byCur[c].toFixed(2) : '—'}</td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}
