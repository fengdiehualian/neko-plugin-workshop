(async () => {
  const out = [];
  const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
  const add = (name, ok, detail) => out.push({ name, ok: !!ok, detail: String(detail == null ? "" : detail) });
  const docEl = () => document.scrollingElement || document.documentElement;
  const overflow = () => ({ h: docEl().scrollWidth - window.innerWidth, v: docEl().scrollHeight - window.innerHeight });
  const composerIn = () => {
    const t = document.querySelector('textarea');
    if (!t) return false;
    const r = t.getBoundingClientRect();
    return r.width > 0 && r.height > 0 && r.bottom <= window.innerHeight + 2 && r.top >= 0;
  };
  const ctrls = (root) => [].slice.call(root.querySelectorAll('input,select,button,textarea'))
    .filter((el) => el.getBoundingClientRect().width > 0);
  const maxBottom = (els) => els.reduce((m, el) => Math.max(m, el.getBoundingClientRect().bottom), 0);
  const setSize = async (v) => {
    const s = document.getElementById('fontSize');
    s.value = String(v);
    s.dispatchEvent(new Event('input', { bubbles: true }));
    s.dispatchEvent(new Event('change', { bubbles: true }));
    await sleep(260);
  };

  // 1) 主界面(面板关闭)
  document.getElementById('setbtn').click();
  await sleep(300);
  const tabs = [].slice.call(document.querySelectorAll('.sptab'));

  for (const sz of [85, 100, 115, 130]) {
    await setSize(sz);
    document.getElementById('setclose').click();
    await sleep(250);
    const o = overflow();
    const hdr = document.querySelector('header');
    const hr = hdr ? hdr.getBoundingClientRect() : null;
    add(`主界面 ${sz}%:无溢出`, o.h <= 2 && o.v <= 8, `h=${o.h} v=${o.v}`);
    add(`主界面 ${sz}%:输入框可见`, composerIn(), (() => { const t = document.querySelector('textarea'); return t ? Math.round(t.getBoundingClientRect().bottom) + '/' + window.innerHeight : '无'; })());
    add(`主界面 ${sz}%:顶栏完整`, !!hr && hr.bottom <= window.innerHeight + 2 && hr.height > 20, hr ? Math.round(hr.height) : '-');
    document.getElementById('setbtn').click();
    await sleep(250);
  }

  // 2) 设置面板 5 个页签(130%)
  await setSize(130);
  for (let i = 0; i < tabs.length; i++) {
    tabs[i].click();
    await sleep(220);
    const sp = document.getElementById('setpanel');
    sp.scrollTop = 0;
    await sleep(80);
    const r0 = sp.getBoundingClientRect();
    const visibleCtrls = ctrls(sp);
    const hBad = visibleCtrls.filter((el) => el.getBoundingClientRect().right > window.innerWidth + 2).length;
    sp.scrollTop = sp.scrollHeight;
    await sleep(150);
    const r1 = sp.getBoundingClientRect();
    const bottom = maxBottom(ctrls(sp));
    add(`设置页签${i + 1} @130%:面板不超出视口`, r0.bottom <= window.innerHeight + 2 && r0.top >= -2,
      `top=${Math.round(r0.top)} bottom=${Math.round(r0.bottom)}/${window.innerHeight}`);
    add(`设置页签${i + 1} @130%:滚到底能看到最后一个控件`, bottom <= r1.bottom + 3,
      `控件底=${Math.round(bottom)} 面板底=${Math.round(r1.bottom)}`);
    add(`设置页签${i + 1} @130%:无横向溢出`, hBad === 0, 'hBad=' + hBad);
    sp.scrollTop = 0;
  }

  // 3) 更新弹窗(130%)
  const updTab = tabs.filter((b) => b.dataset.tab === 'update')[0];
  if (updTab) {
    updTab.click();
    await sleep(200);
    const m = document.getElementById('updModal');
    m.style.display = 'flex';
    await sleep(150);
    const mr = m.getBoundingClientRect();
    const box = m.firstElementChild ? m.firstElementChild.getBoundingClientRect() : null;
    add('更新弹窗 @130%:遮罩铺满视口', Math.abs(mr.width - window.innerWidth) <= 2 && Math.abs(mr.height - window.innerHeight) <= 2,
      `${Math.round(mr.width)}x${Math.round(mr.height)}`);
    add('更新弹窗 @130%:对话框完整可见', !!box && box.top >= -2 && box.bottom <= window.innerHeight + 2 && box.right <= window.innerWidth + 2,
      box ? `y ${Math.round(box.top)}..${Math.round(box.bottom)}` : '无');
    m.style.display = 'none';
  }

  // 4) 还原 100%
  const lookTab = tabs.filter((b) => b.dataset.tab === 'look')[0];
  if (lookTab) { lookTab.click(); await sleep(200); }
  await setSize(100);
  const st = { w: document.body.style.width, h: document.body.style.height, z: document.body.style.zoom };
  add('还原 100%:内联样式清空', st.z === '1' && st.w === '' && st.h === '', JSON.stringify(st));
  document.getElementById('setclose').click();
  await sleep(220);
  const o2 = overflow();
  add('还原 100%:无溢出', o2.h <= 2 && o2.v <= 8, `h=${o2.h} v=${o2.v}`);

  return out;
})()
