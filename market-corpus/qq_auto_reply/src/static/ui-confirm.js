/* 破坏性操作的**页内**确认框 —— 不再依赖浏览器原生 confirm()。

   为什么要换掉原生 confirm：它在嵌入式 / 沙箱化页面里**会被静默拦掉**（不弹窗、直接返回
   false），于是现象是"点了删除没反应"——使用者 2026-09-27 报的就是这个：后端日志里那次
   点击连一次入口调用都没有（`asset` 一条 TRIGGER 都没有），而同一份代码在干净的浏览器
   标签页里点下去是正常的（见 docs/SESSION-HANDOFF.md §18）。页内确认框不依赖任何浏览器
   策略，点一下必然产生可观察的结果。

   用法（在 async 函数里，两个分支都要能看见结果）：
     if (!await UIConfirm.ask(t('ui.shared.sticker.delete_confirm','确定删除？'))) return;
     ...删...
   可选 opts：{ okText, cancelText, danger }
     danger 默认 true（销毁类操作用红按钮）；传 false 走主色按钮。

   细节：
   * 文案用 textContent 写入 —— 消息里带 < > & 也不会被当 HTML 解析；
   * 焦点落在**取消**上（不是确定）：回车不该顺手删东西；
   * Esc / 点背景 / 点取消 都算取消；同一时刻只允许一个确认框；
   * 没有 document 时一律返回 false（宁可不动，也不静默动手）。 */
(function (global) {
    'use strict';

    var OVERLAY_ID = 'ui-confirm-overlay';

    /** 取页面里的 t()；没有就用兜底文案（本文件可能在 t() 之前被解析）。 */
    function text(key, fallback) {
        try {
            if (typeof global.t === 'function') {
                var got = global.t(key, fallback);
                if (got) { return String(got); }
            }
        } catch (e) { /* 忽略：退回兜底文案 */ }
        return String(fallback || key);
    }

    function close(existing) {
        if (existing && existing.parentNode) { existing.parentNode.removeChild(existing); }
    }

    function ask(message, opts) {
        var doc = global.document;
        if (!doc || !doc.body) { return Promise.resolve(false); }
        opts = opts || {};
        close(doc.getElementById(OVERLAY_ID));

        return new Promise(function (resolve) {
            var settled = false;
            function settle(value) {
                if (settled) { return; }
                settled = true;
                doc.removeEventListener('keydown', onKey, true);
                close(overlay);
                resolve(value);
            }
            function onKey(ev) {
                if (ev.key === 'Escape') { ev.preventDefault(); settle(false); }
            }

            var overlay = doc.createElement('div');
            overlay.id = OVERLAY_ID;
            overlay.setAttribute('role', 'dialog');
            overlay.setAttribute('aria-modal', 'true');
            overlay.style.cssText = 'position:fixed;inset:0;z-index:2147483000;' +
                'background:rgba(15,23,42,.45);display:grid;place-items:center;';

            var card = doc.createElement('div');
            card.style.cssText = 'background:var(--card-bg,#fff);border:1px solid var(--border,#e2e8f0);' +
                'border-radius:var(--radius-lg,14px);box-shadow:var(--shadow-card,0 12px 32px rgba(15,23,42,.18));' +
                'max-width:440px;width:calc(100% - 48px);padding:22px 22px 16px;font-size:14px;line-height:1.75;' +
                'color:var(--text-primary,#0f172a);white-space:pre-wrap;word-break:break-word;';

            var textNode = doc.createElement('div');
            textNode.textContent = String(message == null ? '' : message);
            card.appendChild(textNode);

            var row = doc.createElement('div');
            row.style.cssText = 'display:flex;justify-content:flex-end;gap:10px;margin-top:18px;';
            var cancel = doc.createElement('button');
            cancel.type = 'button';
            cancel.className = 'btn';
            cancel.textContent = opts.cancelText || text('ui.shared.btn.cancel', '取消');
            var ok = doc.createElement('button');
            ok.type = 'button';
            ok.className = 'btn ' + (opts.danger === false ? 'btn-primary' : 'btn-danger');
            ok.textContent = opts.okText || text('ui.shared.btn.confirm', '确定');
            cancel.addEventListener('click', function () { settle(false); });
            ok.addEventListener('click', function () { settle(true); });
            row.appendChild(cancel);
            row.appendChild(ok);
            card.appendChild(row);
            overlay.appendChild(card);

            overlay.addEventListener('click', function (ev) {
                if (ev.target === overlay) { settle(false); }   // 点背景 = 取消
            });
            doc.addEventListener('keydown', onKey, true);
            doc.body.appendChild(overlay);
            try { cancel.focus(); } catch (e) { /* 焦点给不上也不影响可用 */ }
        });
    }

    global.UIConfirm = { ask: ask };
})(window);
