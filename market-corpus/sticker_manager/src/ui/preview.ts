// 预览子系统（v0.10.1 拆分自 panel.tsx，尺没动）：缓存 + 懒加载调度 + 取图 hook。
//
// —— 预览懒加载调度（轮 G-2）——
// 视口决定“看得见才拉”，全局并发尺限同时 2 张：单张预览是分段的串行调用，
// 几百格的图墙一次性开跑会踩挤插件子进程。拿不到 IntersectionObserver 就直接排队，
// 宁多拉不漏图。
//
// 预览取图（格子与聚焦卡共用同一套调度）：单张预览是分段的串行调用（宿主回包单帧上限≈4.56MiB，
// 实机超时钉的坑——DESIGN.md 陷阱 14）；循环有护栏，坏协议不许无限转；失败记空串，避免坏图重试风暴。
//
// v0.17.2 加了一档 kind="thumb"：**墙上的格子取 256px 缩略图，点开的聚焦卡才取原图**。
// 之前两者共用原图，实机账是官方区 190 张 ≈34.7MB（base64 后 ≈46MB）过控制通道、
// 并发 2 —— 排在后面的格子等过 30s 就是"加载图片超时"，每张一行 SDK 的 TRIGGER 日志
// 就是"日志刷屏"（当天 363 行里 313 行是 preview）。同一根因的两面，一起修。
// 代价（主人拍板接受）：动图在格子里只显示第一帧。

import { useEffect, useRef, useState } from "@neko/plugin-ui";
import { callAction } from "./shared";
import type { Surface } from "./shared";

// 预览缓存：`档位:id` -> dataUrl。失败记空串，避免每张坏图都重试一轮。
// 档位必须进键：同一张图的缩略图与原图是两个不同的 dataUrl，
// 共用一个键会让"先看过格子"的聚焦卡拿到糊图（或反过来让格子去等原图）。
const previewCache: Record<string, string> = {};

function cacheKey(kind: string, id: string): string {
  return `${kind}:${id}`;
}

const PREVIEW_CONCURRENCY = 2;
let previewActive = 0;
const previewWaiters: Array<() => Promise<void>> = [];
type PreviewRequest = {
  listeners: Set<(dataUrl: string) => void>;
};
const previewRequests = new Map<string, PreviewRequest>();

function pumpPreviewQueue() {
  while (previewActive < PREVIEW_CONCURRENCY && previewWaiters.length > 0) {
    const task = previewWaiters.shift();
    if (!task) {
      continue;
    }
    previewActive += 1;
    const settle = () => {
      previewActive -= 1;
      pumpPreviewQueue();
    };
    task().then(settle, settle);
  }
}

function queuePreview(task: () => Promise<void>, priority: boolean = false) {
  if (priority) previewWaiters.unshift(task);
  else previewWaiters.push(task);
  pumpPreviewQueue();
}

function requestPreview(
  surface: Surface,
  id: string,
  kind: string,
  priority: boolean,
  complete: (dataUrl: string) => void,
): () => void {
  const key = cacheKey(kind, id);
  // A reopened detail joins its unfinished request instead of fetching the same chunks twice.
  const existing = previewRequests.get(key);
  if (existing) {
    existing.listeners.add(complete);
    return () => {
      existing.listeners.delete(complete);
    };
  }
  const request: PreviewRequest = { listeners: new Set([complete]) };
  previewRequests.set(key, request);
  queuePreview(async () => {
    if (request.listeners.size === 0) {
      previewRequests.delete(key);
      return;
    }
    let dataUrl = "";
    try {
      let offset = 0;
      let mime = "";
      const parts: string[] = [];
      for (let guard = 0; guard < 16; guard += 1) {
        const args: Record<string, any> = { id, offset };
        if (kind === "thumb") args.kind = "thumb";
        const result = await callAction(surface, "preview", args);
        if (!result) break;
        mime = String(result.mime || mime);
        parts.push(String(result.chunk_base64 || ""));
        if (result.done) {
          dataUrl = `data:${mime};base64,${parts.join("")}`;
          break;
        }
        const next = Number(result.next_offset || 0);
        if (next <= offset) break;
        offset = next;
      }
    } catch {
      dataUrl = "";
    }
    previewCache[key] = dataUrl;
    previewRequests.delete(key);
    request.listeners.forEach((listener) => listener(dataUrl));
  }, priority);
  return () => {
    request.listeners.delete(complete);
  };
}

const tileHandlers: Map<any, () => void> = new Map();
let sharedObserver: any = null;

function observePreview(el: any, enter: () => void): () => void {
  const Ctor: any = (globalThis as any).IntersectionObserver;
  if (!Ctor || !el) {
    queuePreview(async () => {
      enter();
    });
    return () => {};
  }
  if (!sharedObserver) {
    sharedObserver = new Ctor(
      (entries: any[]) => {
        entries.forEach((entry) => {
          if (!entry.isIntersecting) {
            return;
          }
          const handler = tileHandlers.get(entry.target);
          if (handler) {
            tileHandlers.delete(entry.target);
            sharedObserver.unobserve(entry.target);
            handler();
          }
        });
      },
      { rootMargin: "240px 0px" },
    );
  }
  tileHandlers.set(el, enter);
  sharedObserver.observe(el);
  return () => {
    tileHandlers.delete(el);
    if (sharedObserver) {
      sharedObserver.unobserve(el);
    }
  };
}

export function useStickerPreview(
  surface: Surface,
  id: string,
  auto: boolean,
  kind: string = "full",
) {
  const key = cacheKey(kind, id);
  const [preview, setPreview] = useState<string>(previewCache[key] || "");
  const [loading, setLoading] = useState<boolean>(
    previewCache[key] === undefined,
  );
  const [attempt, setAttempt] = useState(0);
  const boxRef = useRef<any>(null);
  const pendingRef = useRef({ key, pending: false });
  const retryKeyRef = useRef("");

  useEffect(() => {
    let alive = true;
    let releaseRequest = () => {};
    const manual = retryKeyRef.current === key;
    retryKeyRef.current = "";
    pendingRef.current = { key, pending: previewCache[key] === undefined };
    setPreview(previewCache[key] || "");
    setLoading(previewCache[key] === undefined);
    if (previewCache[key] !== undefined) {
      return undefined;
    }
    const load = () => {
      if (!alive) return;
      releaseRequest = requestPreview(surface, id, kind, auto, (dataUrl) => {
        if (!alive) return;
        pendingRef.current.pending = false;
        setPreview(dataUrl);
        setLoading(false);
      });
    };
    if (auto || manual) {
      // 用户点开的原图先于未开始的缩略图；并发与分段协议不变。
      load();
      return () => {
        alive = false;
        releaseRequest();
      };
    }
    // 墙上的格子：滚进视口（提前 240px）才排队；卸载后未开始的任务跳过。
    const stop = observePreview(boxRef.current, load);
    return () => {
      alive = false;
      stop();
      releaseRequest();
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [id, kind, attempt]);

  const retry = () => {
    if (pendingRef.current.key !== key || pendingRef.current.pending || preview) return;
    pendingRef.current.pending = true;
    retryKeyRef.current = key;
    delete previewCache[key];
    setLoading(true);
    setAttempt((current) => current + 1);
  };
  const onError = () => {
    if (pendingRef.current.key !== key || pendingRef.current.pending || !preview) return;
    if (previewCache[key] === preview) previewCache[key] = "";
    setPreview("");
    setLoading(false);
  };

  return { preview, loading, boxRef, retry, onError };
}
