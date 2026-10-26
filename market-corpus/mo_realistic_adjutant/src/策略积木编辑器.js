"use strict";

const API_BASE = location.protocol === "file:" ? "http://127.0.0.1:8765" : "";
const $ = id => document.getElementById(id);
const clone = value => JSON.parse(JSON.stringify(value));
const api = async (path, options = {}) => {
  const response = await fetch(API_BASE + path, options);
  const value = await response.json();
  if (!response.ok) throw new Error(value.error || `请求失败：${response.status}`);
  return value;
};

const DIRECTORY = [
  ["战役", [["官方任务", ["盟军", "苏联", "厄普西隆", "焚风"]], "合作战役", "自定义战役", "未识别任务"]],
  ["遭遇战", [["盟军", ["美国", "欧洲联盟", "太平洋阵线"]], ["苏联", ["苏俄", "拉丁同盟", "中国"]], ["厄普西隆", ["心灵军团", "厄普西隆总部", "天蝎组织"]], ["焚风", ["焚风子阵营一", "焚风子阵营二", "焚风子阵营三"]]]],
  ["多人对战", ["战役合作", ["玩家对战", ["1对1", "2对2", "3对3", "4对4"]], ["混战", ["三人", "四人", "五人至八人"]], "人机混合", "特殊规则"]],
  ["通用战术", ["经济与扩张", "生产与科技", "移动与集结", "编队与装载", "部署与驻守", "基地防御", "接触战", "进攻与撤退", "空军", "海军", "支援技能", "维修与保护", "单位专属反制"]],
  ["游戏适配", ["心灵终结", "红色警戒2原版", "尤里的复仇", "其他模组"]],
];

const state = {
  registry: null, groups: [], definitions: new Map(), targets: [], categories: [],
  selected: null, program: {scripts: []}, scope: "permanent", category: "",
  page: 0, pageSize: 80, total: 0, strategies: [], activeGroup: "事件",
  history: [], future: [], drag: null, activeSlot: null, selectedNodeId: "", zoom: 1,
  cascade: null,
};

const TYPE_ALIASES = {
  "任意值": ["数字", "文字", "布尔", "对象引用", "单位引用", "单位集合", "队伍引用", "建筑引用", "建筑集合", "目标引用", "位置引用", "路径引用"],
  "对象引用": ["单位引用", "单位集合", "队伍引用", "建筑引用", "建筑集合", "目标引用", "位置引用"],
  "目标引用": ["单位引用", "单位集合", "队伍引用", "建筑引用", "建筑集合", "目标引用"],
  "单位集合": ["单位引用", "单位集合", "队伍引用"],
  "建筑集合": ["建筑引用", "建筑集合"],
};

function uid() {
  return `积木_${Date.now().toString(36)}_${Math.random().toString(36).slice(2, 8)}`;
}

function toast(message, error = false) {
  const node = $("toast");
  node.textContent = message;
  node.className = `toast${error ? " error" : ""}`;
  node.hidden = false;
  clearTimeout(toast.timer);
  toast.timer = setTimeout(() => { node.hidden = true; }, 3200);
}

function setConnection(message, kind = "") {
  $("connection").textContent = message;
  $("connection").className = kind;
}

function definition(blockId) {
  if (state.definitions.has(blockId)) return state.definitions.get(blockId);
  if (blockId.startsWith("自制.")) {
    const id = blockId.slice(3);
    const custom = (state.selected?.payload?.custom_blocks || []).find(item => item.id === id || item.name_zh === id);
    if (custom) {
      const parameters = Array.isArray(custom.parameters) ? custom.parameters : [];
      return {
        id: blockId, name_zh: custom.name_zh, shape: custom.shape || "命令", output: custom.output || "",
        color: custom.color || "#8b63ce", group_zh: "我的积木",
        inputs: parameters.map(item => typeof item === "string" ? {name_zh: item, type: "任意值"} : item),
      };
    }
  }
  return null;
}

function makeNode(blockId) {
  const def = definition(blockId);
  if (!def) throw new Error("积木定义不存在");
  const inputs = {};
  (def.inputs || []).forEach(input => {
    if (Object.prototype.hasOwnProperty.call(input, "default")) inputs[input.name_zh] = clone(input.default);
    else if (["菜单", "单位菜单", "文字", "文字输入"].includes(input.type)) inputs[input.name_zh] = "";
  });
  const stackNames = def.stacks || (def.shape === "帽子" ? ["执行"] : []);
  const stacks = Object.fromEntries(stackNames.map(name => [name, []]));
  return {node_id: uid(), block_id: blockId, label_zh: def.name_zh, shape: def.shape, output: def.output || "", inputs, stacks};
}

function cloneNode(node) {
  const result = clone(node);
  const renew = item => {
    item.node_id = uid();
    Object.values(item.inputs || {}).forEach(value => { if (isBlock(value)) renew(value); });
    Object.values(item.stacks || {}).flat().forEach(renew);
  };
  renew(result);
  return result;
}

function isBlock(value) {
  return Boolean(value && typeof value === "object" && (value.block_id || value.custom_id));
}

function canConnect(expected, output) {
  if (!expected || expected === "任意值") return Boolean(output);
  return expected === output || (TYPE_ALIASES[expected] || []).includes(output);
}

function automaticNumberReporter(expected, node) {
  if (expected !== "数字") return node;
  const output = definition(node.block_id)?.output || node.output;
  if (output === "单位集合" || output === "单位引用" || output === "队伍引用") {
    const wrapper = makeNode("侦测.单位数量"); wrapper.inputs.单位 = node; return wrapper;
  }
  if (output === "建筑集合" || output === "建筑引用") {
    const wrapper = makeNode("数量.数量"); wrapper.inputs.对象 = node; return wrapper;
  }
  return node;
}

function canConnectOrWrap(expected, node) {
  const output = definition(node.block_id)?.output || node.output;
  return canConnect(expected, output) || (expected === "数字" && ["单位集合", "单位引用", "队伍引用", "建筑集合", "建筑引用"].includes(output));
}

function snapshot() {
  return JSON.stringify(state.program);
}

