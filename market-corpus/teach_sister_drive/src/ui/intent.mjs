// Equivalent to intent.py; parity is checked with tests/intent_cases.json.
const words = {
  brake: ['刹车','停下','停车','刹住','停止','brake','stop'],
  back: ['倒车','后退','往后','reverse','back'],
  left: ['向左','往左','左转','左边','left'],
  right: ['向右','往右','右转','右边','right'],
  center: ['回正','摆正','直行','方向回中','center'],
  forward: ['前进','往前','加速','油门','向前','forward','出发','起步','踩下油门','踩一下油门','踩一脚油门'],
  fasten_seatbelt: ['系安全带','系上安全带','系好安全带','扣上安全带','扣好安全带','安全带系上','安全带系好','安全带扣上','安全带扣好','系一下安全带','系下安全带','扣一下安全带'],
  start_engine: ['打火','点火','启动引擎','启动发动机','启动汽车','发动汽车','发动引擎','发动发动机','发动车子','把车启动','引擎启动','发动机启动','打一下火','启动一下发动机','启动一下引擎'],
  shift_drive: ['挂前进挡','挂前进档','挂d挡','挂d档'],
};
const lessons = ['fasten_seatbelt','start_engine','shift_drive'];
const negation = /不要|别|不能|不许|不准|禁止|不用|无需|莫|不|\b(?:not|never|don't|do not)\b/;
const defer = /等(?:我|你|到|一下|一会|会儿)|待会|稍后|过一会|如果|假如|要是|当.+?时|\b(?:if|when|later|after)\b/;
const sequence = /然后|接着|随后|之后|以后|[再后](?=往|向|加|前|倒|左|右|开|刹|停|回|打|挂|系)|\b(?:then|next)\b/;
const question = /为什么|怎么|是否|能否|要不要|有没有|我(?:刚才|之前|说过)|你(?:刚才|之前)|吗[？?。！!]*$|[？?]|\b(?:why|whether)\b/;
const numberPattern = '(?:\\d+(?:\\.\\d+)?|[零一二两三四五六七八九十百]+)';
const speedPattern = `(${numberPattern})\\s*(?:公里(?:每小时|每小時|/小时)?|千米(?:每小时|/小时)|km\\s*/?\\s*h|kph|迈)`;
const durationPattern = `(${numberPattern})\\s*(?:秒(?:钟)?|seconds?|secs?)(?!后)`;
const clauses = /[，,。!！;；]|\.(?!\d)|(?:但是|不过|而是|但|而|\bbut\b)/;
const filler = /悠怡|妹妹|猫娘|请|麻烦|帮我|给我|我想让你|自己的|你的|现在|马上|立刻|赶紧|赶快|先|一下|一点|稍微|轻轻|慢慢|慢点|缓慢|低速|轻踩|保持|目标速度|速度|公里每小时|每小时|的速度|方向盘|方向|持续|行驶|前行|踩|油门踏板|转|开|把|车|你|以|到|为|和|并|且|着|好|上|点|吧|呀|啊|喵|～|~|\b(?:please|go|drive|turn|a little|a bit|slowly|slow|for|at|and|now)\b/g;
const fail = error => ({commands: [], error, note: ''});
const succeed = (command, note = '') => ({commands: [command], error: '', note});

function number(value) {
  if (/^\d+(?:\.\d+)?$/.test(value)) return Number(value);
  const digits = Object.fromEntries([...'零一二三四五六七八九'].map((c,i) => [c,i]));
  digits['两'] = 2;
  if (value.includes('百')) throw new Error('unsupported number');
  if (value.includes('十')) {
    if (!/^[一二两三四五六七八九]?十[一二三四五六七八九]?$/.test(value)) throw new Error('unsupported number');
    const [before, after] = value.split('十');
    return (before ? (digits[before] ?? 1) : 1) * 10 + (digits[after] ?? 0);
  }
  if (value.length === 1 && value in digits) return digits[value];
  throw new Error('unsupported number');
}

export function parseCommand(text) {
  if (String(text ?? '').length > 160) return fail('指令过长，请一次说一个动作');
  const raw = String(text ?? '').normalize('NFKC').trim().toLowerCase()
    .replace(/(?<=[\u3400-\u9fff])\s+(?=[\u3400-\u9fff])/g,'')
    .replace(/(不要|不许|不准|别|不用)[，,]\s*/g,'$1');
  if (question.test(raw)) return fail('疑问或转述不作为驾驶指令');
  if (/环顾|看看周围|看一下周围|观察四周/.test(raw)) return fail('环顾由游戏教程动画控制，不能单独执行');
  if (/停止(?:说话|讲话|播放|录音)|[-−负]\s*\d|(?<!\d)\.\d|\d[eE][+-]?\d/.test(raw)) return fail('不是明确的驾驶指令或数值格式不支持');
  const cuts = [defer.exec(raw), sequence.exec(raw)].filter(Boolean).map(m => m.index);
  if (cuts.length) {
    const immediate = raw.slice(0, Math.min(...cuts)).replace(/^[ ，,。;；]+|[ ，,。;；]+$/g, '');
    const first = immediate ? parseCommand(immediate) : fail('');
    if (first.commands.length === 1 && first.commands[0].action === 'brake')
      return succeed(first.commands[0], '只执行当前刹车；后续动作请到时单独下令');
    return fail('包含条件或先后步骤，请一次说一个立即动作');
  }
  const positive = [], found = new Set();
  let negated = false;
  for (const clause of raw.split(clauses)) {
    let residual = clause;
    const hits = [];
    for (const action of lessons) for (const term of words[action]) {
      if (residual.includes(term)) { hits.push(action); residual = residual.replaceAll(term, ''); }
    }
    hits.push(...Object.entries(words).filter(([action,terms]) => !lessons.includes(action) && terms.some(term =>
      /^[\x00-\x7F]+$/.test(term) ? new RegExp(`\\b${term}\\b`).test(residual) : residual.includes(term)
    )).map(([action]) => action));
    if (negation.test(clause)) negated ||= Boolean(hits.length || new RegExp(speedPattern).test(clause));
    else { positive.push(clause); hits.forEach(action => found.add(action)); }
  }
  const positiveText = positive.join('，');
  if (!found.size && !new RegExp(speedPattern).test(positiveText))
    return fail(negated ? '否定指令不会执行' : '没有明确的驾驶动作');
  if (/松(?:开)?油门|松脚|减速|减少速度|\bslow down\b/.test(positiveText))
    return fail('请说目标速度或刹车，不能猜测减速幅度');
  if (new RegExp(`${numberPattern}\\s*度`).test(positiveText))
    return fail('游戏方向量不是角度，请说向左/右转一点');
  let remainder = positiveText.replace(new RegExp(durationPattern, 'g'), '').replace(new RegExp(speedPattern, 'g'), '');
  for (const term of Object.values(words).flat().sort((a,b) => b.length-a.length))
    remainder = /^[\x00-\x7F]+$/.test(term) ? remainder.replace(new RegExp(`\\b${term}\\b`, 'g'), '') : remainder.replaceAll(term, '');
  if (remainder.replace(filler, '').replace(/[\s，,。.!！;；]+/g, ''))
    return fail('只接受明确口令，请直接说动作、速度或持续时间');
  const lesson = lessons.filter(action => found.has(action));
  if (lesson.length) return found.size === 1 ? succeed({action: lesson[0]}) : fail('请一次说一个教程动作');
  const directions = ['left','right','center'].filter(action => found.has(action));
  const pedals = ['forward','back','brake'].filter(action => found.has(action));
  if (directions.length > 1 || pedals.length > 1) return fail('同一句有相互冲突的驾驶动作');
  const speeds = [...positiveText.matchAll(new RegExp(speedPattern, 'g'))];
  const durations = [...positiveText.matchAll(new RegExp(durationPattern, 'g'))];
  if (speeds.length > 1 || durations.length > 1) return fail('请只指定一个速度和持续时间');
  let speed = null, duration = null;
  try {
    speed = speeds.length ? number(speeds[0][1]) : null;
    duration = durations.length ? Math.round(number(durations[0][1]) * 1000) : null;
  } catch { return fail('无法确定数值'); }
  if (directions.length && !pedals.length && positiveText.includes('开')) pedals.push('forward');
  if (speed !== null && !pedals.length) pedals.push(speed === 0 ? 'brake' : 'forward');
  const action = pedals[0] ?? directions[0];
  if (action === 'brake' && (directions.length || (speed !== null && speed !== 0)))
    return fail('刹车与转向或非零速度请分开下令');
  if (action === 'back' && speed !== null) speed = -speed;
  if (speed === null && action === 'forward' && /慢|缓|低速|轻踩|\bslow(?:ly)?\b/.test(positiveText)) speed = 5;
  if (speed !== null && !(speed >= -20 && speed <= 60)) return fail('目标速度必须在倒车20至前进60公里每小时之间');
  if (duration !== null && !(duration >= 100 && duration <= 10000)) return fail('持续时间必须在0.1至10秒之间');
  if (action === 'forward' && speed !== null && speed <= 0) return fail('前进速度必须大于零');
  if (action === 'back' && speed !== null && speed >= 0) return fail('倒车速度必须小于零');
  const command = {action};
  if ((action === 'forward' || action === 'back') && speed !== null) command.target_speed = speed;
  if (action === 'brake') command.target_speed = 0;
  if (directions.length) {
    const amount = /一点|一點|稍微|轻轻|\ba (?:little|bit)\b/.test(positiveText) ? .12 : .25;
    command.steering = {left: -amount, right: amount, center: 0}[directions[0]];
  }
  if (duration !== null) command.duration_ms = duration;
  else if (action === 'left' || action === 'right') command.duration_ms = 800;
  return succeed(command);
}

// Model interpretation must retain the same non-action boundary as the
// deterministic parser. Multi-step immediate manoeuvres still use the model.
export function commandPolicy(text, parsed = parseCommand(text)) {
  if (parsed.commands.length) return {conversationOnly:false,reason:''};
  const raw=String(text ?? '').normalize('NFKC').trim().toLowerCase()
    .replace(/(?<=[\u3400-\u9fff])\s+(?=[\u3400-\u9fff])/g,'')
    .replace(/(不要|不许|不准|别|不用)[，,]\s*/g,'$1');
  const hasPositiveAction=raw.split(clauses).some(clause=>!negation.test(clause)
    && Object.values(words).flat().some(term=>/^[\x00-\x7F]+$/.test(term)
      ? new RegExp(`\\b${term}\\b`).test(clause) : clause.includes(term)));
  const reason=question.test(raw) || /(?:了没(?:有)?|好了没(?:有)?|了么|是不是)[。!！]*$/.test(raw) ? 'question'
    : /[“”「」『』‘’"]/.test(raw) ? 'quoted'
    : defer.test(raw) ? 'deferred'
    : negation.test(raw) && !hasPositiveAction ? 'negated' : '';
  return {conversationOnly:Boolean(reason),reason};
}
