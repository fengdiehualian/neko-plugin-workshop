import {parseCommand, commandPolicy} from './intent.mjs';
import {tutorialState, tutorialPlaybackBusy, validateTutorialActions} from './tutorial.mjs';

export const QUICK_LINES = Object.freeze({center:'方向回正啦～',left:'向左打方向～',right:'向右打方向～',
  brake:'正在刹车～',fasten_seatbelt:'我来系好安全带～',start_engine:'我来启动引擎～',shift_drive:'准备出发啦～',
  microphone:'听到你啦，接下来教我开车吧～'});

// Keyword activation is confined to the native tutorial. Ordinary driving
// keeps model interpretation for colloquial speed and manoeuvre requests.
export function tutorialKeywordDecision(text, state) {
  const tutorial = tutorialState(state);
  if (!tutorial.active || !tutorial.known || tutorial.completed || !tutorial.allowedAction
    || tutorialPlaybackBusy(state) || commandPolicy(text).conversationOnly) return null;
  const raw=String(text).normalize('NFKC').toLowerCase().replace(/\s+/g,'');
  const keywords={fasten_seatbelt:/安全带|seatbelt/,
    start_engine:/打火|点火|引擎|发动机|启动(?:汽车|车子|车)|发动车/,
    shift_drive:/油门|出发|起步|前进|往前|向前|前行|挂d[挡档]|挂前进[挡档]/};
  if (!keywords[tutorial.allowedAction]?.test(raw)) return null;
  const decision={line:QUICK_LINES[tutorial.allowedAction],actions:[{action:tutorial.allowedAction}],emotion:''};
  validateTutorialActions(decision.actions,state);
  return decision;
}

// Only unambiguous, immediate targets bypass model reasoning. Distances,
// timed manoeuvres and relative acceleration still need a complete model plan.
export function quickDecision(text, state) {
  const tutorial = tutorialState(state);
  if (state?.game_active === false || (tutorial.active ? !tutorial.allowedAction : state?.body?.native_input_available !== true)) return null;
  const parsed = parseCommand(text);
  if (parsed.commands.length !== 1 || parsed.note || /秒|seconds?|secs?/i.test(text)) return null;
  const command = parsed.commands[0];
  if (command.action === 'brake') return null; // Dedicated emergency path.
  let action, line;
  if (tutorial.active && tutorial.allowedAction === 'shift_drive' && command.action === 'forward'
    && command.target_speed === undefined && command.steering === undefined)
    return {line:QUICK_LINES.shift_drive,actions:[{action:'shift_drive'}],emotion:''};
  if (['fasten_seatbelt','start_engine','shift_drive'].includes(command.action)) {
    if (!tutorial.active || tutorial.scripted || tutorial.allowedAction !== command.action) return null;
    action = {action:command.action}; line = QUICK_LINES[command.action];
  } else {
    if (tutorial.active) return null;
    const {target_speed,steering} = command;
    if (['forward','back'].includes(command.action)) {
      // An explicit speed is absolute; "forward a bit" is a bounded manoeuvre,
      // and "accelerate" alone depends on the current state.
      if (!Number.isFinite(target_speed)
        || !/[\d零一二两三四五六七八九十]\s*(?:公里|千米|km\s*\/?\s*h|kph|迈)/i.test(text)
        || /一点|一下|稍微|轻轻|a (?:little|bit)/i.test(text)) return null;
      action = {action:'drive',target_speed,...(steering !== undefined ? {steering} : {})};
      line = target_speed < 0 ? `按 ${-target_speed} 公里时速倒车～` : `目标时速 ${target_speed} 公里～`;
    } else if (['left','right','center'].includes(command.action) && Number.isFinite(steering)) {
      action = {action:'drive',steering};
      line = QUICK_LINES[steering === 0 ? 'center' : steering < 0 ? 'left' : 'right'];
    } else return null;
  }
  validateTutorialActions([action],state);
  return {line,actions:[action],emotion:''};
}

// Cancel only this adapter's ordinary reply. Scripted lines and replies with a
// native completion callback must finish through the real playback tracker.
export function interruptSupersededReply(job, player) {
  if (!job || job.replyGeneration === null) return false;
  const sample = job.sample;
  if (sample?.requestId === job.id && sample.speechId
    && player?.NekoSpeechPlaybackState?.speechId === sample.speechId)
    player.appAudioPlayback?.interruptCurrentAssistantSpeech();
  job.controller.abort();
  if (job.id) job.tracker.fail(job.id,'interrupted');
  return true;
}
