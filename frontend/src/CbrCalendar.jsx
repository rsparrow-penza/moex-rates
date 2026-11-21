// CbrCalendar.jsx — справочный блок: график заседаний Совета директоров
// Банка России по ключевой ставке на текущий год.
//
// Даты (общий файл cbrMeetings.js) ЦБ публикует заранее, поэтому держим их
// статично — на новый год достаточно добавить следующий массив. При смене
// года, пока нового массива нет, блок просто не показываем.

import { useEffect, useState } from 'react'
import { MEETINGS } from './cbrMeetings.js'

const ddmm = (iso) => {
  const [y, m, d] = iso.split('-')
  return `${d}.${m}.${y}`
}

const WEEKDAYS = ['вс', 'пн', 'вт', 'ср', 'чт', 'пт', 'сб']
const weekday = (iso) => WEEKDAYS[new Date(`${iso}T12:00+03:00`).getDay()]

// «Сегодня» тоже по МСК, чтобы статус заседания не зависел от часовых поясов
const todayMs = () => {
  const now = new Date(Date.now() + 3 * 3600 * 1000)
  return now.toISOString().slice(0, 10)
}

export default function CbrCalendar() {
  // Тикаем раз в минуту: «до решения осталось…» — хуки до любых return!
  const [now, setNow] = useState(() => Date.now())
  useEffect(() => {
    const id = setInterval(() => setNow(Date.now()), 60 * 1000)
    return () => clearInterval(id)
  }, [])

  const year = Number(todayMs().slice(0, 4))
  const meetings = MEETINGS[year]
  if (!meetings) return null

  const today = todayMs()
  const next = meetings.find((m) => m.date >= today)

  // Обратный отсчёт до публикации решения (13:30 МСК дня заседания)
  let countdown = null
  if (next) {
    const decision = new Date(`${next.date}T13:30:00+03:00`).getTime()
    const ms = decision - now
    if (ms > 0) {
      const d = Math.floor(ms / 86400000)
      const h = Math.floor((ms % 86400000) / 3600000)
      const min = Math.floor((ms % 3600000) / 60000)
      countdown = d > 0 ? `через ${d} дн ${h} ч` : h > 0 ? `через ${h} ч ${min} мин` : `через ${min} мин`
    } else if (next.date === today) {
      countdown = 'решение сегодня в 13:30'
    }
  }

  return (
    <section className="cbr-cal" aria-label="График заседаний ЦБ РФ">
      <h2>📅 Заседания ЦБ по ключевой ставке — {year}</h2>
      {next && (
        <p className="cal-countdown">
          ⏳ Следующее заседание — <b>{ddmm(next.date)}</b>
          {next.anchor ? ' (опорное)' : ''}, решение в 13:30 МСК{countdown ? `: ${countdown}` : ''}
        </p>
      )}
      <div className="cal-chips">
        {meetings.map((m) => {
          const isNext = next && m.date === next.date
          const past = m.date < today
          return (
            <span key={m.date}
                  className={`cal-chip${past ? ' cal-past' : ''}${isNext ? ' cal-next' : ''}`}>
              <b>{ddmm(m.date)}</b>
              <i>{weekday(m.date)}</i>
              {m.anchor && <em title="опорное — вместе с прогнозом">опорное</em>}
              {past && <em className="cal-done" title="заседание состоялось">✓</em>}
              {isNext && <em className="cal-flag">следующее</em>}
            </span>
          )
        })}
      </div>
      <p className="hint">
        Решение публикуется в 13:30 МСК, пресс-конференция — в 15:00 МСК.
        Источник: cbr.ru/dkp/cal_mp
      </p>
    </section>
  )
}
