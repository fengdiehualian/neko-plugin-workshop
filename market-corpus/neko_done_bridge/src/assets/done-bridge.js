(function () {
  "use strict";

  // D-one 桥接脚本：任务数据变更 → N.E.K.O 猫娘
  // 用法（原型阶段二选一）：
  //   A. D-one 窗口内打开 DevTools Console，整段粘贴执行（一次性演示）
  //   B. 加入 D-one 源码 index.html 尾部 <script src="done-bridge.js"></script>（持久）
  // 数据流：localStorage["priority-desk-tasks-v1"] 变更 → POST 全量快照到本插件

  var ENDPOINT = "http://127.0.0.1:48917/hook/tasks";
  var TASKS_KEY = "priority-desk-tasks-v1";

  function readTasks() {
    try {
      var raw = localStorage.getItem(TASKS_KEY);
      return raw ? JSON.parse(raw) : [];
    } catch (e) {
      return [];
    }
  }

  function pushSnapshot(reason) {
    var tasks = readTasks();
    if (!Array.isArray(tasks)) return;
    var xhr = new XMLHttpRequest();
    xhr.open("POST", ENDPOINT, true);
    xhr.setRequestHeader("Content-Type", "application/json");
    xhr.onload = function () {
      console.log("[done-bridge] synced (" + reason + "): " + tasks.length + " tasks");
    };
    xhr.onerror = function () {
      console.warn("[done-bridge] sync failed — N.E.K.O bridge not reachable at " + ENDPOINT);
    };
    xhr.send(JSON.stringify({ source: "d-one", tasks: tasks }));
  }

  // 拦截 localStorage 写入：任务数据一旦变化即推送
  var origSetItem = localStorage.setItem.bind(localStorage);
  localStorage.setItem = function (key, value) {
    origSetItem(key, value);
    if (key === TASKS_KEY) {
      pushSnapshot("set");
    }
  };
  var origRemoveItem = localStorage.removeItem.bind(localStorage);
  localStorage.removeItem = function (key) {
    origRemoveItem(key);
    if (key === TASKS_KEY) {
      pushSnapshot("remove");
    }
  };

  // 注入时先做一次全量同步
  pushSnapshot("inject");
  console.log("[done-bridge] injected → " + ENDPOINT);
})();
