(async () => {
  const out = [];
  const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
  const add = (name, ok, detail) => out.push({ name, ok: !!ok, detail: String(detail == null ? "" : detail) });
  const docEl = () => document.scrollingElement || document.documentElement;

  // 1) 刷新后是否仍应用上次的 130%
  const z = document.body.style.zoom;
  const w = document.body.style.width;
  const h = document.body.style.height;
  add('刷新后沿用 130% 缩放', z === '1.3' && !!w && !!h, `zoom=${z} w=${w} h=${h}`);
  const o0 = { h: docEl().scrollWidth - window.innerWidth, v: docEl().scrollHeight - window.innerHeight };
  add('刷新后主界面无溢出', o0.h <= 2 && o0.v <= 8, `h=${o0.h} v=${o0.v}`);

  // 2) 长内容:聊天区内部滚动 + 不横向撑破
  const chat = document.getElementById('chat');
  if (chat) {
    const long = '这是一段很长的中文测试文本,用来验证放大字号后消息气泡的换行与滚动是否正常。'.repeat(6);
    const solid = 'A'.repeat(300);
    for (let i = 0; i < 12; i++) {
      const d = document.createElement('div');
      d.className = 'msg bot';
      d.innerHTML = '<div>' + (i === 0 ? solid : long) + '</div>';
      chat.appendChild(d);
    }
    await sleep(200);
    const canScroll = chat.scrollHeight > chat.clientHeight + 4;
    chat.scrollTop = chat.scrollHeight;
    await sleep(120);
    const scrolled = chat.scrollTop > 0;
    const innerOverflow = chat.scrollWidth - chat.clientWidth;
    add('130% 下长消息可在聊天区滚动', canScroll && scrolled, `scrollH=${chat.scrollHeight} clientH=${chat.clientHeight} top=${chat.scrollTop}`);
    add('130% 下长文本不横向撑破', innerOverflow <= 2, 'overflowX=' + innerOverflow);
    chat.innerHTML = '';
  } else add('130% 下聊天区存在', false, '找不到 #chat');

  // 3) 交互:开关设置面板 / 切主题,布局不塌
  document.getElementById('setbtn').click();
  await sleep(300);
  const spOpen = getComputedStyle(document.getElementById('setpanel')).display !== 'none';
  const spMax = getComputedStyle(document.getElementById('setpanel')).maxHeight;
  document.getElementById('setclose').click();
  await sleep(200);
  add('130% 下设置面板可开关', spOpen && getComputedStyle(document.getElementById('setpanel')).display === 'none', 'maxHeight=' + spMax);

  const themeBtn = [...document.querySelectorAll('button')].filter((b) => (b.textContent || '').indexOf('主题') >= 0 || (b.getAttribute('onclick') || '').indexOf('theme') >= 0)[0];
  add('130% 下页面可交互(无遮罩卡死)', !!document.elementFromPoint(window.innerWidth / 2, window.innerHeight / 2), (document.elementFromPoint(window.innerWidth / 2, window.innerHeight / 2) || {}).tagName || '');

  // 4) 还原 100%,避免污染后续
  document.getElementById('setbtn').click();
  await sleep(250);
  const look = [...document.querySelectorAll('.sptab')].filter((b) => b.dataset.tab === 'look')[0];
  if (look) { look.click(); await sleep(150); }
  const sl = document.getElementById('fontSize');
  sl.value = '100';
  sl.dispatchEvent(new Event('input', { bubbles: true }));
  sl.dispatchEvent(new Event('change', { bubbles: true }));
  await sleep(300);
  document.getElementById('setclose').click();
  await sleep(200);
  add('还原 100% 后无溢出', (docEl().scrollWidth - window.innerWidth) <= 2 && (docEl().scrollHeight - window.innerHeight) <= 8,
    `h=${docEl().scrollWidth - window.innerWidth} v=${docEl().scrollHeight - window.innerHeight}`);

  return out;
})()
