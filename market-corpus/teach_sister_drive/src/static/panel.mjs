import {callPlugin} from './plugin-api.mjs?v=0.1.0-native1';
import {mainOrigin,resolvePlayerOrigin} from './player-origin.mjs?v=0.1.0-native1';
// The manager is served by the plugin server; the game SDK and voice channel
// belong to the main server. Keep the adapter on that origin inside the panel.
let origin = null;
try {
  const manager = window.parent;
  origin = mainOrigin(new URLSearchParams(manager.location.search).get('yui_opener_origin'))
    || mainOrigin(manager.document.referrer);
} catch { /* Directly opening the panel also works without a manager. */ }
origin ||= mainOrigin(document.referrer)
  || `http://${location.hostname === 'localhost' ? 'localhost' : '127.0.0.1'}:48911`;
const $=id=>document.getElementById(id);
let games=[],busy=false,takeover=false;
const selected=()=>games[Number($('game-choice').value)];
function render() {
  const game=selected();
  $('detect-game').disabled=busy;
  $('game-choice').disabled=busy;
  $('manual-path').disabled=busy;
  $('install-mod').disabled=busy || !game?.supported || game.mod_current && game.loader_installed;
  $('install-mod').textContent=game?.mod_current && game.loader_installed?'模组已安装':game?.mod_installed?'② 更新模组':'② 安装模组';
  $('uninstall-mod').disabled=busy || !game?.mod_installed;
  $('open-drive-panel').disabled=busy || !takeover || !game?.mod_current || !game?.loader_installed;
}
$('game-choice').addEventListener('change',()=>{
  const game=selected();
  $('installation-state').textContent=game?.mod_current && game.loader_installed?'模组已安装，可以启动游戏连接。':'已选择游戏，请安装模组。';
  if(game && !game.supported)$('installation-state').textContent='当前游戏版本尚未验证兼容，暂不安装；已有模组仍可卸载。';
  render();
});
$('detect-game').addEventListener('click',async()=>{
  busy=true;render();$('installation-error').textContent='';$('installation-state').textContent='正在检测本机游戏…';
  try {
    const result=await callPlugin('detect_game',{game_path:$('manual-path').value.trim()},25000);
    games=result.games || [];takeover=result.takeover_available === true;
    $('game-choice').replaceChildren(...games.map((game,index)=>{
      const option=document.createElement('option');option.value=String(index);option.textContent=`${game.name} — ${game.path}`;return option;
    }));
    $('game-choice-label').hidden=!games.length;
    const game=selected();
    $('installation-state').textContent=game?.mod_current && game?.loader_installed?'模组已安装，可以启动游戏连接。':result.game_running && games.length?'已找到游戏；请先正常退出游戏，再点击安装模组。':result.message;
    if(game && !game.supported)$('installation-state').textContent='当前游戏版本尚未验证兼容，暂不安装；已有模组仍可卸载。';
    $('takeover-state').textContent=takeover?'源码版接管已就绪，启动游戏后打开接管面板。':'接管需要通过插件的 launch_backend.py 启动 N.E.K.O 源码版；当前 Steam 版不受支持。';
  } catch(problem) { $('installation-error').textContent=problem.message; }
  finally {busy=false;render();}
});
$('install-mod').addEventListener('click',async()=>{
  const game=selected();if(!game || busy)return;
  busy=true;render();$('installation-error').textContent='';$('installation-state').textContent='正在校验并安装模组，请稍候…';
  try {
    const result=await callPlugin('install_mod',{game_path:game.path});
    Object.assign(game,result);$('installation-state').textContent=result.message;
  } catch(problem) { $('installation-error').textContent=problem.message;$('installation-state').textContent='安装未完成，请处理提示后重试。'; }
  finally {busy=false;render();}
});
$('uninstall-mod').addEventListener('click',async()=>{
  const game=selected();if(!game || busy || !game.mod_installed)return;
  busy=true;render();$('installation-error').textContent='';$('installation-state').textContent='正在备份并卸载模组，请稍候…';
  try {
    const result=await callPlugin('uninstall_mod',{game_path:game.path},35000);
    Object.assign(game,result);$('installation-state').textContent=result.message;
    const frame=$('drive-panel');frame.hidden=true;frame.removeAttribute('src');
  } catch(problem) { $('installation-error').textContent=problem.message;$('installation-state').textContent='卸载未完成，请处理提示后重试。'; }
  finally {busy=false;render();}
});
$('open-drive-panel').addEventListener('click',async()=>{
  if(busy || !takeover)return;
  busy=true;render();$('installation-error').textContent='';
  try {
    const frame=$('drive-panel');
    if(!frame.getAttribute('src')) {
      const playerOrigin=await resolvePlayerOrigin(window,origin);
      frame.src=`${playerOrigin}/static/game/teach-sister-drive/index.html?embedded=1`;
    }
    frame.hidden=false;frame.scrollIntoView({behavior:'smooth',block:'start'});
  } catch(problem) { $('installation-error').textContent=problem.message; }
  finally {busy=false;render();}
});
render();
document.body.dataset.installerReady='true';
