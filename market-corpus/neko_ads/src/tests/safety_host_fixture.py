"""Disposable local host: real routes/controller/native keys, no user host or TTS."""
import asyncio
import json
import os
import socket
from contextlib import asynccontextmanager
from pathlib import Path

import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from plugin.plugins.neko_ads.desktop import DesktopController
from plugin.plugins.neko_ads.session import AdSession
from plugin.server.routes import plugin_ui

session = AdSession()
session.ready()
desktop = DesktopController(session, prepare_voices=False)
mode = {'poll_delay':0, 'start_before':0, 'start_after':0, 'stop_delay':0}
accepted = []


@asynccontextmanager
async def lifespan(app):
    yield
    await desktop.close()
    session.shutdown()


app = FastAPI(lifespan=lifespan)


@app.middleware('http')
async def passthrough(request, call_next):
    return await call_next(request)


async def action(plugin_id, **kwargs):
    args = kwargs['args']
    command = args['command']
    if command == 'start':
        await asyncio.sleep(mode['start_before'])
    try:
        result = await desktop.command(command, args['payload'])
    except ValueError as exc:
        # Same expected-error envelope as the plugin's decorated SDK entry.
        return {'result':{'error':{'message':str(exc)}}}
    accepted.append({'command':command, 'round_id':args['payload'].get('round_id')})
    delay = mode['poll_delay'] if command == 'poll' else mode['start_after'] if command == 'start' else mode['stop_delay'] if command == 'stop' else 0
    if delay:
        await asyncio.sleep(delay)
    return {'result':result}


async def context(plugin_id, **kwargs):
    return {'state':desktop.snapshot()}


# The transport/metadata boundary is a fixture; actual product request handler,
# controller, session, Win32 key registration and renderer are unchanged.
plugin_ui.plugin_ui_query_service.call_surface_action = action
plugin_ui.plugin_ui_query_service.get_surface_context = context
app.mount('/plugin/neko_ads/ui', StaticFiles(directory=Path(__file__).resolve().parents[1]/'static', html=True))
app.include_router(plugin_ui.router)


@app.get('/')
@app.get('/chat')
async def page():
    return HTMLResponse('<!doctype html><meta charset="utf-8"><textarea aria-label="聊天输入"></textarea>'
                        '<script>window.lanlan_config={lanlan_name:"本地验收猫娘"}</script>'
                        '<script defer src="/plugin/neko_ads/ui/desktop-bridge.js"></script>')


@app.get('/plugin/status')
async def status():
    return {'status':{'status':'running'}}


@app.get('/market/status')
async def market():
    return {'market_web_url':'https://market.project-neko.cn'}


@app.get('/market/catalog/api/v1/plugins')
async def catalog():
    return {'items':[]}


@app.post('/__test__/mode')
async def set_mode(request: Request):
    mode.update(await request.json())
    return mode


@app.get('/__test__/state')
async def test_state():
    return {**desktop.snapshot(), 'accepted':accepted, 'pid':os.getpid()}


@app.post('/__test__/native-exit')
async def native_exit():
    key = desktop.hotkey
    if key is None:
        return JSONResponse({'error':'no registered test key'}, status_code=409)
    assert key._user32.PostThreadMessageW(key.thread_id, 0x0312, 1, 0)
    return {'posted':True}


@app.post('/__test__/shutdown')
async def shutdown():
    server.should_exit = True
    return {'stopping':True}


if __name__ == '__main__':
    listener = socket.socket()
    listener.bind(('127.0.0.1', 0))
    config = uvicorn.Config(app, log_level='error', access_log=False, timeout_graceful_shutdown=2)
    server = uvicorn.Server(config)
    print(json.dumps({'port':listener.getsockname()[1], 'pid':os.getpid()}), flush=True)
    server.run(sockets=[listener])
