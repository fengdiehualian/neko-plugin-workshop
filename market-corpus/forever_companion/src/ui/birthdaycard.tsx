// 生日设置卡（1.3.1）：从 moment.tsx 抽出为共享组件（修订轮三）——「时光」页
// 与新手向导"记住生日"页同用一张卡，保存回调都走 update_settings 定向字段，
// 两处行为永远一致。
// 纯设置项：文本框式日期按钮展开**自绘月历**，选日→「确认」直接落盘。原生
// date input 整套退役：其内建弹层是浏览器 UI，「今天」按钮既不能改文案也不能
// 改行为，观感也和面板玻璃拟态割裂。月历是**内嵌展开**（长在卡片正常流里，
// 卡片随之变高），不是绝对定位浮层——Card 的玻璃层（overflow/backdrop-filter）
// 会把浮层裁没（实机踩坑 2026-09-14）。
// 交互口径：确认=保存（走 update_settings 定向字段，不新建动作）；纪念手记
// 开关在日期已设时即时保存（保存成功前不动本地草稿值——失败时 TmSwitch 按
// 乐观回滚契约翻回 checked，父级若提前写脏，回滚就翻到"已改未存"的假值上，
// 5s 轮询因服务端值不变不会纠正，1.3.1 审查轮修），未设日期时只存本地草稿、
// 随确认一并提交；清除只在已设日期时出现（空串 = 清除并休眠）；取消关月历并
// 丢弃草稿。未来日期不可选（生日不存在"还没过到的那一年"）。真校验在后端
// （发码前端翻译，i18n 契约）。能力开关在「功能」页，这里只管"记不记得、留不留念"。
import { Card, useEffect, useState } from "@neko/plugin-ui"
import type { BirthdayView, TFunc } from "./types"
import { monthLabel } from "./utils"
import { TmSwitch } from "./tmswitch"

