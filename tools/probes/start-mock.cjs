// 用 schtasks 拉起 mock 网关(和实例同理:Bash 任务结束会回收 start/spawn 出来的子进程)
// 用法:node tools/probes/start-mock.cjs [端口]
const { spawnSync } = require("child_process")
const path = require("path")
const PORT = Number(process.argv[2] || 4321)
const NODE = process.execPath
const SCRIPT = path.join(__dirname, "mock-gateway.cjs")
const name = "wbmock_" + PORT
function netstatPort() {
  const r = spawnSync("netstat", ["-ano"], { encoding: "latin1" })
  return String(r.stdout || "").split(/\r?\n/).some((l) => l.indexOf(":" + PORT) >= 0 && l.indexOf("LISTENING") >= 0)
}
if (netstatPort()) { console.log("网关已在 " + PORT + " 上运行"); process.exit(0) }
const TR = "cmd /c " + NODE + " " + SCRIPT + " " + PORT + " --capture"
let mk = spawnSync("schtasks", ["/Create", "/TN", name, "/TR", TR, "/SC", "ONCE", "/ST", "00:00", "/F"], { encoding: "latin1" })
if (mk.status !== 0) { console.log("创建失败:" + String(mk.stderr || mk.stdout).trim().slice(0, 160)); process.exit(1) }
const run = spawnSync("schtasks", ["/Run", "/TN", name], { encoding: "latin1" })
spawnSync("schtasks", ["/Delete", "/TN", name, "/F"], { encoding: "latin1" })
if (run.status !== 0) { console.log("/Run 失败:" + String(run.stderr || run.stdout).trim().slice(0, 160)); process.exit(1) }
for (let i = 0; i < 20; i++) {
  const t = Date.now(); while (Date.now() - t < 800) {}
  if (netstatPort()) { console.log("mock 网关已起在 " + PORT); process.exit(0) }
}
console.log("20s 内端口仍未监听")
process.exit(1)
