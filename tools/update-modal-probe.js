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
  // 同时桩 POST(检查/开始) 与 GET(轮询进度),用于模拟下载全流程
  const stubFlow = (postBody, getBody) => {
    keepOrig();
    window.fetch = function (u, o) {
      const m = (o && o.method) || 'GET';
      if (String(u).indexOf('/api/update') >= 0) {
        const payload = m === 'POST' ? postBody : getBody;
        return Promise.resolve(new Response(JSON.stringify(payload), { status: 200, headers: { 'content-type': 'application/json' } }));
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

  // 1) 已是最新 → 弹窗告知
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

  // 2) 有新版本 → 问「是/否」;点「否」不触发下载
  stub({ ok: true, phase: 'idle', current: '1.2.3', latest: '1.2.4', source: 'github', hasUpdate: true });
  click('updCheck');
  await wait(() => visible() && title().indexOf('发现新版本') >= 0, 5000);
  const cancelShown = getComputedStyle(document.getElementById('updCancel')).display !== 'none';
  const goTxt = (document.getElementById('updGo').textContent || '').trim();
  const cancelTxt = (document.getElementById('updCancel').textContent || '').trim();
  out.push({ name: '检查更新(有新版)弹窗+是/否两个选项', ok: title().indexOf('发现新版本') >= 0 && cancelShown && goTxt.indexOf('是') >= 0 && cancelTxt.indexOf('否') >= 0, detail: title() + ' / ' + goTxt + ' / ' + cancelTxt });
  click('updCancel');
  await sleep(150);
  out.push({ name: '点「否」不触发下载', ok: !visible(), detail: visible() ? '弹窗未关' : '' });
  restore();

  // 2b) 点「是」→ 弹窗不关,原地变进度框:测速 → 下载进度条 → 失败恢复按钮
  stubFlow(
    { ok: true, phase: 'testing', current: '1.2.3', latest: '1.2.4', source: 'github', hasUpdate: true, speeds: {}, percent: 0 },
    { ok: true, phase: 'downloading', current: '1.2.3', latest: '1.2.4', source: 'gitee', hasUpdate: true, speeds: { github: 314572, gitee: 1048576 }, percent: 40, received: 12600000, total: 31000000, speed: 1048576 }
  );
  click('updCheck');
  await wait(() => visible() && title().indexOf('发现新版本') >= 0, 5000);
  click('updGo');
  // 弹窗先关再立刻以进度模式重开:以标题实际变成「正在更新」为准,别用固定 sleep
  const began = await wait(() => visible() && title().indexOf('正在更新') >= 0, 5000);
  const progShown = began && getComputedStyle(document.getElementById('updProg')).display !== 'none';
  const btnHidden = began && getComputedStyle(document.getElementById('updGo')).display === 'none';
  out.push({ name: '点「是」后弹窗保持打开并进入进度模式', ok: began && progShown && btnHidden, detail: title() });
  // 测速阶段的流动动画条本身就是 width:40%,必须以「阶段文本切到下载中且动画清除」为准
  const barOk = await wait(() => {
    const ph = (document.getElementById('updMPhase').textContent || '');
    const bar = document.getElementById('updMBar');
    return ph.indexOf('下载中') >= 0 && ph.indexOf('码云镜像') >= 0 && parseInt(bar.style.width, 10) === 40 && !bar.style.animation;
  }, 5000);
  const phaseTxt = (document.getElementById('updMPhase').textContent || '');
  const detailTxt = (document.getElementById('updMDetail').textContent || '');
  const speedTxt = (document.getElementById('updMSpeed').textContent || '');
  out.push({ name: '弹窗进度条随下载更新(40%·码云镜像)', ok: barOk, detail: phaseTxt + ' / bar=' + (document.getElementById('updMBar').style.width || '') });
  out.push({ name: '弹窗显示测速结果与下载量', ok: detailTxt.indexOf('MB') >= 0 && speedTxt.indexOf('码云') >= 0, detail: detailTxt + ' / ' + speedTxt });
  // 模拟两个通道都失败 → 弹窗切回失败态并恢复「重试/关闭」按钮
  window.fetch = function (u, o) {
    const m = (o && o.method) || 'GET';
    if (String(u).indexOf('/api/update') >= 0) {
      const payload = m === 'POST'
        ? { ok: true, phase: 'testing', current: '1.2.3', latest: '1.2.4', hasUpdate: true }
        : { ok: true, phase: 'error', current: '1.2.3', latest: '1.2.4', error: 'github: 模拟下载失败' };
      return Promise.resolve(new Response(JSON.stringify(payload), { status: 200, headers: { 'content-type': 'application/json' } }));
    }
    return window.__origFetch.apply(this, arguments);
  };
  const failShown = await wait(() => title().indexOf('更新失败') >= 0 && getComputedStyle(document.getElementById('updCancel')).display !== 'none', 5000);
  out.push({ name: '下载失败弹窗报错并恢复按钮', ok: failShown, detail: title() });
  click('updCancel');
  restore();

  // 3) 检查失败
  stubFail();
  click('updCheck');
  await wait(() => visible() && title().indexOf('失败') >= 0, 5000);
  out.push({ name: '检查失败也弹窗', ok: visible() && title().indexOf('失败') >= 0, detail: title() });
  click('updCancel');
  restore();

  return out;
})()
