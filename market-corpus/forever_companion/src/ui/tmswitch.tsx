// 动画开关（1.3.1）：面板全部"是/否"类开关的统一自绘组件，替换宿主 Kit 的
// Switch（那枚本质是 16px 原生 checkbox、零动画）。经典顺滑型：滑块 0.25s
// 缓动平移 + 轨道渐变换色；开启态浅蓝色系，与面板淡蓝磨砂底（rgba(147,197,253)
// 弥散光）同族。样式在 styles.ts 的 .tm-sw 段（含暗色孪生）。
//
// 乐观回滚契约（状态条/功能管理这类"点击后经确认+服务端往返才落定"的开关需要）：
// onChange 可返回 Promise——resolve 值 === false（取消/失败/被拒）时开关立即回落
// 到权威值 checked；其余情况本地保持，待父级刷新后 checked 变化时自动收编。
// 同步表单类调用点（设置页 updateForm）checked 立刻变化，天然满足。
import { useEffect, useState } from "@neko/plugin-ui"

export function TmSwitch(props: {
  checked: boolean
  onChange: (value: boolean) => any
  disabled?: boolean
  label?: any
  title?: string
  small?: boolean
  className?: string
}) {
  const { checked, onChange, disabled, label, title, small, className } = props
  const [ovr, setOvr] = useState<boolean | null>(null)
  // 权威值每次变化（含服务端刷新回写）→ 丢弃本地乐观值，回归受控
  useEffect(() => { setOvr(null) }, [checked])
  const shown = ovr === null ? !!checked : ovr

  async function fire() {
    if (disabled) return
    const next = !shown
    setOvr(next)
    try {
      const res = await Promise.resolve(onChange(next))
      if (res === false) setOvr(null)
    } catch {
      setOvr(null)
    }
  }

  const cls = "tm-sw"
    + (shown ? " tm-sw--on" : "")
    + (disabled ? " tm-sw--dis" : "")
    + (small ? " tm-sw--sm" : "")
    + (className ? " " + className : "")
  return (
    <label className={cls} title={title || undefined}>
      <input
        className="tm-sw-input"
        type="checkbox"
        checked={shown}
        disabled={!!disabled}
        onChange={() => { fire() }}
      />
      <span className="tm-sw-track"><span className="tm-sw-knob" /></span>
      {label ? <span className="tm-sw-text">{label}</span> : null}
    </label>
  )
}