export function BirthdaySettingsCard(props: {
  t: TFunc
  birthday?: BirthdayView
  canSave: boolean
  onSave: (date: string, keepDiary: boolean) => Promise<boolean>
}) {
  const { t, birthday, canSave, onSave } = props
  const saved = String(birthday?.date || "")
  const isSet = birthday?.set === true
  const [busy, setBusy] = useState(false)
  const [open, setOpen] = useState(false)
  const [draft, setDraft] = useState(saved)
  const [draftKeep, setDraftKeep] = useState(birthday?.keep_diary !== false)
  // 今天（本地日）；月历默认开到草稿/已存日期所在月，都空则开到今天
  const now = new Date()
  const todayIso = `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, "0")}-${String(now.getDate()).padStart(2, "0")}`
  const base = /^(\d{4})-(\d{2})/.exec(draft || saved || todayIso) || [todayIso, String(now.getFullYear()), String(now.getMonth() + 1)]
  const [viewY, setViewY] = useState(Number(base[1]))
  const [viewM, setViewM] = useState(Number(base[2]))

  useEffect(() => {
    setDraft(String(birthday?.date || ""))
    setDraftKeep(birthday?.keep_diary !== false)
  }, [birthday?.date, birthday?.keep_diary])

  function shiftMonth(delta: number) {
    const total = viewY * 12 + (viewM - 1) + delta
    if (total < 1900 * 12) return
    setViewY(Math.floor(total / 12))
    setViewM((total % 12) + 1)
  }

  function beginPick() {
    setDraft(saved)
    const a = /^(\d{4})-(\d{2})/.exec(saved || todayIso) || [todayIso, String(now.getFullYear()), String(now.getMonth() + 1)]
    setViewY(Number(a[1]))
    setViewM(Number(a[2]))
    setOpen(true)
  }

  function closePick() {
    setDraft(saved)
    setOpen(false)
  }

  async function commit(dateStr: string) {
    setBusy(true)
    const ok = await onSave(String(dateStr || ""), draftKeep)
    setBusy(false)
    if (ok) setOpen(false)
    return ok
  }

  async function onToggleKeep(v: boolean) {
    // 已设日期 → 即时保存：draftKeep 必须等保存成功再动（见文件头契约注）
    if (isSet && saved) {
      setBusy(true)
      const ok = await onSave(saved, v)
      setBusy(false)
      if (ok) setDraftKeep(v)
      return ok
    }
    // 未设日期 → 草稿态（纯本地无保存往返），随确认一并提交
    setDraftKeep(v)
    return true
  }

  const weekdays = [
    t("panel.calendar.wd1", { defaultValue: "一" }),
    t("panel.calendar.wd2", { defaultValue: "二" }),
    t("panel.calendar.wd3", { defaultValue: "三" }),
    t("panel.calendar.wd4", { defaultValue: "四" }),
    t("panel.calendar.wd5", { defaultValue: "五" }),
    t("panel.calendar.wd6", { defaultValue: "六" }),
    t("panel.calendar.wd7", { defaultValue: "日" }),
  ]
  const firstDow = (new Date(viewY, viewM - 1, 1).getDay() + 6) % 7 // 周一起（与「日历」页同制）
  const daysInMonth = new Date(viewY, viewM, 0).getDate()
  const cells: Array<{ iso: string; day: number } | null> = []
  for (let i = 0; i < firstDow; i += 1) cells.push(null)
  for (let d = 1; d <= daysInMonth; d += 1) {
    cells.push({ iso: `${viewY}-${String(viewM).padStart(2, "0")}-${String(d).padStart(2, "0")}`, day: d })
  }

  return (
    <Card title={t("panel.birthday.title", { defaultValue: "主人生日" })}>
      <div className="tm-bday-row">
        <button
          type="button"
          className={draft ? "tm-bday-field tm-bday-field--set" : "tm-bday-field"}
          disabled={!canSave || busy}
          onClick={beginPick}
        >
          {draft || t("panel.birthday.pick", { defaultValue: "选择日期" })}
        </button>
        <TmSwitch
          checked={draftKeep}
          disabled={!canSave || busy}
          small
          onChange={onToggleKeep}
          label={t("panel.birthday.keepDiary", { defaultValue: "当天留一条纪念手记" })}
        />
        {canSave && isSet ? (
          <span className="tm-bday-actions">
            <button type="button" className="tm-bday-btn tm-bday-btn--ghost" disabled={busy} onClick={() => commit("")}>
              {t("panel.birthday.clear", { defaultValue: "清除" })}
            </button>
          </span>
        ) : null}
      </div>
      {open ? (
        <div className="tm-bday-pop">
          <div className="tm-bday-pop-head">
            <button type="button" className="tm-bday-nav" disabled={busy} onClick={() => shiftMonth(-12)}>«</button>
            <button type="button" className="tm-bday-nav" disabled={busy} onClick={() => shiftMonth(-1)}>‹</button>
            <span className="tm-bday-pop-title">{monthLabel(t, `${viewY}-${String(viewM).padStart(2, "0")}`)}</span>
            <button type="button" className="tm-bday-nav" disabled={busy} onClick={() => shiftMonth(1)}>›</button>
            <button type="button" className="tm-bday-nav" disabled={busy} onClick={() => shiftMonth(12)}>»</button>
          </div>
          <div className="tm-bday-week">
            {weekdays.map((w, i) => (
              <span key={i} className="tm-bday-week-cell">{w}</span>
            ))}
          </div>
          <div className="tm-bday-grid">
            {cells.map((c, i) => c === null ? (
              <span key={i} className="tm-bday-day tm-bday-day--blank" />
            ) : (
              <button
                key={c.iso}
                type="button"
                disabled={c.iso > todayIso || busy}
                className={c.iso === draft ? "tm-bday-day tm-bday-day--sel" : c.iso === todayIso ? "tm-bday-day tm-bday-day--today" : "tm-bday-day"}
                onClick={() => setDraft(c.iso)}
              >
                {c.day}
              </button>
            ))}
          </div>
          <div className="tm-bday-pop-foot">
            <button type="button" className="tm-bday-btn tm-bday-btn--ghost" disabled={busy} onClick={closePick}>
              {t("panel.birthday.cancel", { defaultValue: "取消" })}
            </button>
            <button type="button" className="tm-bday-btn" disabled={!draft || busy} onClick={() => commit(draft)}>
              {t("panel.birthday.confirm", { defaultValue: "确认" })}
            </button>
          </div>
        </div>
      ) : null}
    </Card>
  )
}
