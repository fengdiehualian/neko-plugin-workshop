// 库卡动作模型（v0.10.1 拆分自 panel.tsx）：「她的表情库」这张卡的状态与全部动作闭包。
// 本文件不放任何呈现 JSX——它是“这张卡怎么想”，panel.tsx 里只留“怎么摆”。
//
// 尺没动，成文的纪律原样继承：
// - 长任务两把尺对齐（陷阱 19）：导入/导出/收尾走 LONG_CALL，分块是短任务维持默认；
// - 收图走已测的 add 通道逐张收（魔数/查重/体积尺全复用），不再拿文件名当描述（轮 I）；
// - 全库共用**一个**隐藏图片输入框，目标分类走 ref 而不是 state（陷阱区轮 I 注释）；
// - 删分类确认摊的是服务端张数 `section.total`（陷阱 22）。

import { useConfirm, useEffect, useRef, useState } from "@neko/plugin-ui";
import {
  callAction,
  dataUrlToBase64,
  extractCode,
  parseBatchTags,
  readAsDataUrl,
  LONG_CALL,
  MAX_BATCH_FILES,
  MAX_STICKER_BYTES,
} from "./shared";
import type { Section, Surface, ZoneInfo } from "./shared";

const BATCH_ACTION_LIMIT = 200;

function readFileChunk(blob: any): Promise<string> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => {
      const text = String(reader.result || "");
      const comma = text.indexOf(",");
      resolve(comma >= 0 ? text.slice(comma + 1) : "");
    };
    reader.onerror = () => reject(new Error("read_failed"));
    reader.readAsDataURL(blob);
  });
}

