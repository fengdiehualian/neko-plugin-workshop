// Requires plugin-manager development dependencies; SDK assets are served from
// the checkout in an isolated context. Does not acquire the game, mic or AI.
const test = require('node:test');
const assert = require('node:assert/strict');
const {chromium} = require('../../../../frontend/plugin-manager/node_modules/@playwright/test');
const {serveLocalStaticAssets}=require('./browser-assets.cjs');

test('embedded player transport preserves local JSON and forwards voice events', async () => {
  const browser = await chromium.launch({headless:true});
  try {
    const context = await browser.newContext();
    await serveLocalStaticAssets(context);
    await context.route('**/__drive_player_test', route => route.fulfill({
      contentType:'text/html',body:'<!doctype html><title>Voice transport test</title>',
    }));
    const page = await context.newPage();
    await page.goto('http://localhost:48911/static/game/teach-sister-drive/index.html', {waitUntil:'load'});
    const result = await page.evaluate(async () => {
      const player = window.open('/__drive_player_test', 'drive-transport-test');
      await new Promise(resolve => player.addEventListener('load', resolve, {once:true}));
      const {createPlayerHostWindow} = await import('./player-window.mjs');
      const {normalizeDecision} = await import('./possession.mjs');
      const createHost = await window.nekoMiniGameSameOriginHostReady;
      const data = {line:'准备系安全带。',control:{body:{actions:[{action:'fasten_seatbelt'}],emotion:'neutral'}}};
      const options = {gameType:'teach-sister-drive',gameVersion:'0.1.0'};
      const oldHost = createHost({...options,windowImpl:player});
      const oldResponse = await oldHost._bufferResponse(new Response(JSON.stringify(data)));
      const oldData = await oldResponse.json();
      const oldRejected = Object.getPrototypeOf(oldData) !== Object.prototype;
      oldHost.dispose();

      const transportWindow = createPlayerHostWindow(window, player);
      const host = createHost({...options,windowImpl:transportWindow,fetchImpl:window.fetch.bind(window)});
      const response = await host._bufferResponse(new player.Response(JSON.stringify(data)));
      const localData = await response.json();
      let voiceEvents = 0;
      const listener = () => voiceEvents++;
      transportWindow.addEventListener('neko-game-voice-control-message', listener);
      player.dispatchEvent(new player.CustomEvent('neko-game-voice-control-message'));
      transportWindow.removeEventListener('neko-game-voice-control-message', listener);
      player.dispatchEvent(new player.CustomEvent('neko-game-voice-control-message'));
      const timerWorks = await new Promise(resolve => transportWindow.setTimeout(() => resolve(true), 1));
      let exitEvents=0;
      const exitListener=()=>exitEvents++;
      transportWindow.addEventListener('pagehide',exitListener);
      player.dispatchEvent(new player.Event('pagehide'));
      const playerExitIgnored=exitEvents===0;
      window.dispatchEvent(new Event('pagehide'));
      transportWindow.removeEventListener('pagehide',exitListener);
      const checked = {
        oldRejected,
        localJSON:Object.getPrototypeOf(localData) === Object.prototype
          && Object.getPrototypeOf(localData.control.body.actions[0]) === Object.prototype,
        action:normalizeDecision(localData).actions[0].action,
        voiceEvents,timerWorks,playerExitIgnored,exitEvents,
        playerStorage:transportWindow.localStorage === player.localStorage,
        playerChannel:transportWindow.BroadcastChannel === player.BroadcastChannel,
        localResponse:transportWindow.Response === window.Response,
      };
      host.dispose(); player.close();
      return checked;
    });
    assert.deepEqual(result, {oldRejected:true,localJSON:true,action:'fasten_seatbelt',
      voiceEvents:1,timerWorks:true,playerExitIgnored:true,exitEvents:1,playerStorage:true,playerChannel:true,localResponse:true});
  } finally { await browser.close(); }
});
