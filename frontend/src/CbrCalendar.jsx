// CbrCalendar.jsx — справочный блок: график заседаний Совета директоров
// Банка России по ключевой ставке на текущий год.
//
// Даты (общий файл cbrMeetings.js) ЦБ публикует заранее, поэтому держим их
// статично — на новый год достаточно добавить следующий массив. При смене
// года, пока нового массива нет, блок просто не показываем.

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
  const year = Number(todayMs().slice(0, 4))
  const meetings = MEETINGS[year]
  if (!meetings) return null

  const today = todayMs()
  const next = meetings.find((m) => m.date >= today)

  return (
    <section className="cbr-cal" aria-label="График заседаний ЦБ РФ">
      <h2>📅 Заседания ЦБ по ключевой ставке — {year}</h2>
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
