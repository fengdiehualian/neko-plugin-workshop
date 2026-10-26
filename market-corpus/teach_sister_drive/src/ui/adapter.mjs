import {parseCommand, commandPolicy} from './intent.mjs';
import {PlaybackTracker} from './playback.mjs';
import {executeCommand} from './command-client.mjs';
import {PossessionBrain, bodyContract} from './possession.mjs';
import {findPlayerWindow, createPlayerHostWindow, isPlayerReady, isMainPlayerWindow, pendingPlayerAudio, interruptPlayerAudio} from './player-window.mjs?v=0.1.0-native1';
import {tutorialState, tutorialHint, tutorialPlaybackBusy, validateTutorialActions} from './tutorial.mjs';
import {QUICK_LINES, quickDecision, tutorialKeywordDecision, interruptSupersededReply} from './quick-command.mjs';
import {InteractionTimings} from './interaction-timing.mjs';
import {keepCompanionVisible, keepPanelResponsive} from './companion.mjs?v=0.1.0-drag1';

const $ = id => document.getElementById(id);
const BRIDGE = 'http://127.0.0.1:8765';
const GAME = 'teach-sister-drive';
let game = null, tracker = null, session = '', character = '', state = null;
let running = false, connecting = false, generation = 0, cursor = 0, retryable = [];
let disconnecting = false, disconnectPromise = null;
let pollReadError = '';
let heartbeatError = '';
let commandReplyGeneration = 0;
let speechQueueGeneration = 0;
let activeSpeech = null;
let speechPreloadController = null;
let heartbeatTimer, pollTimer, statusTimer, speechQueue = Promise.resolve();
let playerWindow = null, playerEndHandler = null;
let voiceWanted = false, voiceState = null;
let resumingSpeech = false;
let brain = null, inputSequence = 0, decisionEpoch = 0, decisionController = null, memoryPromise = Promise.resolve(), memoryEpoch = 0;
let protectedTutorialCommand = null;
let releaseCompanion = null, releasePanel = null;
let deferredTutorialChat = null;
const seenInputs = new Map(), seenLines = new Set(), speechControllers = new Set();
const recoveredSpeechIds = new Set();
const timings = new InteractionTimings();
let latestTimingId = '';
let speechFailureHint = '';
let failedSpeech = null;
const speechFailureHints = Object.freeze({
  first_audio_timeout:'语音准备超过15秒仍未开口。当前步骤已保留，请重播这句语音。',
  playback_stalled:'语音播放已停滞。当前步骤已保留，请重播未完成的语音。',
  completion_confirmation_timeout:'声音已结束，但未收到合成完成确认。当前步骤已保留，请重试。',
  feedback_failed:'播放结果未能传回游戏。请检查游戏连接后重试，当前步骤尚未确认完成。',
  manual_retry:'正在重新准备这句语音，已执行的动作不会重做。',
});

function startTiming(source) {
  latestTimingId = timings.begin(source);
  renderTiming(latestTimingId);
  return latestTimingId;
}
function renderTiming(id) {
  if (id !== latestTimingId || !$('timing-state')) return;
  const entry = timings.get(id);
  if (!entry) return;
  const at = stage => entry.events.find(event=>event.stage===stage)?.ms;
  const duration = (from,to) => at(to) === undefined || at(from) === undefined ? null : Math.max(0,at(to)-at(from));
  const labels = [['received','action_confirmed','识别后动作确认'],['state_ready','memory_ready','记忆准备'],['memory_ready','decision_ready','模型思考'],
    ['queue_entered','speech_requested','播报排队与准备'],['speech_requested','started','等待声音']];
  const parts = labels.map(([from,to,label])=>{const ms=duration(from,to);return ms===null?'':`${label} ${ms} 毫秒`;}).filter(Boolean);
  if (at('started') !== undefined) parts.push(`本次开口 ${at('started')} 毫秒`);
  $('timing-state').textContent = parts.join(' · ') || '正在记录本次响应阶段…';
}
function speechProgress(value) {
  const timingId = value.scope?.timingId;
  if (['ended','failed','interrupted'].includes(value.phase)) timings.finish(timingId,value.phase,value.reason);
  else timings.mark(timingId,value.phase);
  renderTiming(timingId);
  const job = activeSpeech;
  if (!job || job.id !== value.id || !current(value.scope?.token,value.scope?.scene)) return;
  job.failureReason = value.reason;
  job.terminal = ['feedback_pending','ended','failed','interrupted'].includes(value.phase);
  job.canRetry = ['preparation_slow','playback_slow'].includes(value.phase);
  $('retry-current-speech').hidden = !job.canRetry;
  $('retry-current-speech').disabled = resumingSpeech;
  const hints = {preparation_slow:'语音准备已超过5秒，可以继续等待，或点击「重试这句语音」。',
    playback_slow:'声音暂时没有继续播放，可以继续等待，或重试这句语音。',
    awaiting_confirmation:'声音已播完，正在等待本次合成结果确认…'};
  if (hints[value.phase]) $('speech-state').textContent = hints[value.phase];
}

function interruptOwnedAudio(job) {
  const sample = job.sample;
  if (sample?.requestId === job.id && sample.speechId
    && playerWindow?.NekoSpeechPlaybackState?.speechId === sample.speechId)
    interruptPlayerAudio(playerWindow,sample.speechId);
}

