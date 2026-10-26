// 格子（v0.9.1 定稿，v0.10.1 拆分自 panel.tsx）：纯图方块。
// scale-down 只缩不放——160px 小图塞 360px 大格被放大糊脸，就是"拉伸感"的真凶
//（实机截图钉的坑）。左上角勾选框是唯一的体外控件；
// 点击进入独立详情；稳定锚点供返回图库时恢复原浏览位置。

import { StatusBadge, useState } from "@neko/plugin-ui";
import { useStickerPreview } from "../preview";
import type { StickerRow, Surface } from "../shared";

export function StickerTile(props: {
  key?: string;
  row: StickerRow;
  surface: Surface;
  selected?: boolean;
  disabled?: boolean;
  onToggleSelect?: () => void;
  onOpen?: () => void;
}) {
  const row = props.row;
  const t = props.surface.t;
  const [focused, setFocused] = useState(false);
  // 墙上的格子只要 256px 缩略图（v0.17.2）：动图在这里只显示第一帧，
  // 点开聚焦卡才拉原图看动画——主人拍板的取舍，见 ui/preview.ts 头部。
  const { preview, loading, boxRef, retry, onError } = useStickerPreview(
    props.surface,
    row.id,
    false,
    "thumb",
  );

  const tileBox = (extra: Record<string, unknown>) => {
    const base: Record<string, unknown> = {
      position: "relative",
      width: "100%",
      aspectRatio: "1 / 1",
      borderRadius: "8px",
      overflow: "hidden",
      boxSizing: "border-box",
      border: props.selected ? "2px solid var(--primary)" : "2px solid var(--border)",
      background: "var(--surface)",
      outline: focused ? "2px solid var(--primary)" : "none",
      outlineOffset: "2px",
    };
    return Object.assign(base, extra);
  };
  const centerBox: Record<string, unknown> = {
    position: "absolute",
    top: 0,
    left: 0,
    right: 0,
    bottom: 0,
    display: "flex",
    alignItems: "center",
    justifyContent: "center",
    fontSize: "12px",
    opacity: 0.75,
  };

  return (
    <div
      id={`sticker-tile-${row.id}`}
      className="sticker-tile"
      ref={boxRef}
      style={tileBox({})}
    >
      <button
        id={`sticker-open-${row.id}`}
        type="button"
        disabled={props.disabled}
        aria-label={t("panel.tile.open", {
          name: row.desc || row.id,
          defaultValue: "查看：{name}",
        })}
        onFocus={() => setFocused(true)}
        onBlur={() => setFocused(false)}
        onClick={() => {
          if (!props.disabled && props.onOpen) props.onOpen();
        }}
        style={{
          position: "absolute",
          top: 0,
          left: 0,
          width: "100%",
          height: "100%",
          padding: 0,
          border: 0,
          borderRadius: "6px",
          background: "transparent",
          color: "inherit",
          cursor: props.disabled ? "default" : "pointer",
          outline: "none",
        }}
      >
        {preview ? (
          <img
            src={preview}
            alt={row.desc || row.id}
            onError={onError}
            style={{
              width: "100%",
              height: "100%",
              objectFit: "scale-down",
              display: "block",
            }}
          />
        ) : null}
        {!preview ? (
          <span style={centerBox}>
            {loading
              ? t("panel.tile.loading", { defaultValue: "加载中…" })
              : t("panel.tile.failed", { defaultValue: "图不可用" })}
          </span>
        ) : null}
        {row.disabled ? (
          <span
            style={{
              position: "absolute",
              top: 0,
              left: 0,
              right: 0,
              bottom: 0,
              background: "rgba(20, 20, 20, 0.55)",
              display: "flex",
              alignItems: "center",
              justifyContent: "center",
              pointerEvents: "none",
            }}
          >
            <StatusBadge
              tone="warning"
              label={t("panel.badge.disabled", { defaultValue: "已禁用" })}
            />
          </span>
        ) : null}
      </button>
      {!preview && !loading ? (
        <button
          type="button"
          disabled={props.disabled}
          onFocus={() => setFocused(true)}
          onBlur={() => setFocused(false)}
          onClick={(event: any) => {
            event.stopPropagation();
            if (!props.disabled) retry();
          }}
          style={{
            position: "absolute",
            bottom: "12px",
            left: "50%",
            transform: "translateX(-50%)",
            maxWidth: "calc(100% - 16px)",
            padding: "4px 8px",
            fontSize: "12px",
            border: "1px solid var(--border)",
            borderRadius: "4px",
            background: "var(--bg)",
            color: "var(--text)",
            cursor: props.disabled ? "default" : "pointer",
            whiteSpace: "normal",
          }}
        >
          {t("panel.preview.retry", { defaultValue: "重试图片" })}
        </button>
      ) : null}
      <div
        style={{
          position: "absolute",
          top: "3px",
          left: "3px",
          background: "rgba(255, 255, 255, 0.85)",
          borderRadius: "4px",
          padding: "1px 3px",
          lineHeight: 1,
        }}
      >
        <input
          type="checkbox"
          checked={!!props.selected}
          disabled={props.disabled}
          aria-label={t("panel.tile.select", {
            name: row.desc || row.id,
            defaultValue: "选择：{name}",
          })}
          onFocus={() => setFocused(true)}
          onBlur={() => setFocused(false)}
          onChange={() => {
            if (!props.disabled && props.onToggleSelect) props.onToggleSelect();
          }}
        />
      </div>
    </div>
  );
}
