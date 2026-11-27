// Penza.jsx — вкладка «Пензенская область».
//
// Данные: GET /api/penza — федеральные выгрузки Росстата по субъектам:
//   ipc_RF_fo_sub_*.xlsx (ИПЦ по месяцам, с 2022), sred_potreb_cen_*.xlsx
//   (цены по позициям мониторинга), IVBO_sub_RF_*.xlsx (индекс выпуска, % г/г),
//   sred_cen_perv/vtor_*.xlsx (жильё, ₽/м²), urov_10subg/11subg_*.xlsx (доходы).
//
// Почему не с сайта Пензастата: 58.rosstat.gov.ru резолвится, но TCP молчит и
// на 80, и на 443 — и с локальной машины, и с сервера; старый pensastat.gks.ru
// не резолвится вовсе (проверено 27.11.2026). Те же наблюдения территориальный
// орган сдаёт в Росстат, и Росстат публикует их файлами по субъектам — это тот
// же первоисточник, только доступный.
//
// Правила, важные для честности:
//   • сравнение всегда в той же базе, что и ряд области (г/г к г/г, ₽ к ₽);
//   • «…» в файле — отсутствие данных: прочерк, а не ноль, и средняя по колонке
//     его не оживляет;
//   • цены по товарам и жильё покрыт только текущий год (годовых архивов по
//     субъектам нет, а ссылки на прошлые кварталы отдают 404) — глубина растёт
//     у нас в базе с каждым прогоном, и это подписано в «Откуда это».

import { useEffect, useMemo, useState } from 'react'
import { LineChart } from './Econ.jsx'
import Spark from './Spark.jsx'

const MON = ['', 'янв', 'фев', 'мар', 'апр', 'май', 'июн', 'июл', 'авг', 'сен', 'окт', 'ноя', 'дек']
const fmt0 = (n) => n == null ? '—' : Math.round(n).toLocaleString('ru-RU')
const fmt2 = (n) => n == null ? '—' : n.toFixed(2).replace('.', ',')
const pct = (n) => n == null ? '—' : `${n > 0 ? '+' : ''}${n.toFixed(1).replace('.', ',')}%`
const plabel = (p) => (p ? `${MON[+p.slice(5, 7)]} ${p.slice(0, 4)}` : '—')

const C = { region: '#4c6ef5', pfo: '#20c997', rf: '#adb5bd', city: '#fab005' }

// ряды API ({period, value}) → точки LineChart и спарклайна
const pts = (rows) => (rows || []).map((r) => ({ period: r.period, value: r.value }))
const sparkPts = (rows) => (rows || []).map((r) => ({ date: r.period, value: r.value }))
const last = (rows) => (rows && rows.length ? rows[rows.length - 1] : null)
const prev = (rows) => (rows && rows.length > 1 ? rows[rows.length - 2] : null)

function Tile({ label, value, unit, note, color, fmt = fmt2 }) {
  return (
    <div className="ec-tile" title={`${label} · ${note || ''}`}>
      <span className="ec-tile-label">{label}</span>
      {value == null
        ? <span className="ec-tile-empty">нет данных</span>
        : <span className="ec-tile-value" style={{ color }}>
            {fmt(value)}{unit && <i>{' '}{unit}</i>}
          </span>}
      <em className="ec-tile-note">{note || 'нет периода'}</em>
    </div>
  )
}