function error(message) { $('error').textContent = message || ''; }
function showInterpretation(actions, policy) {
  const target=$('intent-state');
  if (!target) return;
  const labels={fasten_seatbelt:'系安全带',start_engine:'启动引擎',shift_drive:'踩油门出发',brake:'刹车',
    forward:'前进',back:'倒车',left:'向左转',right:'向右转',center:'方向回正',drive:'调整驾驶'};
  const describe=action=>{
    if(action.action !== 'drive') return labels[action.action] || '调整驾驶';
    const parts=[];
    if(Number.isFinite(action.target_speed))parts.push(`目标时速 ${action.target_speed} 公里`);
    if(Number.isFinite(action.steering))parts.push(action.steering===0?'方向回正':action.steering<0?'向左转':'向右转');
    return parts.join('、') || '调整驾驶';
  };
  target.textContent=actions.length ? `识别到：${actions.map(describe).join(' → ')}`
    : policy?.conversationOnly ? '这句按交流处理，不改变驾驶动作。' : '正在结合上下文理解这句话…';
}
function showVoiceState(value) {
  voiceState = value;
  const active = value.active === true || value.recording === true;
  $('microphone').textContent = active ? '关闭麦克风' : '开启麦克风';
  if (value.starting) $('voice-state').textContent = '正在连接语音服务，暂不能下口令…';
  else if (active && value.ready === true) $('voice-state').textContent = '语音已就绪，可以下驾驶口令。';
  else if (voiceWanted) $('voice-state').textContent = '语音服务尚未就绪，口令暂不可用。请点击麦克风重试，或先输入文本口令。';
  else $('voice-state').textContent = '麦克风未开启。';
}
function controls(active) {
  for (const id of ['disconnect','microphone','command','send','emergency']) $(id).disabled = !active;
  $('connect').disabled = active || connecting || disconnecting;
  $('character').disabled = active || connecting || disconnecting;
}
function describeError(problem) {
  const key = String(problem?.message || problem?.error?.message || problem?.code || problem || '连接失败');
  if (problem?.code === 'tutorial_action_mismatch' || /^tutorial is waiting for /.test(key))
    return tutorialHint(state,retryable.length>0) || '当前教程步骤暂未接受这个动作，请等待状态更新。';
  if (key === 'Voice control request timed out')
    return '猫娘语音窗口没有回应，请刷新插件面板后重新接管，并保持配套语音窗口开启。';
  if (problem?.code === 'invalid_contract' || /dialogue response control\.|required field/.test(key))
    return '猫娘的动作回复未通过检查，本回合没有执行新的驾驶动作。';
  const known = {
    stale_scene:'关卡已经切换，请重新下令。', session_not_owner:'控制权已经释放，请重新接管。',
    tutorial_keeps_the_car:'当前仍在教程中，请按教程步骤操作。', no_player_car:'没有找到可驾驶的车辆。',
    timeout:'等待超时，请确认 N.E.K.O 主窗口和游戏仍在运行。',
    provider_unavailable:'猫娘的对话服务暂时不可用，请重试；立即刹车仍可使用。',
    native_scripted_sequence_in_progress:'当前正在剧情演出，请等结束后再驾驶。',
    tutorial_already_completed:'教程已通过，请在游戏里点击「下一关」。',
    'native tutorial input state unavailable':'暂未读到教程的输入状态，请等待状态更新后再试。',
    plan_exceeds_vehicle_speed_limits:'动作超过这辆车的速度范围，未开始执行。',
    superseded_turn:'已有新的输入，这个旧决定已取消。',
    unknown_emotion:'猫娘的表情回复未确认，身体计划未执行，请再说一次。',
    'tutorial owns driving; use its pending lesson step':'当前仍是教程，请按面板提示完成当前步骤；剧情结束前不能自由调整车速或方向。',
  };
  return known[key] || key;
}
async function bridge(path, body, {signal, keepalive = false, timeoutMs = 3500} = {}) {
  const controller = new AbortController();
  const cancel = () => controller.abort();
  signal?.addEventListener('abort', cancel, {once:true});
  const timeout = setTimeout(cancel, timeoutMs);
  if (signal?.aborted) cancel();
  try {
    const response = await fetch(BRIDGE + path, {method:body ? 'POST':'GET',
      ...(body ? {headers:{'Content-Type':'application/json'}, body:JSON.stringify(body)} : {}),
      signal:controller.signal, keepalive});
    const result = await response.json();
    const pendingCommand = /^\/command(?:s\/|$)/.test(path) && ['queued','executing'].includes(result?.status);
    if (!response.ok || (result?.ok !== true && !pendingCommand))
      throw Object.assign(new Error(result?.error || `游戏桥返回 ${response.status}`), {receipt:result});
    return result;
  } finally { clearTimeout(timeout); signal?.removeEventListener('abort', cancel); }
}
function cancelSpeech({sceneChanged = false} = {}) {
  speechQueueGeneration++;
  for (const controller of speechControllers) controller.abort();
  speechControllers.clear();
  tracker?.close({notify:!sceneChanged});
  speechQueue = Promise.resolve();
  $('retry-current-speech').hidden = true;
  speechFailureHint = '';
  failedSpeech = null;
}
function applyStatus(next) {
  if (state?.scene_id && next.scene_id !== state.scene_id) {
    retireDeferredTutorialChat();
    generation++;
    cancelSpeech({sceneChanged:true});
    cursor = 0;
    seenLines.clear();
    seenInputs.clear();
    recoveredSpeechIds.clear();
    $('receipt').textContent = '关卡已切换，旧指令已失效';
  }
  state = next;
  const sequence = next.body?.sequence;
  $('body-state').textContent = sequence?.phase === 'running' ? `正在执行第 ${sequence.step}/${sequence.count} 段动作` : sequence?.phase === 'failed' ? `动作中止：${describeError(sequence.error)}` : '身体由猫娘接管，等待互动';
  $('scene').textContent = next.scene || '—';
  $('car').textContent = next.car || '未找到车辆';
  const speed = next.telemetry?.SpeedKmh;
  $('speed').textContent = Number.isFinite(speed) ? `${speed.toFixed(1)} km/h` : '暂不可读';
  renderTutorial();
}
function renderTutorial() {
  const tutorial = tutorialState(state);
  $('tutorial-state').hidden = !tutorial.active;
  $('tutorial-state').textContent = tutorialHint(state, retryable.length > 0);
  $('continue-tutorial').hidden = !tutorial.active || !(tutorial.scripted || tutorial.recovering) || !retryable.length;
  $('continue-tutorial').disabled = !running || resumingSpeech;
}
function current(token, scene) { return running && token === generation && scene === state?.scene_id; }

