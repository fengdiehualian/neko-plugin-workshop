// The compact selection bar stays reachable; secondary actions expand in flow.
import { Button, Field, Input, Select } from "@neko/plugin-ui";
import { categoryOptions, parseBatchTags } from "../shared";
import type { Surface } from "../shared";

export function BatchBar(props: {
  surface: Surface;
  selected: string[];
  hiddenCount: number;
  zone: string;
  disabled?: boolean;
  batchTags: string;
  setBatchTags: (next: string) => void;
  batchGroup: string;
  setBatchGroup: (next: string) => void;
  onRun: (patch: Record<string, unknown>) => void;
  onDelete: () => void;
  onClear: () => void;
  onClearHidden: () => void;
}) {
  const t = props.surface.t;
  if (!props.selected.length) return null;
  const tagsReady = !props.disabled && parseBatchTags(props.batchTags).length > 0;
  return (
    <div className="sticker-batch">
      <div className="sticker-batch-main">
        <p className="sticker-batch-count">
          {t("panel.batch.selected", {
            count: props.selected.length,
            defaultValue: "已选 {count} 张",
          })}
          {props.hiddenCount > 0
            ? " · " + t("panel.batch.hidden", {
              count: props.hiddenCount,
              defaultValue: "隐藏 {count} 张",
            })
            : ""}
        </p>
        {props.hiddenCount > 0 ? (
          <Button disabled={props.disabled} onClick={props.onClearHidden}>
            {t("panel.batch.clear_hidden", { defaultValue: "取消隐藏选择" })}
          </Button>
        ) : null}
      </div>
      <div className="sticker-batch-move">
        <label style={{ minWidth: 0 }}>
          <span className="sticker-sr-only">
            {t("panel.batch.destination", { defaultValue: "目标分类" })}
          </span>
          <Select
            disabled={props.disabled}
            value={props.batchGroup}
            options={categoryOptions(props.surface, t, props.zone)}
            onChange={(next: any) => props.setBatchGroup(String(next ?? ""))}
          />
        </label>
        <Button
          disabled={props.disabled}
          onClick={() => props.onRun({ group: props.batchGroup })}
        >
          {t("panel.batch.move", { defaultValue: "移入分类" })}
        </Button>
        <Button disabled={props.disabled} onClick={props.onClear}>
          {t("panel.batch.clear", { defaultValue: "取消选择" })}
        </Button>
      </div>
      <details className="sticker-menu">
        <summary
          aria-disabled={!!props.disabled}
          onClick={(event: any) => {
            if (props.disabled) event.preventDefault();
          }}
        >
          {t("panel.batch.more_actions", { defaultValue: "更多操作" })}
        </summary>
        <div className="sticker-batch-more">
          <Field label={t("panel.batch.tags", { defaultValue: "批量标签" })}>
            <Input
              disabled={props.disabled}
              value={props.batchTags}
              onChange={props.setBatchTags}
              placeholder={t("panel.batch.tags_ph", { defaultValue: "标签，逗号分隔" })}
            />
          </Field>
          <div className="sticker-menu-actions" style={{ marginTop: "8px" }}>
            <Button disabled={!tagsReady} onClick={() => {
              if (tagsReady) props.onRun({ tags_add: props.batchTags });
            }}>
              {t("panel.batch.add_tags", { defaultValue: "加标签" })}
            </Button>
            <Button disabled={!tagsReady} onClick={() => {
              if (tagsReady) props.onRun({ tags_remove: props.batchTags });
            }}>
              {t("panel.batch.remove_tags", { defaultValue: "删标签" })}
            </Button>
            <Button disabled={props.disabled} onClick={() => props.onRun({ disabled: false })}>
              {t("panel.batch.enable", { defaultValue: "启用" })}
            </Button>
            <Button disabled={props.disabled} onClick={() => props.onRun({ disabled: true })}>
              {t("panel.batch.disable", { defaultValue: "禁用" })}
            </Button>
            <Button tone="danger" disabled={props.disabled} onClick={props.onDelete}>
              {t("panel.batch.delete", { defaultValue: "删除所选" })}
            </Button>
          </div>
        </div>
      </details>
    </div>
  );
}