export default function Penza() {
  const [d, setD] = useState(null)
  const [error, setError] = useState('')
  const [sort, setSort] = useState('delta')          // delta | name

  useEffect(() => {
    const load = () => fetch('/api/penza')
      .then((r) => { if (!r.ok) throw new Error(`сервер ответил ${r.status}`); return r.json() })
      .then(setD)
      .catch((e) => setError(`Не удалось загрузить данные по области: ${e.message}`))
    load()
    // региональные выгрузки выходят раз в месяц — чаще ходить не за чем
    const id = setInterval(load, 6 * 60 * 60 * 1000)
    return () => clearInterval(id)
  }, [])

  const byKey = useMemo(() => Object.fromEntries((d?.cpi || []).map((s) => [s.key, s])), [d])

  const goods = useMemo(() => {
    const rows = (d?.goods || []).map((g) => {
      // g.city / g.region / g.rf — это массивы рядов; показывать надо последнюю
      // точку, а не «первое значение» и не сам массив
      const c = last(g.city), r = last(g.region), f = last(g.rf)
      return {
        ...g,
        cityL: c, regionL: r, rfL: f,
        shown: c || r,
        cityVsRf: c && f ? (c.value / f.value - 1) * 100 : null,
        regionVsRf: r && f ? (r.value / f.value - 1) * 100 : null,
        spark: sparkPts(g.city.length > 1 ? g.city : g.region),
      }
    })
    const byDelta = (a, b) => (b.cityVsRf ?? 1e9) - (a.cityVsRf ?? 1e9)
    return sort === 'delta' ? rows.slice().sort(byDelta)
                            : rows.slice().sort((a, b) => a.label.localeCompare(b.label, 'ru'))
  }, [d, sort])

  if (error) return <section className="econ"><div className="error">{error}</div></section>
  if (!d) return <section className="econ"><p className="hint">Загрузка данных по области…</p></section>

  const lines = (specs) => specs.filter((s) => s.points.length > 1)

  const cpiLines = lines([
    { key: 'pz', label: 'Пензенская область', color: C.region, points: pts(byKey.region_yoy?.series) },
    { key: 'pfo', label: 'Приволжский ФО', color: C.pfo, points: pts(byKey.pfo_yoy?.series) },
    { key: 'rf', label: 'Россия', color: C.rf, points: pts(byKey.rf_yoy?.series) },
  ])
  const outLines = lines([
    { key: 'pzout', label: 'Пензенская область', color: C.region,
      points: pts((d.output.find((o) => o.geo === 'region') || {}).series) },
    { key: 'pfoout', label: 'Приволжский ФО', color: C.pfo,
      points: pts((d.output.find((o) => o.geo === 'pfo') || {}).series) },
  ])

  const sdd = d.income.find((i) => i.kind === 'sdd')
  const rdd = d.income.find((i) => i.kind === 'rdd')
  const incLines = lines([
    { key: 'pzinc', label: 'СДД, область, ₽/мес', color: C.region, points: pts(sdd?.region) },
    { key: 'rfinc', label: 'СДД, Россия, ₽/мес', color: C.rf, points: pts(sdd?.rf) },
  ])

  const cpiMom = last(byKey.region_mom?.series)
  const rddLast = last(rdd?.region)

  return (
    <section className="econ" aria-label="Пензенская область">
      <h2>🏞 Пензенская область</h2>
      <p className="cf-sub">
        Федеральные выгрузки Росстата по субъектам · данные на {plabel(d.as_of)} ·
        каждое сравнение построено на той же базе, что и ряд области
      </p>

      {/* ——— инфляция ——— */}
      <h3 className="ec-title"
          title="ИПЦ к тому же месяцу прошлого года: произведение 12 месячных индексов, посчитанное у нас из месячных рядов одного файла">
        Инфляция: область, округ, Россия
      </h3>
      <div className="ec-tiles">
        <Tile label="Область, за месяц" value={cpiMom?.value} unit="%"
              note={cpiMom ? `${plabel(cpiMom.period)} · к ${plabel(prev(byKey.region_mom?.series)?.period)}` : null}
              color={C.region} />
        <Tile label="Россия, за месяц" value={last(byKey.rf_mom?.series)?.value} unit="%"
              note={plabel(last(byKey.rf_mom?.series)?.period)} color={C.rf} />
        <Tile label="Область, год к году" value={last(byKey.region_yoy?.series)?.value} unit="%"
              note={plabel(last(byKey.region_yoy?.series)?.period)} color={C.region} />
        <Tile label="Продовольствие, область, за месяц" value={last(byKey.region_food_mom?.series)?.value}
              unit="%" note={plabel(last(byKey.region_food_mom?.series)?.period)} color={C.city} />
        <Tile label="Непродовольственные, область" value={last(byKey.region_nonfood_mom?.series)?.value}
              unit="%" note={plabel(last(byKey.region_nonfood_mom?.series)?.period)} color={C.city} />
        <Tile label="Услуги, область" value={last(byKey.region_services_mom?.series)?.value}
              unit="%" note={plabel(last(byKey.region_services_mom?.series)?.period)} color={C.city} />
      </div>
      {cpiLines.length
        ? <LineChart series={cpiLines} />
        : <p className="hint">Годовых рядов инфляции пока нет — они появятся, когда наберётся 12 месячных точек.</p>}

      {/* ——— цены на конкретные товары ——— */}
      <h3 className="ec-title"
          title="Средние потребительские цены по позициям мониторинга: ₽ за единицу измерения, которая стоит в названии позиции; наблюдение — на конец месяца">
        Почём в Пензе
      </h3>
      <div className="ec-chips">
        <button type="button" className={`ec-chip${sort === 'delta' ? ' on' : ''}`} style={{ '--c': C.city }}
                title="сначала то, что в Пензе дешевле всего относительно России"
                onClick={() => setSort('delta')}>выгоднее всего</button>
        <button type="button" className={`ec-chip${sort === 'name' ? ' on' : ''}`} style={{ '--c': C.rf }}
                title="по названию позиции" onClick={() => setSort('name')}>по алфавиту</button>
      </div>
      <div className="ec-scroll">
        <table className="ec-table">
          <thead>
            <tr><th>Позиция</th><th>Пенза</th><th>Область</th><th>Россия</th>
                <th title="цена Пензы к средней по России, %">Пенза / РФ</th>
                <th title="месяцы текущего года, даты мм.гг">Цены 2026</th></tr>
          </thead>
          <tbody>
            {goods.map((g) => (
              <tr key={g.key}
                  title={`${g.label}, ${g.unit} · позиция ${g.code} · данных на ${plabel(g.shown?.period)}` +
                        `${!g.cityL && g.shown ? ' · по городу цена закрыта прочерком, показана областная' : ''}`}>
                <td className="mt-name">{g.label}<em>{g.unit}</em></td>
                <td className="ec-price" title={g.cityL ? `Пенза: ${fmt2(g.cityL.value)} ₽` : 'по городу — прочерк в файле'}>
                  {g.cityL ? fmt2(g.cityL.value) : '—'}
                </td>
                <td className="ec-price">{g.regionL ? fmt2(g.regionL.value) : '—'}</td>
                <td className="ec-mat">{g.rfL ? fmt2(g.rfL.value) : '—'}</td>
                <td className={`ec-chg${g.cityVsRf < 0 ? ' up' : g.cityVsRf > 0 ? ' down' : ''}`}
                    title="минус — в Пензе дешевле, чем в среднем по России">
                  {pct(g.cityVsRf)}
                </td>
                <td><Spark points={g.spark} color={C.city} width={132} height={42} suffix="₽" /></td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <p className="hint">
        Позиции берутся по коду, а не по названию: названия в файлах правят, коды
        стабильны. По части товаров городская цена в файле закрыта точками — тогда
        в таблице прочерк, а рядом показана областная. Ряд покрывает только
        текущий год: годового архива цен по субъектам Росстат не публикует, так что
        история копится здесь, с момента подключения блока.
      </p>

      {/* ——— жильё ——— */}
      <h3 className="ec-title"
          title="Средние цены предложения жилья, ₽ за м². В листе «по центру субъекта РФ» строка названа областью, но цифры относятся к её центру — то есть к Пензе">
        Жильё, ₽ за м²
      </h3>
      <table className="ec-table">
        <thead>
          <tr><th>Рынок</th><th>Пенза</th><th>Область</th><th>Россия</th>
              <th title="цена Пензы к средней по России, %">Пенза / РФ</th><th>Период</th></tr>
        </thead>
        <tbody>
          {d.flat.map((f) => {
            const c = last(f.city), r = last(f.region), rf = last(f.rf)
            const dv = c && rf ? (c.value / rf.value - 1) * 100 : null
            return (
              <tr key={f.kind}
                  title={`${f.label}: ${plabel(c?.period || r?.period)}, ₽ за м² общей площади`}>
                <td className="mt-name">{f.label}<em>все типы квартир</em></td>
                <td className="ec-price">{c ? fmt0(c.value) : '—'}</td>
                <td className="ec-price">{r ? fmt0(r.value) : '—'}</td>
                <td className="ec-mat">{rf ? fmt0(rf.value) : '—'}</td>
                <td className={`ec-chg${dv < 0 ? ' up' : dv > 0 ? ' down' : ''}`}>{pct(dv)}</td>
                <td className="ec-mat">{plabel(c?.period || r?.period)}</td>
              </tr>
            )
          })}
        </tbody>
      </table>
      <p className="hint">
        Это цены предложения, а не сделок: «хотят», а не «продалось». Прошлые
        кварталы в Росстате остаются только в текущем файле года, поэтому ряд
        жилья начинается с 2026-го и дальше будет удлиняться сам.
      </p>

      {/* ——— доходы ——— */}
      <h3 className="ec-title"
          title="СДД — среднедушевой денежный доход, ₽ на человека в месяц в среднем за квартал; РДД — реальные доходы к тому же кварталу прошлого года, с поправкой на инфляцию">
        Доходы населения
      </h3>
      <div className="ec-tiles">
        <Tile label="СДД, область" value={last(sdd?.region)?.value} unit="₽/мес"
              note={plabel(last(sdd?.region)?.period)} color={C.region} fmt={fmt0} />
        <Tile label="СДД, Россия" value={last(sdd?.rf)?.value} unit="₽/мес"
              note={plabel(last(sdd?.rf)?.period)} color={C.rf} fmt={fmt0} />
        <Tile label="Реальные доходы, область" value={rddLast?.value} unit="% г/г"
              note={rddLast ? `${plabel(rddLast.period)} · ${rddLast.value < 100 ? 'отстали от цен' : 'опередили цены'}` : null}
              color={rddLast && rddLast.value < 100 ? '#ff6b6b' : C.city} />
        <Tile label="Реальные доходы, Россия" value={last(rdd?.rf)?.value} unit="% г/г"
              note={plabel(last(rdd?.rf)?.period)} color={C.rf} />
      </div>
      {incLines.length ? <LineChart series={incLines} /> : null}
      <p className="hint">
        Отставание СДД области от российского — примерно постоянная величина,
        поэтому полезнее смотреть динамику, чем абсолютный разрыв. У РДД база
        «к тому же кварталу прошлого года»: ниже 100 % значит, что доходы
        отстали от цен, даже если в рублях выросли.
      </p>

      {/* ——— производство ——— */}
      <h3 className="ec-title"
          title="Индекс выпуска товаров и услуг по базовым видам экономической деятельности, % к соответствующему месяцу прошлого года">
        Выпуск товаров и услуг
      </h3>
      <p className="cf-sub">
        Строки «Российская Федерация» в этом файле нет — только округа и области,
        поэтому сравнение с Приволжским ФО, а не со страной
      </p>
      {outLines.length ? <LineChart series={outLines} /> : <p className="hint">Ряд выпуска пока пуст.</p>}
      <p className="hint">
        100 % — «столько же, сколько год назад». В месячном разрезе у региона
        бывают прыжки в десятки пунктов: один крупный отгрузочный месяц на
        небольшом предприятии перевешивает, поэтому для вывода о тренде
        сравнивай несколько месяцев подряд, а не одну точку.
      </p>

      {/* ——— откуда и чего нет ——— */}
      <details className="tb-how">
        <summary>Откуда это и чего здесь нет</summary>
        <table className="tb-how-table">
          <tbody>
            <tr>
              <th>Пензастат</th>
              <td className="tb-how-form">недоступен</td>
              <td>58.rosstat.gov.ru резолвится в 194.226.89.60, но TCP молчит и
                  на 80, и на 443 — и с локальной машины, и с сервера; старый
                  pensastat.gks.ru не резолвится вовсе. Проверено 27.11.2026.
                  Региональные цифры поэтому берутся из федеральных выгрузок
                  Росстата по субъектам: территориальный орган сдает в них те же
                  наблюдения.</td>
            </tr>
            <tr>
              <th>ИПЦ и выпуск</th>
              <td className="tb-how-form">{d.meta.cpi_file || '—'}<br />{d.meta.output_file || '—'}</td>
              <td>Лист на каждый месяц. ИПЦ — к предыдущему месяцу, годовая
                  инфляция считается у нас произведением 12 месяцев и появляется
                  только когда все 12 на месте; выпуск — сразу % к тому же месяцу
                  прошлого года. Глубина обоих — с 2022 года.</td>
            </tr>
            <tr>
              <th>Цены и жильё</th>
              <td className="tb-how-form">{d.meta.goods_file || '—'}<br />{d.meta.flat_vtor_file || '—'}</td>
              <td>В месячном файле цен лежат листы только текущего года, а
                  ссылки на квартальные файлы жилья прошлых лет на странице
                  висят, но отдают 404 (проверено на всех). Значит, эти два ряда
                  растут у нас в базе с каждым прогоном, и короткие линии на
                  графике — это начало накопления, а не потерянные данные.</td>
            </tr>
            <tr>
              <th>Чего нет</th>
              <td className="tb-how-form">зарплата и безработица по области</td>
              <td>Месячные ряды по субъектам живут на fedstat/ЕМИСС, а оттуда нам
                  403 (проверено с двух адресов). Вместо них — квартальные
                  доходы: тот же сюжет про кошельки, но данными, которые реально
                  открываются.</td>
            </tr>
            <tr>
              <th>Проверить цифру</th>
              <td className="tb-how-form">по имени файла</td>
              <td>Все имена файлов лежат в метаданных блока (и отдаются
                  эндпоинтом /api/penza), поэтому любую цифру можно открыть у
                  первоисточника: rosstat.gov.ru → «Цены, инфляция», «Уровень
                  жизни», «Национальные счета». Последняя загрузка —
                  {` ${d.meta.updated_at || '—'}`}.</td>
            </tr>
          </tbody>
        </table>
      </details>
    </section>
  )
}
