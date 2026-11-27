// Ofz.jsx — закладка «ОФЗ»: живой борт целиком + фиксированная корзина для кривой.
//
// Два источника, два разных права на ошибку:
//
//   • GET /api/ofz/all — ВСЕ выпуски ОФЗ с борта «Т+: Гособлигации» (61 строка
//     по данным на 27.11.2026: 34 ПД, 21 ПК, 4 ИН, 2 АД). Это таблица, строка =
//     строка биржи, поэтому новые выпуски Минфина появляются в ней сами, в тот
//     же день: править код не надо никогда.
//   • GET /api/econ/bonds — дневной срез по фиксированной корзине
//     (OFZ_UNIVERSE, 13 выпусков). Она нужна только там, где постоянство состава
//     защищает смысл: кривая доходностей, её наклон и история рядов.
//
// Клик по любой строке таблицы добавляет выпуск на график. Если ряда ещё нет в
// базе, фронт просит GET /api/ofz/series?secid=… — бэкенд подтягивает историю
// YIELDCLOSE с биржи (и отдаёт кеш, если данные свежие). Так «новый выпуск»
// можно посмотреть в динамике, не дожидаясь, пока он попадёт в корзину.
//
// Выведенные выпуски (OFZ_LINEAGE) не исчезают молча: строка остаётся серой,
// с датой последней биржевой записи и указанием преемника. Такие строки приходят
// из /api/econ/bonds, потому что на борту их уже нет.

import { useEffect, useMemo, useState } from 'react'
import { LineChart } from './Econ.jsx'

const fmt2 = (n) => n.toFixed(2).replace('.', ',')
const fmt1 = (n) => (Math.abs(n) >= 100 ? Math.round(n).toLocaleString('ru-RU') : n.toFixed(1)).replace('.', ',')

// Цвет привязан к выпуску, а не к порядку на графике, иначе линии разъезжаются,
// стоит убрать или добавить серию
const PALETTE = ['#4c6ef5', '#845ef7', '#f06595', '#e8590c', '#fab005', '#20c997', '#adb5bd']
// отдельная палитра для выпусков, добавленных кликом: их цвет не должен
// «перекрашивать» корзину при следующем перерисовывании
const EXTRA_PALETTE = ['#3bc9db', '#a9e34b', '#ff9c6b', '#da77f2', '#748ffc', '#63e6be']

const GRAY = '#6c757d'

// Сколько строк живой таблицы показывать сразу (61 — это простыня)
const PAGE = 15

// Типы по-русски: подпись колонки + порядок фильтров
const KIND_LABEL = { PD: 'ПД', PK: 'ПК', IN: 'ИН', AD: 'АД', '??': '·' }
const KIND_FULL = { PD: 'ОФЗ-ПД', PK: 'ОФЗ-ПК', IN: 'ОФЗ-ИН', AD: 'ОФЗ-АД', '??': 'ОФЗ' }
const KIND_ORDER = ['PD', 'PK', 'IN', 'AD', '??']

// Сколько выпусков держать на графике по умолчанию
const DEFAULT_PICK = ['SU26207RMFS9', 'SU26242RMFS6', 'SU26249RMFS1', 'SU26247RMFS5']

const ru = (d) => (d ? d.split('-').reverse().join('.') : '—')

