const {contextBridge, ipcRenderer} = require('electron');
contextBridge.exposeInMainWorld('cableHost', {
  frame: samples => ipcRenderer.send('nvb-cable-frame', samples),
  lost: () => ipcRenderer.send('nvb-cable-lost'),
});
