const steps = {FastenSeatbelt:'fasten_seatbelt',StartEngine:'start_engine',Go:'shift_drive'};
const labels = {FastenSeatbelt:'系安全带',StartEngine:'启动引擎',Go:'踩油门出发'};

export function tutorialState(state) {
  const text = String(state?.body?.tutorial || '');
  const raw = state?.body?.tutorial_state;
  const native = raw?.version === 1;
  const microphoneStage = native ? raw.microphone_stage || '' : '';
  const legacyActive = text.startsWith('教学中，') || state?.car === 'TutorialSceneCarController';
  const active = native ? raw.active === true || legacyActive && raw.active !== false : legacyActive;
  const completed = active && native && raw.completed === true;
  const waiting = native ? raw.waiting_step : /，等待 ([A-Za-z]+)/.exec(text)?.[1] || '';
  const current = native ? raw.current_action : /，当前动作 ([A-Za-z]+)/.exec(text)?.[1] || '';
  const input = native ? raw.input_available : state?.body?.native_input_available;
  const seatbelt = native ? raw.seatbelt_fastened === true : text.includes('安全带已系');
  const engine = native ? raw.engine_started === true : text.includes('引擎已启动');
  const enumKnown = value => ['FastenSeatbelt','StartEngine','Go','None','NotSet'].includes(value);
  const known = !active || (native ? raw.known === true && typeof raw.active === 'boolean'
    && typeof raw.seatbelt_fastened === 'boolean' && typeof raw.engine_started === 'boolean'
    && typeof raw.pending_command_speech === 'boolean' : !raw)
    && typeof input === 'boolean' && enumKnown(waiting) && enumKnown(current);
  // Only the native callback owner can confirm recoverability. Set flags alone
  // mean an animation was triggered, not that speech is still blocking it.
  const commandPending = native && raw.pending_command_speech === true && (waiting === 'None' || waiting === current);
  const recovering = active && known && native && input === true && commandPending
    && raw.recoverable_step === current && ['FastenSeatbelt','StartEngine'].includes(current);
  const allowedAction = active && !completed && known && input === true && !microphoneStage
    ? recovering ? steps[current] : current !== waiting ? steps[waiting] || null : null : null;
  const count = key => native && Number.isInteger(raw.speech?.[key]) && raw.speech[key] > 0 ? raw.speech[key] : 0;
  const phase = !active ? 'inactive' : completed ? 'completed' : !known ? 'unknown' : count('retryable') ? 'speech_failed'
    : count('playing') ? 'speaking' : count('queued') || commandPending ? 'awaiting_speech'
    : microphoneStage === 'waiting_input' ? 'microphone_input'
    : microphoneStage === 'awaiting_speech' ? 'awaiting_speech'
    : microphoneStage ? 'microphone_intro'
    : input !== true ? 'scripted' : allowedAction ? 'waiting' : 'transition';
  return {active,known,completed,source:native?'native':'legacy',waiting,current,phase,
    microphoneStage,microphoneInput:active && known && input === true && ['waiting_input','awaiting_speech'].includes(microphoneStage),
    scripted:active && !allowedAction,recovering,allowedAction,
    label:labels[waiting] || labels[current] || '剧情',seatbelt,engine};
}

export function tutorialHint(state, retryable = false) {
  const tutorial = tutorialState(state);
  if (!tutorial.active) return '';
  if (tutorial.completed) return '教程已通过！请在游戏里点击「下一关」继续。';
  if (!tutorial.known) return '教程步骤尚未确认，暂不执行动作。请等待状态更新；若持续不变，请重新连接游戏。';
  if (retryable || tutorial.phase === 'speech_failed') return '教程播报未完成，请点击「继续剧情语音」恢复；恢复播放完成后才继续。';
  if (tutorial.phase === 'speaking') return '正在播放教程语音；可以闲聊，回复会排在剧情语音后面，动作不会被打断。';
  if (tutorial.phase === 'microphone_input') return '教程正在等你试说一句话：开启麦克风说“你好”，或在这里输入文字。猫娘确认语音播完后进入安全带教学，无需再按游戏空格键。';
  if (tutorial.phase === 'microphone_intro') return '正在进行开场教学，播报结束后会提示你试说一句话。';
  if (tutorial.recovering) return `${tutorial.label}动作已触发，等待确认语音。若一直未继续，可重播未完成台词，或重复“${tutorial.label}”恢复播报。`;
  if (tutorial.phase === 'awaiting_speech') return '教程正在准备语音；可以闲聊，当前动作和剧情会完整执行。';
  if (tutorial.phase === 'transition') return '教程正在切换步骤，等待游戏给出下一步口令。';
  if (tutorial.scripted) return '教程剧情或动画正在进行，可以闲聊；新的动作口令请等当前步骤结束后再说。';
  return `教程当前步骤：${tutorial.label}。请说“${tutorial.label}”。收到有效口令后会完整执行动作和播报，期间可以闲聊，不打断或重复操作。`;
}

// Once a keyword starts a lesson, new input must not interrupt its animation,
// callback-bound reply or following native script. Failed speech stays retryable.
export function tutorialPlaybackBusy(state) {
  const tutorial = tutorialState(state);
  if (!tutorial.active || tutorial.completed) return false;
  // Speech counters include ordinary chat. A ready native step remains
  // available during chat; the adapter separately protects owned story lines.
  if (['speaking','awaiting_speech'].includes(tutorial.phase)) return tutorial.scripted || tutorial.recovering;
  return ['scripted','microphone_intro','transition'].includes(tutorial.phase);
}

export function validateTutorialActions(actions, state) {
  const tutorial = tutorialState(state);
  if (!tutorial.active || !actions.length) return;
  const action = actions.length === 1 ? actions[0].action : '';
  if (!tutorial.scripted && action === tutorial.allowedAction) return;
  const message = !tutorial.known ? '教程步骤尚未确认，本回合不能执行动作，actions 必须为空。' : tutorial.scripted
    ? '教程正在等待剧情语音或动画结束，本回合只能说话，actions 必须为空。'
    : `教程当前等待 ${tutorial.waiting}，本回合只能用 ${tutorial.allowedAction} 完成这一步，不能使用 drive 或 brake。`;
  throw Object.assign(new Error(message), {code:'tutorial_action_mismatch'});
}