function playerReady(candidate) {
  return isPlayerReady(candidate,location.origin);
}
function acquirePlayerWindow() {
  const existing = findPlayerWindow(window, playerReady);
  if (existing) return existing;
  if (playerReady(playerWindow)) return playerWindow;
  const loading = findPlayerWindow(window,candidate=>isMainPlayerWindow(candidate,location.origin));
  if (loading) return loading;
  return window.open('/', 'neko-drive-player');
}
async function attachPlayer(candidate) {
  const connected = () => playerReady(candidate) && candidate.appState.socket?.readyState === 1;
  for (let attempt = 0; attempt < 100 && !connected(); attempt++)
    await new Promise(resolve => setTimeout(resolve, 200));
  if (!connected()) throw new Error('请允许打开猫娘语音窗口，并等待其连接成功后重试。');
  playerWindow = candidate;
  playerEndHandler = event => {
    const turnId = String(event.detail?.turnId || '');
    const playback = playerWindow.appState;
    const hostSpeechId = playerWindow.NekoSpeechPlaybackState?.speechId;
    const closed = turnId && playback.assistantAudioStreamClosedTurnId === turnId
      && playback.assistantAudioStreamClosedEpoch === playback.incomingAudioEpoch
      && (!hostSpeechId || pendingPlayerAudio(playerWindow,hostSpeechId) === 0);
    tracker?.playerEnded(turnId, Boolean(closed));
  };
  playerWindow.addEventListener('neko-assistant-speech-end', playerEndHandler);
  releaseCompanion = keepCompanionVisible(playerWindow);
}
function detachPlayer() {
  releaseCompanion?.(); releaseCompanion = null;
  releasePanel?.(); releasePanel = null;
  try { playerWindow?.removeEventListener('neko-assistant-speech-end', playerEndHandler); } catch { /* Player closed. */ }
  playerEndHandler = null;
}

function enqueueSpeech(line, {id = '', requestId = crypto.randomUUID(), interrupt = false, token = generation, scene = state?.scene_id, replyGeneration = null, adapt = false, timingId = ''} = {}) {
  if (!line || !current(token, scene)) return Promise.resolve();
  if (interrupt) cancelSpeech();
  timingId ||= startTiming('scripted');
  timings.mark(timingId,'queue_entered');
  const queueGeneration = speechQueueGeneration;
  const run = async () => {
    if (!current(token, scene) || queueGeneration !== speechQueueGeneration
      || replyGeneration !== null && replyGeneration !== commandReplyGeneration) {
      timings.finish(timingId,'interrupted'); return;
    }
    const controller = new AbortController();
    const speechTracker = tracker, client = game;
    const job = {controller,tracker:speechTracker,id,replyGeneration,sample:null,token,scene,timingId};
    activeSpeech = job;
    speechFailureHint = '';
    failedSpeech = null;
    speechControllers.add(controller);
    let speechId = id;
    try {
      if (adapt && speechId && brain) {
        try {
          const decision = await brain.decide({text:line,state,kind:'native_line',signal:controller.signal});
          if (!current(token,scene) || queueGeneration !== speechQueueGeneration) return;
          await bridge('/speech/adapt', {session_id:session,scene_id:scene,speech_id:speechId,text:decision.line,emotion:decision.emotion}, {signal:controller.signal});
          line = decision.line;
          timings.mark(timingId,'speech_adapted');
        } catch (problem) {
          if (controller.signal.aborted || !current(token,scene) || queueGeneration !== speechQueueGeneration) return;
          error('剧情台词暂按原文代读，猫娘改写未确认。');
        }
      }
      if (!speechId) {
        const registered = await bridge('/speech', {session_id:session, scene_id:scene, request_id:requestId, text:line}, {signal:controller.signal});
        speechId = registered.speech_id;
      }
      job.id = speechId;
      timings.mark(timingId,'speech_registered');
      if (controller.signal.aborted || !speechId || !current(token, scene) || queueGeneration !== speechQueueGeneration) return;
      const finished = job.finished = speechTracker.begin(speechId,{token,scene,session,timingId});
      $('subtitle').textContent = line;
      $('speech-state').textContent = '正在准备语音…';
      timings.mark(timingId,'speech_requested'); renderTiming(timingId);
      const request = client.speech.speak({text:line, requestId:speechId, source:'teach-sister-drive',
        interruptExisting:interrupt, reuseSynthesizedAudio:true, mirrorText:true, emitTurnEnd:true}, {signal:controller.signal, timeoutMs:90000});
      request.then(response => speechTracker.generated(speechId, response), () => speechTracker.fail(speechId));
      const outcome = await finished;
      // The player's interruption can arrive before the speak HTTP response.
      // Retire that request too, or it retains the backend's serialized TTS
      // slot while the next reply is already waiting to speak.
      if (outcome !== 'ended') { controller.abort(); interruptOwnedAudio(job); }
      if (current(token, scene) && (replyGeneration === null || replyGeneration === commandReplyGeneration)) {
        const message = speechFailureHints[job.failureReason]
          || {ended:'播放完成', interrupted:'语音被打断，未推进教程', failed:'播放未确认，请检查主窗口声音；教程保留在当前步骤'}[outcome];
        if (outcome === 'failed') {
          speechFailureHint = message;
          if (!job.feedbackFailed) failedSpeech = {id:speechId,token,scene};
        }
        $('speech-state').textContent = message;
      }
    } catch (problem) {
      if (speechId) speechTracker?.fail(speechId);
      timings.finish(timingId,controller.signal.aborted?'interrupted':'failed','speech_request_failed');
      if (!controller.signal.aborted && current(token, scene)) error(describeError(problem));
    } finally {
      speechControllers.delete(controller);
      if (activeSpeech === job) { activeSpeech = null; $('retry-current-speech').hidden = true; }
      const entry = timings.get(timingId);
      if (entry && !entry.outcome) timings.finish(timingId,'interrupted');
      renderTiming(timingId);
    }
  };
  speechQueue = speechQueue.then(run, run);
  return speechQueue;
}

