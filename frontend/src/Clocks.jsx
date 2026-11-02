// Clocks.jsx — панель мировых часов: четыре аналоговых циферблата на SVG.
//
// Как узнаём время в другом городе. Соблазн — взять время компьютера и
// «прибавить смещение» (+6 к Токио, −8 к Нью-Йорку…). Так делать НЕЛЬЗЯ:
// смещения меняются от летнего/зимнего времени, а исторически — ещё и
// решениями правительств. Правильный способ — передать имя часового пояса
// браузеру: Intl.DateTimeFormat сам знает, что в Нью-Йорке сейчас действует
// EDT (UTC−4), а в ноябре — EST (UTC−5).
//
// Циферблат — SVG-круг 100×100. Главная формула: угол → координата.
// 12 часов по кругу = 360°, стрелка смотрит вверх (12 часов), а в SVG
// ось Y растёт вниз — поэтому sin/cos с «перевёрнутым» cos.

import { useEffect, useState } from 'react'

const CITIES = [
  { label: 'Токио', tz: 'Asia/Tokyo' },
  { label: 'Гонконг', tz: 'Asia/Hong_Kong' },
  { label: 'Москва', tz: 'Europe/Moscow' },
  { label: 'Нью-Йорк', tz: 'America/New_York' },
]

// Разбивка времени в нужной зоне на числа {h, m, s} + короткое смещение «GMT+3»
function zoneParts(date, tz) {
  const fmt = new Intl.DateTimeFormat('ru-RU', {
    timeZone: tz, hourCycle: 'h23',
    hour: '2-digit', minute: '2-digit', second: '2-digit',
    timeZoneName: 'shortOffset',
  })
  const parts = Object.fromEntries(
    fmt.formatToParts(date).map((p) => [p.type, p.value]),
  )
  return {
    h: Number(parts.hour), m: Number(parts.minute), s: Number(parts.second),
    offset: parts.timeZoneName.replace('GMT', '').replace('±', '') || '±0',
  }
}

// Полярные координаты → SVG. Угол в градусах от «12 часов» по часовой стрелке.
function polar(cx, cy, r, deg) {
  const rad = ((deg - 90) * Math.PI) / 180   // −90°: ноль ours у нас наверху
  return [cx + r * Math.cos(rad), cy + r * Math.sin(rad)]
}

// Стрелка: от центра (50,50) к точке на циферблате
function Hand({ angle, length, width, color, round = true }) {
  const [x, y] = polar(50, 50, length, angle)
  return <line x1="50" y1="50" x2={x} y2={y}
               stroke={color} strokeWidth={width}
               strokeLinecap={round ? 'round' : 'butt'} />
}

function ClockFace({ city, now }) {
  const { h, m, s, offset } = zoneParts(now, city.tz)

  // Углы стрелок. Секундная — шаг по 6°; минутная идёт плавно,
  // учитывая секунды; часовая — 30° за час и плавно ползёт от минут.
  const secAngle = s * 6
  const minAngle = m * 6 + s * 0.1
  const hourAngle = (h % 12) * 30 + m * 0.5

  // Числа 12/3/6/9 и 12 делений по кругу
  const numerals = [12, 3, 6, 9]

  return (
    <figure className="clock">
      <svg viewBox="0 0 100 100" role="img" aria-label={`${city.label}, ${h}:${String(m).padStart(2, '0')}`}>
        {/* ободок */}
        <circle cx="50" cy="50" r="47" className="clock-rim" />
        <circle cx="50" cy="50" r="44" className="clock-face" />

        {/* деления: часовые крупнее */}
        {Array.from({ length: 60 }, (_, i) => {
          const major = i % 5 === 0
          const [x1, y1] = polar(50, 50, major ? 41 : 43, i * 6)
          const [x2, y2] = polar(50, 50, 45, i * 6)
          return <line key={i} x1={x1} y1={y1} x2={x2} y2={y2}
                       className={major ? 'clock-tick-major' : 'clock-tick'} />
        })}

        {numerals.map((n) => {
          const [x, y] = polar(50, 50, 34, n * 30)
          return <text key={n} x={x} y={y + 3.5} textAnchor="middle"
                       className="clock-numeral">{n}</text>
        })}

        <Hand angle={hourAngle} length={22} width={4} color="#f2f2f7" />
        <Hand angle={minAngle} length={32} width={2.5} color="#f2f2f7" />
        <Hand angle={secAngle} length={36} width={1.2} color="#ff6b6b" round={false} />
        <circle cx="50" cy="50" r="2" fill="#ff6b6b" />
      </svg>
      <figcaption>
        <div className="clock-city">{city.label}</div>
        <div className="clock-digital">
          {String(h).padStart(2, '0')}:{String(m).padStart(2, '0')}:{String(s).padStart(2, '0')}
          <span className="clock-offset">GMT{offset === '0' ? '±0' : offset}</span>
        </div>
      </figcaption>
    </figure>
  )
}

export default function Clocks() {
  // Один общий «тик» на всю панель: раз в секунду перерисовываем все
  // четыре циферблата одним состоянием (дешевле и синхроннее, чем 4 таймера)
  const [now, setNow] = useState(() => new Date())
  useEffect(() => {
    const id = setInterval(() => setNow(new Date()), 1000)
    return () => clearInterval(id)          // не забываем убирать таймер!
  }, [])

  return (
    <section className="clocks" aria-label="Мировое время">
      {CITIES.map((city) => <ClockFace key={city.tz} city={city} now={now} />)}
    </section>
  )
}
