const { contextBridge, ipcRenderer } = require('electron');

contextBridge.exposeInMainWorld('electronAPI', {
  platform: process.platform,
  openMicSettings: () => ipcRenderer.invoke('open-mic-settings'),
  // Single-consumer camera pipeline: only the hidden output window opens the
  // webcam; the control window previews the composed output frames (JPEG).
  onOutputFrame: cb => ipcRenderer.on('output-frame', (_e, buf) => cb(buf)),
  // Output window → main → control window: camera gave up, ask the user.
  reportCameraFailed: () => ipcRenderer.invoke('camera-failed'),
  onCameraFailed: cb => ipcRenderer.on('camera-failed', () => cb()),
});
