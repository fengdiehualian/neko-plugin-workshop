const t = await Bun.file(process.env.TEMP + "/anbig.txt").text()
for (const line of t.split("\n")) {
  if (line.includes("content_block_start")) console.log(line.trim().slice(0, 240))
  if (line.includes("message_delta")) console.log(line.trim().slice(0, 240))
}
