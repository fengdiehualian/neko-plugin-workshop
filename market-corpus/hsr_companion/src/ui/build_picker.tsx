import { useLocalState } from "@neko/plugin-ui"

type Item = { id: string; name: string; label?: string; note?: string; search?: string }

// Plugin-local presentation control: selected ID and temporary search text
// are separate. Typing a query never writes a new equipment/character ID.
export function BuildPicker({ id, label, value, items, placeholder, emptyLabel, disabled, t, onChange }: {
  id: string; label: string; value: string; items: Item[]; placeholder: string; emptyLabel: string
  disabled?: boolean; t: (key: string, args?: any) => string; onChange: (id: string) => any
}) {
  const [open, setOpen] = useLocalState("buildPickerOpen:" + id, false)
  const [query, setQuery] = useLocalState("buildPickerQuery:" + id, "")
  const [active, setActive] = useLocalState("buildPickerActive:" + id, 0)
  const selected = items.find(item => item.id === value)
  const needle = query.trim().toLocaleLowerCase()
  const matches = items.filter(item => [item.name, item.label, item.note, item.search].join(" ").toLocaleLowerCase().includes(needle))
  const choices = needle ? matches : [{ id: "", name: emptyLabel }, ...matches]
  const index = Math.max(0, Math.min(active, choices.length - 1))
  function show() { if (!disabled && !open) { setQuery(""); setActive(0); setOpen(true) } }
  function close() { setOpen(false); setQuery(""); setActive(0) }
  function choose(next: string) { if (!disabled) { close(); onChange(next) } }
  function keys(event: any) {
    if (disabled || event.isComposing || event.target?.__nekoComposing) return
    if (event.key === "Escape") { event.preventDefault(); close() }
    if (event.key === "ArrowDown" || event.key === "ArrowUp") {
      event.preventDefault()
      if (!open) show()
      else if (choices.length) setActive((index + (event.key === "ArrowDown" ? 1 : choices.length - 1)) % choices.length)
    }
    if (event.key === "Enter" && open && choices[index]) { event.preventDefault(); choose(choices[index].id) }
  }
  return <div className="neko-field" style={{ minWidth: 0 }}>
    <label key="label" className="neko-field-label" htmlFor={id}>{label}</label>
    <div key="control" style={{ position: "relative" }}>
      <input key="input" id={id} className="neko-input" role="combobox" aria-label={label} aria-autocomplete="list" aria-expanded={open ? "true" : "false"}
        aria-controls={id + "-options"} aria-activedescendant={open && choices.length ? id + "-option-" + index : undefined}
        autoComplete="off" disabled={disabled} placeholder={placeholder} value={open ? query : selected?.label || selected?.name || ""}
        onFocus={show} onClick={show} onKeyDown={keys}
        onCompositionStart={(event: any) => { event.target.__nekoComposing = true }}
        onCompositionEnd={(event: any) => { event.target.__nekoComposing = false; setQuery(event.target.value); setActive(0) }}
        onInput={(event: any) => { setQuery(event.target.value); setActive(0); setOpen(true) }}
        onBlur={(event: any) => { if (!event.currentTarget.parentElement.contains(event.relatedTarget)) close() }} />
      <div key="options" id={id + "-options"} role="listbox" aria-label={label} hidden={!open || disabled}
        style={{ display: open && !disabled ? "block" : "none", position: "absolute", zIndex: 20, top: "100%", left: 0, right: 0, maxHeight: "240px", overflowY: "auto",
          border: "1px solid var(--border)", borderRadius: "8px", background: "var(--surface-strong)", boxShadow: "0 6px 20px #0003" }}>
        {choices.map((item, i) => <button key={item.id} type="button" role="option" id={id + "-option-" + i}
          aria-selected={i === index ? "true" : "false"} tabIndex={-1}
          onMouseDown={(event: any) => event.preventDefault()} onClick={() => choose(item.id)}
          style={{ display: "block", width: "100%", border: 0, borderRadius: 0, padding: "10px 12px", textAlign: "left",
            font: "inherit", color: "var(--text)", background: i === index ? "rgba(64,158,255,.16)" : "transparent", cursor: "pointer" }}>
          <span style={{ display: "block" }}>{item.label || item.name}</span>
          {"note" in item && item.note ? <small style={{ display: "block", color: "var(--muted)", marginTop: "3px" }}>{item.note}</small> : null}
        </button>)}
        {!matches.length ? <div role="status" style={{ padding: "10px 12px", color: "var(--muted)" }}>{t("build.noMatches")}</div> : null}
      </div>
    </div>
    <small key="note" hidden={open || !selected?.note} style={{ display: !open && selected?.note ? "block" : "none", color: "inherit", opacity: .85 }}>{selected?.note || ""}</small>
  </div>
}