function mutate(change) {
  state.history.push(snapshot());
  if (state.history.length > 80) state.history.shift();
  state.future = [];
  change();
  $("saveState").textContent = "有未保存修改";
  renderWorkspace();
  updateUndoButtons();
}

function restore(serialized) {
  state.program = JSON.parse(serialized);
  state.selectedNodeId = "";
  state.activeSlot = null;
  renderWorkspace();
  updateUndoButtons();
}

function undo() {
  if (!state.history.length) return;
  state.future.push(snapshot());
  restore(state.history.pop());
}

function redo() {
  if (!state.future.length) return;
  state.history.push(snapshot());
  restore(state.future.pop());
}

function updateUndoButtons() {
  $("undo").disabled = !state.history.length;
  $("redo").disabled = !state.future.length;
}

function visitNodes(callback, nodes = state.program.scripts, parent = state.program.scripts) {
  for (let index = 0; index < nodes.length; index += 1) {
    const node = nodes[index];
    if (callback(node, nodes, index, parent) === false) return false;
    for (const value of Object.values(node.inputs || {})) {
      if (isBlock(value) && visitNodes(callback, [value], node.inputs) === false) return false;
    }
    for (const children of Object.values(node.stacks || {})) {
      if (visitNodes(callback, children, node.stacks) === false) return false;
    }
  }
  return true;
}

function locate(nodeId) {
  const search = nodes => {
    for (let index = 0; index < nodes.length; index += 1) {
      const node = nodes[index];
      if (node.node_id === nodeId) return {node, array: nodes, index};
      for (const [name, value] of Object.entries(node.inputs || {})) {
        if (!isBlock(value)) continue;
        if (value.node_id === nodeId) return {node: value, inputOwner: node, inputName: name};
        const nested = searchInputs(value);
        if (nested) return nested;
      }
      for (const children of Object.values(node.stacks || {})) {
        const nested = search(children); if (nested) return nested;
      }
    }
    return null;
  };
  const searchInputs = node => {
    for (const [name, value] of Object.entries(node.inputs || {})) {
      if (!isBlock(value)) continue;
      if (value.node_id === nodeId) return {node: value, inputOwner: node, inputName: name};
      const nested = searchInputs(value); if (nested) return nested;
    }
    for (const children of Object.values(node.stacks || {})) {
      const nested = search(children); if (nested) return nested;
    }
    return null;
  };
  return search(state.program.scripts);
}

function removeNode(nodeId) {
  const found = locate(nodeId);
  if (found?.array) found.array.splice(found.index, 1);
  else if (found?.inputOwner) delete found.inputOwner.inputs[found.inputName];
  return found?.node || null;
}

function currentInsertionArray() {
  const found = locate(state.selectedNodeId);
  if (found) {
    const def = definition(found.node.block_id);
    const name = (def?.stacks || (def?.shape === "帽子" ? ["执行"] : []))[0];
    if (name && found.node.stacks?.[name]) return found.node.stacks[name];
  }
  const script = state.program.scripts[0];
  return script?.stacks?.执行 || null;
}

function placePaletteBlock(blockId) {
  const node = makeNode(blockId);
  const def = definition(blockId);
  if (def.shape === "帽子") {
    mutate(() => state.program.scripts.push(node));
    return;
  }
  if (["报告器", "布尔"].includes(def.shape)) {
    if (!state.activeSlot) return toast("先点击画布中的发光插槽，再选择这个积木。", true);
    const slot = state.activeSlot;
    if (!canConnectOrWrap(slot.expected, node)) return toast(`这里需要“${slot.expected}”积木。`, true);
    mutate(() => { slot.owner.inputs[slot.name] = automaticNumberReporter(slot.expected, node); state.activeSlot = null; });
    return;
  }
  const target = currentInsertionArray();
  if (!target) return toast("请先放入一个事件积木。", true);
  mutate(() => target.push(node));
}

function takeDraggedNode() {
  if (!state.drag) return null;
  if (state.drag.kind === "palette") return makeNode(state.drag.blockId);
  const found = locate(state.drag.nodeId);
  return found ? found.node : null;
}

function finishMove(node, targetArray, index = targetArray.length) {
  mutate(() => {
    if (state.drag?.kind === "existing") removeNode(state.drag.nodeId);
    const safeIndex = Math.max(0, Math.min(index, targetArray.length));
    targetArray.splice(safeIndex, 0, node);
  });
  state.drag = null;
}

function installDropZone(element, accept, drop) {
  element.addEventListener("dragover", event => {
    const node = takeDraggedNode();
    if (node && accept(node)) { event.preventDefault(); element.classList.add("drag-ready"); }
  });
  element.addEventListener("dragleave", () => element.classList.remove("drag-ready"));
  element.addEventListener("drop", event => {
    event.preventDefault(); event.stopPropagation(); element.classList.remove("drag-ready");
    const node = takeDraggedNode();
    if (node && accept(node)) drop(node);
  });
}

function menuOptions(input, node) {
  if (Array.isArray(input.options)) return input.options.map(value => ({value, label: value}));
  if (input.menu_source === "变量") return Object.keys(state.selected?.payload?.variables || {}).map(value => ({value, label: value}));
  if (input.menu_source === "列表") return Object.keys(state.selected?.payload?.lists || {}).map(value => ({value, label: value}));
  if (input.menu_source === "策略") return state.strategies.map(item => ({value: item.id, label: item.name_zh}));
  if (input.type === "单位菜单") return unitOptions(input, node);
  return [];
}

const ROLE_ZH = {
  anti_infantry: "反步兵单位", anti_armor: "反装甲单位", anti_air: "防空单位", miner: "采矿单位",
  transport: "运输单位", support: "支援单位", defense: "防御建筑", power: "电力建筑", production: "生产建筑",
  technology: "科技建筑", map_object: "地图物件", structure: "建筑", infantry: "步兵", aircraft: "飞机", naval: "海军单位",
};

function chineseUnitName(unit, index) {
  const name = String(unit.name_zh || "");
  if (/[㐀-鿿]/.test(name)) return name;
  const role = ROLE_ZH[unit.role] || ROLE_ZH[unit.battlefield_class] || unit.group || "单位";
  const tags = (unit.threat_tags || []).map(tag => ({anti_infantry: "反步兵", anti_armor: "反装甲", anti_air: "防空", anti_structure: "攻坚", area_anti_infantry: "范围反步兵"}[tag])).filter(Boolean);
  return `${role} · 科技${unit.tech_level}${tags.length ? " · " + tags.join("、") : ""} · 候选${index + 1}`;
}

