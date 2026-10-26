// Sending preferences and diagnostics share actions, not a wall of explanatory text.
import {
  Alert,
  Button,
  Field,
  Inline,
  Input,
  SegmentedControl,
  Slider,
  Stack,
  Text,
  useEffect,
  useRef,
  useState,
} from "@neko/plugin-ui";
import { callAction, extractCode } from "../shared";
import type { Surface } from "../shared";

export function AwarenessCard(props: {
  surface: Surface;
  mode: "settings" | "status";
  disabled?: boolean;
  onBusyChange?: (busy: boolean) => void;
  onRefreshFailed?: () => void;
  onRefreshRecovered?: () => void;
}) {
  const surface = props.surface;
  const t = surface.t;
  const state = surface.state || {};
  const awareness = state.awareness || {};
  const runState = state.run || {};
  const attachmentStatus = String(state.eager_attachment?.status || "idle");
  const configuredInterval = awareness.min_interval_sec ?? 60;
  const [intervalDraft, setIntervalDraft] = useState(configuredInterval);
  const configuredAttachmentTurns = state.config?.eager_attachment_turns ?? 1;
  const [attachmentTurnsDraft, setAttachmentTurnsDraft] = useState(configuredAttachmentTurns);
  const configuredCooldown = state.config?.cooldown_sec ?? 20;
  const [cooldownDraft, setCooldownDraft] = useState(String(configuredCooldown));
  const cooldownValue = Number(cooldownDraft);
  const cooldownValid = cooldownDraft.trim() !== "" && Number.isFinite(cooldownValue)
    && cooldownValue >= 0 && cooldownValue <= 3600;
  const [note, setNote] = useState("");
  const [failed, setFailed] = useState(false);
  const [pending, setPending] = useState("");
  const busyRef = useRef(false);
  const disabled = !!props.disabled || !!pending;
  useEffect(() => {
    setIntervalDraft(configuredInterval);
  }, [configuredInterval]);
  useEffect(() => {
    setCooldownDraft(String(configuredCooldown));
  }, [configuredCooldown]);
  useEffect(() => {
    setAttachmentTurnsDraft(configuredAttachmentTurns);
  }, [configuredAttachmentTurns]);

  const run = async (action: string, args: Record<string, unknown>) => {
    if (busyRef.current || props.disabled) return;
    busyRef.current = true;
    setPending(action);
    props.onBusyChange?.(true);
    setNote("");
    setFailed(false);
    try {
      const result = await callAction(surface, action, args);
      if (action === "set_reminder_interval") {
        if (result?.min_interval_sec !== undefined) setIntervalDraft(result.min_interval_sec);
        setNote(t("panel.awareness.interval.saved", { defaultValue: "提醒间隔已保存" }));
      } else if (action === "set_send_cooldown") {
        if (result?.cooldown_sec !== undefined) setCooldownDraft(String(result.cooldown_sec));
        setNote(t("panel.send.cooldown.saved", { defaultValue: "发送冷却已保存" }));
      } else if (action === "set_eager_attachment_turns") {
        if (result?.turns !== undefined) setAttachmentTurnsDraft(result.turns);
        setNote(t("panel.attachment.turns.saved", { defaultValue: "补图轮数已保存" }));
      } else if (action === "awareness_now" && result) {
        const status = String(result.status || "");
        setNote(t(`panel.awareness.status.${status}`, { defaultValue: status }));
      }
      try {
        await surface.api.refresh();
        props.onRefreshRecovered?.();
      } catch {
        props.onRefreshFailed?.();
      }
    } catch (error) {
      const raw = error instanceof Error ? error.message : String(error ?? "failed");
      const code = extractCode(raw);
      setFailed(true);
      setNote(t(`panel.error.${code}`, { defaultValue: code }));
    } finally {
      busyRef.current = false;
      setPending("");
      props.onBusyChange?.(false);
    }
  };
  const cadence = t(`panel.awareness.cadence.${String(awareness.inject_mode || "interval_n")}`, {
    defaultValue: "提醒门槛 {n} 轮 · 距上次提醒 {since} 轮",
  })
    .replace("{n}", String(awareness.inject_interval_n || 1))
    .replace("{since}", String(awareness.turns_since ?? 0));
  const statusItems = [
    {
      key: "status",
      label: t("panel.awareness.status", { defaultValue: "最近一次" }),
      value: t(`panel.awareness.status.${String(awareness.status || "")}`, {
        defaultValue: awareness.status || "—",
      }),
    },
    {
      key: "target",
      label: t("panel.awareness.target", { defaultValue: "注给" }),
      value: awareness.target || "—",
    },
    {
      key: "next",
      label: t("panel.awareness.next", { defaultValue: "下次最快" }),
      value: `${Math.ceil(awareness.min_next_wait_sec || 0)}s`,
    },
    {
      key: "cadence",
      label: t("panel.awareness.cadenceLabel", { defaultValue: "节奏" }),
      value: cadence,
    },
    {
      key: "driver",
      label: t("panel.awareness.driverLabel", { defaultValue: "驱动" }),
      value: t(`panel.awareness.driver.${String(awareness.driver || "")}`, {
        defaultValue: awareness.driver || "—",
      }),
    },
  ];
  const runItems = [
    { key: "turns", label: t("panel.run.turns", { defaultValue: "她的话轮" }), value: String(runState.turns || 0) },
    { key: "calls", label: t("panel.run.calls", { defaultValue: "她调用工具" }), value: String(runState.tool_calls || 0) },
    { key: "sent", label: t("panel.run.sent", { defaultValue: "发出成功" }), value: String(runState.sent || 0) },
    { key: "refused", label: t("panel.run.refused", { defaultValue: "被拦下" }), value: String(runState.refused || 0) },
    {
      key: "surface",
      label: t("panel.run.surface", { defaultValue: "常驻目录" }),
      value: String(runState.surface_categories || 0) + t("panel.run.surface.unit", { defaultValue: " 类" }),
    },
  ];
  const libraryItems = [
    { key: "total", label: t("panel.stat.total", { defaultValue: "收藏" }), value: String(state.counts?.total || 0) },
    { key: "available", label: t("panel.stat.available", { defaultValue: "可用" }), value: String(state.counts?.enabled || 0) },
    { key: "sent", label: t("panel.stat.sent", { defaultValue: "累计发出" }), value: String(state.counts?.sent_total || 0) },
    { key: "groups", label: t("panel.category.filter", { defaultValue: "分类" }), value: String(state.counts?.groups || 0) },
  ];
  const readouts = (items: Array<{ key: string; label: string; value: string }>) => (
    <dl className="sticker-readouts">
      {items.map((item) => (
        <div key={item.key}>
          <dt>{item.label}</dt>
          <dd>{item.value}</dd>
        </div>
      ))}
    </dl>
  );

  const diagnostics = (
    <div className="sticker-diagnostics">
      <h2 className="sticker-section-title">
        {t("panel.library.overview", { defaultValue: "图库概况" })}
      </h2>
      {readouts(libraryItems)}
      <h2 className="sticker-section-title">
        {t("panel.run.title", { defaultValue: "本次运行（重启插件归零）" })}
      </h2>
      {readouts(runItems)}
      {state.eagerness === "eager" ? (
        <Text>
          {t("panel.attachment.counts", { defaultValue: "补图选图 {attempts} 次 · 已提交 {sent} 张" })
            .replace("{attempts}", String(state.eager_attachment?.attempts || 0))
            .replace("{sent}", String(state.eager_attachment?.sent || 0))}
        </Text>
      ) : null}
      {runState.last_call ? (
        <Text>
          {t("panel.run.last", { defaultValue: "最近一次" }) + "：" + runState.last_call +
            (runState.last_reason ? " · " + runState.last_reason : "")}
        </Text>
      ) : null}
      <h2 className="sticker-section-title">
        {t("panel.awareness.title", { defaultValue: "配图提醒" })}
      </h2>
      {readouts(statusItems)}
      <Inline gap={8}>
        <Button tone="default" disabled={disabled} onClick={() => { run("awareness_now", {}); }}>
          {pending === "awareness_now"
            ? t("panel.awareness.checking", { defaultValue: "检查中…" })
            : t("panel.awareness.button", { defaultValue: "测试提醒" })}
        </Button>
      </Inline>
    </div>
  );

  return (
    <div className="sticker-settings">
      {note ? <Alert tone={failed ? "danger" : "info"} message={note} /> : null}
      {props.mode === "settings" ? (
        <div className="sticker-preferences">
          <section className="sticker-setting">
            <h2 className="sticker-section-title">
              {t("panel.awareness.eagerness", { defaultValue: "配图积极度" })}
            </h2>
            <SegmentedControl
              disabled={disabled}
              value={String(state.eagerness || "natural")}
              options={[
                { value: "reserved", label: t("panel.preference.reserved", { defaultValue: "矜持" }) },
                { value: "natural", label: t("panel.preference.natural", { defaultValue: "自然" }) },
                { value: "eager", label: t("panel.preference.eager", { defaultValue: "爱发" }) },
              ]}
              onChange={(next: any) => {
                run("set_eagerness", { eagerness: String(next || "natural") });
              }}
            />
            {state.eagerness === "eager" && attachmentStatus === "model_unbound" ? (
              <Alert tone="info" message={t("panel.attachment.unbound", {
                defaultValue: "请在插件模型配置中为“爱发补图选图”绑定文字模型。",
              })} />
            ) : state.eagerness === "eager" && attachmentStatus === "model_unavailable" ? (
              <Alert tone="info" message={t("panel.attachment.unavailable", {
                defaultValue: "补图选图模型暂不可用，请检查模型绑定与连接。",
              })} />
            ) : state.eagerness === "eager" && attachmentStatus === "selecting" ? (
              <Text>{t("panel.attachment.selecting", { defaultValue: "正在为本轮回复选图…" })}</Text>
            ) : null}
          </section>
          {state.eagerness === "eager" ? (
            <section className="sticker-setting">
              <Field label={t("fields.eager_attachment_turns", { defaultValue: "补图触发轮数" })}>
                <Stack gap={8}>
                  <Slider
                    value={attachmentTurnsDraft}
                    min={0}
                    max={10}
                    step={1}
                    showValue
                    disabled={disabled}
                    onChange={setAttachmentTurnsDraft}
                  />
                  <Inline gap={12} wrap>
                    <Text>
                      {attachmentTurnsDraft === 0
                        ? t("panel.attachment.turns.every", { defaultValue: "每轮帮选" })
                        : t("panel.attachment.turns.gap", {
                          defaultValue: "连续 {turns} 轮未配图后帮选",
                        }).replace("{turns}", String(attachmentTurnsDraft))}
                    </Text>
                    <Button
                      tone="default"
                      disabled={disabled || attachmentTurnsDraft === configuredAttachmentTurns}
                      onClick={() => {
                        run("set_eager_attachment_turns", { turns: attachmentTurnsDraft });
                      }}
                    >
                      {pending === "set_eager_attachment_turns"
                        ? t("panel.awareness.interval.saving", { defaultValue: "保存中" })
                        : t("actions.set_eager_attachment_turns.label", { defaultValue: "保存补图轮数" })}
                    </Button>
                  </Inline>
                </Stack>
              </Field>
            </section>
          ) : null}
          <section className="sticker-setting">
            <div className="sticker-setting-control">
              <Field
                label={t("fields.cooldown_sec", { defaultValue: "发送冷却（秒）" })}
                help={t("panel.send.cooldown.range", { defaultValue: "0–3600 秒，0 为关闭" })}
                error={!cooldownValid
                  ? t("panel.send.cooldown.invalid", { defaultValue: "请输入 0–3600 之间的数字" })
                  : ""}
              >
                <Input
                  type="number"
                  value={cooldownDraft}
                  min={0}
                  max={3600}
                  step={1}
                  invalid={!cooldownValid}
                  disabled={disabled}
                  onChange={setCooldownDraft}
                />
              </Field>
              <Button
                tone="default"
                disabled={disabled || !cooldownValid || cooldownValue === configuredCooldown}
                onClick={() => {
                  if (cooldownValid && cooldownValue !== configuredCooldown) {
                    run("set_send_cooldown", { cooldown_sec: cooldownValue });
                  }
                }}
              >
                {pending === "set_send_cooldown"
                  ? t("panel.awareness.interval.saving", { defaultValue: "保存中" })
                  : t("actions.set_send_cooldown.label", { defaultValue: "保存发送冷却" })}
              </Button>
            </div>
            <p className="sticker-setting-current">
              {configuredCooldown === 0
                ? t("panel.send.cooldown.off", { defaultValue: "关闭发送冷却" })
                : t("panel.awareness.send_cooldown", {
                  defaultValue: "当前发送冷却：{seconds} 秒",
                }).replace("{seconds}", String(configuredCooldown))}
            </p>
          </section>
          <details className="sticker-menu sticker-advanced">
            <summary>{t("panel.settings.advanced", { defaultValue: "高级设置" })}</summary>
            <div className="sticker-menu-body">
              <Field label={t("panel.awareness.min_interval_sec", { defaultValue: "提醒最小间隔（秒）" })}>
                <Stack gap={8}>
                  <Slider
                    value={intervalDraft}
                    min={0}
                    max={60}
                    step={1}
                    showValue
                    disabled={disabled}
                    onChange={setIntervalDraft}
                  />
                  <Inline gap={12} wrap>
                    <Text>
                      {intervalDraft === 0
                        ? t("panel.awareness.interval.off", { defaultValue: "提醒无时间限制" })
                        : t("panel.awareness.interval.seconds", {
                          defaultValue: "提醒最少间隔 {seconds} 秒",
                        }).replace("{seconds}", String(intervalDraft))}
                    </Text>
                    <Button
                      tone="default"
                      disabled={disabled || intervalDraft === configuredInterval}
                      onClick={() => {
                        run("set_reminder_interval", { min_interval_sec: intervalDraft });
                      }}
                    >
                      {pending === "set_reminder_interval"
                        ? t("panel.awareness.interval.saving", { defaultValue: "保存中" })
                        : t("panel.awareness.interval.save", { defaultValue: "保存" })}
                    </Button>
                  </Inline>
                </Stack>
              </Field>
            </div>
          </details>
          <details className="sticker-menu sticker-advanced">
            <summary>{t("panel.settings.diagnostics", { defaultValue: "运行诊断" })}</summary>
            <div className="sticker-menu-body">{diagnostics}</div>
          </details>
        </div>
      ) : diagnostics}
    </div>
  );
}
