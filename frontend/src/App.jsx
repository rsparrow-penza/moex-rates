// App.jsx — курсы USD и CNY с ММВБ: выбор диапазона дат + таблица + график.
//
// Структура запроса: GET /api/rates?from=…&to=…&currencies=USD,CNY
// Ответ — «длинный список» строк {currency, rate_date, rate};
// для графика мы разворачиваем его в две серии по датам.

import { useCallback, useEffect, useMemo, useState } from 'react'
import Chart from './Chart.jsx'
import './App.css'

// Дефолтный диапазон: последний месяц — смотрим через toISOString,
// чтобы формат даты был YYYY-MM-DD независимо от локали браузера.
function dateMinus(days) {
  const d = new Date()
  d.setDate(d.getDate() - days)
  return d.toISOString().slice(0, 10)
}

const ALL_CURRENCIES = ['USD', 'CNY']

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

  // Грузим данные сами и каждые 5 минут — планировщик на сервере
  // дописывает строки, и таблица должна «жить» без перезагрузки страницы.
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

  // Две серии для графика и таблицы: сначала сортируем список по дате,
  // useMemo — чтобы не пересортировывать лишний раз на каждый чих стейта.
  const sorted = useMemo(
    () => [...rates].sort((a, b) => a.rate_date.localeCompare(b.rate_date)),
    [rates],
  )
  const series = useMemo(() => {
    const byCode = {}
    for (const r of sorted) (byCode[r.currency] ||= []).push(r)
    return byCode
  }, [sorted])

  return (
    <div className="app">
      <h1>📊 Курсы валют ММВБ</h1>
      <p className="hint">USD — биржевой фиксинг (обновляется раз в день после 13:00),
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
          <tr><th>Дата</th><th>USD, ₽</th><th>CNY, ₽</th></tr>
        </thead>
        <tbody>
          {/* Разворачиваем длинный список в широкую таблицу: строка = дата */}
          {Array.from(new Set(sorted.map((r) => r.rate_date))).map((day) => {
            const usd = series.USD?.find((r) => r.rate_date === day)
            const cny = series.CNY?.find((r) => r.rate_date === day)
            return (
              <tr key={day}>
                <td>{day}</td>
                <td>{usd ? usd.rate.toFixed(2) : '—'}</td>
                <td>{cny ? cny.rate.toFixed(2) : '—'}</td>
              </tr>
            )
          })}
        </tbody>
      </table>
    </div>
  )
}