function unitOptions(input, node) {
  const isBuilding = input.menu_source === "建筑" || input.name_zh === "具体建筑";
  const faction = node.inputs?.子阵营 || "任意子阵营";
  const category = node.inputs?.类别 || (isBuilding ? "任意建筑" : "任意类别");
  let values = state.targets.filter(item => isBuilding ? item.group === "建筑" : item.group !== "建筑");
  if (faction !== "任意子阵营") values = values.filter(item => (item.factions || []).includes(faction));
  if (!isBuilding && !["任意类别", "步兵", "装甲", "飞机", "海军"].includes(category)) {
    const roleWords = {"采矿单位": ["miner", "resource"], "建设单位": ["construction", "builder"], "运输单位": ["transport"], "支援单位": ["support"]}[category] || [];
    values = values.filter(item => roleWords.some(word => String(item.role || "").toLowerCase().includes(word)));
  } else if (!isBuilding && category !== "任意类别") values = values.filter(item => item.group === category);
  if (isBuilding && category === "建筑插件") values = values.filter(item => item.is_plug);
  return [{value: "任意", label: "任意"}, ...values.slice(0, 500).map((unit, index) => ({value: unit.id, label: chineseUnitName(unit, index)}))];
}

const CASCADE_RELATIONS = ["我方", "敌方", "中立", "任意关系"];
const CASCADE_FACTIONS = ["任意子阵营", "美国", "欧洲联盟", "太平洋阵线", "苏俄", "拉丁同盟", "中国", "心灵军团", "厄普西隆总部", "天蝎组织", "焚风子阵营一", "焚风子阵营二", "焚风子阵营三"];
const CASCADE_UNIT_GROUPS = ["任意类别", "步兵", "装甲", "飞机", "海军", "采矿单位", "建设单位", "运输单位", "支援单位"];
const CASCADE_BUILDING_GROUPS = ["任意建筑", "基地车", "电厂", "兵营", "矿场", "重工", "机场", "科技建筑", "防御建筑", "超级武器", "中立科技建筑", "建筑插件"];

function specificName(node, isBuilding) {
  const id = node.inputs?.[isBuilding ? "具体建筑" : "具体单位"] || "任意";
  if (id === "任意") return "任意";
  const index = state.targets.findIndex(item => item.id === id);
  return index >= 0 ? chineseUnitName(state.targets[index], index) : "指定对象";
}

function cascadeLabel(node, isBuilding) {
  return [node.inputs?.关系 || "我方", node.inputs?.子阵营 || "任意子阵营", node.inputs?.类别 || (isBuilding ? "任意建筑" : "任意类别"), specificName(node, isBuilding)].join(" · ");
}

function buildingCategoryMatches(item, category) {
  if (category === "任意建筑") return true;
  if (category === "建筑插件") return item.is_plug === true;
  const text = `${item.role || ""} ${item.building_class || ""}`.toLowerCase();
  const words = {
    "基地车": ["construction", "base", "yard"], "电厂": ["power"], "兵营": ["barracks", "infantry_production"],
    "矿场": ["refinery", "resource"], "重工": ["war_factory", "vehicle_production"], "机场": ["airfield", "aircraft_production"],
    "科技建筑": ["technology", "tech"], "防御建筑": ["defense"], "超级武器": ["superweapon"], "中立科技建筑": ["tech_building", "neutral"],
  }[category] || [];
  return words.some(word => text.includes(word));
}

function unitCategoryMatches(item, category) {
  if (["任意类别", "步兵", "装甲", "飞机", "海军"].includes(category)) return category === "任意类别" || item.group === category;
  const text = `${item.role || ""} ${item.battlefield_class || ""}`.toLowerCase();
  const words = {"采矿单位": ["miner", "resource"], "建设单位": ["construction", "builder"], "运输单位": ["transport"], "支援单位": ["support", "repair", "medic"]}[category] || [];
  return words.some(word => text.includes(word));
}

function cascadeTargets(node, isBuilding, search = "") {
  const faction = node.inputs?.子阵营 || "任意子阵营";
  const category = node.inputs?.类别 || (isBuilding ? "任意建筑" : "任意类别");
  const token = search.trim().toLowerCase();
  return state.targets.filter(item => {
    if (isBuilding ? item.group !== "建筑" : item.group === "建筑") return false;
    if (faction !== "任意子阵营" && !(item.factions || []).includes(faction)) return false;
    if (isBuilding ? !buildingCategoryMatches(item, category) : !unitCategoryMatches(item, category)) return false;
    return !token || `${item.name_zh} ${item.role || ""}`.toLowerCase().includes(token);
  }).slice(0, 600);
}

function closeCascade(commit = true) {
  const session = state.cascade;
  if (!session) return;
  if (!commit) {
    session.node.inputs = clone(session.beforeInputs);
  } else if (JSON.stringify(session.beforeInputs) !== JSON.stringify(session.node.inputs)) {
    state.history.push(session.beforeProgram);
    if (state.history.length > 80) state.history.shift();
    state.future = [];
    $("saveState").textContent = "有未保存修改";
  }
  state.cascade = null;
  $("cascadePicker").hidden = true;
  renderWorkspace(); updateUndoButtons();
}

function cascadeColumn(title, options, selected, choose, renderLabel = value => value) {
  const column = document.createElement("section"); column.className = "cascade-column";
  const heading = document.createElement("strong"); heading.textContent = title; column.append(heading);
  const list = document.createElement("div"); list.className = "cascade-options";
  options.forEach(value => {
    const button = document.createElement("button"); button.type = "button"; button.className = value === selected ? "active" : "";
    button.textContent = renderLabel(value); button.addEventListener("click", event => { event.stopPropagation(); choose(value); }); list.append(button);
  });
  column.append(list); return column;
}

