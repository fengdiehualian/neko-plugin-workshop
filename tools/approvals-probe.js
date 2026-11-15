(async () => {
  const out = [];
  const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
  const wait = async (fn, ms) => {
    const t0 = Date.now();
    while (Date.now() - t0 < ms) {
      try { if (fn()) return true; } catch (e) {}
      await sleep(80);
    }
    try { return fn(); } catch (e) { return false; }
  };
  const txt = () => document.getElementById('chat').innerText || '';
  const btns = () => [...document.querySelectorAll('#chat button')].filter((b) => /允许这一次|这一路都放行|🚫 不要/.test(b.textContent || ''));
  const orig = window.fetch;
  const posts = [];
  try {
    // 任务桩延迟 2.4s 返回:期间页面每 500ms 轮询一次 /api/approvals,确认卡有足够时间出现
    window.fetch = function (u, o) {
      const url = String(u);
      const m = ((o && o.method) || 'GET').toUpperCase();
      if (url.indexOf('/api/task') >= 0 && m === 'POST') {
        return new Promise((res) => setTimeout(() => res(new Response(JSON.stringify({
          ok: true, reply: '探针:任务已结束', thinking: '', artifacts: [], modifiedFiles: [],
          approvals: [{ id: 'p1', kind: 'permission', what: 'git push(推送到远端仓库)', reply: 'reject', at: Date.now() }],
        }), { status: 200, headers: { 'content-type': 'application/json' } })), 2400));
      }
      if (url.indexOf('/api/approvals') >= 0) {
        if (m === 'POST') {
          posts.push(String((o && o.body) || ''));
          return Promise.resolve(new Response(JSON.stringify({ ok: true, found: true }), { status: 200, headers: { 'content-type': 'application/json' } }));
        }
        return Promise.resolve(new Response(JSON.stringify({
          ok: true,
          items: [{
            id: 'p1', kind: 'permission', title: 'Agent 想执行会写到仓库外的命令',
            reason: 'git push(推送到远端仓库)', detail: 'git push origin main', askedAt: Date.now(),
          }],
        }), { status: 200, headers: { 'content-type': 'application/json' } }));
      }
      return orig.apply(this, arguments);
    };
    const ta = document.getElementById('t');
    ta.value = '探针:外发写确认卡';
    document.getElementById('f').requestSubmit();
    const appeared = await wait(() => /写到仓库外/.test(txt()) && btns().length >= 3, 6000);
    out.push({ name: '外发写确认卡渲染(命令详情+三个按钮)', ok: appeared, detail: '按钮 ' + btns().length + ' 个 / ' + (/git push origin main/.test(txt()) ? '含命令原文' : '缺命令原文') });
    out.push({ name: '确认卡说明「不点=拒绝」', ok: /不点|按拒绝/.test(txt()), detail: '' });

    // 点「🚫 不要」→ 必须把 reply:reject 发回服务端
    const no = btns().find((b) => /不要/.test(b.textContent || ''));
    if (no) no.click();
    const replied = await wait(() => posts.length >= 1, 3000);
    out.push({ name: '点拒绝会回发 reply=reject', ok: replied && /"reply":"reject"/.test(posts[0]), detail: posts[0] || '没发请求' });
    const settled = await wait(() => /已拒绝/.test(txt()), 4000);
    out.push({ name: '拒绝后卡片落定为已拒绝', ok: settled, detail: '' });

    // 任务收尾:服务端返回的 approvals 记成一条通知(审计痕迹)
    const done = await wait(() => /写到仓库外的动作要你确认|个「写到仓库外」/.test(txt()), 9000);
    out.push({ name: '任务结束汇总外发确认次数(审计行)', ok: done, detail: '' });

    // 提问型确认:选项按钮取自真实选项
    window.fetch = function (u, o) {
      const url = String(u);
      const m = ((o && o.method) || 'GET').toUpperCase();
      if (url.indexOf('/api/task') >= 0 && m === 'POST') {
        return new Promise((res) => setTimeout(() => res(new Response(JSON.stringify({ ok: true, reply: '探针完成', artifacts: [], modifiedFiles: [] }), { status: 200, headers: { 'content-type': 'application/json' } })), 1600));
      }
      if (url.indexOf('/api/approvals') >= 0) {
        if (m === 'POST') { posts.push(String((o && o.body) || '')); return Promise.resolve(new Response(JSON.stringify({ ok: true, found: true }), { status: 200, headers: { 'content-type': 'application/json' } })); }
        return Promise.resolve(new Response(JSON.stringify({
          ok: true,
          items: [{ id: 'q1', kind: 'question', title: 'Agent 在问你是否要往仓库外发布', reason: '文案里明确要往仓库外发布/推送', questions: [{ question: '要把这次改动推送到 GitHub 吗?', options: ['好,推送', '先不推'] }] }],
        }), { status: 200, headers: { 'content-type': 'application/json' } }));
      }
      return orig.apply(this, arguments);
    };
    document.getElementById('t').value = '探针:提问型外发确认';
    document.getElementById('f').requestSubmit();
    const qOk = await wait(() => {
      const b = [...document.querySelectorAll('#chat button')].filter((x) => /先不推|好,推送/.test(x.textContent || ''));
      return b.length >= 2;
    }, 6000);
    out.push({ name: '提问型确认渲染真实选项', ok: qOk, detail: '' });
    const decl = [...document.querySelectorAll('#chat button')].find((x) => /先不推/.test(x.textContent || ''));
    if (decl) decl.click();
    const qPosted = await wait(() => posts.some((p) => p.indexOf('先不推') >= 0), 3000);
    out.push({ name: '选择「先不推」回传 answers', ok: qPosted, detail: '' });
    await sleep(2600);
  } catch (e) {
    out.push({ name: '外发写确认探针', ok: false, detail: String((e && e.message) || e) });
  } finally {
    window.fetch = orig;
  }
  return out;
})()
