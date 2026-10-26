import {BrowserWindow, ipcMain, session} from 'electron';
import path from 'node:path';
import {CableBuffer} from './cable-buffer.mjs';

// Native capture permission is confined to our local hidden renderer. KOOK's
// web renderer still has no native microphone/camera permission.
export class CableCapture extends CableBuffer {
  constructor(root, resourceHandler) {
    super(); this.root = root; this.resourceHandler = resourceHandler; this.window = null;
    const trusted = event => this.window && !this.window.isDestroyed() && event.sender === this.window.webContents &&
      event.senderFrame === this.window.webContents.mainFrame && event.senderFrame.url === 'nvb://app/cable.html';
    ipcMain.on('nvb-cable-frame', (event, samples) => {if (trusted(event)) this.feed(samples);});
    ipcMain.on('nvb-cable-lost', event => {if (trusted(event)) this.setReady(false);});
  }
  async open() {
    if (this.window && !this.window.isDestroyed()) return;
    const ses = session.fromPartition('nvb-cable-capture');
    if (!this.protocolRegistered) {ses.protocol.handle('nvb', this.resourceHandler); this.protocolRegistered = true;}
    const trusted = contents => !!this.window && !this.window.isDestroyed() && contents === this.window.webContents && contents.getURL() === 'nvb://app/cable.html';
    ses.setPermissionCheckHandler((contents, permission, _origin, details) =>
      trusted(contents) && permission === 'media' && details.mediaType === 'audio' && details.isMainFrame === true);
    ses.setPermissionRequestHandler((contents, permission, callback, details) => callback(
      trusted(contents) && permission === 'media' && details.isMainFrame === true &&
      details.mediaTypes?.length === 1 && details.mediaTypes[0] === 'audio'));
    this.window = new BrowserWindow({show: false, webPreferences: {partition: 'nvb-cable-capture',
      preload: path.join(this.root, 'desktop/cable-preload.cjs'), sandbox: true, contextIsolation: true,
      nodeIntegration: false, backgroundThrottling: false}});
    this.window.webContents.setWindowOpenHandler(() => ({action: 'deny'}));
    this.window.webContents.on('will-navigate', e => e.preventDefault());
    this.window.webContents.on('render-process-gone', () => this.setReady(false));
    this.window.on('closed', () => {this.setReady(false); this.window = null;});
    await this.window.loadURL('nvb://app/cable.html');
  }
  async devices() {await this.open(); return this.window.webContents.executeJavaScript('cableCapture.devices()');}
  async start(id) {
    await this.open(); this.setReady(false);
    try {
      await this.window.webContents.executeJavaScript(`cableCapture.start(${JSON.stringify(id)})`);
      this.lastError = null; this.setReady(true);
    } catch (error) {
      const code = error.message.match(/NEKO_CABLE_[A-Z_]+/)?.[0] || 'NEKO_CABLE_CAPTURE_FAILED';
      this.fail(code); throw new Error(code);
    }
  }
  async stop() {
    this.setReady(false);
    if (this.window && !this.window.isDestroyed()) await this.window.webContents.executeJavaScript('cableCapture.stop()').catch(() => {});
    this.lastError = null;
  }
  destroy() {this.setReady(false); this.window?.destroy();}
}