function renderCascadePicker() {
  const session = state.cascade; if (!session) return;
  const {node, isBuilding} = session; const picker = $("cascadePicker"); picker.innerHTML = ""; picker.hidden = false;
  const head = document.createElement("div"); head.className = "cascade-head";
  const title = document.createElement("strong"); title.textContent = isBuilding ? "选择建筑" : "选择单位";
  const search = document.createElement("input"); search.placeholder = "搜索具体对象"; search.value = session.search || "";
  const done = document.createElement("button"); done.type = "button"; done.textContent = "完成"; done.addEventListener("click", () => closeCascade(true));
  head.append(title, search, done); picker.append(head);
  const columns = document.createElement("div"); columns.className = "cascade-columns";
  const resetSpecific = () => { node.inputs[isBuilding ? "具体建筑" : "具体单位"] = "任意"; session.search = ""; renderCascadePicker(); };
  columns.append(cascadeColumn("关系", CASCADE_RELATIONS, node.inputs.关系, value => { node.inputs.关系 = value; renderCascadePicker(); }));
  columns.append(cascadeColumn("十二子阵营", CASCADE_FACTIONS, node.inputs.子阵营, value => { node.inputs.子阵营 = value; resetSpecific(); }));
  const groups = isBuilding ? CASCADE_BUILDING_GROUPS : CASCADE_UNIT_GROUPS;
  columns.append(cascadeColumn("类别", groups, node.inputs.类别, value => { node.inputs.类别 = value; resetSpecific(); }));
  const targetKey = isBuilding ? "具体建筑" : "具体单位";
  const targets = [{id: "任意", name_zh: "任意"}, ...cascadeTargets(node, isBuilding, session.search).map((item, index) => ({...item, name_zh: chineseUnitName(item, index)}))];
  columns.append(cascadeColumn("具体对象", targets.map(item => item.id), node.inputs[targetKey] || "任意", value => { node.inputs[targetKey] = value; closeCascade(true); }, value => targets.find(item => item.id === value)?.name_zh || "指定对象"));
  picker.append(columns);
  search.addEventListener("input", () => { session.search = search.value; renderCascadePicker(); const next = $("cascadePicker").querySelector("input"); next?.focus(); next?.setSelectionRange(next.value.length, next.value.length); });
}

function openCascadePicker(node, isBuilding, anchor) {
  if (state.cascade) closeCascade(true);
  state.cascade = {node, isBuilding, beforeInputs: clone(node.inputs), beforeProgram: snapshot(), search: ""};
  const box = anchor.getBoundingClientRect(); const picker = $("cascadePicker");
  picker.style.left = `${Math.max(12, Math.min(box.left, window.innerWidth - 790))}px`;
  picker.style.top = `${Math.min(window.innerHeight - 390, box.bottom + 6)}px`;
  renderCascadePicker();
}

function renderCascadeInput(node, isBuilding) {
  const button = document.createElement("button"); button.type = "button"; button.className = "cascade-input";
  button.textContent = cascadeLabel(node, isBuilding); button.title = "点击后依次选择关系、子阵营、类别和具体对象";
  button.addEventListener("click", event => { event.stopPropagation(); openCascadePicker(node, isBuilding, button); });
  return button;
}

function renderInput(input, node) {
  const value = node.inputs?.[input.name_zh];
  if (["数字输入", "文字输入", "文字"].includes(input.type)) {
    const field = document.createElement("input");
    field.className = "literal-input";
    field.type = input.type === "数字输入" ? "number" : "text";
    field.value = value ?? input.default ?? "";
    field.title = input.name_zh;
    field.addEventListener("change", () => mutate(() => { node.inputs[input.name_zh] = field.type === "number" ? Number(field.value) : field.value; }));
    return field;
  }
  if (["菜单", "单位菜单"].includes(input.type)) {
    const select = document.createElement("select");
    select.className = "menu-input";
    select.title = input.name_zh;
    const options = menuOptions(input, node);
    if (!options.length) options.push({value: "", label: input.menu_source ? `请先创建${input.menu_source}` : "请选择"});
    options.forEach(option => select.add(new Option(option.label, option.value)));
    select.value = value ?? input.default ?? options[0].value;
    node.inputs[input.name_zh] = select.value;
    select.addEventListener("change", () => mutate(() => {
      node.inputs[input.name_zh] = select.value;
      if (["子阵营", "类别"].includes(input.name_zh)) {
        if (Object.prototype.hasOwnProperty.call(node.inputs, "具体单位")) node.inputs.具体单位 = "任意";
        if (Object.prototype.hasOwnProperty.call(node.inputs, "具体建筑")) node.inputs.具体建筑 = "任意";
      }
    }));
    return select;
  }
  if (typeof value === "number" && input.type === "数字") {
    const field = document.createElement("input");
    field.type = "number"; field.className = "literal-input"; field.value = value;
    field.addEventListener("change", () => mutate(() => { node.inputs[input.name_zh] = Number(field.value); }));
    return field;
  }
  const slot = document.createElement("span");
  slot.className = `slot${input.type === "布尔" ? " boolean-slot" : ""}${isBlock(value) ? " filled" : ""}`;
  slot.title = `需要：${input.type}`;
  if (isBlock(value)) slot.append(renderNode(value, null, true));
  else slot.textContent = input.name_zh;
  slot.addEventListener("click", event => {
    event.stopPropagation();
    state.activeSlot = {owner: node, name: input.name_zh, expected: input.type};
    document.querySelectorAll(".slot").forEach(item => item.classList.remove("drag-ready"));
    slot.classList.add("drag-ready");
    toast(`已选择“${input.name_zh}”插槽，请从左侧选择“${input.type}”积木。`);
  });
  installDropZone(slot, dragged => canConnectOrWrap(input.type, dragged), dragged => {
    mutate(() => {
      if (state.drag?.kind === "existing") removeNode(state.drag.nodeId);
      node.inputs[input.name_zh] = automaticNumberReporter(input.type, dragged);
      state.activeSlot = null;
    });
    state.drag = null;
  });
  return slot;
}

