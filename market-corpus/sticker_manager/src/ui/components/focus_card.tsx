// Standalone detail workspace: no overlay, fixed descendant, or nested kit Card.
import {
  Alert,
  Button,
  Field,
  Inline,
  Input,
  Select,
  Stack,
  StatusBadge,
  Text,
  useConfirm,
  useRef,
  useState,
} from "@neko/plugin-ui";
import { useStickerPreview } from "../preview";
import { callAction, categoryOptions, extractCode, formatTime } from "../shared";
import type { StickerRow, Surface } from "../shared";

export function FocusCard(props: {
  key?: string;
  surface: Surface;
  row: StickerRow;
  onExit: () => void;
  onRefreshFailed?: () => void;
  onRefreshRecovered?: () => void;
}) {
  const row = props.row;
  const surface = props.surface;
  const t = surface.t;
  const confirm = useConfirm();
  const { preview, loading, boxRef, retry, onError } = useStickerPreview(surface, row.id, true);
  const [note, setNote] = useState("");
  const [busy, setBusy] = useState("");
  const [refreshFailed, setRefreshFailed] = useState(false);
  const refreshFailedRef = useRef(false);
  const busyRef = useRef(false);
  const [editing, setEditing] = useState(false);
  const [editDesc, setEditDesc] = useState(row.desc || "");
  const [editTags, setEditTags] = useState((row.tags || []).join(","));
  const [editGroup, setEditGroup] = useState(row.group || "");
  const [editCaption, setEditCaption] = useState(row.caption || "");
  const [editVisible, setEditVisible] = useState(row.visible_text || "");

  const resetDraft = () => {
    setEditDesc(row.desc || "");
    setEditTags((row.tags || []).join(","));
    setEditGroup(row.group || "");
    setEditCaption(row.caption || "");
    setEditVisible(row.visible_text || "");
  };
  const reportError = (error: unknown) => {
    const raw = error instanceof Error ? error.message : String(error ?? "failed");
    const code = extractCode(raw);
    setNote(t(`panel.error.${code}`, { defaultValue: code }));
  };
  const refreshAfterAction = async () => {
    try {
      await surface.api.refresh();
      refreshFailedRef.current = false;
      setRefreshFailed(false);
      if (props.onRefreshRecovered) props.onRefreshRecovered();
    } catch {
      refreshFailedRef.current = true;
      setRefreshFailed(true);
      if (props.onRefreshFailed) props.onRefreshFailed();
    }
  };
  const retryRefresh = async () => {
    if (busyRef.current) return;
    busyRef.current = true;
    setBusy("refresh");
    try {
      await refreshAfterAction();
    } finally {
      busyRef.current = false;
      setBusy("");
    }
  };
  const run = async (
    actionId: string,
    args: Record<string, unknown>,
  ): Promise<boolean> => {
    if (busyRef.current || refreshFailedRef.current) return false;
    busyRef.current = true;
    setBusy(actionId);
    setNote("");
    try {
      await callAction(surface, actionId, args);
      await refreshAfterAction();
      return true;
    } catch (error) {
      console.warn("sticker_manager action failed", actionId, error);
      reportError(error);
      return false;
    } finally {
      busyRef.current = false;
      setBusy("");
    }
  };
  const save = async () => {
    const ok = await run("update", {
      id: row.id,
      desc: editDesc,
      tags: editTags,
      group: editGroup,
      caption: editCaption,
      visible_text: editVisible,
    });
    if (ok) setEditing(false);
  };
  const remove = async () => {
    if (busyRef.current || refreshFailedRef.current) return;
    busyRef.current = true;
    setBusy("confirm");
    setNote("");
    let answer = false;
    try {
      answer = await confirm({
        title: t("panel.remove.title", { defaultValue: "删除表情包" }),
        message: t("panel.remove.message", {
          defaultValue: "这张图和它的记录都会被删掉，不可恢复。",
        }),
        tone: "danger",
      });
    } catch (error) {
      reportError(error);
    } finally {
      busyRef.current = false;
      setBusy("");
    }
    if (answer && await run("remove", { id: row.id })) props.onExit();
  };
  const details = [
    { key: "id", label: "ID", value: row.id },
    {
      key: "desc",
      label: t("panel.detail.desc", { defaultValue: "描述" }),
      value: row.desc || "—",
    },
    {
      key: "caption",
      label: t("panel.detail.caption", { defaultValue: "梗义" }),
      value: row.caption || "—",
    },
    {
      key: "visible",
      label: t("panel.detail.visible", { defaultValue: "图内原文" }),
      value: row.visible_text || "—",
    },
    {
      key: "group",
      label: t("panel.detail.group", { defaultValue: "分类" }),
      value: row.group || "—",
    },
    {
      key: "tags",
      label: t("panel.detail.tags", { defaultValue: "标签" }),
      value: (row.tags || []).join(" / ") || "—",
    },
    {
      key: "uses",
      label: t("panel.thumb.uses", { defaultValue: "发出次数" }),
      value: String(row.use_count || 0),
    },
    {
      key: "last",
      label: t("panel.thumb.last", { defaultValue: "最近发出" }),
      value: formatTime(row.last_used_at),
    },
    {
      key: "added",
      label: t("panel.detail.added", { defaultValue: "入库" }),
      value: formatTime(row.added_at),
    },
  ];

  return (
    <div
      id="sticker-focus-workspace"
      style={{ display: "grid", gap: "16px", minWidth: 0 }}
    >
      <div
        id="sticker-focus-back"
        style={{
          position: "sticky",
          top: "0px",
          zIndex: 1,
          minWidth: 0,
          background: "var(--bg)",
          padding: "8px 0",
          borderBottom: "1px solid var(--border)",
        }}
      >
        <Inline gap={8} align="center" wrap>
          <Button disabled={!!busy} onClick={props.onExit}>
            {t("panel.focus.back", { defaultValue: "返回图库" })}
          </Button>
          <div style={{ minWidth: 0, flex: "1 1 140px", overflowWrap: "anywhere" }}>
            <Text>{row.group || t("panel.group.none", { defaultValue: "未分组" })}</Text>
          </div>
          {row.disabled ? (
            <StatusBadge
              tone="warning"
              label={t("panel.badge.disabled", { defaultValue: "已禁用" })}
            />
          ) : null}
        </Inline>
      </div>
      <h2
        style={{
          margin: 0,
          fontSize: "18px",
          lineHeight: 1.5,
          overflowWrap: "anywhere",
        }}
      >
        {row.desc || row.caption || row.id}
      </h2>
      {note ? <Alert tone="danger" message={note} /> : null}
      {refreshFailed ? (
        <Stack gap={8}>
          <Alert
            tone="warning"
            message={t("panel.refresh.failed", {
              defaultValue: "操作已完成，但列表刷新失败。",
            })}
          />
          <Inline gap={8}>
            <Button disabled={!!busy} onClick={retryRefresh}>
              {busy === "refresh"
                ? t("panel.refresh.loading", { defaultValue: "刷新中…" })
                : t("panel.refresh.retry", { defaultValue: "重新刷新" })}
            </Button>
          </Inline>
        </Stack>
      ) : null}
      <div
        style={{
          display: "grid",
          gridTemplateColumns: "repeat(auto-fit, minmax(min(100%, 280px), 1fr))",
          gap: "20px",
          minWidth: 0,
          alignItems: "start",
        }}
      >
        <div
          ref={boxRef}
          style={{
            width: "100%",
            minWidth: 0,
            height: "min(420px, 58vh)",
            minHeight: "160px",
            display: "flex",
            alignItems: "center",
            justifyContent: "center",
            background: "rgba(128, 128, 128, 0.10)",
            border: "1px solid rgba(128, 128, 128, 0.25)",
            borderRadius: "8px",
            overflow: "hidden",
          }}
        >
          {preview ? (
            <img
              src={preview}
              alt={row.desc || row.id}
              onError={onError}
              style={{
                maxWidth: "100%",
                maxHeight: "100%",
                width: "auto",
                height: "auto",
                objectFit: "scale-down",
                display: "block",
              }}
            />
          ) : (
            <Stack gap={8}>
              <Text>
                {loading
                  ? t("panel.tile.loading", { defaultValue: "加载中…" })
                  : t("panel.tile.failed", { defaultValue: "图不可用" })}
              </Text>
              {!loading ? (
                <Button onClick={retry}>
                  {t("panel.preview.retry", { defaultValue: "重试图片" })}
                </Button>
              ) : null}
            </Stack>
          )}
        </div>
        <div style={{ minWidth: 0 }}>
          {editing ? (
            <Stack gap={10}>
              <Field label={t("panel.edit.desc", { defaultValue: "描述（面板里的短标签）" })}>
                <Input
                  value={editDesc}
                  disabled={!!busy}
                  onChange={setEditDesc}
                  placeholder={t("panel.edit.desc.ph", {
                    defaultValue: "一句话说明图里在干什么",
                  })}
                />
              </Field>
              <Field label={t("panel.edit.caption", {
                defaultValue: "梗义（她选图时看到的正文；留空=清掉标注）",
              })}>
                <Input
                  value={editCaption}
                  disabled={!!busy}
                  onChange={setEditCaption}
                  placeholder={t("panel.edit.caption.ph", {
                    defaultValue: "例：被催了很久之后终于交差，得意中带点解脱",
                  })}
                />
              </Field>
              <Field label={t("panel.edit.visible", {
                defaultValue: "图内原文（只帮她搜到，不上目录）",
              })}>
                <Input
                  value={editVisible}
                  disabled={!!busy}
                  onChange={setEditVisible}
                  placeholder={t("panel.edit.visible.ph", { defaultValue: "例：就这？" })}
                />
              </Field>
              <Field label={t("panel.edit.tags", { defaultValue: "标签（逗号分隔）" })}>
                <Input
                  value={editTags}
                  disabled={!!busy}
                  onChange={setEditTags}
                  placeholder="开心, 猫"
                />
              </Field>
              <Field label={t("panel.edit.group", {
                defaultValue: "套图分类（只能选已有的；要新名字先点「新建分类」）",
              })}>
                <Select
                  value={editGroup}
                  disabled={!!busy}
                  options={categoryOptions(surface, t, String(row.zone || ""))}
                  onChange={(next: any) => setEditGroup(String(next ?? ""))}
                />
              </Field>
              <Inline gap={8} wrap>
                <Button tone="primary" disabled={!!busy} onClick={save}>
                  {busy
                    ? t("panel.edit.saving", { defaultValue: "保存中…" })
                    : t("panel.edit.save", { defaultValue: "保存" })}
                </Button>
                <Button
                  disabled={!!busy}
                  onClick={() => {
                    resetDraft();
                    setEditing(false);
                    setNote("");
                  }}
                >
                  {t("panel.edit.cancel", { defaultValue: "取消" })}
                </Button>
              </Inline>
            </Stack>
          ) : (
            <dl style={{ margin: 0, display: "grid", gap: "10px", minWidth: 0 }}>
              {details.map((item) => (
                <div
                  key={item.key}
                  style={{
                    display: "grid",
                    gap: "3px",
                    minWidth: 0,
                    paddingBottom: "10px",
                    borderBottom: "1px solid rgba(128, 128, 128, 0.18)",
                  }}
                >
                  <dt style={{ fontSize: "12px", color: "var(--muted)" }}>
                    {item.label}
                  </dt>
                  <dd style={{ margin: 0, lineHeight: 1.5, overflowWrap: "anywhere" }}>
                    {item.value}
                  </dd>
                </div>
              ))}
            </dl>
          )}
        </div>
      </div>
      {!editing ? (
        <Inline gap={8} wrap>
          <Button
            tone="success"
            disabled={!!busy || refreshFailed}
            onClick={() => {
              run("send", { id: row.id });
            }}
          >
            {busy === "send"
              ? t("panel.action.sending", { defaultValue: "发送中…" })
              : t("panel.action.send", { defaultValue: "发到聊天" })}
          </Button>
          <Button
            disabled={!!busy || refreshFailed}
            onClick={() => {
              if (busyRef.current || refreshFailedRef.current) return;
              resetDraft();
              setNote("");
              setEditing(true);
            }}
          >
            {t("panel.action.edit", { defaultValue: "编辑" })}
          </Button>
          <Button
            tone="warning"
            disabled={!!busy || refreshFailed}
            onClick={() => {
              run("update", { id: row.id, disabled: !row.disabled });
            }}
          >
            {row.disabled
              ? t("panel.action.enable", { defaultValue: "恢复启用" })
              : t("panel.action.disable", { defaultValue: "禁用" })}
          </Button>
          <Button tone="danger" disabled={!!busy || refreshFailed} onClick={remove}>
            {busy === "remove"
              ? t("panel.action.removing", { defaultValue: "删除中…" })
              : t("panel.action.remove", { defaultValue: "删除" })}
          </Button>
        </Inline>
      ) : null}
    </div>
  );
}
