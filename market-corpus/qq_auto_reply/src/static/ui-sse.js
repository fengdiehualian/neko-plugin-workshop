/* 共享 SSE 通信（#2822 通道）：run 完成事件 + 状态/日志刷新信号。

- EventSource 懒加载单例：连接 /plugin/{plugin_id}/ui-api/events
- type:"run"（后端 runs bus 桥接）→ 按 run_id 完成 call()/callPlugin()
  的等待，替代前端紧轮询 /runs/{id}
- type:"logs" / type:"status"（插件经 /ui-api/push 推送）→ 分发给页面注册的 handler
- 兜底：awaitRun 内**快速起步的退避轮询**（100ms×1.6 → 上限 2s）+ 总超时，
  SSE 断线/丢帧时仍能完成请求
- 死连接自愈：EventSource 掉到 CLOSED(2) 是**终态**，浏览器不会自己重连 ——
  ensureEs() 遇到 CLOSED 会丢掉重建，onerror 再补一个 3s 后的重建（见 scheduleReopen）

用法：
    UISSE.on('status', function(){ loadRuntimeStatus(); loadAttention(); });
    var status = await UISSE.awaitRun(runId, { fetchStatus: async (id) => 'succeeded'|null });
*/
(function () {
  'use strict';
  var es = null;
  var reopenTimer = null;
  var reopenCount = 0;    // 观测：重建过几次（测试与排查用）
  var runHandlers = {};   // run_id -> callback(status)
  var typeHandlers = {};  // type -> callback(data)

  /* 掉到 CLOSED(2) 的 EventSource 是**终态**：浏览器只对 CONNECTING(0) 自动重连。
     以前这里是 `if (es) return es` —— 死连接被一直返回下去，于是这个页面**余生**
     每次都吃满兜底轮询、状态/日志也再收不到推送（只剩 30s 兜底），现象正是"刷新有延迟"。
     2026-09-27 实测：SSE 正常时每个刷新 4~11ms；把连接 close() 掉后每次 2010~2022ms。 */
  function scheduleReopen() {
    if (reopenTimer) return;
    reopenTimer = setTimeout(function () {
      reopenTimer = null;
      es = null;
      reopenCount += 1;
      ensureEs();
    }, 3000);
  }

  function ensureEs() {
    if (es && es.readyState === 2) { es = null; }   // 死连接必须丢掉重建
    if (es) return es;
    var m = location.pathname.match(/\/plugin\/([^/]+)\/ui\//);
    var pluginId = m ? m[1] : 'qq_auto_reply';
    try {
      es = new EventSource('/plugin/' + encodeURIComponent(pluginId) + '/ui-api/events');
    } catch (e) {
      es = null;
      return null;
    }
    es.onmessage = function (ev) {
      var data;
      try { data = JSON.parse(ev.data); } catch (e) { return; }
      if (!data || typeof data.type !== 'string') return;
      if (data.type === 'run') {
        var cb = runHandlers[data.run_id];
        if (cb) { delete runHandlers[data.run_id]; cb(data.status); }
        return;
      }
      var h = typeHandlers[data.type];
      if (h) h(data);
    };
    es.onerror = function () {
      // 0(CONNECTING) 浏览器会自己重连；只有掉到 2(CLOSED) 才需要我们出手
      if (es && es.readyState === 2) { scheduleReopen(); }
    };
    return es;
  }

  /**
   * 等待一个 run 到达终端状态。
   * opts:
   *   timeout     总超时（默认 20000ms）
   *   pollInterval 兜底轮询的**起步**间隔（默认 100ms；每次 ×1.6 退避）
   *   maxPollInterval 退避上限（默认 2000ms）
   *   fetchStatus async (runId) -> 终端状态串 'succeeded'|'failed'|'canceled'|'timeout'，未终态返回 null/undefined
   * resolve: 终端状态串；reject: Error(status 或 'timeout')
   */
  function awaitRun(runId, opts) {
    opts = opts || {};
    var timeout = opts.timeout || 20000;
    var pollDelay = opts.pollInterval || 100;
    var maxPollDelay = opts.maxPollInterval || 2000;
    var fetchStatus = opts.fetchStatus;
    var deadline = Date.now() + timeout;
    return new Promise(function (resolve, reject) {
      var done = false;
      var timer = null;
      var iv = null;
      function finish(fn, arg) {
        if (done) return;
        done = true;
        if (timer) clearTimeout(timer);
        if (iv) clearTimeout(iv);
        if (runHandlers[runId]) delete runHandlers[runId];
        fn(arg);
      }
      // SSE 提前完成：run 事件在同一同步块内注册，不可能在注册前到达
      ensureEs();
      runHandlers[runId] = function (status) {
        if (status === 'succeeded') finish(resolve, status);
        else finish(reject, new Error(status));
      };
      timer = setTimeout(function () { finish(reject, new Error('timeout')); }, timeout);
      /* 兜底轮询：**先快后慢**。固定 2s 的话，SSE 一旦没在工作，每次刷新就白等一整个
         周期（实测 loadStickers 2014ms / loadBacklog 2022ms）；而它只是"兜底"，
         第一个探针越快越不容易被当成"这页面卡了"。 */
      if (typeof fetchStatus === 'function') {
        var pump = async function () {
          if (done) return;
          if (Date.now() > deadline) { finish(reject, new Error('timeout')); return; }
          try {
            var status = await fetchStatus(runId);
            if (status) {
              if (status === 'succeeded') finish(resolve, status);
              else finish(reject, new Error(status));
              return;
            }
          } catch (e) { /* 单次轮询失败忽略，下一 tick 重试 */ }
          pollDelay = Math.min(maxPollDelay, Math.round(pollDelay * 1.6));
          iv = setTimeout(pump, pollDelay);
        };
        iv = setTimeout(pump, pollDelay);
      }
    });
  }

  function on(type, handler) {
    if (typeof handler === 'function') {
      typeHandlers[type] = handler;
      ensureEs();  // 注册处理器即建立 EventSource——页面只 on('status', ...) 就能收实时刷新
    }
  }

  window.UISSE = {
    ensureEs: ensureEs,
    awaitRun: awaitRun,
    on: on,
    // 排查用：连接状态与重建次数（0=连接中 1=已连 2=已关）
    state: function () { return { readyState: es ? es.readyState : null, reopenCount: reopenCount }; }
  };
})();
