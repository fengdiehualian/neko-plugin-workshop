// Category actions remain in normal flow; no clipped popover or overlay.
import { Button, Field, Inline, Input, Stack, Text } from "@neko/plugin-ui";
import { StickerTile } from "./sticker_tile";
import type { Section, Surface } from "../shared";

export function CategorySection(props: {
  key?: string;
  surface: Surface;
  section: Section;
  selected: string[];
  collectBusy: boolean;
  disabled?: boolean;
  descEditing: string;
  descDraft: string;
  onToggleSelect: (id: string) => void;
  onOpen: (id: string) => void;
  onCollect: () => void;
  onSelectAll: () => void;
  onToggleDescEditing: () => void;
  setDescDraft: (next: string) => void;
  onSaveDesc: () => void;
  onCancelDesc: () => void;
  onRemoveCategory: () => void;
}) {
  const t = props.surface.t;
  const section = props.section;
  return (
    <section className="sticker-category">
      <div className="sticker-category-header">
        <h2 className="sticker-category-name">
          {section.name}
          {" · "}
          {section.rows.length === section.total
            ? section.total
            : `${section.rows.length} / ${section.total}`}
        </h2>
        <Button tone="default" disabled={props.disabled || props.collectBusy} onClick={props.onCollect}>
          {props.collectBusy
            ? t("panel.collect.busy_one", { defaultValue: "收藏中…" })
            : t("panel.collect.button", { defaultValue: "添加图片" })}
        </Button>
      {section.desc || section.editable || section.rows.length ? (
        <details className="sticker-menu">
          <summary
            aria-label={`${section.name} · ${t("panel.category.manage", { defaultValue: "分类管理" })}`}
            aria-disabled={!!props.disabled}
            onClick={(event: any) => {
              if (props.disabled) event.preventDefault();
            }}
          >
            {t("panel.category.actions", { defaultValue: "更多" })}
          </summary>
          <div className="sticker-menu-body">
            {section.rows.length ? (
              <Inline gap={8}>
                <Button tone="default" disabled={props.disabled} onClick={props.onSelectAll}>
                  {t("panel.section.select_all", { defaultValue: "全选本组" })}
                </Button>
              </Inline>
            ) : null}
            {section.desc && props.descEditing !== section.group ? <Text>{section.desc}</Text> : null}
            {section.editable ? (
              <div className="sticker-menu-actions">
                <Button tone="default" disabled={props.disabled} onClick={props.onToggleDescEditing}>
                  {t("panel.section.desc_edit", { defaultValue: "编辑说明" })}
                </Button>
                <Button tone="danger" disabled={props.disabled} onClick={props.onRemoveCategory}>
                  {t("panel.group.delete_button", { defaultValue: "删分类" })}
                </Button>
              </div>
            ) : null}
            {section.editable && props.descEditing === section.group ? (
              <Stack gap={8}>
                <Field label={t("panel.group.description", { defaultValue: "分类说明" })}>
                  <Input
                    value={props.descDraft}
                    disabled={props.disabled}
                    onChange={props.setDescDraft}
                  />
                </Field>
                <Inline gap={8} wrap>
                  <Button tone="primary" disabled={props.disabled} onClick={props.onSaveDesc}>
                    {t("panel.group.desc_save", { defaultValue: "存分组说明" })}
                  </Button>
                  <Button disabled={props.disabled} onClick={props.onCancelDesc}>
                    {t("panel.section.desc_cancel", { defaultValue: "取消" })}
                  </Button>
                </Inline>
              </Stack>
            ) : null}
          </div>
        </details>
      ) : null}
      </div>
      {section.total === 0 ? (
        <Text>{t("panel.category.empty", { defaultValue: "暂无表情包" })}</Text>
      ) : null}
      <div className="sticker-grid">
        {section.rows.map((row) => (
          <StickerTile
            key={row.id}
            row={row}
            surface={props.surface}
            selected={props.selected.indexOf(row.id) >= 0}
            disabled={props.disabled}
            onToggleSelect={() => props.onToggleSelect(row.id)}
            onOpen={() => props.onOpen(row.id)}
          />
        ))}
      </div>
    </section>
  );
}
