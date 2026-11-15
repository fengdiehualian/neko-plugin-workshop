// 重启某个工坊实例(按端口杀干净再起,避免旧进程占端口让「改了没生效」)。
// 用法:node tools/probes/restart-instance.cjs <实例目录> <端口> [引擎端口] [KEY=VAL ...]
const fs = require("fs")
const path = require("path")
const { execSync, spawn } = require("child_process")

const P = require("./paths.cjs")
const dir = path.resolve(process.argv[2] || P.testDir())
const port = Number(process.argv[3] || 5199)
const engPort = Number(process.argv[4] || 0)
const env = {}
for (const kv of process.argv.slice(5)) {
  const i = kv.indexOf("=")
  if (i > 0) env[kv.slice(0, i)] = kv.slice(i + 1)
}
const BUN = P.bunExe()

function killPort(p) {
  if (!p) return
  let out = ""
  try { out = execSync("netstat -ano | findstr :" + p + " | findstr LISTENING", { encoding: "latin1" }) } catch { return }
  for (const line of out.split(/\r?\n/)) {
    const m = line.trim().match(/(\d+)\s*$/)
    if (m && Number(m[1]) > 4) {
      try { execSync("taskkill /PID " + m[1] + " /T /F", { stdio: "ignore" }) } catch {}
    }
  }
}
killPort(port)
if (engPort) killPort(engPort)
fs.rmSync(path.join(dir, "runtime", "studio.lock"), { force: true })
// 关键:不能用 `cmd start` 拉实例。助手侧的 Bash 任务一结束就回收整棵进程树,
// `start` 分离出去的子进程照样被杀 —— 现象是脚本打印「已重启」、studio.log 都写到了「对话页」,
// 但下一次工具调用时端口已经空了(本轮在这上面连败三次)。**只有 schtasks 拉起的进程能活过工具调用。**
// 另两个坑:start 的窗口标题必须是第一个参数(写反了会把标题当程序名,实例根本没起来);
// 环境变量名必须是 WB_STUDIO_NOWINDOW(studio 读的就是这个),写错会每次弹出 Chrome 窗口。
const { spawnSync } = require("child_process")
const taskName = "wbinst_" + port
const envPairs = Object.entries(Object.assign({ WB_STUDIO_NOWINDOW: "1" }, env))
const sets = envPairs.map(([k, v]) => "set " + k + "=" + v + "&&").join("")
// /TR 里别再套引号:这条链路上的目录/程序路径都没有空格,加引号反而被 schtasks 拆坏
// (踩过:带内层引号的 /TR 创建成功、/Run 也返回成功,但进程根本没起来,端口一直空)
const TR = "cmd /c cd /d " + dir + " && " + sets + " " + BUN + " wb-studio.mjs"
const mk = spawnSync("schtasks", ["/Create", "/TN", taskName, "/TR", TR, "/SC", "ONCE", "/ST", "00:00", "/F"], { encoding: "latin1" })
console.log("  schtasks /Create →", (mk.status === 0 ? "OK " : "FAIL ") + String(mk.stdout || mk.stderr).replace(/\r?\n/g, " ").trim().slice(0, 140))
if (mk.status !== 0) {
  console.log("  退回 cmd start(注意会被工具调用回收)")
  const c = spawn("cmd.exe", ["/c", "start", "\"wbinst\"", "/min", BUN, path.join(dir, "wb-studio.mjs")], {
    cwd: dir, env: Object.assign({}, process.env, { WB_STUDIO_NOWINDOW: "1" }, env), windowsHide: true, stdio: "ignore",
  })
  c.unref()
} else {
  const run = spawnSync("schtasks", ["/Run", "/TN", taskName], { encoding: "latin1" })
  console.log("  schtasks /Run →", (run.status === 0 ? "OK " : "FAIL ") + String(run.stdout || run.stderr).replace(/\r?\n/g, " ").trim().slice(0, 140))
  spawnSync("schtasks", ["/Delete", "/TN", taskName, "/F"], { encoding: "latin1" })
}
console.log("已重启 " + dir + " (端口 " + port + ",经 schtasks 以免被回收)")