export function useLibraryModel(surface: Surface) {
  const t = surface.t;
  const confirm = useConfirm();
  const [query, setQuery] = useState("");
  const [libraryNote, setLibraryNote] = useState("");
  const [pending, setPending] = useState("");
  const [refreshFailed, setRefreshFailed] = useState(false);
  const busyRef = useRef("");
  const refreshFailedRef = useRef(false);
  const [uploading, setUploading] = useState(false);
  const [selected, setSelected] = useState<string[]>([]);
  const [batchTags, setBatchTags] = useState("");
  const [batchGroup, setBatchGroup] = useState("");
  // 轮 G：分类说明改为就地编辑——同一时刻只开一个区块的编辑行。
  const [descEditing, setDescEditing] = useState("");
  const [descDraft, setDescDraft] = useState("");
  // 轮 I（分类优先）：新建分类也是就地展开（同一时刻只开一个），
  // 不用覆盖层弹窗——理由见陷阱 20（kit Modal 在宿主 iframe 里平台级残废）。
  const [creating, setCreating] = useState(false);
  const [newName, setNewName] = useState("");
  const [newDesc, setNewDesc] = useState("");
  const [collectBusy, setCollectBusy] = useState(false);
  // 详情替换图库工作区；模型常驻，浏览条件与选择不随详情卸载。
  const [focus, setFocus] = useState("");
  const browsePositionRef = useRef<{ id: string; x: number; y: number } | null>(null);
  const restorePositionRef = useRef<{ id: string; x: number; y: number } | null>(null);
  const zipInputRef = useRef<any>(null);
  // 轮 I：全库共用**一个**隐藏图片输入框，目标分类走 ref 而不是 state——
  // “点区块头收图→click()→onChange” 三步里 onChange 的闭包可能抓到旧 state，
  // ref 赋值当场生效，不靠重渲染传参。
  const imgInputRef = useRef<any>(null);
  const collectTargetRef = useRef<string>("");
  const collectZoneRef = useRef<string>("");
  // 收图的目标区也走 ref（与目标分类同一条闭包坑：click()→onChange 三步里 state 可能是旧的）。
  const viewZoneRef = useRef<string>("");
  // J-1：区（分类的上层）。`viewZone` 是主人正在看的 tab；它掉了（被删/未选）就回激活区。
  // 她只感知激活区；主人可以浏览任意区——但所有写入（建类/收图/导入）都落在正在看的区。
  const zonesList: ZoneInfo[] = (surface.state && surface.state.zones) || [];
  const activeZone = String((surface.state && surface.state.active_zone) || "");
  const [viewZone, setViewZone] = useState("");
  const zoneExists = (id: string) => {
    for (let i = 0; i < zonesList.length; i += 1) {
      if (zonesList[i].id === id) {
        return true;
      }
    }
    return false;
  };
  const view = zoneExists(viewZone) ? viewZone : activeZone;
  const beginTask = (action: string, clearNote = true) => {
    if (busyRef.current || (refreshFailedRef.current && action !== "refresh")) return false;
    busyRef.current = action;
    setPending(action);
    if (clearNote) setLibraryNote("");
    return true;
  };
  const endTask = () => {
    busyRef.current = "";
    setPending("");
  };
  const reportFailure = (error: unknown) => {
    const raw = error instanceof Error ? error.message : String(error ?? "failed");
    const code = extractCode(raw);
    setLibraryNote(t("panel.toast.failed", {
      code: code === "pack_too_many_entries"
        ? t("panel.export.too_many", {
            defaultValue: "图库超过单包 512 张上限，未生成导出文件。",
          })
        : code,
      defaultValue: "操作失败：{code}",
    }));
  };
  const targetZoneNote = (name: string) => t("panel.operation.target_zone", {
    name,
    defaultValue: "（目标区：{name}）",
  });
  const zoneName = (id: string) =>
    String(zonesList.find((zone) => zone.id === id)?.name || id);
  const reportRefreshFailure = () => {
    refreshFailedRef.current = true;
    setRefreshFailed(true);
  };
  const clearRefreshFailure = () => {
    refreshFailedRef.current = false;
    setRefreshFailed(false);
  };
  const refreshAfterAction = async () => {
    try {
      await surface.api.refresh();
      clearRefreshFailure();
      return true;
    } catch {
      reportRefreshFailure();
      return false;
    }
  };
  const retryRefresh = async () => {
    if (!beginTask("refresh", false)) return;
    try {
      await refreshAfterAction();
    } finally {
      endTask();
    }
  };
  const openFocus = (id: string) => {
    const scrolling = document.scrollingElement || document.documentElement;
    browsePositionRef.current = {
      id,
      x: window.scrollX || scrolling.scrollLeft || 0,
      y: window.scrollY || scrolling.scrollTop || 0,
    };
    restorePositionRef.current = null;
    setFocus(id);
  };
  const closeFocus = () => {
    restorePositionRef.current = browsePositionRef.current;
    setFocus("");
  };
  const switchViewZone = (id: string) => {
    if (id === view) return;
    browsePositionRef.current = null;
    restorePositionRef.current = null;
    setFocus("");
    setSelected([]);
    setBatchGroup("");
    setViewZone(id);
  };
  useEffect(() => {
    let secondFrame = 0;
    const firstFrame = window.requestAnimationFrame(() => {
      secondFrame = window.requestAnimationFrame(() => {
        if (focus) {
          document.getElementById("sticker-focus-workspace")?.scrollIntoView({
            block: "start",
            behavior: "auto",
          });
          window.scrollTo(0, 0);
          document.getElementById("sticker-focus-back")
            ?.querySelector?.<HTMLButtonElement>("button")?.focus?.({ preventScroll: true });
          return;
        }
        const position = restorePositionRef.current;
        if (!position) return;
        // scrollIntoView also brings an externally scrolled iframe back to its tile.
        document.getElementById(`sticker-tile-${position.id}`)?.scrollIntoView({
          block: "nearest",
          behavior: "auto",
        });
        window.scrollTo(position.x, position.y);
        document.getElementById(`sticker-open-${position.id}`)?.focus?.({ preventScroll: true });
        restorePositionRef.current = null;
      });
    });
    return () => {
      window.cancelAnimationFrame(firstFrame);
      window.cancelAnimationFrame(secondFrame);
    };
  }, [focus]);
  useEffect(() => {
    const rows = (surface.state && surface.state.stickers) || [];
    setSelected((previous) => {
      const next = previous.filter((id) =>
        rows.some((row) => row.id === id && String(row.zone || "") === view),
      );
      return next.length === previous.length ? previous : next;
    });
  }, [surface.state, view]);
  // 当场同步给 ref：收图/导入的闭包落点永远跟当前视图一致。
  viewZoneRef.current = view;
  useEffect(() => {
    if (viewZone && !zoneExists(viewZone)) {
      setViewZone("");
    }
  }, [surface.state]);

  // 区动作一把尺：五个入口形状一样（调→报→刷），只把文案差交出去。
  const zoneAction = async (
    actionId: string,
    args: Record<string, unknown>,
    doneKey: string,
    doneDefault: string,
    params?: Record<string, unknown>,
  ) => {
    if (!beginTask(actionId)) return false;
    try {
      const result = await callAction(
        surface,
        actionId,
        args,
        actionId === "zone_remove" || actionId === "zone_restore_official" ? LONG_CALL : undefined,
      );
      const extra: Record<string, unknown> = { defaultValue: doneDefault };
      if (result) {
        // 拆区报删掉的张数，恢复官方报收进的张数——同一只 toast 位，两把尺各自取数。
        extra.count = result.removed ?? result.imported ?? 0;
      }
      if (params) {
        const keys = Object.keys(params);
        for (let i = 0; i < keys.length; i += 1) {
          extra[keys[i]] = params[keys[i]];
        }
      }
      setLibraryNote(t(doneKey, extra));
      setSelected([]);
      await refreshAfterAction();
      return true;
    } catch (error) {
      reportFailure(error);
      return false;
    } finally {
      endTask();
    }
  };
  const createZone = (name: string, desc: string) =>
    zoneAction("zone_create", { zone: name, desc }, "panel.zone.created", "区「{name}」已建好", { name });
  const renameZone = (zoneId: string, name: string) =>
    zoneAction("zone_rename", { zone_id: zoneId, zone: name }, "panel.zone.renamed", "已改区名");
  const setZoneDesc = (zoneId: string, desc: string) =>
    zoneAction("zone_set_desc", { zone_id: zoneId, desc }, "panel.zone.desc_saved", "区说明已更新");
  const activateZone = (zoneId: string) =>
    zoneAction("zone_activate", { zone_id: zoneId }, "panel.zone.activated", "她的世界已切到这个区");
  const removeZone = (zoneId: string, name: string) =>
    zoneAction("zone_remove", { zone_id: zoneId }, "panel.zone.removed", "已拆区「{name}」（连带 {count} 张图）", { name });
  // J-2：官方区被拆后的补救（拍板 P3：只播一次 + 恢复按钮）——长任务，走 LONG_CALL 那把尺。
  const restoreOfficial = () =>
    zoneAction("zone_restore_official", {}, "panel.zone.restored", "官方收藏已恢复：收了 {count} 张");

  const exportPack = async () => {
    if (!beginTask("export_pack")) return;
    setLibraryNote(t("panel.export.busy", { defaultValue: "正在导出…" }));
    try {
      const result = await callAction(surface, "export_pack", {}, LONG_CALL);
      if (result) {
        setLibraryNote(
          t("panel.export.done", {
            exported: result.exported ?? 0,
            skipped: result.skipped ?? 0,
            file: result.file ?? "",
            defaultValue: "已导出 {exported} 张（跳过 {skipped}）：{file}",
          }),
        );
      }
    } catch (error) {
      reportFailure(error);
    } finally {
      endTask();
    }
  };

  const repair = async () => {
    if (!beginTask("repair")) return;
    setLibraryNote(t("panel.repair.busy", { defaultValue: "正在体检与修复…" }));
    try {
      const result = await callAction(surface, "repair", {});
      if (result) {
        setLibraryNote(
          t("panel.repair.done", {
            entries: result.removed_entries ?? 0,
            files: result.purged_files ?? 0,
            hashes: result.backfilled_hashes ?? 0,
            defaultValue:
              "体检完成：清理条目 {entries}、孤儿文件 {files}、回填指纹 {hashes}",
          }),
        );
      }
      await refreshAfterAction();
    } catch (error) {
      reportFailure(error);
    } finally {
      endTask();
    }
  };

  // 收件箱导入的按钮已于 v0.10.3 从面板退场（主人拍板）：`import_inbox` 服务端入口与
  // `panel.inbox.done` 键照旧保留，只是这里不再有人按它。

  // 套图包直传（v0.6.0）：选择 .zip → 分块上传 → 服务端同一把尺导入。
  // 分块大小由服务端 start 回包定（与预览共用同一条 ZMQ 帧尺），面板不硬编码。
  const importZip = async (file: any) => {
    if (!beginTask("import_zip")) return;
    const targetZone = viewZoneRef.current;
    const targetName = zoneName(targetZone);
    setUploading(true);
    setLibraryNote(t("panel.upload.starting", { defaultValue: "正在准备上传…" }) + targetZoneNote(targetName));
    try {
      const start = await callAction(surface, "import_upload_start", {
        name: file.name,
        size: file.size,
      });
      const sid = String((start && start.session) || "");
      const chunkBytes = Number((start && start.chunk_bytes) || 3145728);
      const total = Math.max(1, Math.ceil(Number(file.size) / chunkBytes));
      let seq = 0;
      for (let offset = 0; offset < Number(file.size); offset += chunkBytes) {
        const chunk = await readFileChunk(
          file.slice(offset, offset + chunkBytes),
        );
        await callAction(surface, "import_upload_chunk", {
          session: sid,
          seq,
          data_base64: chunk,
        });
        seq += 1;
        setLibraryNote(
          t("panel.upload.progress", {
            done: seq,
            total,
            defaultValue: "上传中 {done}/{total}…",
          }) + targetZoneNote(targetName),
        );
      }
      setLibraryNote(t("panel.upload.importing", { defaultValue: "上传完成，正在导入…" }) + targetZoneNote(targetName));
      const fin = await callAction(
        surface,
        "import_upload_finish",
        { session: sid, zone: targetZone },
        LONG_CALL,
      );
      if (fin) {
        setLibraryNote(
          t("panel.upload.done", {
            imported: fin.imported ?? 0,
            duplicates: fin.duplicates ?? 0,
            rejected: fin.rejected ?? 0,
            failed: fin.failed ?? 0,
            defaultValue:
              "套图包导入完成：收进 {imported}、重复跳过 {duplicates}、拒收（坏图/超限/跨区分类冲突）{rejected}、失败 {failed}",
          }) + targetZoneNote(targetName),
        );
      }
      await refreshAfterAction();
    } catch (error) {
      reportFailure(error);
    } finally {
      setUploading(false);
      endTask();
    }
  };

  // 轮 G：分组说明编辑（“分类即 prompt”的一句维护入口），就地挂在区块头上。
  const saveGroupDesc = async () => {
    if (!descEditing || !beginTask("group_set_desc")) return;
    const group = descEditing;
    const desc = descDraft.trim();
    try {
      await callAction(surface, "group_set_desc", {
        group,
        desc,
      });
      setLibraryNote(
        t("panel.group.desc_saved", { defaultValue: "分组说明已更新" }),
      );
      setDescEditing((current) => current === group ? "" : current);
      await refreshAfterAction();
    } catch (error) {
      reportFailure(error);
    } finally {
      endTask();
    }
  };

  // 轮 I：分类优先——先立分类（名字必填、说明可空），再往分类里收图。
  // 逐图字段（描述/梗义/标签）不在收图时问：收完点开图在聚焦卡里补。
  const createCategory = async () => {
    if (busyRef.current) return;
    const name = String(newName || "").trim();
    if (!name) {
      setLibraryNote(
        t("panel.group.name_required", { defaultValue: "先给分类起个名字" }),
      );
      return;
    }
    if (!beginTask("group_create")) return;
    const targetZone = viewZoneRef.current;
    const targetName = zoneName(targetZone);
    try {
      await callAction(surface, "group_create", {
        group: name,
        desc: String(newDesc || "").trim(),
        zone: targetZone,
      });
      setLibraryNote(
        t("panel.group.created", {
          name: name,
          defaultValue: "分类「{name}」已建好——点它块头的「收图」往里塞表情。",
        }) + targetZoneNote(targetName),
      );
      setNewName("");
      setNewDesc("");
      setCreating(false);
      // 防“建完了但屏幕上看不到”：搜索词会把零张新区块筛掉（它不命中分类名），
      // 刚建的分类应当当场就在眼前。
      setQuery("");
      await refreshAfterAction();
    } catch (error) {
      reportFailure(error);
    } finally {
      endTask();
    }
  };

  const cancelCreate = () => {
    setCreating(false);
    setNewName("");
    setNewDesc("");
  };

  // 删分类 = 连带删它里的图（主人拍板 1C）：确认里先把精确张数摊开。
  const removeCategory = async (section: Section) => {
    if (!beginTask("group_remove")) return;
    try {
      const answer = await confirm({
        title: t("panel.group.delete_title", { defaultValue: "删分类" }),
        message: section.total
          ? t("panel.group.delete_message", {
              name: section.name,
              count: section.total,
              defaultValue:
                "将拆掉分类「{name}」，连带删它里的 {count} 张图与文件，不可恢复。",
            })
          : t("panel.group.delete_message_empty", {
              name: section.name,
              defaultValue:
                "删掉空分类「{name}」？（它里面对她不可见，不会影哿发图）",
            }),
        tone: "danger",
      });
      if (!answer) return;
      const result = await callAction(
        surface,
        "group_remove",
        { group: section.group },
        LONG_CALL,
      );
      setLibraryNote(
        t("panel.group.delete_done", {
          name: section.name,
          count: (result && result.removed) ?? 0,
          defaultValue: "已删分类「{name}」（连带 {count} 张图）",
        }),
      );
      setSelected([]);
      // 聚焦卡不需要手动收：删掉的图不在 state.stickers 里，focusRow 自然为空。
      await refreshAfterAction();
    } catch (error) {
      reportFailure(error);
    } finally {
      endTask();
    }
  };

  // 轮 I（2A）：收图分散到区块头。目标分类走 ref 传递（见 collectTargetRef 注释）；
  // 逐张走已测的 add 通道（魔数/查重/体积尺全复用）。
  const collectInto = (group: string) => {
    if (busyRef.current || refreshFailedRef.current) return;
    collectTargetRef.current = group;
    collectZoneRef.current = viewZoneRef.current;
    if (imgInputRef.current) {
      imgInputRef.current.click();
    }
  };

  const importFiles = async (files: any, group: string, targetZone: string) => {
    const list: any[] = Array.from(files || []);
    if (!list.length || !beginTask("collect")) return;
    const taken = list.slice(0, MAX_BATCH_FILES);
    const targetName = zoneName(targetZone);
    setCollectBusy(true);
    let ok = 0;
    let dup = 0;
    let big = 0;
    let fail = 0;
    try {
      for (let i = 0; i < taken.length; i += 1) {
        const file = taken[i];
        if (Number(file.size) > MAX_STICKER_BYTES) {
          big += 1;
        } else {
          try {
            const b64 = dataUrlToBase64(await readAsDataUrl(file));
            if (!b64) {
              fail += 1;
            } else {
              // 收图不问逐图字段；分类说明承担未标注图片的目录正文。
              const result = await callAction(surface, "add", {
                data_base64: b64,
                desc: "",
                group,
                zone: targetZone,
              });
              if (result && result.note === "sticker_added") {
                ok += 1;
              } else {
                fail += 1;
              }
            }
          } catch (error) {
            const raw =
              error instanceof Error ? error.message : String(error ?? "");
            if (extractCode(raw) === "duplicate_image") {
              dup += 1;
            } else {
              fail += 1;
            }
          }
        }
        setLibraryNote(
          t("panel.collect.busy", {
            done: i + 1,
            total: taken.length,
            defaultValue: "收藏中 {done}/{total}…",
          }) + targetZoneNote(targetName),
        );
      }
      const extra = list.length - taken.length;
      setLibraryNote(
        t("panel.collect.done", {
          ok, dup, big, fail,
          defaultValue:
            "已收 {ok} · 重复跳过 {dup} · 超限略过 {big} · 失败 {fail}",
        }) +
          (group
            ? t("panel.collect.into", {
                name: group,
                defaultValue: "（进「{name}」）",
              })
            : "") +
          targetZoneNote(targetName) +
          (extra > 0
            ? t("panel.batch.more", {
                extra,
                defaultValue: "；本次未处理 {extra} 张",
              })
            : ""),
      );
      await refreshAfterAction();
    } catch (error) {
      reportFailure(error);
    } finally {
      setCollectBusy(false);
      endTask();
    }
  };

  // 隐藏输入框的落点：目标分类当场从 ref 取（轮 I 的 ref 纪律）。
  const chooseCollectedFiles = (files: any) => {
    return importFiles(files, collectTargetRef.current, collectZoneRef.current || viewZoneRef.current);
  };

  const toggleSelected = (id: string) => {
    setSelected((previous: string[]) =>
      previous.indexOf(id) >= 0
        ? previous.filter((x) => x !== id)
        : previous.concat(id),
    );
  };

  // 全选本组：没全选过→补齐；已全选→再点取消本组选择。
  const selectSection = (rows: Section["rows"]) => {
    const ids = rows.map((row) => row.id);
    setSelected((previous: string[]) => {
      const missing = ids.filter((id) => previous.indexOf(id) < 0);
      return missing.length
        ? previous.concat(missing)
        : previous.filter((id) => ids.indexOf(id) < 0);
    });
  };

  const clearSelection = () => {
    setSelected([]);
  };

  const keepVisibleSelection = (visibleIds: string[]) => {
    const visible = new Set(visibleIds);
    setSelected((previous) => previous.filter((id) => visible.has(id)));
  };
  const clearHiddenSelection = keepVisibleSelection;

  const executeBatch = async (
    actionId: "batch_update" | "batch_remove",
    ids: string[],
    patch: Record<string, unknown> = {},
  ) => {
    const successful = new Set<string>();
    let completed = 0;
    let missing = 0;
    let processed = 0;
    let failure: unknown = null;
    for (let offset = 0; offset < ids.length; offset += BATCH_ACTION_LIMIT) {
      const chunk = ids.slice(offset, offset + BATCH_ACTION_LIMIT);
      setLibraryNote(t("panel.batch.progress", {
        done: processed,
        total: ids.length,
        defaultValue: "批量处理中 {done}/{total}…",
      }));
      try {
        const result = await callAction(surface, actionId, { ...patch, ids: chunk }, LONG_CALL);
        const count = result && Number(actionId === "batch_remove" ? result.removed : result.updated);
        const absent = result && Array.isArray(result.missing)
          ? new Set<string>(result.missing.filter((id: unknown) => typeof id === "string"))
          : null;
        // Only acknowledge IDs when the response accounts for this entire chunk.
        if (!absent || !Number.isInteger(count) || count < 0 ||
            Array.from(absent).some((id) => chunk.indexOf(id) < 0) ||
            count + absent.size !== chunk.length) {
          throw new Error("invalid_batch_result");
        }
        chunk.forEach((id) => {
          if (!absent.has(id)) successful.add(id);
        });
        completed += count;
        missing += absent.size;
        processed += chunk.length;
      } catch (error) {
        failure = error;
        break;
      }
    }
    return { completed, missing, remaining: ids.length - processed, successful, failure };
  };

  const batchFailureNote = (summary: {
    completed: number;
    missing: number;
    remaining: number;
    failure: unknown;
  }) => {
    const raw = summary.failure instanceof Error
      ? summary.failure.message : String(summary.failure ?? "failed");
    return t("panel.batch.partial_failed", {
      completed: summary.completed,
      failed: summary.missing,
      remaining: summary.remaining,
      code: extractCode(raw),
      defaultValue: "批量未全部完成：成功 {completed}、失败 {failed}、结果未确认 {remaining}：{code}",
    });
  };

  const runBatch = async (patch: Record<string, unknown>) => {
    if (!selected.length) return;
    const frozenPatch = { ...patch };
    for (const key of ["tags_add", "tags_remove"]) {
      if (!(key in frozenPatch)) continue;
      const tags = parseBatchTags(frozenPatch[key]);
      if (tags.length) frozenPatch[key] = tags.join(",");
      else delete frozenPatch[key];
    }
    if (!frozenPatch.tags_add && !frozenPatch.tags_remove &&
        typeof frozenPatch.group !== "string" && typeof frozenPatch.disabled !== "boolean") {
      setLibraryNote(t("panel.batch.empty_tags", { defaultValue: "请先填写有效标签。" }));
      return;
    }
    if (!beginTask("batch_update")) return;
    const ids = selected.slice();
    try {
      const summary = await executeBatch("batch_update", ids, frozenPatch);
      setLibraryNote(summary.failure
        ? batchFailureNote(summary)
        : t("panel.batch.done_update", {
            updated: summary.completed,
            missing: summary.missing,
            defaultValue: "批量完成：改了 {updated}、不存在/失败 {missing}",
          }));
      await refreshAfterAction();
    } catch (error) {
      reportFailure(error);
    } finally {
      endTask();
    }
  };

  const batchDelete = async (hiddenCount = 0) => {
    if (!selected.length || !beginTask("batch_remove")) return;
    const ids = selected.slice();
    const hidden = Math.min(ids.length, Math.max(0, Math.trunc(hiddenCount) || 0));
    try {
      const answer = await confirm({
        title: t("panel.batch.delete_title", { defaultValue: "批量删除" }),
        message: t("panel.batch.delete_message", {
          count: ids.length,
          defaultValue: "将删掉 {count} 张图和它们的记录，不可恢复。",
        }) + (hidden ? t("panel.batch.delete_hidden", {
          hidden,
          defaultValue: "其中 {hidden} 张不在当前筛选结果中。",
        }) : ""),
        tone: "danger",
      });
      if (!answer) return;
      const summary = await executeBatch("batch_remove", ids);
      setSelected((previous) => previous.filter((id) => !summary.successful.has(id)));
      setLibraryNote(summary.failure
        ? batchFailureNote(summary)
        : t("panel.batch.delete_done", {
            removed: summary.completed,
            missing: summary.missing,
            defaultValue: "已删 {removed} 张、不存在/失败 {missing}",
          }));
      await refreshAfterAction();
    } catch (error) {
      reportFailure(error);
    } finally {
      endTask();
    }
  };

  return {
    query,
    setQuery,
    zonesList,
    activeZone,
    view,
    setViewZone: switchViewZone,
    officialInfo: (surface.state && surface.state.official) || {},
    createZone,
    renameZone,
    setZoneDesc,
    activateZone,
    removeZone,
    restoreOfficial,
    libraryNote: refreshFailed
      ? [libraryNote, t("panel.refresh.failed", {
          defaultValue: "操作已完成，但列表刷新失败；请重试刷新。",
        })].filter(Boolean).join(" ")
      : libraryNote,
    pending,
    refreshFailed,
    retryRefresh,
    reportRefreshFailure,
    clearRefreshFailure,
    uploading,
    collectBusy,
    selected,
    toggleSelected,
    selectSection,
    clearSelection,
    clearHiddenSelection,
    keepVisibleSelection,
    batchTags,
    setBatchTags,
    batchGroup,
    setBatchGroup,
    creating,
    setCreating,
    cancelCreate,
    newName,
    setNewName,
    newDesc,
    setNewDesc,
    descEditing,
    setDescEditing,
    descDraft,
    setDescDraft,
    focus,
    openFocus,
    closeFocus,
    zipInputRef,
    imgInputRef,
    exportPack,
    repair,
    importZip,
    saveGroupDesc,
    createCategory,
    removeCategory,
    collectInto,
    chooseCollectedFiles,
    runBatch,
    batchDelete,
  };
}