function renderNode(node, parentArray, inline = false) {
  const def = definition(node.block_id);
  if (!def) return document.createTextNode("");
  node.label_zh = def.name_zh; node.shape = def.shape; node.output = def.output || "";
  node.inputs = node.inputs && typeof node.inputs === "object" ? node.inputs : {};
  node.stacks = node.stacks && typeof node.stacks === "object" ? node.stacks : {};
  const block = document.createElement("div");
  block.className = `block-node shape-${def.shape}${state.selectedNodeId === node.node_id ? " selected" : ""}`;
  block.style.setProperty("--block-color", def.color || "#4c97ff");
  block.draggable = true;
  block.dataset.nodeId = node.node_id;
  block.addEventListener("dragstart", event => {
    event.stopPropagation();
    state.drag = {kind: "existing", nodeId: node.node_id};
    event.dataTransfer.effectAllowed = "move";
    event.dataTransfer.setData("text/plain", node.node_id);
  });
  block.addEventListener("dragend", () => { state.drag = null; document.querySelectorAll(".drag-ready").forEach(item => item.classList.remove("drag-ready")); });
  block.addEventListener("click", event => { event.stopPropagation(); state.selectedNodeId = node.node_id; renderWorkspace(); });

  const line = document.createElement("div"); line.className = "block-line";
  const label = document.createElement("span"); label.className = "block-label"; label.textContent = def.name_zh; line.append(label);
  if (def.editor === "级联单位选择器" || def.editor === "级联建筑选择器") {
    line.append(renderCascadeInput(node, def.editor === "级联建筑选择器"));
  } else {
    (def.inputs || []).forEach(input => line.append(renderInput(input, node)));
  }
  if (!inline) {
    const controls = document.createElement("span"); controls.className = "block-controls";
    const copyButton = document.createElement("button"); copyButton.type = "button"; copyButton.textContent = "复制";
    copyButton.addEventListener("click", event => { event.stopPropagation(); if (parentArray) mutate(() => parentArray.splice(parentArray.indexOf(node) + 1, 0, cloneNode(node))); });
    const deleteButton = document.createElement("button"); deleteButton.type = "button"; deleteButton.textContent = "删除";
    deleteButton.addEventListener("click", event => { event.stopPropagation(); mutate(() => removeNode(node.node_id)); });
    controls.append(copyButton, deleteButton); line.append(controls);
  }
  block.append(line);

  const stackNames = def.stacks || (def.shape === "帽子" ? ["执行"] : []);
  stackNames.forEach(name => {
    if (!Array.isArray(node.stacks[name])) node.stacks[name] = [];
    const wrap = document.createElement("div"); wrap.className = "stack-wrap";
    const arm = document.createElement("div"); arm.className = "stack-arm";
    const body = document.createElement("div"); body.className = "stack-body";
    const stackLabel = document.createElement("div"); stackLabel.className = "stack-label"; stackLabel.textContent = name;
    const drop = document.createElement("div"); drop.className = "stack-drop";
    const children = node.stacks[name];
    if (!children.length) { const empty = document.createElement("div"); empty.className = "empty-stack"; empty.textContent = "拖入命令或控制积木"; drop.append(empty); }
    children.forEach((child, index) => {
      const childElement = renderNode(child, children);
      childElement.addEventListener("dragover", event => { event.preventDefault(); event.stopPropagation(); });
      childElement.addEventListener("drop", event => {
        event.preventDefault(); event.stopPropagation();
        const dragged = takeDraggedNode();
        const shape = dragged && definition(dragged.block_id)?.shape;
        if (dragged && ["命令", "容器"].includes(shape)) finishMove(dragged, children, index);
      });
      drop.append(childElement);
    });
    installDropZone(drop, dragged => ["命令", "容器"].includes(definition(dragged.block_id)?.shape), dragged => finishMove(dragged, children));
    body.append(stackLabel, drop); wrap.append(arm, body); block.append(wrap);
  });
  return block;
}

function renderWorkspace() {
  const area = $("scriptArea"); area.innerHTML = "";
  state.program.scripts.forEach(script => {
    const stack = document.createElement("div"); stack.className = "script-stack";
    stack.append(renderNode(script, state.program.scripts)); area.append(stack);
  });
  $("rootDrop").hidden = state.program.scripts.length > 3;
  installRootDrop();
}

function installRootDrop() {
  const root = $("rootDrop");
  installDropZone(root, node => definition(node.block_id)?.shape === "帽子", node => finishMove(node, state.program.scripts));
}

function renderPalette() {
  const categoryBox = $("blockCategories"); categoryBox.innerHTML = "";
  const groups = [...state.groups];
  if ((state.selected?.payload?.custom_blocks || []).length) groups.push({name_zh: "我的积木", color: "#8b63ce", blocks: []});
  groups.forEach(group => {
    const button = document.createElement("button"); button.className = `category-button${group.name_zh === state.activeGroup ? " active" : ""}`;
    const dot = document.createElement("span"); dot.className = "category-dot"; dot.style.background = group.color;
    button.append(dot, document.createTextNode(group.name_zh));
    button.addEventListener("click", () => { state.activeGroup = group.name_zh; renderPalette(); });
    categoryBox.append(button);
  });
  const group = groups.find(item => item.name_zh === state.activeGroup) || groups[0];
  if (!group) return;
  $("drawerTitle").textContent = group.name_zh;
  const search = $("blockSearch").value.trim();
  let blocks = group.blocks;
  if (group.name_zh === "我的积木") blocks = (state.selected?.payload?.custom_blocks || []).map(item => definition(`自制.${item.id}`)).filter(Boolean);
  const catalog = $("blockCatalog"); catalog.innerHTML = "";
  blocks.filter(block => !search || block.name_zh.includes(search)).forEach(blockDef => {
    const button = document.createElement("button");
    button.className = `palette-block shape-${blockDef.shape}`; button.style.setProperty("--block-color", blockDef.color || group.color);
    button.textContent = blockDef.name_zh; button.draggable = true;
    button.addEventListener("dragstart", event => {
      state.drag = {kind: "palette", blockId: blockDef.id}; event.dataTransfer.effectAllowed = "copy"; event.dataTransfer.setData("text/plain", blockDef.id);
    });
    button.addEventListener("dragend", () => { state.drag = null; });
    button.addEventListener("click", () => placePaletteBlock(blockDef.id));
    catalog.append(button);
  });
}

