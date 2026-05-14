const { app, BrowserWindow, dialog, ipcMain, session, shell, systemPreferences } = require('electron');
const { spawn, execSync }                      = require('child_process');
const path = require('path');
const fs   = require('fs');
const net  = require('net');

const IS_MAC = process.platform === 'darwin';
const IS_WIN = process.platform === 'win32';

// ── Paths ──────────────────────────────────────────────────────
// When packaged the repo lives at ~/code/Laolao; in dev it's one level up.
const ROOT    = app.isPackaged
  ? path.join(app.getPath('home'), 'code', 'Laolao')
  : path.join(__dirname, '..');
const VENV_PY = IS_WIN
  ? path.join(ROOT, 'venv', 'Scripts', 'python.exe')
  : path.join(ROOT, 'venv', 'bin', 'python');
const SERVER_PY = path.join(ROOT, 'server.py');
const VCAM_PY   = path.join(ROOT, 'virtual_cam.py');
const OVERLAY   = path.join(ROOT, 'overlay', 'index.html');

// ── macOS DAL plugin (one-time install, not needed on Windows) ──
const PLUGIN_NAME = 'obs-mac-virtualcam.plugin';
const PLUGIN_SRC  = path.join(__dirname, 'resources', PLUGIN_NAME);
const PLUGIN_DST  = `/Library/CoreMediaIO/Plug-Ins/DAL/${PLUGIN_NAME}`;

function isPluginInstalled() {
  return fs.existsSync(PLUGIN_DST);
}

function installPlugin() {
  if (!fs.existsSync(PLUGIN_SRC)) {
    throw new Error(
      `Plugin not found in app resources.\n` +
      `Place ${PLUGIN_NAME} in electron/resources/ — see electron/resources/PLUGIN_README.md`
    );
  }
  const cmd = `mkdir -p '/Library/CoreMediaIO/Plug-Ins/DAL' && cp -r '${PLUGIN_SRC}' '${PLUGIN_DST}'`;
  const script = `do shell script "${cmd.replace(/"/g, '\\"')}" with administrator privileges`;
  execSync(`osascript -e '${script}'`);
}

async function ensurePlugin() {
  if (!IS_MAC) return;       // Windows uses OBS DirectShow — no driver install needed
  if (isPluginInstalled()) return;

  const { response } = await dialog.showMessageBox({
    type: 'info',
    title: 'One-time Setup — Virtual Camera',
    message: 'Laolao needs to install a virtual camera driver',
    detail:
      'This lets your captions appear as a real camera in WeChat, Zoom, FaceTime, ' +
      'and any other video call app. macOS will ask for your admin password once.',
    buttons: ['Install Driver', 'Quit'],
    defaultId: 0,
    cancelId: 1,
  });

  if (response === 1) { app.quit(); return; }

  try {
    installPlugin();
  } catch (e) {
    await dialog.showMessageBox({
      type: 'error',
      title: 'Install Failed',
      message: 'Could not install the virtual camera driver.',
      detail: String(e),
      buttons: ['OK'],
    });
    app.quit();
  }
}

// ── Python child processes ─────────────────────────────────────
function spawnPython(script, args = []) {
  // On Windows, add the OBS bin dir to PATH so obs-virtualcam-module64.dll
  // can find its dependencies (obs.dll etc.) when pyvirtualcam loads it.
  const env = { ...process.env };
  if (IS_WIN) {
    const obsBin = 'C:\\Program Files\\obs-studio\\bin\\64bit';
    env.PATH = `${obsBin};${env.PATH || ''}`;
  }

  const proc = spawn(VENV_PY, [script, ...args], {
    cwd: ROOT,
    stdio: ['ignore', 'pipe', 'pipe'],
    env,
  });
  const tag = path.basename(script, '.py');
  proc.stdout.on('data', d => process.stdout.write(`[${tag}] ${d}`));
  proc.stderr.on('data', d => process.stderr.write(`[${tag}] ${d}`));
  proc.on('exit', code => console.log(`[${tag}] exited (${code})`));
  return proc;
}

function killAll() {
  [pythonServer, virtualCamProc].forEach(p => { try { p?.kill(); } catch {} });
}

// ── Frame capture → virtual camera pipeline ────────────────────
const VCAM_PORT = 8766;
const CAM_FPS   = 30;
const CAM_W     = 1280;
const CAM_H     = 720;

let mainWindow     = null;
let pythonServer   = null;
let virtualCamProc = null;
let vcSocket       = null;

function connectVcSocket() {
  const sock = new net.Socket();
  sock.connect(VCAM_PORT, '127.0.0.1', () => {
    console.log('[vcam] socket connected — starting capture loop');
    vcSocket = sock;
    captureLoop();
  });
  sock.on('error', () => {});
  sock.on('close', () => {
    vcSocket = null;
    setTimeout(connectVcSocket, 1000);
  });
}

let captureRunning = false;
function captureLoop() {
  if (captureRunning) return;
  captureRunning = true;

  async function tick() {
    if (!mainWindow || mainWindow.isDestroyed() || !vcSocket) {
      captureRunning = false;
      return;
    }
    try {
      const img    = await mainWindow.webContents.capturePage();
      const scaled = img.resize({ width: CAM_W, height: CAM_H });
      const jpeg   = scaled.toJPEG(80);
      const header = Buffer.allocUnsafe(4);
      header.writeUInt32BE(jpeg.length);
      vcSocket.write(header);
      vcSocket.write(jpeg);
    } catch { /* window closing or socket gone */ }

    setTimeout(tick, 1000 / CAM_FPS);
  }

  tick();
}

// ── IPC handlers ──────────────────────────────────────────────
ipcMain.handle('open-mic-settings', () => {
  const url = IS_MAC
    ? 'x-apple.systempreferences:com.apple.preference.security?Privacy_Microphone'
    : 'ms-settings:privacy-microphone';
  shell.openExternal(url);
});

// ── App lifecycle ──────────────────────────────────────────────
app.whenReady().then(async () => {
  await ensurePlugin();

  // Auto-grant camera and microphone without a browser-style prompt
  session.defaultSession.setPermissionRequestHandler((_wc, permission, cb) => {
    cb(['camera', 'microphone', 'media', 'mediaKeySystem'].includes(permission));
  });

  // Trigger macOS microphone permission dialog (packaged app requires this; no-op on Windows)
  if (IS_MAC && systemPreferences.askForMediaAccess) {
    await systemPreferences.askForMediaAccess('microphone');
  }

  pythonServer = spawnPython(SERVER_PY);

  // Give the WebSocket server a moment to bind before the overlay connects
  await new Promise(r => setTimeout(r, 2000));

  virtualCamProc = spawnPython(VCAM_PY);

  // Give virtual_cam.py a moment to open its TCP socket
  await new Promise(r => setTimeout(r, 1000));
  connectVcSocket();

  mainWindow = new BrowserWindow({
    width: CAM_W,
    height: CAM_H,
    title: '老老 Laolao',
    aspectRatio: CAM_W / CAM_H,
    webPreferences: {
      preload: path.join(__dirname, 'preload.js'),
      contextIsolation: true,
    },
  });

  mainWindow.loadFile(OVERLAY);
  mainWindow.on('closed', () => { mainWindow = null; });
});

app.on('window-all-closed', () => {
  killAll();
  app.quit();
});

app.on('before-quit', killAll);