export default function Ofz() {
  const [data, setData] = useState(null)       // корзина: /api/econ/bonds
  const [board, setBoard] = useState(null)     // весь борт: /api/ofz/all
  const [extra, setExtra] = useState({})       // secid → ряд, подтянутый по клику
  const [busy, setBusy] = useState({})         // secid → идёт подтяжка истории
  const [failed, setFailed] = useState({})     // secid → текст ошибки (не молчим)
  const [error, setError] = useState('')
  const [picked, setPicked] = useState(DEFAULT_PICK)
  const [kind, setKind] = useState('PD')       // фильтр живой таблицы
  const [onlyBasket, setOnlyBasket] = useState(false)
  const [limit, setLimit] = useState(PAGE)

  useEffect(() => {
    const load = () => Promise.all([
      fetch('/api/econ/bonds?from=' + new Date(Date.now() - 400 * 864e5).toISOString().slice(0, 10))
        .then((r) => { if (!r.ok) throw new Error(`сервер ответил ${r.status}`); return r.json() }),
      fetch('/api/ofz/all').then((r) => {
        if (!r.ok) throw new Error(`борт ответил ${r.status}`)   // сюда мы не падаем
        return r.json()
      }),
    ])
      .then(([b, all]) => { setData(b); setBoard(all) })
      .catch((e) => setError(`Не удалось загрузить ОФЗ: ${e.message}`))
    load()
    // биржевой снимок обновляется планировщиком раз в 12 часов
    const id = setInterval(load, 30 * 60 * 1000)
    return () => clearInterval(id)
  }, [])

  // ——— единая модель строки: борт + выведенные из корзины ———
  const rows = useMemo(() => {
    if (!data && !board) return []
    const basket = new Set((data?.curve || []).map((c) => c.secid))
    const fromBoard = (board?.rows || []).map((c) => ({
      secid: c.secid,
      shortname: c.shortname,
      kind: c.kind,
      kindLabel: c.kind_label,
      matdate: c.matdate,
      coupon: c.coupon_prc,
      accrued: c.accrued,
      price: c.price_prc,
      // Δ дня без сегодняшней цены — фиктивное «0,00»: до начала сессии борт
      // отдаёт ноль, и читается это как «не изменилось», а не как «нет данных»
      chg: c.price_prc == null ? null : c.price_chg_prc,
      yield: c.yield_prc,
      turnover: c.valtoday_rur,
      size: c.issue_size_mlrd,
      inBasket: c.in_basket || basket.has(c.secid),
      retired: !!c.retired,
      snap: c.snap_date,
      hasSeries: !!c.has_series || !!(data?.series || {})[c.secid],
    }))
    // выведенные: на борту их уже нет — берём из корзинного ответа
    const retired = (data?.curve || []).filter((c) => c.retired).map((c) => ({
      secid: c.secid,
      shortname: c.shortname,
      kind: 'PD',
      kindLabel: 'ОФЗ-ПД',
      matdate: c.matdate,
      coupon: c.coupon_prc,
      accrued: c.accrued,
      price: c.price_prc,
      chg: c.price_chg_prc,
      yield: c.yield_prc,
      turnover: c.valtoday_rur,
      size: c.issue_size_mlrd,
      inBasket: false,
      retired: true,
      snap: c.snap_date,
      exitDate: c.exit_date,
      replName: c.replaced_by_name,
      hasSeries: !!(data.series || {})[c.secid],
    }))
    // Откатный слой: вне торговой сессии (и при первом запуске) свежего среза
    // борта нет — но вчерашний снимок по корзине есть всегда, и показывать
    // пустую таблицу вместо него смысла нет.
    const onBoard = new Set(fromBoard.map((r) => r.secid))
    const basketFallback = (data?.curve || []).filter((c) => !c.retired && !onBoard.has(c.secid)).map((c) => ({
      secid: c.secid,
      shortname: c.shortname,
      kind: 'PD',
      kindLabel: 'ОФЗ-ПД',
      matdate: c.matdate,
      coupon: c.coupon_prc,
      accrued: c.accrued,
      price: c.price_prc,
      chg: c.price_chg_prc,
      yield: c.yield_prc,
      turnover: c.valtoday_rur,
      size: c.issue_size_mlrd,
      inBasket: true,
      retired: false,
      snap: c.snap_date,
      exitDate: c.exit_date,
      replName: c.replaced_by_name,
      hasSeries: !!(data.series || {})[c.secid],
    }))
    const seen = new Set(fromBoard.map((r) => r.secid))
    return fromBoard
      .concat(retired.filter((r) => !seen.has(r.secid)), basketFallback)
      .sort((a, b) => (a.matdate || '').localeCompare(b.matdate || ''))
      // один и тот же выпуск мог прийти и с борта, и из откатного слоя
      .filter((r, i, arr) => arr.findIndex((x) => x.secid === r.secid) === i)
  }, [data, board])

  // Счётчики и фильтры — по строкам таблицы, а не по срезу борта: ночью борта
  // нет, и чип «Все 0» над тринадцатью строками был бы прямым противоречием
  const kinds = useMemo(() => {
    const m = {}
    rows.forEach((r) => { m[r.kind] = (m[r.kind] || 0) + 1 })
    return KIND_ORDER.filter((k) => m[k]).map((k) => ({ kind: k, n: m[k], label: KIND_LABEL[k] }))
  }, [rows])

  const shown = useMemo(() => rows.filter((r) => {
    if (onlyBasket && !r.inBasket && !r.retired) return false
    return kind === 'ALL' || r.kind === kind
  }), [rows, kind, onlyBasket])

  // ——— корзина: наклон и счётчик считаются только по живым ПД из корзины ———
  const basket = useMemo(() => (data?.curve || []).filter((c) => !c.retired && c.yield_prc != null), [data])
  const slope = basket.length > 1 ? basket[basket.length - 1].yield_prc - basket[0].yield_prc : null

  // ——— цвета: корзина из основной палитры,добавленные кликом — из дополнительной ———
  const colorOf = useMemo(() => {
    const m = {}
    ;(data?.curve || []).filter((c) => !c.retired).forEach((c, i) => {
      m[c.secid] = PALETTE[i % PALETTE.length]
    })
    ;(data?.curve || []).filter((c) => c.retired).forEach((c) => { m[c.secid] = GRAY })
    let k = 0
    // цвет назначается по порядку выбора, а не по факту загрузки ряда: иначе
    // линия на миг серела, пока история подтягивается
    picked.forEach((secid) => {
      if (!m[secid]) { m[secid] = EXTRA_PALETTE[k % EXTRA_PALETTE.length]; k += 1 }
    })
    return m
  }, [data, extra, picked])

  // ——— линии графика ———
  const series = useMemo(() => {
    const byId = Object.fromEntries(rows.map((r) => [r.secid, r]))
    return picked
      .map((secid) => {
        const r = byId[secid]
        const pts = (data?.series || {})[secid] || extra[secid]?.points || []
        return {
          key: secid,
          label: r ? `${r.shortname} (${(r.matdate || '').slice(0, 4)})` : secid,
          color: colorOf[secid] || GRAY,
          points: pts.map((p) => ({ period: p.date, value: p.yield })),
        }
      })
      .filter((s) => s.points.length > 1)
  }, [picked, rows, data, extra, colorOf])

  // клик по строке: снять линию или добавить, при необходимости — подтянуть ряд
  const toggle = async (r) => {
    if (picked.includes(r.secid)) {
      setPicked((p) => p.filter((k) => k !== r.secid))
      return
    }
    setPicked((p) => [...p, r.secid])
    // ряд уже в состоянии? тогда не трогаем сеть. hasSeries у борта означает
    // «есть в базе», но в ответе /api/econ/bonds чужие выпуски не приходят —
    // поэтому смотреть надо на то, что реально лежит у клиента
    const known = (data?.series || {})[r.secid] || extra[r.secid]?.points
    if (known?.length > 1 || busy[r.secid]) return
    setBusy((b) => ({ ...b, [r.secid]: true }))
    setFailed((f) => { const n = { ...f }; delete n[r.secid]; return n })
    try {
      const res = await fetch(`/api/ofz/series?secid=${encodeURIComponent(r.secid)}`)
      const j = await res.json()
      if (!res.ok) throw new Error(j.error || `сервер ответил ${res.status}`)
      // у индексируемых выпусков биржа доходность не рассчитывает: ряд пустой,
      // и линия из него не получится — сказать это, а не рисовать «на графике»
      if (!j.points || j.points.length < 2) {
        throw new Error('у биржи нет расчётной доходности по этому выпуску')
      }
      setExtra((e) => ({ ...e, [r.secid]: { shortname: j.shortname, points: j.points } }))
    } catch (e) {
      // не молчим: без строки в подсказке человек решит, что у выпуска нет истории
      setFailed((f) => ({ ...f, [r.secid]: e.message }))
      setPicked((p) => p.filter((k) => k !== r.secid))
    } finally {
      setBusy((b) => { const n = { ...b }; delete n[r.secid]; return n })
    }
  }

  if (error) return <section className="ofz"><div className="error">{error}</div></section>
  if (!data && !board) return <section className="ofz"><p className="hint">Загрузка ОФЗ…</p></section>

  const nBoard = (board?.rows || []).length
  const snap = board?.snap_date || data?.snap_date
  // борта нет → таблица собрана только из корзины, и надо это сказать
  const partial = nBoard === 0

  return (
    <section className="ofz" aria-label="Рынок ОФЗ">
      <h2>
        🧱 ОФЗ
        {slope != null && (
          <span className="ec-slope"
                title={`${basket[basket.length - 1].shortname} минус ${basket[0].shortname} по корзине из ${basket.length} выпусков: сколько просят за длинные деньги сверх коротких`}>
            наклон {slope >= 0 ? '+' : ''}{fmt1(slope)} п.п.
          </span>
        )}
      </h2>
      <p className="cf-sub">
        {partial ? `среза борта нет — показаны ${rows.length} выпусков корзины по снимку ${snap}`
                 : `${nBoard} выпусков на борту «Т+: Гособлигации» · снимок ${snap}`}
        {' · '}кривая по корзине {basket.length}
        {rows.some((r) => r.retired) ? ` + ${rows.filter((r) => r.retired).length} выведенн${rows.filter((r) => r.retired).length === 1 ? 'ый' : 'ых'}` : ''}
        {' · '}клик по строке добавляет выпуск на график
      </p>

      <p className="cf-sub ofz-legend"
         title="ПД — постоянный купон; ПК — переменный купон, зависящий от ставки рынка; ИН — индексируемый к инфляции; АД — амортизационный долг, номинал погашается частями">
        Сокращения: <b>ПД</b> — постоянный купон · <b>ПК</b> — переменный купон ·
        <b>ИН</b> — индексируемый к инфляции · <b>АД</b> — амортизационный долг
      </p>

      {/* ——— живой борт: все выпуски, включая те, кого в корзине нет ——— */}
      <h3 className="ec-title"
          title="Таблица = биржевой борт целиком. Новый выпуск Минфина появляется здесь сам, на следующий после размещения день: состав здесь никто не держит руками">
        Все ОФЗ на бирже
      </h3>
      <div className="ec-chips">
        <button type="button" className={`ec-chip${kind === 'ALL' ? ' on' : ''}`}
                style={{ '--c': '#86869a' }}
                title={partial ? 'все строки, которые есть на этот снимок' : `все ${nBoard} выпусков с борта`}
                onClick={() => { setKind('ALL'); setLimit(PAGE) }}>
          Все {rows.length}
        </button>
        {kinds.map((k) => (
          <button key={k.kind} type="button" className={`ec-chip${kind === k.kind ? ' on' : ''}`}
                  style={{ '--c': k.kind === 'PD' ? '#4c6ef5' : '#86869a' }}
                  title={`${KIND_FULL[k.kind] || ''} · ${(board?.notes || {})[`ОФЗ-${KIND_LABEL[k.kind]}`] || ''}`.trim()}
                  onClick={() => { setKind(k.kind); setLimit(PAGE) }}>
            {KIND_LABEL[k.kind] || k.kind} {k.n}
          </button>
        ))}
        <button type="button" className={`ec-chip${onlyBasket ? ' on' : ''}`}
                style={{ '--c': '#fab005' }}
                title="только те, что входят в корзину кривой (OFZ_UNIVERSE)"
                onClick={() => { setOnlyBasket((v) => !v); setLimit(PAGE) }}>
          в корзине
        </button>
      </div>

      <div className="ec-scroll">
      <table className="ec-table">
        <thead>
          <tr><th>Выпуск</th><th>Тип</th><th>Погашение</th>
              <th title="купон, % годовых от номинала; у ОФЗ-ПК плавает и на борта не выкладывается">Купон</th>
              <th title="последняя сделка, % от номинала (1000 ₽)">Цена</th><th>Δ дня</th>
              <th title="доходность к погашению; у индексируемых и инфляционных биржа её не считает">Доходность</th>
              <th>Оборот, млн ₽</th></tr>
        </thead>
        <tbody>
          {shown.slice(0, limit).map((r) => (
            <tr key={r.secid}
                onClick={() => toggle(r)}
                className={[
                  picked.includes(r.secid) ? 'row-on' : '',
                  r.retired ? 'ec-retired' : '',
                ].filter(Boolean).join(' ')}
                title={[
                  `${r.inBasket ? 'в корзине кривой' : 'не в корзине — клик добавит линию на график'}`,
                  r.retired ? `погашен ${ru(r.exitDate || r.matdate)}, в корзине его место занимает ${r.replName ?? '—'}` : '',
                  r.exitDate && !r.retired ? `выйдет ${ru(r.exitDate)}` : '',
                  `НКД ${r.accrued != null ? fmt2(r.accrued) : '—'} ₽ · объём ${r.size != null ? fmt1(r.size) : '—'} млрд ₽`,
                  r.turnover == null ? 'оборот: данных нет' : `оборот ${fmt1(r.turnover / 1e6)} млн ₽${r.turnover === 0 ? ' — сегодня сделок не было' : ''}`,
                  r.yield == null ? (r.kind === 'PK'
                    ? 'доходность: биржа не рассчитывает (купон следует за ставкой overnight)'
                    : r.kind === 'IN' ? 'доходность реальная (к инфляции), с номинальной не сравниваем'
                    : 'доходности нет: сделок, по которым её считать, не было') : '',
                  busy[r.secid] ? 'подтягиваем историю доходности с биржи…' : '',
                  failed[r.secid] ? `не вышло: ${failed[r.secid]}` : '',
                  r.retired ? `последняя биржевая запись ${r.snap}` : '',
                ].filter(Boolean).join(' · ')}>
              <td>
                <i className="mt-dot" style={{ background: colorOf[r.secid] || '#3a3a52' }} />
                {r.shortname}
                {r.inBasket && <em className="ec-badge soon2">в корзине</em>}
                {picked.includes(r.secid) && <em className="ec-badge on2">на графике</em>}
                {r.exitDate && (
                  <em className={`ec-badge${r.retired ? ' off' : ' soon'}`}>
                    {r.retired ? `погашен ${ru(r.exitDate)}` : `выйдет ${ru(r.exitDate)}`}
                  </em>
                )}
                {busy[r.secid] && <em className="ec-badge">ждём…</em>}
              </td>
              <td className="ec-mat" title={`${r.kindLabel} · ${(board?.notes || {})[r.kindLabel] || ''}`}>
                {KIND_LABEL[r.kind] || r.kind}
              </td>
              <td className="ec-mat">{r.matdate || '—'}</td>
              <td className="ec-cpn" title={r.kind === 'PK' ? 'купон плавающий, на борту значения нет' : 'купон, % годовых от номинала'}>
                {r.coupon != null ? `${fmt2(r.coupon)}%` : '—'}
              </td>
              <td className="ec-price">{r.price != null ? fmt2(r.price) : '—'}</td>
              <td className={`ec-chg${(r.chg ?? 0) > 0 ? ' up' : (r.chg ?? 0) < 0 ? ' down' : ''}`}>
                {r.chg != null ? `${r.chg > 0 ? '+' : ''}${fmt2(r.chg)}` : '—'}
              </td>
              <td className="ec-yld">{r.yield != null ? `${fmt2(r.yield)}%` : '—'}</td>
              <td className="ec-cpn">{r.turnover != null ? fmt1(r.turnover / 1e6) : '—'}</td>
            </tr>
          ))}
        </tbody>
      </table>
      </div>
      {shown.length > limit && (
        <button type="button" className="table-more" onClick={() => setLimit((v) => v + 20)}>
          Показать ещё 20 из {shown.length - limit}
        </button>
      )}
      {shown.length === 0 && (
        <p className="hint">По этому фильтру строк нет — попробуй «Все» или сними
          отметку «в корзине».</p>
      )}
      {shown.length > 0 && shown.length <= limit && (
        <p className="hint">Показаны все {shown.length} строк этого фильтра.</p>
      )}

      <p className="hint">
        Доходность есть не у всех: у ОФЗ-ПК (21 выпуск) биржа её не рассчитывает —
        купон следует за ставкой overnight, и цифра зависела бы от прогноза решений
        ЦБ; у ОФЗ-ИН (4) доходность реальная, к инфляции, рядом с номинальной её
        читать нельзя; у ОФЗ-АД (2) доходом служит график погашения тела. Прочерк
        здесь — это «данных нет», а не «ноль». У индексируемых выпусков линия на
        графике может обрываться посреди окна: ММВБ выкладывает YIELDCLOSE не по
        всем дням и не по всем выпускам (проверено на ОФЗ 29007 — ряд кончается
        19.12.2025), и это тоже отсутствие данных, а не «доходность перестала
        меняться».
      </p>

      {/* ——— чем дольше держишь, тем сильнее ожидания ——— */}
      <h3 className="ec-title">Как менялись доходности</h3>
      <div className="ec-chips">
        {rows.filter((r) => r.inBasket || r.retired || picked.includes(r.secid)).map((r) => (
          <button key={r.secid} type="button"
                  className={`ec-chip${picked.includes(r.secid) ? ' on' : ''}${r.retired ? ' off' : ''}`}
                  style={{ '--c': colorOf[r.secid] || GRAY }}
                  title={`${r.shortname}: погашение ${r.matdate}, купон ${r.coupon != null ? fmt2(r.coupon) + '%' : '—'}${
                    r.retired ? ` · выведен ${ru(r.exitDate)}, замена — ${r.replName ?? 'не выбрана'}` : ''}`}
                  onClick={() => toggle(r)}>
            {r.shortname}{r.retired ? ' †' : ''}
          </button>
        ))}
      </div>
      {series.length > 0
        ? <LineChart series={series} />
        : <p className="hint">Выбери хотя бы два выпуска в таблице выше — и на графике появится сравнение.</p>}

      <details className="tb-how">
        <summary>Как выбраны выпуски в корзине</summary>
        <table className="tb-how-table">
          <tbody>
            <tr>
              <th>Зачем вообще корзина</th>
              <td className="tb-how-form">13 из {nBoard || 61}</td>
              <td>Таблица «Все ОФЗ на бирже» живёт целиком по борту — там новые
                  выпуски появляются сами. Корзина нужна только для кривой и её
                  наклона: если пускать туда состав по обороту дня, ряд доходностей
                  переписывался бы задним числом и сравнение «как менялись
                  ожидания» потеряло бы смысл.</td>
            </tr>
            <tr>
              <th>Только ОФЗ-ПД</th>
              <td className="tb-how-form">34 ПД, 21 ПК, 4 ИН, 2 АД</td>
              <td>В кривую берутся выпуски с постоянным купоном. У ОФЗ-ПК купон
                  индексируется к ставке overnight, у ОФЗ-ИН — к инфляции, у
                  ОФЗ-АД доходом служит график погашения тела: их доходность
                  описывает ожидания по ставке и ценам, а не срок до погашения.</td>
            </tr>
            <tr>
              <th>Горизонт 2027–2041</th>
              <td className="tb-how-form">34 подходящих, взято 13</td>
              <td>Примерно по выпуску на каждые полтора года погашения: от короткого
                  26207 (февраль 2027) до 26238 (май 2041). Тринадцать линий на
                  графике ещё читаются, тридцать четыре — уже нет. В 2027-м теперь
                  два выпуска (26207 и 26232): первый гасится в феврале, и без
                  второго короткий конец кривой повис бы на 2028 годе.</td>
            </tr>
            <tr>
              <th>Состав зафиксирован</th>
              <td className="tb-how-form">OFZ_UNIVERSE в econ.py</td>
              <td>Правится руками, автоматической пересборки нет. Любой выпуск
                  борта при этом можно посмотреть в динамике прямо сейчас — клик по
                  строке подтянет его историю доходности с биржи; это не меняет
                  корзину и не трогает уже нарисованные линии.</td>
            </tr>
            <tr>
              <th>Ликвидность</th>
              <td className="tb-how-form">оборот 30–1800 млн ₽/день</td>
              <td>Взятые выпуски ликвидны неодинаково: у 26235 (2031) и 26207
                  (2027) оборот тонкий, поэтому цена последней сделки и доходность
                  на этих участках могут отставать и прыгать — изломы кривой там
                  осторожнее читать как шум, а не как сигнал.</td>
            </tr>
            <tr>
              <th>Что не вошло</th>
              <td className="tb-how-form">26254, 26240, 26248</td>
              <td>Это ОФЗ-ПД с бо́льшим оборотом дня, чем у нескольких взятых:
                  26254 (октябрь 2040, ~1,9 млрд ₽), 26240 (июль 2036, ~1,1 млрд),
                  26248 (май 2040, ~0,9 млрд). Не брали их ради равномерности
                  сроков: на 2036–2040 уже есть точки 26245, 26243, 26253, 26247,
                  а дальше 2041 горизонт не нужен.</td>
            </tr>
            <tr>
              <th>Выведенные выпуски</th>
              <td className="tb-how-form">OFZ_LINEAGE в econ.py</td>
              <td>Дата погашения известна заранее, поэтому выпуск не исчезает из
                  таблицы втихаря. Пока он торгуется — с жёлтой меткой «выйдет
                  ДД.ММ.ГГГГ» и указанием преемника в подсказке; после погашения
                  строка остаётся серой, с зачёркнутой доходностью, датой последней
                  биржевой записи и подписью, чем выпуск заменён. Первый записанный
                  уход: ОФЗ 26207 (погашение 03.02.2027) → ОФЗ 26232.</td>
            </tr>
            <tr>
              <th>Справочник выпуска</th>
              <td className="tb-how-form">карточка ММВБ, раз в месяц</td>
              <td>Дата эмиссии, купон, номинал и объём выпуска — из
                  <code> /iss/securities/&lt;ISIN&gt;/issue.json</code>; сама
                  доходность и цена — из сегодняшнего борта TQOB.</td>
            </tr>
          </tbody>
        </table>
        <p className="hint">
          Все цифры здесь сняты с живого борта TQOB 27.11.2026. Если состав
          эмиссии изменится, счётчики поплывут — блок это отражает, а не прячет.
        </p>
      </details>

      <p className="hint">
        Цена — последняя сделка в процентах от номинала (1000 ₽); НКД, объём
        выпуска и оборот дня — в подсказке строки. Наклон кривой — сколько рынок
        просит сверх коротких ставок за длинные деньги: пологая или перевёрнутая
        кривая — ожидание быстрого смягчения ЦБ, крутая — что низких ставок
        в обозримом будущем не будет. Наклон считается по корзине, чтобы он не
        менялся от того, какие ещё бумаги сегодня торгуются на борту.
      </p>
    </section>
  )
}
