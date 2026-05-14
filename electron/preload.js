const { contextBridge, ipcRenderer } = require('electron');

contextBridge.exposeInMainWorld('electronAPI', {
  platform: process.platform,
  openMicSettings: () => ipcRenderer.invoke('open-mic-settings'),
});
