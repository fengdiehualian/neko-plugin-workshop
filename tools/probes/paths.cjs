// 探针与自检脚本的路径解析(issue #13:别再写死 C:\Users\Administrator\...)。
// 约定:环境变量优先,其次按 %USERPROFILE% 推导,最后才退回本机已知路径。
// 全部脚本从这里取路径,换机器只需要设几个环境变量(或什么都不设,用推导值)。
const fs = require("fs")
const os = require("os")
const path = require("path")

const HOME = os.homedir()

function firstExisting(list) {
  for (const p of list) if (p && fs.existsSync(p)) return p
  return ""
}
// 工坊安装实例(带 _zcode/_engine 的那份):打包与底座素材的来源
function installDir() {
  return (
    process.env.NEKO_INSTALL_DIR ||
    firstExisting([
      path.join(process.env.LOCALAPPDATA || path.join(HOME, "AppData", "Local"), "Programs", "NEKOWorkshop"),
      path.join(HOME, "AppData", "Local", "Programs", "NEKOWorkshop"),
    ])
  )
}
// 隔离测试实例(UI 自检与安全复验的对象,绝不是用户真实实例)
function testDir() {
  return (
    process.env.WB_TEST_DIR ||
    firstExisting([path.join(HOME, "dev", "_wb_test"), path.join(HOME, "_wb_test")]) ||
    path.join(HOME, "dev", "_wb_test")
  )
}
function feedDir() {
  return (
    process.env.WB_FEED_DIR ||
    firstExisting([path.join(HOME, "dev", "_wb_feed"), path.join(HOME, "_wb_feed")]) ||
    path.join(HOME, "dev", "_wb_feed")
  )
}
function gitExe() {
  return (
    process.env.GIT_BIN ||
    firstExisting([
      path.join(HOME, "dev", "tools", "PortableGit", "1.3.0", "cmd", "git.exe"),
      path.join(HOME, ".workbuddy", "binaries", "PortableGit", "versions", "1.3.0", "cmd", "git.exe"),
      "C:\\Program Files\\Git\\cmd\\git.exe",
    ]) ||
    "git"
  )
}
function bunExe() {
  const inst = installDir()
  return (
    process.env.BUN_BIN ||
    firstExisting([
      inst && path.join(inst, "_bin", "bun.exe"),
      path.join(HOME, ".bun", "bin", "bun.exe"),
    ]) ||
    "bun"
  )
}
function nodeExe() {
  return (
    process.env.NODE_BIN ||
    firstExisting([
      path.join(HOME, "dev", "tools", "node24", "node-v24.14.0-win-x64", "node.exe"),
      process.execPath,
    ]) ||
    "node"
  )
}
function zcodeDir() {
  const inst = installDir()
  return process.env.NEKO_ZCODE_DIR || (inst ? path.join(inst, "_zcode") : "")
}
// 脚本自己所在的仓库根(不用到处 __dirname/../..)
const REPO = path.resolve(__dirname, "..", "..")


// 临时目录(截图/解包/演练都用它,别写死 C:\Users\<某人>\AppData\Local\Temp)
function tmpDir() {
  return process.env.NEKO_TMP_DIR || path.join(os.tmpdir() || path.join(HOME, "AppData", "Local", "Temp"), "")
}
// 真实安装实例的 runtime(只有排查「装好的工坊」时才用;绝不往里面写业务数据)
function installRuntimeDir() {
  const inst = installDir()
  return inst ? path.join(inst, "runtime") : ""
}
// 本机市场语料(仓库内 market-corpus;历史探针曾指向已删除的 dev/market-plugins)
function corpusDir() {
  return (
    process.env.NEKO_CORPUS_DIR ||
    firstExisting([path.join(REPO, "market-corpus"), path.join(HOME, "dev", "market-plugins")]) ||
    path.join(REPO, "market-corpus")
  )
}

module.exports = { REPO, HOME, installDir, testDir, feedDir, gitExe, bunExe, nodeExe, zcodeDir, firstExisting, tmpDir, installRuntimeDir, corpusDir }
