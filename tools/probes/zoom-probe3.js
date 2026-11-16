(async () => {
  const out = [];
  const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
  const add = (name, ok, detail) => out.push({ name, ok: !!ok, detail: String(detail == null ? "" : detail) });
  const docEl = () => document.scrollingElement || document.documentElement;
  const ov = () => ({ h: docEl().scrollWidth - window.innerWidth, v: docEl().scrollHeight - window.innerHeight });
  const composerIn = () => {
    const t = document.querySelector('textarea');
    if (!t) return false;
    const r = t.getBoundingClientRect();
    return r.width > 0 && r.bottom <= window.innerHeight + 2 && r.top >= 0;
  };
  const setSize = async (v) => {
    const s = document.getElementById('fontSize');
    s.value = String(v);
    s.dispatchEvent(new Event('input', { bubbles: true }));
    s.dispatchEvent(new Event('change', { bubbles: true }));
    await sleep(240);
  };
  const openLook = async () => {
    document.getElementById('setbtn').click();
    await sleep(260);
    const t = [].slice.call(document.querySelectorAll('.sptab')).filter((b) => b.dataset.tab === 'look')[0];
    if (t) { t.click(); await sleep(160); }
    return t;
  };

  // 1) 四套主题 × 130%:换主题不破坏缩放布局
  const themes = ['pink', 'blue', 'white', 'black'];
  await openLook();
  await setSize(130);
  for (const th of themes) {
    const btn = document.querySelector('.themeopt[data-t="' + th + '"]');
    if (!btn) { add(`主题 ${th} @130%:按钮存在`, false, '未找到'); continue; }
    btn.click();
    await sleep(220);
    document.getElementById('setclose').click();
    await sleep(200);
    const o = ov();
    const cls = document.body.className;
    add(`主题 ${th} @130%:无溢出`, o.h <= 2 && o.v <= 8, `h=${o.h} v=${o.v} cls=${cls}`);
    add(`主题 ${th} @130%:输入框可见`, composerIn(), '');
    document.getElementById('setbtn').click();
    await sleep(200);
  }

  // 2) 字体预设 × 130%:换字体不破坏布局
  const fonts = [].slice.call(document.querySelectorAll('.fontopt'));
  for (const f of fonts) {
    f.click();
    await sleep(200);
    document.getElementById('setclose').click();
    await sleep(200);
    const o = ov();
    add(`字体 ${f.dataset.f} @130%:无溢出`, o.h <= 2 && o.v <= 8, `h=${o.h} v=${o.v}`);
    document.getElementById('setbtn').click();
    await sleep(200);
    [].slice.call(document.querySelectorAll('.sptab')).filter((b) => b.dataset.tab === 'look')[0].click();
    await sleep(120);
  }

  // 3) 长会话列表 @130%:侧栏内部可滚动、条目不横向溢出
  document.getElementById('setclose').click();
  await sleep(200);
  const side = document.getElementById('side') || document.querySelector('#slist')?.parentElement;
  const slist = document.getElementById('slist');
  if (slist && side) {
    for (let i = 0; i < 25; i++) {
      const it = document.createElement('div');
      it.className = 'sitem';
      it.innerHTML = '<span class="t">压测会话标题很长很长很长 ' + i + '</span>';
      slist.appendChild(it);
    }
    await sleep(200);
    const hOver = side.scrollWidth - side.clientWidth;
    const canScroll = side.scrollHeight > side.clientHeight + 4;
    add('长会话列表 @130%:侧栏可滚动', canScroll, `scrollH=${side.scrollHeight} clientH=${side.clientHeight}`);
    add('长会话列表 @130%:侧栏不横向溢出', hOver <= 2, 'overflowX=' + hOver);
    [].slice.call(slist.querySelectorAll('.sitem')).slice(-25).forEach((e) => e.remove());
  } else add('长会话列表:结构存在', false, '找不到侧栏');

  // 4) 更新页 @130%:版本行与弹窗都正常(不真查网,直接打开弹窗看布局)
  document.getElementById('setbtn').click();
  await sleep(240);
  const updTab = [].slice.call(document.querySelectorAll('.sptab')).filter((b) => b.dataset.tab === 'update')[0];
  if (updTab) {
    updTab.click();
    await sleep(200);
    const cur = document.getElementById('updCur');
    const lat = document.getElementById('updLatest');
    add('更新页 @130%:两个版本号都渲染', !!(cur && lat), `cur=${cur && cur.textContent} lat=${lat && lat.textContent}`);
    document.getElementById('updModal').style.display = 'flex';
    await sleep(150);
    const box = document.querySelector('#updModal > div');
    const br = box ? box.getBoundingClientRect() : null;
    add('更新弹窗 @130%:完整可见', !!br && br.top >= -2 && br.bottom <= window.innerHeight + 2 && br.right <= window.innerWidth + 2,
      br ? `${Math.round(br.top)}..${Math.round(br.bottom)}/${window.innerHeight}` : '-');
    document.getElementById('updModal').style.display = 'none';
  }

  // 5) 还原
  const look2 = [].slice.call(document.querySelectorAll('.sptab')).filter((b) => b.dataset.tab === 'look')[0];
  if (look2) { look2.click(); await sleep(150); }
  await setSize(100);
  const f0 = [].slice.call(document.querySelectorAll('.fontopt')).filter((b) => b.dataset.f === 'default')[0];
  if (f0) f0.click();
  document.getElementById('setclose').click();
  await sleep(240);
  const o2 = ov();
  add('还原后无溢出', o2.h <= 2 && o2.v <= 8, `h=${o2.h} v=${o2.v}`);

  return out;
})()
