const {contextBridge, ipcRenderer} = require('electron');
contextBridge.exposeInMainWorld('nekoBridge', {command: (action, data) => ipcRenderer.invoke('nvb-command', action, data)});
