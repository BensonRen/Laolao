const { contextBridge } = require('electron');

// Expose a minimal flag so overlay/index.html knows it's running inside Electron.
// Add IPC channels here as needed.
contextBridge.exposeInMainWorld('electronAPI', {
  platform: process.platform,
});
