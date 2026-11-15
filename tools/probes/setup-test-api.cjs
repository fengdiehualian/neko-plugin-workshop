// 给隔离实例写一套「指向本地 mock 网关」的 API 配置,让它进入主页好跑 UI 自检。
// 用法:node tools/probes/setup-test-api.cjs [实例目录] [网关端口]
const fs = require("fs")
const path = require("path")

const P = require("./paths.cjs")
const dir = process.argv[2] || P.testDir()
const port = Number(process.argv[3] || 4321)
fs.mkdirSync(path.join(dir, "runtime"), { recursive: true })
const file = path.join(dir, "runtime", "apis.json")
const cfg = {
  enabledId: "mock",
  items: [
    {
      id: "mock",
      name: "本地Mock",
      protocol: "openai",
      baseURL: "http://127.0.0.1:" + port + "/v1",
      apiKey: "mock-key",
      modelID: "mock-model",
      models: ["mock-model", "mock-model-2"],
    },
  ],
}
fs.writeFileSync(file, JSON.stringify(cfg, null, 2), "utf8")
console.log("已写入 " + file + " → " + cfg.enabledId + " @ " + cfg.items[0].baseUrl)