async function refreshMemory(query = '') {
  const epoch = ++memoryEpoch;
  if (!brain || !$('memory').checked) { brain?.setMemory({}); $('memory-state').textContent = '本次未沿用已有记忆'; return; }
  const ownedSession = session, currentBrain = brain;
  $('memory-state').textContent = '正在连接猫娘的已有记忆…';
  try {
    const context = await bridge('/neko-context', {session_id:session,query}, {timeoutMs:7000});
    if (!running || session !== ownedSession || brain !== currentBrain || epoch !== memoryEpoch || !$('memory').checked) return;
    brain.setMemory(context);
    $('memory-state').textContent = context.available ? '已沿用猫娘的已有记忆；本局对话仅在本次接管中保留' : '已有记忆暂不可用，仍使用猫娘角色设定和本局上下文';
  } catch {
    if (running && session === ownedSession && epoch === memoryEpoch && $('memory').checked) $('memory-state').textContent = '已有记忆连接未确认，仍可继续本局互动';
  }
}

function retireDeferredTutorialChat() {
  if (deferredTutorialChat) timings.finish(deferredTutorialChat.timingId,'interrupted');
  deferredTutorialChat = null;
}

async function handleInput(text, requestId, source = 'text', {conversationOnly=false,alreadySeen=false,timingId:deferredTimingId=''} = {}) {
  text = String(text || '').trim();
  if (!running || !brain || !text) return;
  if (text.length > 160) { error('请把这一句控制在160字内。'); return; }
  const id = requestId || crypto.randomUUID();
  if (!alreadySeen && seenInputs.has(id)) return;
  seenInputs.set(id,true);
  while (seenInputs.size > 128) seenInputs.delete(seenInputs.keys().next().value);
  const timingId = deferredTimingId || startTiming(source);
  const pendingLesson = protectedTutorialCommand && current(protectedTutorialCommand.token,protectedTutorialCommand.scene);
  if (source !== 'emergency' && pendingLesson) {
    // Preserve the submitted lesson until its receipt arrives. Keep only the
    // latest conversational input, never a delayed driving command.
    retireDeferredTutorialChat();
    deferredTutorialChat = {text,id,source,timingId,token:generation,scene:state?.scene_id};
    $('heard').textContent = text;
    $('receipt').textContent = '已收到，正在确认教程动作；随后接话，剧情不会被打断。';
    return;
  }
  let speechQueued = false;
  const token = generation, scene = state?.scene_id, turn = ++inputSequence, epoch = ++decisionEpoch;
  const replyGeneration = ++commandReplyGeneration;
  interruptSupersededReply(activeSpeech,playerWindow);
  decisionController?.abort();
  const controller = new AbortController(); decisionController = controller;
  const stillCurrent = () => current(token,scene) && epoch === decisionEpoch && !controller.signal.aborted;
  $('heard').textContent = text;
  error('');
  $('receipt').textContent = '猫娘正在结合路况思考…';
  let stopReceipt = null;
  let lessonLease = null;
  try {
    await bridge('/turn', {session_id:session,scene_id:scene,input_seq:turn});
    timings.mark(timingId,'turn_accepted');
    if (!stillCurrent()) return;
    const parsed = parseCommand(text);
    const policy = commandPolicy(text,parsed);
    showInterpretation(parsed.commands,policy);
    const emergency = source === 'emergency' || !tutorialState(state).active && parsed.commands.length === 1 && parsed.commands[0].action === 'brake';
    if (emergency) {
      retireDeferredTutorialChat();
      cancelSpeech();
      const stopId = `${id}-stop`;
      stopReceipt = await executeCommand({requestId:stopId,
        post:() => bridge('/command',{session_id:session,scene_id:scene,request_id:stopId,ttl_ms:1500,source:'possession_emergency',commands:[{action:'brake'}]}),
        lookup:value=>bridge(`/commands/${encodeURIComponent(value)}`),
        wait:ms=>new Promise(resolve=>setTimeout(resolve,ms)),isCurrent:stillCurrent});
      if (stopReceipt.ok !== true) throw new Error(stopReceipt.error || '急停未确认');
      $('receipt').textContent = '急停输入已执行，猫娘正在回应';
    }
    if (!emergency) {
      // The display polls once a second; decide against a fresh road/input lock.
      const latest = await bridge('/status',undefined,{signal:controller.signal});
      if (!stillCurrent()) return;
      applyStatus(latest);
      timings.mark(timingId,'state_ready');
      if (!stillCurrent()) return;
    }
    const tutorial = tutorialState(state);
    const protectedReply = tutorial.active && activeSpeech?.replyGeneration === null
      && !activeSpeech.terminal && current(activeSpeech.token,activeSpeech.scene);
    const chatDuringStory = conversationOnly || tutorialPlaybackBusy(state) || protectedReply;
    let decision = emergency ? {line:QUICK_LINES.brake,actions:[],emotion:''}
      : !chatDuringStory && tutorial.phase === 'microphone_input'
        ? {line:QUICK_LINES.microphone,actions:[],emotion:'',microphoneInput:true}
        : chatDuringStory ? null : tutorial.active ? tutorialKeywordDecision(text,state) : quickDecision(text,state);
    if (!decision && !chatDuringStory && tutorial.active && parsed.commands.length === 1
      && ['fasten_seatbelt','start_engine','shift_drive'].includes(parsed.commands[0].action))
      validateTutorialActions([{action:parsed.commands[0].action}],state);
    if (!decision) {
      if (!chatDuringStory) await memoryPromise;
      if (/记得|上次|之前|以前|remember|last time/i.test(text) && $('memory').checked) {
        if (chatDuringStory) void refreshMemory(text);
        else await refreshMemory(text);
      }
      timings.mark(timingId,'memory_ready');
      if (!stillCurrent()) return;
      decision = await brain.decide({text,state,kind:emergency?'already_braked':chatDuringStory || tutorial.active || policy.conversationOnly?'conversation_only':'user',receipt:stopReceipt,signal:controller.signal});
    }
    if (!stillCurrent()) return;
    timings.mark(timingId,'decision_ready'); renderTiming(timingId);
    if (parsed.error === '否定指令不会执行' && decision.actions.length) throw new Error('否定口令不执行驾驶动作');
    showInterpretation(decision.actions,{conversationOnly:!decision.actions.length});
    if (tutorialState(state).active && (decision.microphoneInput || decision.actions.length)) {
      lessonLease = {token,scene};
      protectedTutorialCommand = lessonLease;
    }
    const result = await executeCommand({requestId:id,
      post:()=>bridge('/plan',{session_id:session,scene_id:scene,request_id:id,input_seq:turn,actions:decision.actions,emotion:decision.emotion,
        ...(decision.microphoneInput ? {microphone_input:true} : {})}),
      lookup:value=>bridge(`/commands/${encodeURIComponent(value)}`),
      // Reconcile submitted actions even if a newer input arrives. The game
      // fences execution by input_seq; accepted tutorial actions still need
      // their original speech completion, which must not be silently lost.
      wait:ms=>new Promise(resolve=>setTimeout(resolve,ms)),isCurrent:()=>current(token,scene)});
    if (!current(token,scene)) return;
    if (result.ok!==true || result.status!=='executed' || result.request_id!==id || result.scene_id!==scene)
      throw new Error(result.error || '身体控制未确认');
    timings.mark(timingId,'action_confirmed'); renderTiming(timingId);
    if (result.tutorial_auto_step)
      decision = {...decision,line:'教程正在继续当前步骤，我会跟着完成。'};
    const completionBound = result.requires_speech_completion === true;
    // Protect this reply before checking the input epoch again: execution is
    // irreversible, but another utterance can supersede ordinary reasoning.
    if (completionBound) { speechQueued = true; void enqueueSpeech(decision.line,{requestId:id,token,scene,replyGeneration:null,timingId}); }
    if (!stillCurrent()) return;
    brain.remember(text,decision,{status:result.status,actions:result.action_count,telemetry:result.telemetry});
    $('receipt').textContent = decision.microphoneInput ? '教程输入已收到，等待确认语音播放完成。'
      : decision.actions.length ? `已接收 ${decision.actions.length} 段动作，运动结果以车辆状态为准`
      : chatDuringStory ? '闲聊已理解，回复排在当前剧情语音后面，不改变教程动作。' : '猫娘正在和你说话';
    if (!completionBound) { speechQueued = true; void enqueueSpeech(decision.line,{requestId:id,token,scene,replyGeneration,timingId}); }
  } catch (problem) {
    if (!stillCurrent()) return;
    $('receipt').textContent = stopReceipt?.ok === true ? '急停输入已执行，猫娘的回应暂未确认' : problem.uncertain ? describeError(problem) : `本回合未确认完成：${describeError(problem)}`;
    error(describeError(problem));
  } finally {
    if (lessonLease && protectedTutorialCommand === lessonLease) {
      protectedTutorialCommand = null;
      const deferred = deferredTutorialChat;
      deferredTutorialChat = null;
      if (deferred && current(deferred.token,deferred.scene))
        void handleInput(deferred.text,deferred.id,deferred.source,{conversationOnly:true,alreadySeen:true,timingId:deferred.timingId});
      else if (deferred) timings.finish(deferred.timingId,'interrupted');
    }
    if (!speechQueued) timings.finish(timingId,stillCurrent()?'failed':'interrupted','decision_failed');
    renderTiming(timingId);
  }
}

