(() => {
  "use strict";

  const PLUGIN_ID = "recall";
  const RUNS_URL = "/runs";
  const SOURCE_ID = "official.cet4.core.v1";
  const PREVIEW_KEY = "neko.recall.preview.v2";
  const params = new URLSearchParams(location.search);
  const isPreview = params.get("preview") === "1";
  const curatedPreviewItems = [
    ["cet4.abandon", "abandon", "/əˈbændən/", "v. 放弃；抛弃", "They had to abandon the plan.", "他们不得不放弃这个计划。"],
    ["cet4.ability", "ability", "/əˈbɪləti/", "n. 能力；才能", "Practice improves your ability to recall words.", "练习会提高你回忆单词的能力。"],
    ["cet4.absorb", "absorb", "/əbˈzɔːb/", "v. 吸收；理解", "Plants absorb light from the sun.", "植物吸收太阳光。"],
    ["cet4.accurate", "accurate", "/ˈækjərət/", "adj. 准确的；精确的", "The report gives an accurate description.", "这份报告给出了准确的描述。"],
    ["cet4.adapt", "adapt", "/əˈdæpt/", "v. 适应；改编", "It takes time to adapt to a new routine.", "适应新的日常安排需要时间。"],
    ["cet4.approach", "approach", "/əˈprəʊtʃ/", "n./v. 方法；接近", "We need a different approach to the problem.", "我们需要用不同的方法处理这个问题。"],
    ["cet4.available", "available", "/əˈveɪləbl/", "adj. 可获得的；有空的", "The book is available online.", "这本书可以在线获取。"],
    ["cet4.aware", "aware", "/əˈweə(r)/", "adj. 意识到的；知晓的", "She was aware of the risk.", "她意识到了风险。"],
    ["cet4.benefit", "benefit", "/ˈbenɪfɪt/", "n./v. 益处；受益", "Regular review benefits long-term memory.", "定期复习有益于长期记忆。"],
    ["cet4.challenge", "challenge", "/ˈtʃælɪndʒ/", "n./v. 挑战", "Learning a language is a rewarding challenge.", "学习语言是一项值得的挑战。"],
    ["cet4.concentrate", "concentrate", "/ˈkɒnsntreɪt/", "v. 集中；专心", "Try to concentrate on one word at a time.", "试着一次专注于一个单词。"],
    ["cet4.consequence", "consequence", "/ˈkɒnsɪkwəns/", "n. 结果；后果", "Every choice has a consequence.", "每个选择都有结果。"],
  ].map(([id, word, phonetic, meaning, example, example_zh]) => ({ id, word, phonetic, meaning, example, example_zh }));
  let previewItems = curatedPreviewItems;

  async function loadPreviewLibrary() {
    if (!isPreview) return;
    try {
      const response = await fetch("./data/cet4-core.v1.json", { cache: "no-store" });
      if (!response.ok) return;
      const payload = await response.json();
      const curatedById = new Map(curatedPreviewItems.map((item) => [item.id, item]));
      previewItems = (Array.isArray(payload.items) ? payload.items : [])
        .filter((item) => item?.id && item?.word && item?.meaning)
        .map((item) => {
          const curated = curatedById.get(item.id);
          return curated ? { ...item, example: curated.example, example_zh: curated.example_zh } : item;
        });
    } catch (_error) { /* the 12 curated cards remain an offline fallback */ }
  }

  const state = {
    dashboard: null,
    activeSession: null,
    currentCard: null,
    encoding: false,
    revealed: false,
    pendingIntent: "mixed",
    selectedBox: null,
    activeBox: null,
    boxOriginX: 0,
    openedBoxes: new Set(),
    characterName: "N.E.K.O.",
    characterSrc: "",
  };

  const $ = (selector) => document.querySelector(selector);
  const $$ = (selector) => Array.from(document.querySelectorAll(selector));
  const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
  const eventId = () => globalThis.crypto?.randomUUID?.() || `evt-${Date.now()}-${Math.random().toString(16).slice(2)}`;
  const resetViewport = () => {
    const appShell = $(".app-shell");
    const studyPane = $(".study-pane");
    if (appShell) appShell.scrollTop = 0;
    if (studyPane) studyPane.scrollTop = 0;
    window.scrollTo(0, 0);
  };
  const shuffled = (values) => {
    const copy = [...values];
    for (let index = copy.length - 1; index > 0; index -= 1) {
      const target = Math.floor(Math.random() * (index + 1));
      [copy[index], copy[target]] = [copy[target], copy[index]];
    }
    return copy;
  };

  function unwrap(payload) {
    let value = payload;
    for (let i = 0; i < 6; i += 1) {
      if (!value || typeof value !== "object") return value;
      if (value.success === false) throw new Error(value.error?.message || value.message || "插件执行失败");
      if (!Object.prototype.hasOwnProperty.call(value, "data")) return value;
      value = value.data;
    }
    return value || {};
  }

  async function callEntry(entryId, args = {}, timeoutMs = 25000) {
    if (isPreview) return previewCall(entryId, args);
    const created = await fetch(RUNS_URL, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ plugin_id: PLUGIN_ID, entry_id: entryId, args }),
    });
    if (!created.ok) throw new Error(`Recall 请求失败：HTTP ${created.status}`);
    const createdData = await created.json();
    const runId = createdData.run_id || createdData.id;
    if (!runId) throw new Error("Recall 没有返回任务编号");
    const deadline = Date.now() + timeoutMs;
    while (Date.now() < deadline) {
      await sleep(220);
      const polled = await fetch(`${RUNS_URL}/${encodeURIComponent(runId)}`);
      if (!polled.ok) continue;
      const record = await polled.json();
      const status = String(record.status || "").toLowerCase();
      if (status === "succeeded") {
        const exported = await fetch(`${RUNS_URL}/${encodeURIComponent(runId)}/export`);
        if (!exported.ok) return {};
        const body = await exported.json();
        for (const item of Array.isArray(body.items) ? body.items : []) {
          if (item?.type === "json" && item.json) return unwrap(item.json);
          if (item?.type === "text" && typeof item.text === "string") {
            try { return unwrap(JSON.parse(item.text)); } catch (_error) { /* continue */ }
          }
        }
        return {};
      }
      if (["failed", "canceled", "timeout"].includes(status)) {
        throw new Error(record.error?.message || record.message || `Recall ${status}`);
      }
    }
    throw new Error("Recall 响应超时");
  }

  function newPreviewState() {
    return { version: 2, offsetDays: 0, familiarity: "unset", items: {}, activeSession: null };
  }

  function readPreview() {
    try {
      const data = JSON.parse(localStorage.getItem(PREVIEW_KEY) || "null");
      return data?.version === 2 ? data : newPreviewState();
    } catch (_error) {
      return newPreviewState();
    }
  }

  function writePreview(data) {
    localStorage.setItem(PREVIEW_KEY, JSON.stringify(data));
    updatePreviewClock(data);
  }

  function previewNow(data = readPreview()) {
    const origin = new Date("2026-09-29T02:00:00.000Z");
    return new Date(origin.getTime() + Number(data.offsetDays || 0) * 86400000);
  }

  function previewDashboard(data = readPreview()) {
    const now = previewNow(data).getTime();
    const records = Object.values(data.items || {});
    const due = records.filter((record) => new Date(record.due_at).getTime() <= now).length;
    return {
      due_count: due,
      learned_count: records.length,
      mastered_count: records.filter((record) => Number(record.stability || 0) >= 21).length,
      total_count: previewItems.length,
      new_count: Math.max(0, previewItems.length - records.length),
      familiarity: data.familiarity,
      active_session: data.activeSession?.status === "active" ? previewSessionPayload(data) : null,
      source: { name: "CET-4 核心词汇", notice: `离线可用 · ${previewItems.length} 词 · ECDICT` },
    };
  }

  function previewSessionPayload(data) {
    const session = data.activeSession;
    if (!session) return null;
    const queue = session.queue || [];
    const position = Number(session.position || 0);
    const entry = queue[position];
    const base = entry ? previewItems.find((item) => item.id === entry.item_id) : null;
    const current = base ? { ...base, kind: entry.kind, attempt: entry.attempt || 0, is_new: entry.kind === "new" && !data.items[base.id], progress: data.items[base.id] || {} } : null;
    return {
      id: session.id,
      intent: session.intent,
      status: session.status,
      position,
      total: queue.length,
      box_size: session.box_size || Math.min(5, queue.length),
      remaining: Math.max(0, queue.length - position),
      answered: session.answered || 0,
      forgotten: session.forgotten || 0,
      new_seen: session.new_seen || 0,
      reviewed: session.reviewed || 0,
      current_card: current,
    };
  }

  function previewCreateSession(intent, resume = true) {
    const data = readPreview();
    if (resume && data.activeSession?.status === "active" && data.activeSession.intent === intent) return previewSessionPayload(data);
    const now = previewNow(data).getTime();
    const due = shuffled(previewItems.filter((item) => data.items[item.id] && new Date(data.items[item.id].due_at).getTime() <= now).slice(0, 5));
    const fresh = shuffled(previewItems.filter((item) => !data.items[item.id]).slice(0, 30)).slice(0, 5);
    let queue = [];
    if (intent === "review_due") queue = due.map((item) => ({ item_id: item.id, kind: "review", attempt: 0 }));
    else if (intent === "learn_new") queue = fresh.map((item) => ({ item_id: item.id, kind: "new", attempt: 0 }));
    else {
      queue = due.map((item) => ({ item_id: item.id, kind: "review", attempt: 0 }));
      queue.push(...fresh.slice(0, Math.max(0, 5 - queue.length)).map((item) => ({ item_id: item.id, kind: "new", attempt: 0 })));
    }
    data.activeSession = {
      id: `preview-${Date.now()}`,
      intent,
      status: queue.length ? "active" : "complete",
      position: 0,
      queue,
      box_size: queue.length,
      answered: 0,
      forgotten: 0,
      new_seen: 0,
      reviewed: 0,
    };
    writePreview(data);
    return previewSessionPayload(data);
  }

  function previewSubmit(args) {
    const data = readPreview();
    const session = data.activeSession;
    const entry = session?.queue?.[session.position];
    if (!session || session.id !== args.session_id || entry?.item_id !== args.item_id) throw new Error("当前卡片已经变化，请重新打开会话");
    const previous = data.items[args.item_id] || { repetitions: 0, lapses: 0, stability: 1, difficulty: 5, interval_days: 0 };
    let interval;
    let stability;
    let difficulty;
    if (args.grade === "forgot") {
      interval = 0;
      stability = Math.max(0.25, previous.stability * 0.45);
      difficulty = Math.min(10, previous.difficulty + 1.2);
    } else if (args.grade === "hard") {
      stability = Math.max(0.75, previous.stability * 1.18);
      difficulty = Math.min(10, previous.difficulty + 0.35);
      interval = Math.max(1, stability * 0.9);
    } else {
      stability = Math.max(2, previous.stability * 2.05);
      difficulty = Math.max(1, previous.difficulty - 0.18);
      interval = Math.max(2, stability);
    }
    const now = previewNow(data);
    const due = new Date(now.getTime() + interval * 86400000).toISOString();
    const wasNew = entry.kind === "new" && !data.items[args.item_id];
    data.items[args.item_id] = {
      ...previous,
      repetitions: previous.repetitions + 1,
      lapses: previous.lapses + (args.grade === "forgot" ? 1 : 0),
      stability,
      difficulty,
      interval_days: interval,
      due_at: due,
      last_grade: args.grade,
      last_reviewed_at: now.toISOString(),
    };
    session.answered += 1;
    session.forgotten += args.grade === "forgot" ? 1 : 0;
    session.new_seen += wasNew ? 1 : 0;
    session.reviewed += wasNew ? 0 : 1;
    if (args.grade === "forgot" && !entry.attempt) session.queue.push({ item_id: args.item_id, kind: "review", attempt: 1 });
    session.position += 1;
    if (session.position >= session.queue.length) session.status = "complete";
    writePreview(data);
    const label = interval === 0 ? "这一轮稍后再见" : interval < 2 ? "明天" : `约 ${Math.round(interval)} 天后`;
    const item = previewItems.find((candidate) => candidate.id === args.item_id);
    const payload = previewSessionPayload(data);
    return {
      item_id: args.item_id,
      grade: args.grade,
      schedule: { due_at: due, interval_days: interval, label },
      session: payload,
      next_card: payload?.current_card || null,
      companion_cue: {
        text: session.status === "complete" ? "做得好，今天这组完成啦。" : args.grade === "forgot" ? `${item.word} 有点生也没关系，我们再来一次。` : args.grade === "hard" ? `${item.word} 已经有印象了，继续就好。` : `接住了，${item.word}。`,
      },
      dashboard: previewDashboard(data),
    };
  }

  function previewCall(entryId, args) {
    if (entryId === "get_dashboard") return Promise.resolve(previewDashboard());
    if (entryId === "create_session") return Promise.resolve(previewCreateSession(args.intent || "mixed", args.resume !== false));
    if (entryId === "get_active_session") return Promise.resolve({ active_session: previewSessionPayload(readPreview()) });
    if (entryId === "submit_review") return Promise.resolve(previewSubmit(args));
    if (entryId === "set_familiarity") {
      const data = readPreview(); data.familiarity = args.level; writePreview(data);
      return Promise.resolve({ familiarity: args.level });
    }
    if (entryId === "companion_reply") return Promise.resolve({ reply: args.message.includes("难") ? "没关系，想不起来也不用硬撑。" : "我在。慢慢想，想不起来也没关系。" });
    return Promise.resolve({});
  }

  function showScreen(name) {
    $$(".screen").forEach((node) => node.classList.toggle("is-active", node.dataset.screen === name));
    document.title = `Recall · ${name === "draw" ? "记忆盲盒" : name === "review" ? "复习中" : name === "sources" ? "选择内容" : "今天"}`;
    updateCompanionLine(name);
    resetViewport();
    requestAnimationFrame(resetViewport);
  }

  function toast(message) {
    const node = $("#toast");
    node.textContent = message;
    node.hidden = false;
    clearTimeout(toast.timer);
    toast.timer = setTimeout(() => { node.hidden = true; }, 2600);
  }

  function updateStartButtonLabel() {
    const active = state.dashboard?.active_session;
    const due = Number(state.dashboard?.due_count || 0);
    const fresh = Number(state.dashboard?.new_count || 0);
    const button = $("#startButton");
    if (active?.remaining) {
      $("#startButtonLabel").textContent = "继续这一盒";
      button.disabled = false;
      return;
    }
    if (due > 0) {
      $("#startButtonLabel").textContent = "打开今天这一盒";
      button.disabled = false;
      return;
    }
    if (fresh > 0) {
      $("#startButtonLabel").textContent = "打开今天这一盒";
      button.disabled = false;
      return;
    }
    $("#startButtonLabel").textContent = "今天先休息";
    button.disabled = true;
  }

  function renderDashboard(data) {
    state.dashboard = data;
    const due = Number(data.due_count || 0);
    const fresh = Number(data.new_count || 0);
    const total = Number(data.total_count || 0);
    const learned = Number(data.learned_count || 0);
    const active = data.active_session?.remaining;
    $("#homeTitle").textContent = active ? "刚才那一盒，还在等你。" : due > 0 ? "今天有几个词回来啦。" : fresh > 0 ? "看看今天会遇见哪些词。" : "今天先休息一下。";
    $("#progressText").textContent = `${learned} / ${total}`;
    $("#progressFill").style.width = total ? `${Math.min(100, (learned / total) * 100)}%` : "0%";
    $("#sourceLabel").textContent = data.source?.name || "CET-4 · 体验版";
    updateStartButtonLabel();
    if ($(".screen.is-active")?.dataset.screen === "home") updateCompanionLine("home");
  }

  async function refreshDashboard() {
    try { renderDashboard(await callEntry("get_dashboard")); }
    catch (error) {
      renderDashboard({ due_count: 0, learned_count: 0, total_count: 0, new_count: 0, familiarity: "unset", source: { name: "Recall 暂时未连接", notice: "请稍后重试" } });
      toast(`暂时无法读取 Recall：${error.message}`);
    }
  }

  function startFromHome() {
    state.pendingIntent = "mixed";
    if (state.dashboard?.active_session?.remaining) {
      resumeSession();
      return;
    }
    if (state.dashboard?.familiarity === "unset" && Number(state.dashboard?.learned_count || 0) === 0) {
      showScreen("familiarity");
      return;
    }
    beginSession("mixed");
  }

  async function resumeSession() {
    try {
      const result = await callEntry("get_active_session", { source_id: SOURCE_ID });
      if (!result.active_session) { await refreshDashboard(); return; }
      acceptSession(result.active_session);
    } catch (error) { toast(error.message); }
  }

  async function beginSession(intent = "mixed", resume = true, trigger = $("#startButton")) {
    trigger.disabled = true;
    try {
      const session = await callEntry("create_session", { source_id: SOURCE_ID, intent, resume });
      if (!session?.current_card) { showScreen("empty"); return; }
      acceptSession(session);
    } catch (error) { toast(error.message); }
    finally {
      trigger.disabled = false;
      if (trigger === $("#startButton")) updateStartButtonLabel();
    }
  }

  function acceptSession(session) {
    const changedSession = state.activeSession?.id !== session.id;
    state.activeSession = session;
    state.currentCard = session.current_card;
    if (changedSession) {
      const resumedCount = Math.min(Number(session.position || 0), Number(session.box_size || 5));
      state.openedBoxes = new Set(Array.from({ length: resumedCount }, (_value, index) => index));
    }
    showBoxDraw();
  }

  function showBoxDraw(companionText = "") {
    const session = state.activeSession;
    if (!session?.current_card) return;
    const boxSize = Math.max(1, Math.min(5, Number(session.box_size || session.total || 5)));
    const visibleBoxes = $$(".blind-box").slice(0, boxSize);
    if (visibleBoxes.every((button) => state.openedBoxes.has(Number(button.dataset.box)))) {
      state.openedBoxes.clear();
    }
    state.selectedBox = null;
    const wordCard = $("#wordCard");
    wordCard.classList.remove("is-from-box", "is-storing");
    const boxChoice = $("#boxChoice");
    boxChoice.classList.remove("is-opening-one", "is-resuming");
    $("#boxProgress").textContent = String(Math.min(Number(session.answered || session.position || 0), Number(session.total || boxSize)));
    $("#boxTotal").textContent = String(Number(session.total || boxSize));
    $$(".blind-box").forEach((button, index) => {
      button.hidden = index >= boxSize;
      button.disabled = index >= boxSize || state.openedBoxes.has(index);
      button.classList.toggle("is-opened", state.openedBoxes.has(index));
      button.classList.remove("is-selected", "is-opening");
    });
    $("#openBoxButton").disabled = true;
    $("#openBoxButton strong").textContent = "选一个盲盒";
    showScreen("draw");
    void boxChoice.offsetWidth;
    boxChoice.classList.add("is-resuming");
    window.setTimeout(() => boxChoice.classList.remove("is-resuming"), 440);
    $("#captionText").textContent = companionText || "挑一个吧，里面是什么还不知道。";
  }

  function selectBox(button) {
    if (button.disabled) return;
    state.selectedBox = Number(button.dataset.box);
    $$(".blind-box").forEach((candidate) => candidate.classList.toggle("is-selected", candidate === button));
    $("#openBoxButton").disabled = false;
    $("#openBoxButton strong").textContent = "拆开";
    $("#captionText").textContent = "要拆开这个吗？";
  }

  async function openSelectedBox() {
    if (state.selectedBox == null || !state.currentCard) return;
    const button = $(`.blind-box[data-box="${state.selectedBox}"]`);
    const pane = $(".study-pane");
    const buttonRect = button?.getBoundingClientRect();
    const paneRect = pane?.getBoundingClientRect();
    state.activeBox = state.selectedBox;
    state.boxOriginX = buttonRect && paneRect
      ? (buttonRect.left + buttonRect.width / 2) - (paneRect.left + paneRect.width / 2)
      : 0;
    $("#wordCard").style.setProperty("--box-origin-x", `${state.boxOriginX}px`);
    $("#openBoxButton").disabled = true;
    $("#boxChoice").classList.add("is-opening-one");
    button?.classList.add("is-opening");
    $("#openBoxButton strong").textContent = "正在打开…";
    $("#captionText").textContent = "盒子里有张单词签……";
    await sleep(560);
    state.openedBoxes.add(state.selectedBox);
    state.selectedBox = null;
    renderCard();
    showScreen("review");
    const wordCard = $("#wordCard");
    wordCard.classList.remove("is-from-box", "is-storing");
    void wordCard.offsetWidth;
    wordCard.classList.add("is-from-box");
    window.setTimeout(() => wordCard.classList.remove("is-from-box"), 720);
    $("#captionText").textContent = `这盒里是 ${state.currentCard.word}。先接住它。`;
  }

  function setRevealButton(title) {
    $("#revealButton strong").textContent = title;
  }

  function renderCard() {
    const item = state.currentCard;
    if (!item) return;
    state.revealed = false;
    state.encoding = Boolean(item.is_new && !item.attempt);
    const session = state.activeSession;
    $("#reviewCounter").textContent = `${Math.min(session.position + 1, session.total)} / ${session.total}`;
    $("#sessionProgress").style.width = `${session.total ? (session.position / session.total) * 100 : 0}%`;
    $("#cardKicker").textContent = state.encoding ? "先认识一下" : item.attempt ? "刚才忘了，再碰一次" : "先回忆一下";
    $("#wordText").textContent = item.word;
    $("#phoneticText").textContent = item.phonetic || "";
    $("#meaningText").textContent = item.meaning;
    $("#exampleText").textContent = item.example;
    $("#exampleZhText").textContent = item.example_zh;
    $("#exampleBlock").hidden = !item.example;
    $("#answerPanel").hidden = !state.encoding;
    $("#revealButton").hidden = false;
    $("#gradeActions").hidden = true;
    if (state.encoding) {
      setRevealButton("记住了，遮住试试");
    } else {
      setRevealButton("看看答案");
    }
    updateCompanionLine("review");
  }

  function revealOrRecall() {
    resetViewport();
    requestAnimationFrame(resetViewport);
    if (state.encoding) {
      state.encoding = false;
      $("#answerPanel").hidden = true;
      $("#cardKicker").textContent = "第一次回忆";
      setRevealButton("看看答案");
      $("#captionText").textContent = "好，现在不看答案，把刚才的意思找回来。";
      return;
    }
    state.revealed = true;
    $("#answerPanel").hidden = false;
    $("#revealButton").hidden = true;
    $("#gradeActions").hidden = false;
    $("#captionText").textContent = "想起来多少就算多少，不用为了‘答对’硬撑。";
  }

  async function gradeCurrent(grade) {
    const item = state.currentCard;
    if (!item || !state.activeSession) return;
    resetViewport();
    requestAnimationFrame(resetViewport);
    $$('[data-grade]').forEach((button) => { button.disabled = true; });
    const wordCard = $("#wordCard");
    const transitionStarted = performance.now();
    wordCard.classList.remove("is-from-box");
    wordCard.classList.add("is-storing");
    $("#captionText").textContent = "收好了，下一盒还在等你。";
    try {
      const result = await callEntry("submit_review", {
        session_id: state.activeSession.id,
        item_id: item.id,
        grade,
        client_event_id: eventId(),
      });
      state.dashboard = result.dashboard || state.dashboard;
      state.activeSession = result.session;
      state.currentCard = result.next_card;
      const transitionRemaining = 380 - (performance.now() - transitionStarted);
      if (transitionRemaining > 0) await sleep(transitionRemaining);
      if (result.session?.status === "complete" || !result.next_card) {
        const availableNew = Number(result.dashboard?.new_count || 0);
        $("#completedCount").textContent = String(result.session?.answered || 0);
        $("#completeTitle").textContent = result.session?.forgotten ? "难的词也没有被丢下。" : "这些词，又被你接住了。";
        $("#continueNewButton").hidden = availableNew <= 0;
        $("#continueNewButton strong").textContent = "再开一盒新词";
        $("#sessionProgress").style.width = "100%";
        showScreen("complete");
        $("#captionText").textContent = result.companion_cue?.text || "今天到这里就很好。";
        renderDashboard(result.dashboard);
      } else {
        showBoxDraw(result.companion_cue?.text || "继续，我们再拆一个。");
      }
    } catch (error) {
      wordCard.classList.remove("is-storing");
      toast(error.message);
    } finally { $$('[data-grade]').forEach((button) => { button.disabled = false; }); }
  }

  function updateCompanionLine(screen) {
    const item = state.currentCard;
    const messages = {
      home: state.dashboard?.active_session?.remaining ? "刚才的进度还在，我陪你接着来。" : "我在这边陪你，准备好就开始。",
      sources: "现在可以学习 CET-4 核心词汇。",
      familiarity: "不用想得太精确，选最接近的就行。",
      draw: state.selectedBox == null ? "挑一个吧，里面是什么还不知道。" : "要拆开这个吗？",
      review: state.encoding ? `先认识 ${item?.word || "这个词"}，等下我们马上遮住试一次。` : state.revealed ? "诚实选感受就好，我不会给你打分。" : item ? `先想想 ${item.word}，空白也没关系。` : "慢慢来。",
      complete: "做得好，今天这组完成啦。",
      empty: "今天先休息，我会陪你等下一次。",
    };
    $("#captionText").textContent = messages[screen] || messages.home;
  }

  async function loadCharacter() {
    const image = $("#characterImage");
    const avatar = $("#characterAvatar");
    const captionAvatar = $("#captionAvatar");
    const backdrop = $("#characterBackdrop");
    const empty = $("#characterEmpty");
    try {
      const data = isPreview
        ? { available: true, name: "Yui", data_url: "/plugin/plugins/study_companion/static/assets/yui/yui_companion_upper.webp" }
        : await callEntry("get_companion_identity");
      if (!data?.available) throw new Error("character unavailable");
      state.characterName = String(data.name || "N.E.K.O.");
      state.characterSrc = String(data.data_url || "");
      applyCharacter(image, avatar, captionAvatar, backdrop, empty);
    } catch (_error) {
      state.characterName = isPreview ? "Yui" : "N.E.K.O.";
      if (isPreview) state.characterSrc = "/plugin/plugins/study_companion/static/assets/yui/yui_companion_upper.webp";
      applyCharacter(image, avatar, captionAvatar, backdrop, empty);
    }
  }

  function applyCharacter(image, avatar, captionAvatar, backdrop, empty) {
    $("#characterName").textContent = state.characterName;
    $("#dialogName").textContent = state.characterName;
    $("#captionName").textContent = state.characterName;
    $("#characterStatus").textContent = `跟随系统角色 · ${state.characterName}`;
    updateStartButtonLabel();
    if (state.characterSrc) {
      image.src = state.characterSrc; avatar.src = state.characterSrc; captionAvatar.src = state.characterSrc; backdrop.src = state.characterSrc;
      image.hidden = false; backdrop.hidden = false; empty.hidden = true;
    } else { image.hidden = true; backdrop.hidden = true; empty.hidden = false; }
  }

  function setConversation(open) {
    $("#conversation").hidden = !open;
    $("#captionToggle").hidden = open;
    $("#captionToggle").setAttribute("aria-expanded", String(open));
    $("#companionPane").classList.toggle("is-chatting", open);
    if (open) $("#conversationMessage").focus();
  }

  async function sendCompanionMessage(event) {
    event.preventDefault();
    const input = $("#conversationMessage");
    const message = input.value.trim();
    if (!message) return;
    input.value = ""; input.disabled = true; $("#dialogReply").textContent = "让我想一下…";
    const activeScreen = $(".screen.is-active")?.dataset.screen || "home";
    try {
      const result = await callEntry("companion_reply", { message, stage: activeScreen === "review" && !state.revealed ? "question" : activeScreen, word: state.currentCard?.word || "" });
      $("#dialogReply").textContent = result.reply || "我在。";
    } catch (error) { $("#dialogReply").textContent = "刚才没接上，不过我还在这里。"; toast(error.message); }
    finally { input.disabled = false; input.focus(); }
  }

  function updatePreviewClock(data = readPreview()) {
    if (!isPreview) return;
    $("#previewClock").textContent = `Day ${Number(data.offsetDays || 0)} · ${previewNow(data).toLocaleDateString("zh-CN", { month: "numeric", day: "numeric" })}`;
  }

  async function movePreview(days) {
    const data = readPreview();
    data.offsetDays += days;
    data.activeSession = null;
    writePreview(data);
    showScreen("home");
    await refreshDashboard();
    toast(`时间已前进 ${days} 天，首页已按新的到期状态重算。`);
  }

  function bindEvents() {
    $("#closeWindow").addEventListener("click", () => window.close());
    $("#startButton").addEventListener("click", startFromHome);
    $("#sourceRow").addEventListener("click", () => showScreen("sources"));
    $("#chooseCet4").addEventListener("click", () => {
      if (state.dashboard?.familiarity === "unset" && Number(state.dashboard?.learned_count || 0) === 0) {
        showScreen("familiarity");
        return;
      }
      showScreen("home");
      toast("继续学习 CET-4 核心词汇。");
    });
    $$('[data-back]').forEach((button) => button.addEventListener("click", async () => { showScreen(button.dataset.back); if (button.dataset.back === "home") await refreshDashboard(); }));
    $$('[data-familiarity]').forEach((button) => button.addEventListener("click", async () => {
      try { await callEntry("set_familiarity", { level: button.dataset.familiarity }); } catch (_error) { /* non-blocking */ }
      await beginSession(state.pendingIntent);
    }));
    $("#continueNewButton").addEventListener("click", () => beginSession("learn_new", false, $("#continueNewButton")));
    $$(".blind-box").forEach((button) => button.addEventListener("click", () => selectBox(button)));
    $("#openBoxButton").addEventListener("click", openSelectedBox);
    $("#leaveDraw").addEventListener("click", async () => { showScreen("home"); await refreshDashboard(); });
    $("#leaveReview").addEventListener("click", async () => { showScreen("home"); await refreshDashboard(); });
    $("#revealButton").addEventListener("click", revealOrRecall);
    $$('[data-grade]').forEach((button) => button.addEventListener("click", () => gradeCurrent(button.dataset.grade)));
    $("#captionToggle").addEventListener("click", () => setConversation(true));
    $("#collapseConversation").addEventListener("click", () => setConversation(false));
    $("#conversationForm").addEventListener("submit", sendCompanionMessage);
    if (isPreview) {
      $("#advanceOneDay").addEventListener("click", () => movePreview(1));
      $("#advanceThreeDays").addEventListener("click", () => movePreview(3));
      $("#resetPreview").addEventListener("click", async () => { localStorage.removeItem(PREVIEW_KEY); updatePreviewClock(); showScreen("home"); await refreshDashboard(); toast("体验数据已重置。"); });
    }
  }

  async function init() {
    await loadPreviewLibrary();
    bindEvents();
    if (isPreview) { $("#previewLab").hidden = false; updatePreviewClock(); }
    await Promise.all([refreshDashboard(), loadCharacter()]);
    if (params.get("screen") === "review") await beginSession(params.get("intent") || "mixed");
    if (params.get("chat") === "1") setConversation(true);
  }

  init();
})();
