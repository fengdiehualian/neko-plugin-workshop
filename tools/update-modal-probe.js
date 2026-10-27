(async () => {
  const out = [];
  const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
  const wait = async (fn, ms) => {
    const t0 = Date.now();
    while (Date.now() - t0 < ms) {
      if (fn()) return true;
      await sleep(100);
    }
    return fn();
  };
  const modal = document.getElementById('updModal');
  const title = () => (document.getElementById('updTitle').textContent || '').trim();
  const visible = () => getComputedStyle(modal).display !== 'none';
  const click = (id) => document.getElementById(id).click();

  const keepOrig = () => { if (!window.__origFetch) window.__origFetch = window.fetch; };
  const stub = (payload) => {
    keepOrig();
    const body = payload;
    window.fetch = function (u, o) {
      if (String(u).indexOf('/api/update') >= 0 && o && o.method === 'POST') {
        return Promise.resolve(new Response(JSON.stringify(body), { status: 200, headers: { 'content-type': 'application/json' } }));
      }
      return window.__origFetch.apply(this, arguments);
    };
  };
  const stubFail = () => {
    keepOrig();
    window.fetch = function (u, o) {
      if (String(u).indexOf('/api/update') >= 0 && o && o.method === 'POST') return Promise.reject(new Error('模拟断网'));
      return window.__origFetch.apply(this, arguments);
    };
  };
  const restore = () => { if (window.__origFetch) { window.fetch = window.__origFetch; delete window.__origFetch; } };

  const tab = [].slice.call(document.querySelectorAll('.sptab')).filter((b) => b.dataset.tab === 'update')[0];
  if (!tab) return [{ name: '更新页签', ok: false, detail: '找不到 data-tab=update' }];
  tab.click();
  await sleep(300);

  // 1) 已是最新
  stub({ ok: true, phase: 'idle', current: '1.2.3', latest: '1.2.3', source: 'github', hasUpdate: false });
  click('updCheck');
  await wait(visible, 5000);
  out.push({ name: '检查更新(最新版)必须弹窗', ok: visible() && title().indexOf('已是最新') >= 0, detail: title() });
  const latTxt = (document.getElementById('updLatest').textContent || '').trim();
  out.push({ name: '版本行显示最新版本号', ok: latTxt === 'v1.2.3', detail: latTxt });
  click('updGo');
  await sleep(150);
  out.push({ name: '弹窗可关闭', ok: !visible(), detail: visible() ? '仍显示' : '' });
  restore();

  // 2) 有新版本
  stub({ ok: true, phase: 'idle', current: '1.2.3', latest: '1.2.4', source: 'github', hasUpdate: true });
  click('updCheck');
  await wait(() => title().indexOf('发现新版本') >= 0, 5000);
  const cancelShown = getComputedStyle(document.getElementById('updCancel')).display !== 'none';
  out.push({ name: '检查更新(有新版)弹窗+可取消', ok: title().indexOf('发现新版本') >= 0 && cancelShown, detail: title() });
  click('updCancel');
  await sleep(150);
  out.push({ name: '点「以后再说」不触发下载', ok: !visible(), detail: visible() ? '弹窗未关' : '' });
  restore();

  // 3) 检查失败
  stubFail();
  click('updCheck');
  await wait(() => title().indexOf('失败') >= 0, 5000);
  out.push({ name: '检查失败也弹窗', ok: title().indexOf('失败') >= 0, detail: title() });
  click('updCancel');
  restore();

  return out;
})()
