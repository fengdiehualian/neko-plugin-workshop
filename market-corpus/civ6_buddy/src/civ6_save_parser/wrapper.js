// civ6_save_parser JSON wrapper for civ6_buddy plugin.
// 用法: node wrapper.js <savefile> [--simple]
// 输出: 标准 JSON（civ6-save-parser 的 CLI 默认用 util.inspect，非 JSON，无法被 Python 直接解析，
//       故提供本 wrapper 用 JSON.stringify 输出干净 JSON。）
'use strict';

const fs = require('fs');
const path = require('path');
const parser = require('./index.js');

const argv = process.argv.slice(2);
if (argv.length < 1) {
  console.error('usage: node wrapper.js <savefile> [--simple]');
  process.exit(2);
}

const saveFile = argv[0];
const simple = argv.includes('--simple');

if (!fs.existsSync(saveFile)) {
  console.error(JSON.stringify({ error: 'SAVE_NOT_FOUND', path: saveFile }));
  process.exit(1);
}

let buffer;
try {
  buffer = fs.readFileSync(saveFile);
} catch (e) {
  console.error(JSON.stringify({ error: 'SAVE_READ_FAILED', detail: String(e) }));
  process.exit(1);
}

try {
  const result = parser.parse(buffer, { simple });
  // 只输出 parsed 结构；simple 模式下所有 data 都是标量/数组/对象，可安全 JSON 序列化。
  process.stdout.write(JSON.stringify(result.parsed));
} catch (e) {
  console.error(JSON.stringify({ error: 'PARSE_FAILED', detail: String(e && e.stack || e) }));
  process.exit(1);
}