function runtimeOutput(output) {
  if (!running || output?.type !== 'game_external_input') return;
  const text = output.event?.userVoiceText || output.event?.userText || output.meta?.inputText;
  void handleInput(text,output.request_id,output.event?.userVoiceText?'voice':'text');
}

async function pollEvents() {
  if (!running || !state?.scene_id) return;
  const token = generation, scene = state?.scene_id, ownedSession = session;
  try {
    if (resumingSpeech) return;
    const response = await bridge(`/events?after=${cursor}&session_id=${encodeURIComponent(session)}`);
    if (!current(token, scene) || response.scene_id !== scene || response.session_id !== session) return;
    // A retry publishes a replacement before its HTTP response can identify it.
    // Do not consume that event until we can preserve its already-adapted text.
    if (resumingSpeech) return;
    if (pollReadError && $('error').textContent === pollReadError) error('');
    pollReadError = '';
    cursor = response.cursor;
    retryable = response.retryable || [];
    $('retry').hidden = !retryable.length && !(failedSpeech && current(failedSpeech.token,failedSpeech.scene));
    renderTutorial();
    if (response.error && !speechFailureHint) $('speech-state').textContent = `语音状态：${response.error}`;
    for (const event of response.events || []) {
      if (seenLines.has(event.speech_id)) continue;
      seenLines.add(event.speech_id);
      if (seenLines.size > 128) seenLines.delete(seenLines.values().next().value);
      const recovered = recoveredSpeechIds.delete(event.speech_id);
      void enqueueSpeech(event.text, {id:event.speech_id, interrupt:event.interrupt_existing, token, scene,
        adapt:!recovered,timingId:recovered?startTiming('recovery'):''});
    }
  } catch (problem) {
    // A missed read must not release ownership and interrupt a native tutorial
    // line. The independent heartbeat still enforces the six-second lease.
    if (running && session === ownedSession) {
      pollReadError = `暂未读到剧情状态，正在重试：${describeError(problem)}`;
      error(pollReadError);
    }
  }
  finally { if (running && session === ownedSession) pollTimer = setTimeout(pollEvents, 250); }
}
async function heartbeat() {
  if (!running) return;
  const ownedSession = session;
  try {
    if (!playerReady(playerWindow)) throw new Error('猫娘语音窗口已关闭，请重新接管。');
    const next = await bridge('/session', {owner:'browser', session_id:session, lanlan_name:character, active:true});
    if (running && session === ownedSession) {
      applyStatus(next);
      $('connection').textContent = `${character} 已接管游戏`;
      if (heartbeatError && $('error').textContent === heartbeatError) error('');
      heartbeatError = '';
    }
  } catch (problem) {
    if (running && session === ownedSession) {
      // Scene loading can stall Unity's main thread past a bridge request's
      // deadline. Keep retrying the same session; the bridge still fences old
      // scenes/turns and releases expired leases independently.
      const sceneChanged = ['scene changed before command execution','stale scene generation'].includes(problem.message);
      if (sceneChanged || /game thread busy|bridge_busy|timeout|timed out|failed to fetch|networkerror|aborterror/i.test(`${problem.name || ''} ${problem.message || ''}`)) {
        heartbeatError = '游戏暂未回应，正在恢复接管；恢复前请等待，旧动作不会补执行。';
        $('connection').textContent = '正在恢复游戏连接…';
        error(heartbeatError);
        heartbeatTimer = setTimeout(heartbeat,500);
        return;
      }
      error(`接管已中断：${describeError(problem)}`); await disconnect();
    }
  }
  if (running && session === ownedSession) heartbeatTimer = setTimeout(heartbeat, 2000);
}
async function refreshStatus() {
  if (!running) return;
  const ownedSession = session;
  try { const next = await bridge('/status'); if (running && session === ownedSession) applyStatus(next); }
  catch (problem) { if (running && session === ownedSession) error(describeError(problem)); }
  if (running && session === ownedSession) statusTimer = setTimeout(refreshStatus, 1000);
}

