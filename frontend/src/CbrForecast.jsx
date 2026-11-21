// CbrForecast.jsx — справочный блок: среднесрочный прогноз Банка России.
//
// Траектория ключевой ставки и инфляция по сценариям — из прогноза,
// опубликованного 23.10.2026. ЦБ обновляет прогноз на опорных заседаниях
// (апрель/июль/октябрь/декабрь) — при выкатке после такого меняем цифры,
// дату и ссылку на PDF. Идея для читателя: даже в базовом сценарии к 2029
// ставка вдвое ниже сегодняшней.

const FORECAST = {
  published: '23.10.2026',
  pdf: 'https://www.cbr.ru/Content/Document/File/198897/forecast_261023.pdf',
  ondmp: 'https://www.cbr.ru/about_br/publ/ondkp/on_2027_2029/',
  years: ['2027', '2028', '2029'],
  scenarios: [
    { name: 'дезинфляционный', rate: ['10,0–12,0', '7,5–8,5', '7,5–8,5'], cpi27: '3,0–4,0' },
    { name: 'базовый', base: true, rate: ['12,5–13,5', '9,0–10,0', '7,5–8,5'], cpi27: '4,0' },
    { name: 'инфляционный', rate: ['14,0–16,0', '11,5–12,5', '8,5–9,5'], cpi27: '4,5–5,5' },
  ],
  note2026: 'Средняя ставка 2026 — 14,6%, инфляция по итогам года — 7,1–7,6%.',
}

export default function CbrForecast() {
  return (
    <section className="cbr-forecast" aria-label="Среднесрочный прогноз Банка России">
      <h2>🎯 Прогноз ЦБ: траектория ключевой ставки</h2>
      <p className="cf-sub">
        Средняя ключевая ставка, % годовых · прогноз от {FORECAST.published}
      </p>

      <table className="cf-table">
        <thead>
          <tr>
            <th>Сценарий</th>
            {FORECAST.years.map((y) => <th key={y}>{y}</th>)}
            <th>Инфляция {FORECAST.years[0]}</th>
          </tr>
        </thead>
        <tbody>
          {FORECAST.scenarios.map((s) => (
            <tr key={s.name} className={s.base ? 'cf-base' : undefined}>
              <td>{s.name}</td>
              {s.rate.map((r, i) => <td key={i}>{r}</td>)}
              <td>{s.cpi27}%</td>
            </tr>
          ))}
        </tbody>
      </table>

      <p className="hint">{FORECAST.note2026}</p>
      <p className="cf-links">
        <a href={FORECAST.pdf} target="_blank" rel="noopener noreferrer">среднесрочный прогноз (PDF)</a>
        {' · '}
        <a href={FORECAST.ondmp} target="_blank" rel="noopener noreferrer">Основные направления ДКП на 2027–2029</a>
      </p>
    </section>
  )
}