function standardLegacyNode(raw) {
  const text = String(typeof raw === "object" ? raw.legacy_id || raw.label_zh || raw.opcode || "" : raw).toLowerCase();
  if (/counter|hard_counter|intercept/.test(text)) { const node = makeNode("克制.切换方案"); node.inputs.威胁类型 = "步兵"; return node; }
  if (/delay|retreat|regroup/.test(text)) return makeNode("控制.重新判断");
  if (/commit|attack|assault|destroy/.test(text)) return makeNode("控制.重新判断");
  if (/deploy|garrison/.test(text)) { const node = makeNode("行动.部署"); node.inputs.单位 = makeNode("单位.空闲"); return node; }
  if (/repair/.test(text)) { const node = makeNode("行动.维修"); node.inputs.对象 = makeNode("建筑.受损"); return node; }
  return makeNode("控制.重新判断");
}

function normalizeProgram(payload) {
  const current = payload?.block_program;
  if (current && Array.isArray(current.scripts) && current.scripts.length) {
    const sanitize = node => {
      const def = definition(node.block_id);
      if (!def) return null;
      node.node_id = node.node_id || uid(); node.label_zh = def.name_zh; node.shape = def.shape; node.output = def.output || "";
      node.inputs = node.inputs && typeof node.inputs === "object" ? node.inputs : {};
      Object.entries(node.inputs).forEach(([key, value]) => { if (isBlock(value)) node.inputs[key] = sanitize(value); });
      node.stacks = node.stacks && typeof node.stacks === "object" ? node.stacks : {};
      Object.keys(node.stacks).forEach(key => { node.stacks[key] = (node.stacks[key] || []).map(sanitize).filter(Boolean); });
      return node;
    };
    return {scripts: current.scripts.map(sanitize).filter(Boolean)};
  }
  const event = makeNode(payload?.conditions ? "事件.敌军出现" : "事件.对局开始");
  const oldActions = current?.actions || payload?.flow || payload?.actions || [];
  event.stacks.执行 = oldActions.slice(0, 24).map(standardLegacyNode);
  if (!event.stacks.执行.length) event.stacks.执行.push(makeNode("控制.重新判断"));
  return {scripts: [event]};
}

function newStrategy(showToast = true) {
  const event = makeNode("事件.对局开始"); event.stacks.执行.push(makeNode("控制.重新判断"));
  state.selected = {
    id: `learned_${Date.now().toString(36)}`, name_zh: "未命名策略", category_path: "猫娘学习/临时方案",
    strategy_type: "action_flow", library_scope: "temporary", status: "draft", enabled: true,
    usable_in_match: true, priority: 30, weight: 20, risk_level: "medium", source_kind: "player_blocks",
    source_text: "", payload: {variables: {}, lists: {}, custom_blocks: [], block_program: {scripts: []}},
  };
  state.program = {scripts: [event]}; state.history = []; state.future = []; state.selectedNodeId = "";
  fillMetadata(); renderWorkspace(); renderPalette(); renderData(); updateUndoButtons();
  $("saveState").textContent = "新方案尚未保存";
  if (showToast) toast("已经建立空白积木方案。先选择事件，再添加行动。")
}

function fillMetadata() {
  const item = state.selected; if (!item) return;
  $("strategyName").value = item.name_zh || "未命名策略";
  $("scopeBadge").textContent = item.library_scope === "permanent" ? "正式策略" : "临时方案";
  $("metaCategory").value = item.category_path || "猫娘学习/临时方案";
  $("metaType").value = item.strategy_type || "action_flow";
  $("metaPriority").value = item.priority ?? 30;
  $("metaRisk").value = item.risk_level || "medium";
  $("metaStatus").value = item.status || "draft";
  $("metaScope").value = item.library_scope || "temporary";
  $("metaCategoryPaths").textContent = (item.category_paths || [item.category_path]).filter(Boolean).join("　·　") || "保存后自动归类";
  $("metaNotes").value = item.source_text || "";
  $("toggleStrategy").textContent = item.enabled === false ? "启用策略" : "暂停策略";
}

async function openStrategy(id) {
  try {
    const item = await api(`/api/strategies/${encodeURIComponent(id)}`);
    state.selected = item; state.program = normalizeProgram(item.payload || {}); state.history = []; state.future = []; state.selectedNodeId = "";
    fillMetadata(); renderWorkspace(); renderPalette(); renderData(); renderStrategyList(); updateUndoButtons();
    $("saveState").textContent = "已读取"; $("diagnosticList").textContent = "已按树形积木脚本载入，可以继续拖放编辑。";
  } catch (error) { toast(error.message, true); }
}

function applyMetadata() {
  const item = state.selected;
  item.name_zh = $("strategyName").value.trim() || "未命名策略";
  item.category_path = $("metaCategory").value.trim() || "猫娘学习/临时方案";
  item.strategy_type = $("metaType").value; item.priority = Number($("metaPriority").value) || 0;
  item.risk_level = $("metaRisk").value; item.status = $("metaStatus").value;
  item.library_scope = $("metaScope").value; item.source_text = $("metaNotes").value;
}

async function compileCurrent(showSuccess = true) {
  const payload = clone(state.selected?.payload || {});
  payload.block_program = {scripts: clone(state.program.scripts)};
  try {
    const result = await api("/api/blocks/compile", {method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify(payload)});
    const messages = result.diagnostics || [];
    $("diagnosticList").textContent = messages.length ? messages.join("　") : "结构正确：所有积木连接类型均有效。";
    $("diagnosticList").className = messages.length ? "" : "diagnostic-ok";
    if (showSuccess) toast(messages.length ? "结构可执行，但还有改进建议。" : "积木结构检查通过。")
    return result;
  } catch (error) {
    $("diagnosticList").textContent = error.message; $("diagnosticList").className = "diagnostic-error";
    if (showSuccess) toast(error.message, true);
    throw error;
  }
}

