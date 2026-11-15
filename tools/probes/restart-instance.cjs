// 重启某个工坊实例(按端口杀干净再起,避免旧进程占端口让「改了没生效」)。
// 用法:node tools/probes/restart-instance.cjs <实例目录> <端口> [引擎端口] [KEY=VAL ...]
const fs = require("fs")
const path = require("path")
const { execSync, spawn } = require("child_process")

const dir = path.resolve(process.argv[2] || "C:\\Users\\Administrator\\dev\\_wb_test")
const port = Number(process.argv[3] || 5199)
const engPort = Number(process.argv[4] || 0)
const env = {}
for (const kv of process.argv.slice(5)) {
  const i = kv.indexOf("=")
  if (i > 0) env[kv.slice(0, i)] = kv.slice(i + 1)
}
const BUN = process.env.BUN_BIN || "C:\\Users\\Administrator\\AppData\\Local\\Programs\\NEKOWorkshop\\_bin\\bun.exe"

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
// stdio 必须 ignore:否则调用方(助手侧的 shell 任务)会一直等管道关闭而卡住不返回
const c = spawn("cmd.exe", ["/c", "start", "/min", "\"wbinst\"", BUN, path.join(dir, "wb-studio.mjs")], {
  cwd: dir, env: Object.assign({}, process.env, { WB_STUDIO_NOWINDOW: "1" }, env), windowsHide: true, stdio: "ignore",
})
c.unref()
console.log("已重启 " + dir + " (端口 " + port + ")")
