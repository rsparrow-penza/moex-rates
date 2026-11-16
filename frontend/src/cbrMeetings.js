// cbrMeetings.js — официальные даты заседаний Совета директоров Банка России
// по ключевой ставке (только сами заседания; без публикаций резюме и докладов).
// Источник: cbr.ru/dkp/cal_mp. ЦБ утверждает календарь на год заранее —
// после публикации добавляем в MEETINGS следующий год.
//
// anchor — опорное заседание: вместе с решением публикуется среднесрочный прогноз.

export const MEETINGS = {
  2023: [
    { date: '2023-02-10', anchor: true },
    { date: '2023-03-17', anchor: false },
    { date: '2023-04-28', anchor: true },
    { date: '2023-06-09', anchor: false },
    { date: '2023-07-21', anchor: true },
    { date: '2023-08-15', anchor: false, extra: true },   // внеочередное
    { date: '2023-09-15', anchor: true },
    { date: '2023-10-27', anchor: true },
    { date: '2023-12-15', anchor: false },
  ],
  2024: [
    { date: '2024-02-16', anchor: true },
    { date: '2024-03-22', anchor: false },
    { date: '2024-04-26', anchor: true },
    { date: '2024-06-07', anchor: false },
    { date: '2024-07-26', anchor: true },
    { date: '2024-09-13', anchor: false },
    { date: '2024-10-25', anchor: true },
    { date: '2024-12-20', anchor: false },
  ],
  2025: [
    { date: '2025-02-14', anchor: true },
    { date: '2025-03-21', anchor: false },
    { date: '2025-04-25', anchor: true },
    { date: '2025-06-06', anchor: false },
    { date: '2025-07-25', anchor: true },
    { date: '2025-09-12', anchor: false },
    { date: '2025-10-24', anchor: true },
    { date: '2025-12-19', anchor: false },
  ],
  2026: [
    { date: '2026-02-13', anchor: true },
    { date: '2026-03-20', anchor: false },
    { date: '2026-04-24', anchor: true },
    { date: '2026-06-19', anchor: false },
    { date: '2026-07-24', anchor: true },
    { date: '2026-09-11', anchor: false },
    { date: '2026-10-23', anchor: true },
    { date: '2026-12-18', anchor: false },
  ],
}

// Плоский отсортированный список всех известных заседаний
export const meetingList = () =>
  Object.values(MEETINGS).flat().sort((a, b) => a.date.localeCompare(b.date))