async function saveStrategy() {
  if (!state.selected) return;
  applyMetadata();
  const payload = clone(state.selected.payload || {});
  payload.block_program = {scripts: clone(state.program.scripts)};
  payload.block_editor = "scratch_tree";
  try {
    const compiled = await compileCurrent(false);
    payload.block_diagnostics = compiled.diagnostics || [];
    const body = {...state.selected, payload, name_zh: state.selected.name_zh};
    const exists = Boolean(state.selected.created_at || state.strategies.some(item => item.id === state.selected.id));
    const path = exists ? `/api/strategies/${encodeURIComponent(state.selected.id)}` : "/api/strategies";
    state.selected = await api(path, {method: exists ? "PUT" : "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify(body)});
    state.program = normalizeProgram(state.selected.payload || {}); state.history = []; state.future = [];
    fillMetadata(); renderWorkspace(); renderPalette(); updateUndoButtons(); await loadStrategies();
    $("saveState").textContent = "已保存"; toast("策略已保存为树形积木脚本。")
  } catch (error) { toast(`无法保存：${error.message}`, true); }
}

function categoryCount(path) {
  return Number(state.categories.find(item => item.path === path)?.count || 0);
}

function renderDirectoryNode(value, parentPath = "") {
  const name = Array.isArray(value) ? value[0] : value;
  const children = Array.isArray(value) ? value[1] : [];
  const path = parentPath ? `${parentPath}/${name}` : name;
  const wrap = document.createElement("div");
  const button = document.createElement("button"); button.className = `tree-row${state.category === path ? " active" : ""}`;
  const toggle = document.createElement("span"); toggle.className = "toggle"; toggle.textContent = children?.length ? "▾" : "";
  const label = document.createElement("span"); label.textContent = name;
  const count = document.createElement("span"); count.className = "count"; count.textContent = categoryCount(path);
  button.append(toggle, label, count); wrap.append(button);
  let childBox = null;
  if (children?.length) {
    childBox = document.createElement("div"); childBox.className = "tree-children";
    children.forEach(child => childBox.append(renderDirectoryNode(child, path))); wrap.append(childBox);
    toggle.addEventListener("click", event => { event.stopPropagation(); childBox.hidden = !childBox.hidden; toggle.textContent = childBox.hidden ? "▸" : "▾"; });
  }
  button.addEventListener("click", () => { state.category = path; state.page = 0; renderCategoryTree(); loadStrategies(); });
  return wrap;
}

function renderCategoryTree() {
  const tree = $("categoryTree"); tree.innerHTML = ""; DIRECTORY.forEach(item => tree.append(renderDirectoryNode(item)));
}

async function loadStrategies() {
  const query = new URLSearchParams({scope: state.scope, limit: state.pageSize, offset: state.page * state.pageSize, search: $("strategySearch").value.trim()});
  if (state.category) query.set("category", state.category);
  try {
    const data = await api(`/api/strategies?${query}`);
    state.strategies = data.items || []; state.total = data.total || 0;
    renderStrategyList(); setConnection("已连接", "ok");
  } catch (error) { setConnection("连接失败", "error"); toast(error.message, true); }
}

function renderStrategyList() {
  const list = $("strategyList"); list.innerHTML = "";
  state.strategies.forEach(item => {
    const row = document.createElement("button"); row.className = `strategy-row${state.selected?.id === item.id ? " active" : ""}`;
    const name = document.createElement("strong"); name.textContent = item.name_zh || "未命名策略";
    const meta = document.createElement("small"); meta.textContent = `${item.category_path || "未分类"} · ${item.status === "stable" ? "稳定" : item.status === "recommended" ? "推荐" : item.status === "experimental" ? "试验中" : "草稿"}`;
    row.append(name, meta); row.addEventListener("click", () => openStrategy(item.id)); list.append(row);
  });
  const pages = Math.max(1, Math.ceil(state.total / state.pageSize));
  $("resultCount").textContent = `${state.total} 条`; $("pageLabel").textContent = `${state.page + 1} / ${pages}`;
  $("previousPage").disabled = state.page === 0; $("nextPage").disabled = state.page + 1 >= pages;
}

function renderData() {
  const payload = state.selected?.payload || {};
  const variables = payload.variables || {}; const lists = payload.lists || {}; const custom = payload.custom_blocks || [];
  const renderRows = (target, entries, remove) => {
    target.innerHTML = ""; entries.forEach(([name, detail]) => {
      const row = document.createElement("div"); row.className = "data-row";
      const text = document.createElement("div"); const strong = document.createElement("strong"); strong.textContent = name;
      const small = document.createElement("small"); small.textContent = detail; text.append(strong, small);
      const button = document.createElement("button"); button.type = "button"; button.textContent = "删除"; button.addEventListener("click", () => { remove(name); renderData(); renderPalette(); });
      row.append(text, button); target.append(row);
    });
  };
  renderRows($("variableList"), Object.entries(variables).map(([name, value]) => [name, typeof value === "object" ? `${value.type || "任意值"}，初始值：${value.value ?? ""}` : `初始值：${value}`]), name => delete variables[name]);
  renderRows($("listList"), Object.entries(lists).map(([name, value]) => [name, `类型：${value?.type || "任意值"}，${Array.isArray(value) ? value.length : (value?.items || []).length} 项`]), name => delete lists[name]);
  renderRows($("customList"), custom.map(value => [value.name_zh, `${value.shape || "命令"}积木，${(value.parameters || []).length} 个参数`]), name => { payload.custom_blocks = custom.filter(value => value.name_zh !== name); });
  state.selected.payload = payload;
}

function addVariable() {
  const name = $("variableName").value.trim(); if (!name) return toast("请先填写变量名称。", true);
  const payload = state.selected.payload; payload.variables = payload.variables || {};
  if (Object.prototype.hasOwnProperty.call(payload.variables, name)) return toast("这个变量已经存在。", true);
  const raw = $("variableValue").value; const type = $("variableType").value;
  payload.variables[name] = {type, value: type === "数字" ? Number(raw || 0) : type === "真假" ? raw === "真" : raw};
  $("variableName").value = ""; $("variableValue").value = ""; renderData(); renderPalette(); toast("变量已加入当前策略。")
}

function addList() {
  const name = $("listName").value.trim(); if (!name) return toast("请先填写列表名称。", true);
  const payload = state.selected.payload; payload.lists = payload.lists || {};
  if (Object.prototype.hasOwnProperty.call(payload.lists, name)) return toast("这个列表已经存在。", true);
  payload.lists[name] = {type: $("listType").value, items: []}; $("listName").value = ""; renderData(); renderPalette(); toast("列表已加入当前策略。")
}

function createCustomBlock() {
  const name = $("customName").value.trim(); if (!name) return toast("请填写我的积木名称。", true);
  const found = locate(state.selectedNodeId); const def = found && definition(found.node.block_id);
  const stackName = (def?.stacks || (def?.shape === "帽子" ? ["执行"] : []))[0];
  const body = stackName ? found.node.stacks?.[stackName] : null;
  if (!body?.length) return toast("先选中一个含有子积木的事件或控制积木。", true);
  const parameters = $("customParameters").value.split(",").map(part => {
    const [parameterName, type = "任意值"] = part.split(":").map(value => value.trim()); return parameterName ? {name_zh: parameterName, type} : null;
  }).filter(Boolean);
  const payload = state.selected.payload; payload.custom_blocks = payload.custom_blocks || [];
  if (payload.custom_blocks.some(item => item.name_zh === name)) return toast("这个我的积木名称已经存在。", true);
  payload.custom_blocks.push({id: `我的_${Date.now().toString(36)}`, name_zh: name, shape: $("customShape").value, color: "#8b63ce", parameters, body: clone(body)});
  $("customName").value = ""; $("customParameters").value = ""; state.activeGroup = "我的积木"; renderData(); renderPalette(); toast("已经从选中子栈创建我的积木。")
}

async function toggleStrategy() {
  if (!state.selected?.created_at) return toast("新策略保存后才能启用或暂停。", true);
  try {
    state.selected = await api(`/api/strategies/${encodeURIComponent(state.selected.id)}/enable`, {method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify({enabled: state.selected.enabled === false})});
    fillMetadata(); toast(state.selected.enabled ? "策略已启用。" : "策略已暂停。")
  } catch (error) { toast(error.message, true); }
}

async function deleteStrategy() {
  if (!state.selected?.created_at) return newStrategy();
  if (!confirm("确认把这条策略移入回收站？")) return;
  try { await api(`/api/strategies/${encodeURIComponent(state.selected.id)}`, {method: "DELETE"}); $("metaDialog").close(); newStrategy(false); await loadStrategies(); toast("策略已移入回收站。") } catch (error) { toast(error.message, true); }
}

function setZoom(value) {
  state.zoom = Math.max(.55, Math.min(1.55, value));
  $("workspaceCanvas").style.transform = `scale(${state.zoom})`; $("zoomReset").textContent = `${Math.round(state.zoom * 100)}%`;
}

function bindEvents() {
  $("newStrategy").addEventListener("click", () => newStrategy()); $("undo").addEventListener("click", undo); $("redo").addEventListener("click", redo);
  $("checkStrategy").addEventListener("click", () => compileCurrent()); $("saveStrategy").addEventListener("click", saveStrategy);
  $("openMeta").addEventListener("click", () => { fillMetadata(); $("metaDialog").showModal(); });
  $("openData").addEventListener("click", () => { renderData(); $("dataDialog").showModal(); });
  $("strategyName").addEventListener("input", () => { if (state.selected) { state.selected.name_zh = $("strategyName").value; $("saveState").textContent = "有未保存修改"; } });
  $("blockSearch").addEventListener("input", renderPalette); $("strategySearch").addEventListener("input", () => { clearTimeout(loadStrategies.timer); loadStrategies.timer = setTimeout(() => { state.page = 0; loadStrategies(); }, 180); });
  document.querySelectorAll("[data-scope]").forEach(button => button.addEventListener("click", () => {
    state.scope = button.dataset.scope; state.page = 0; document.querySelectorAll("[data-scope]").forEach(item => item.classList.toggle("active", item === button)); loadStrategies();
  }));
  $("clearCategory").addEventListener("click", () => { state.category = ""; state.page = 0; renderCategoryTree(); loadStrategies(); });
  $("previousPage").addEventListener("click", () => { if (state.page) { state.page -= 1; loadStrategies(); } });
  $("nextPage").addEventListener("click", () => { state.page += 1; loadStrategies(); });
  $("clearWorkspace").addEventListener("click", () => { if (confirm("清空画布中的所有积木？")) mutate(() => { state.program = {scripts: []}; }); });
  $("zoomOut").addEventListener("click", () => setZoom(state.zoom - .1)); $("zoomIn").addEventListener("click", () => setZoom(state.zoom + .1)); $("zoomReset").addEventListener("click", () => setZoom(1));
  $("addVariable").addEventListener("click", addVariable); $("addList").addEventListener("click", addList); $("createCustom").addEventListener("click", createCustomBlock);
  $("toggleStrategy").addEventListener("click", toggleStrategy); $("deleteStrategy").addEventListener("click", deleteStrategy);
  document.addEventListener("keydown", event => {
    if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === "z") { event.preventDefault(); event.shiftKey ? redo() : undo(); }
    if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === "s") { event.preventDefault(); saveStrategy(); }
  });
  $("workspaceViewport").addEventListener("wheel", event => { if (event.ctrlKey) { event.preventDefault(); setZoom(state.zoom + (event.deltaY < 0 ? .08 : -.08)); } }, {passive: false});
}

async function start() {
  bindEvents();
  try {
    const [registry, catalog, categories] = await Promise.all([api("/api/block-registry"), api("/api/target-catalog"), api("/api/categories")]);
    state.registry = registry; state.groups = registry.groups || []; state.targets = catalog.units || []; state.categories = categories.categories || [];
    state.groups.forEach(group => group.blocks.forEach(block => state.definitions.set(block.id, {...block, color: group.color, group_zh: group.name_zh})));
    const options = $("categoryOptions"); state.categories.filter(item => !/[A-Za-z]/.test(item.path)).forEach(item => options.append(new Option(item.path, item.path)));
    renderCategoryTree(); newStrategy(false); renderPalette(); await loadStrategies(); setConnection("已连接", "ok");
  } catch (error) {
    setConnection("连接失败", "error"); $("diagnosticList").textContent = `无法读取策略库：${error.message}`; $("diagnosticList").className = "diagnostic-error";
  }
}

start();