function disconnect() {
  if (disconnecting) return disconnectPromise;
  disconnecting = true;
  running = false; generation++; decisionEpoch++; decisionController?.abort(); brain?.clear(); brain=null;
  retireDeferredTutorialChat();
  voiceWanted = false; voiceState = null;
  $('voice-state').textContent = '麦克风未开启。';
  clearTimeout(heartbeatTimer); clearTimeout(pollTimer); clearTimeout(statusTimer);
  speechPreloadController?.abort(); speechPreloadController = null;
  cancelSpeech();
  detachPlayer();
  const oldGame = game; game = null;
  const oldSession = session, oldCharacter = character;
  // Detach local state synchronously. Concurrent teardown callbacks must not
  // clear a subsequently connected session after awaiting an old HTTP reply.
  session = ''; tracker = null; state = null;
  pollReadError = ''; heartbeatError = '';
  retryable = []; renderTutorial();
  recoveredSpeechIds.clear();
  $('connection').textContent = '接管已结束';
  controls(false);
  disconnectPromise = (async () => {
    if (oldSession) {
      try { await bridge('/session', {owner:'browser',session_id:oldSession,lanlan_name:oldCharacter,active:false}); } catch { /* Lease expires and brakes after six seconds. */ }
    }
    if (oldGame) {
      try { await oldGame.runtime.end({reason:'adapter_closed'}); } catch { /* Host heartbeat also expires. */ }
      oldGame.dispose();
    }
  })().finally(() => {
    disconnecting = false; disconnectPromise = null; controls(running);
  });
  return disconnectPromise;
}

