/* 页面间跳转统一在这里加"此刻"的版本号 —— 静态页是强缓存，手写 ?v=N 迟早会忘。

   为什么要有它：插件 UI 的静态文件响应头是 `public, max-age=3600`（**强缓存 1 小时**），
   而 index.html 里原先的做法是"手写 ?v=N，改完这几个页面记得 +1"。那些数字散落在
   index 与两页互相跳转的链接里，**只提一处就会拿到"新页面配旧缓存"的混合版本**，
   而且现象是"改了没生效"，很难查（历史见 tests/test_qq_console_page_navigation.py）。

   现在改成运行时生成：每次从 index 进子页都是**新的 URL**，浏览器必然重新取一份，
   手写版本号也就不必维护了。反向（返回首页）同样带 —— index.html 自己也是强缓存的，
   回程不带的话，改完 index 仍会看到旧的一份。

   用法（两种都行）：
     <a class="card" href="napcat.html" data-nav="napcat.html" data-nav-mode="napcat">…</a>
     <span class="tb-switch" data-nav="open_platform.html">切换到 QQ 开放平台</span>
   锚点保留 href 是为了无 JS / 中键新标签页仍可用；data-nav-mode 会先写
   localStorage.qq_connection_mode（原来写在 index 的 go() 里），行为不变。

   本文件在**末尾**加载（各页的 <script> 都在 body 尾部），所以自行 wire 一次即可。 */
(function (global) {
    'use strict';

    /** 给 url 追加一个每秒都在变的版本号（保留原有查询串与 #hash）。 */
    function fresh(url) {
        var raw = String(url || '');
        var hash = '';
        var hashAt = raw.indexOf('#');
        if (hashAt >= 0) {
            hash = raw.slice(hashAt);
            raw = raw.slice(0, hashAt);
        }
        var sep = raw.indexOf('?') >= 0 ? '&' : '?';
        return raw + sep + 'v=' + Date.now() + hash;
    }

    /** 跳到 url（带新版本号）。返回值固定 false，方便 `onclick="return Nav.go('x')"`。 */
    function go(url) {
        global.location.href = fresh(url);
        return false;
    }

    function wire(root) {
        var scope = root || global.document;
        if (!scope || !scope.querySelectorAll) { return 0; }
        var nodes = scope.querySelectorAll('[data-nav]');
        var wired = 0;
        for (var i = 0; i < nodes.length; i++) {
            var el = nodes[i];
            if (el.__navWired) { continue; }   // 重复 wire 不该叠出两次跳转
            el.__navWired = true;
            el.addEventListener('click', onNavClick);
            wired += 1;
        }
        return wired;
    }

    function onNavClick(ev) {
        var el = ev.currentTarget;
        var target = el.getAttribute('data-nav');
        if (!target) { return; }
        var mode = el.getAttribute('data-nav-mode');
        if (mode) {
            try { global.localStorage.setItem('qq_connection_mode', mode); } catch (e) { /* 隐私模式等：不拦跳转 */ }
        }
        ev.preventDefault();
        go(target);
    }

    global.Nav = { fresh: fresh, go: go, wire: wire };

    if (global.document) {
        if (global.document.readyState === 'loading') {
            global.document.addEventListener('DOMContentLoaded', function () { wire(); });
        } else {
            wire();
        }
    }
})(window);