async function connect(candidate) {
  if (connecting || disconnecting || running) return;
  connecting = true; controls(false); error('');
  $('connection').textContent = '正在连接游戏与猫娘…';
  try {
    if (!['127.0.0.1','localhost'].includes(location.hostname) || location.port !== '48911')
      throw new Error('请从 N.E.K.O 主窗口的同一地址打开此页（默认端口 48911）。');
    const initial = await bridge('/status');
    if (initial.protocol_version !== 2 || initial.possession_available !== true) throw new Error('游戏模组需要更新到 0.1.0，更新后重启游戏。');
    await attachPlayer(candidate);
    releasePanel = await keepPanelResponsive();
    const createHost = await window.nekoMiniGameSameOriginHostReady;
    game = await window.NekoMiniGame.connect({id:GAME,version:'0.1.0',protocolVersion:'1',
      requiredCapabilities:['runtime','logging','voice-input','speech-output','avatar-renderer','dialogue'],
      contracts:{controls:{body:bodyContract}}},
      // Route voice/playback events through the ready player, while HTTP and
      // Response stay in this SDK page's realm for strict JSON validation.
      {transport:createHost({gameType:GAME,gameVersion:'0.1.0',displayName:'教妹妹开车',
        windowImpl:createPlayerHostWindow(window,playerWindow),fetchImpl:window.fetch.bind(window)})});
    const bound = await game.runtime.bindCharacter($('character').value.trim() || playerWindow.lanlan_config?.lanlan_name || undefined);
    if (!bound) throw new Error('没有找到所选猫娘。');
    character = bound.name;
    $('character').value = character;
    game.runtime.configure({payload:() => ({currentState:state?.telemetry || {},gameMemoryEnabled:false}),
      heartbeat:{intervalMs:2000,timeoutMs:4000}, outputs:{intervalMs:500,timeoutMs:8000,limit:30},
      pageExit:{payload:() => ({reason:'adapter_closed'})}});
    game.events.on('runtime-output', ({payload}) => runtimeOutput(payload));
    game.events.on('runtime-error', ({payload}) => {
      if (running) error(`宿主游戏连接异常：${describeError(payload.error || payload.reason || '请重新接管')}`);
    });
    game.events.on('runtime-inactive', () => { if (running) { error('猫娘的游戏会话已结束。'); void disconnect(); } });
    game.events.on('page-exit', () => {
      running = false; generation++; cancelSpeech();
      detachPlayer();
      speechPreloadController?.abort(); speechPreloadController = null;
      if (session) void bridge('/session', {owner:'browser',session_id:session,lanlan_name:character,active:false}, {keepalive:true}).catch(() => {});
    });
    await game.runtime.start({currentState:initial.telemetry,gameMemoryEnabled:false,game_memory_enabled:false});
    // This proves the main window shares the host channel. Never take control
    // merely because a route was created in a different browser partition.
    for (let attempt=0;attempt<30 && playerWindow.appState.gameRouteInstanceId !== game.runtime.session.routeInstanceId;attempt++)
      await new Promise(resolve=>setTimeout(resolve,100));
    const voiceStatus = await game.voice.query({timeoutMs:5000});
    if (voiceStatus?.ok !== true || voiceStatus?.available !== true)
      throw new Error(`猫娘语音窗口尚未接入本次游戏：${voiceStatus?.reason || 'voice_unavailable'}`);
    session = game.runtime.session.id;
    state = null; cursor = 0; seenInputs.clear(); seenLines.clear(); generation++;
    const ownedSession = session;
    tracker = new PlaybackTracker({onProgress:speechProgress,send:(body,scope) => {
      if (scope && !current(scope.token,scope.scene)) return Promise.reject(new Error('speech_context_retired'));
      return bridge('/playback', {session_id:ownedSession,...body});
    },onError:(problem,scope) => {
      if (activeSpeech && activeSpeech.timingId === scope?.timingId) activeSpeech.feedbackFailed = true;
      if (running && session === ownedSession && (!scope || current(scope.token,scope.scene)))
        error(`播放反馈失败：${describeError(problem)}`);
    }});
    game.speech.onState(value => {
      if (activeSpeech && value.requestId === activeSpeech.id) activeSpeech.sample = value;
      tracker?.accept(value);
      if (!activeSpeech || activeSpeech.terminal || value.requestId !== activeSpeech.id) return;
      if (value.requestId && value.audioContextState === 'suspended')
        $('speech-state').textContent = '请在猫娘语音窗口点击一次，允许浏览器播放声音。';
      else if (value.requestId && value.active && value.audioContextState === 'running'
        && !activeSpeech.canRetry && value.playbackStartAudioTime > 0 && value.audioContextTime >= value.playbackStartAudioTime)
        $('speech-state').textContent = '正在播放 · 嘴型跟随声音';
    });
    game.voice.onTranscript(value => { void handleInput(value.text, value.requestId,'voice'); });
    game.voice.onState(showVoiceState);
    game.voice.onError(problem => error(`麦克风：${describeError(problem)}`));
    applyStatus(await bridge('/session', {owner:'browser',session_id:session,lanlan_name:character,active:true}));
    running = true; brain = new PossessionBrain(game); inputSequence=0; decisionEpoch++;
    // Silent, character/voice-keyed official cache. Never block control or ASR
    // on a cold/unsupported preload; a miss still uses the normal TTS pipeline.
    speechPreloadController = new AbortController();
    void game.speech.preload(Object.values(QUICK_LINES),{signal:speechPreloadController.signal,timeoutMs:45000}).catch(()=>{});
    memoryPromise = refreshMemory();
    $('connection').textContent = `${character} 已接管游戏`;
    controls(true);
    void pollEvents();
    heartbeatTimer = setTimeout(heartbeat, 2000);
    statusTimer = setTimeout(refreshStatus, 1000);
  } catch (problem) { error(describeError(problem)); await disconnect(); }
  finally { connecting = false; controls(running); }
}

$('connect').addEventListener('click', () => { void connect(acquirePlayerWindow()); });
$('emergency').addEventListener('click',()=>{void handleInput('刹车',crypto.randomUUID(),'emergency');});
$('memory').addEventListener('change',()=>{memoryPromise=refreshMemory();});
$('disconnect').addEventListener('click', () => { void disconnect(); });
$('microphone').addEventListener('click', async () => {
  voiceWanted = !(voiceState?.active || voiceState?.recording || voiceState?.starting);
  $('microphone').disabled = true;
  if (voiceWanted) $('voice-state').textContent = '正在连接语音服务，暂不能下口令…';
  try { showVoiceState(await game.voice.toggle({timeoutMs:15000})); }
  catch (problem) {
    $('voice-state').textContent = `语音连接未确认：${describeError(problem)}。请重试或使用文本口令。`;
  } finally { $('microphone').disabled = !running; }
});
$('command-form').addEventListener('submit', event => {
  event.preventDefault();
  const text = $('command').value; $('command').value = '';
  void handleInput(text, crypto.randomUUID());
});
async function retryNativeSpeech(speechId, token, scene, ownedSession) {
  const result = await bridge('/speech/retry',{session_id:ownedSession,scene_id:scene,speech_id:speechId});
  if (!current(token,scene) || session !== ownedSession) return false;
  if (!result.speech_id) throw new Error('重播台词编号未确认，请刷新游戏状态后重试。');
  recoveredSpeechIds.add(result.speech_id);
  if (failedSpeech?.id === speechId) failedSpeech = null;
  while (recoveredSpeechIds.size > 128) recoveredSpeechIds.delete(recoveredSpeechIds.values().next().value);
  return true;
}
async function retrySpeech() {
  const line = retryable.at(-1) || (failedSpeech && current(failedSpeech.token,failedSpeech.scene) ? {speech_id:failedSpeech.id} : null);
  if (!line || resumingSpeech || !running || !state?.scene_id) return;
  const ownedSession = session, scene = state.scene_id, token = generation;
  interruptSupersededReply(activeSpeech,playerWindow);
  resumingSpeech = true; renderTutorial();
  try {
    if (!await retryNativeSpeech(line.speech_id,token,scene,ownedSession)) return;
    retryable = retryable.filter(item => item.speech_id !== line.speech_id);
    error(''); $('speech-state').textContent = '正在恢复未完成的剧情语音…';
  }
  catch (problem) { if (running && session === ownedSession) error(describeError(problem)); }
  finally { resumingSpeech = false; renderTutorial(); }
}
$('retry').addEventListener('click', () => { void retrySpeech(); });
$('continue-tutorial').addEventListener('click', () => { void retrySpeech(); });

$('retry-current-speech').addEventListener('click', async () => {
  const job = activeSpeech;
  if (!job?.canRetry || !job.finished || resumingSpeech || !current(job.token,job.scene)) return;
  resumingSpeech = true;
  $('retry-current-speech').disabled = true;
  try {
    job.tracker.fail(job.id,'failed','manual_retry');
    await job.finished; // Publish failed before asking Unity to rebind this callback.
    if (!current(job.token,job.scene)) return;
    if (job.feedbackFailed) throw new Error('游戏未确认收到语音恢复请求，请恢复游戏连接后再重试。');
    await retryNativeSpeech(job.id,job.token,job.scene,session);
    if (current(job.token,job.scene)) { error(''); $('speech-state').textContent='正在重新准备这句语音，已执行的动作不会重做。'; }
  } catch (problem) { if (current(job.token,job.scene)) error(describeError(problem)); }
  finally { resumingSpeech = false; $('retry-current-speech').disabled = false; renderTutorial(); }
});

$('export-timing').addEventListener('click', () => {
  const blob = new Blob([JSON.stringify(timings.snapshot(),null,2)],{type:'application/json'});
  const url = URL.createObjectURL(blob), link = document.createElement('a');
  link.href = url; link.download = 'neko-driving-timings.json'; link.click();
  setTimeout(()=>URL.revokeObjectURL(url),1000);
});

window.addEventListener('pagehide',detachPlayer);
controls(false);
