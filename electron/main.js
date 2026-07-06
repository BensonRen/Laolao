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

// ── Ports & camera geometry ────────────────────────────────────
const WS_PORT   = 8765;
const VCAM_PORT = 8766;
const CAM_FPS   = 30;

// Camera geometry is FIXED at 1280×720. Empirically verified (2026-07-05):
// pyvirtualcam happily creates the OBS Camera Extension camera at other
// sizes (e.g. 720×1280 portrait) and consumers even enumerate that size,
// but the extension logs "Pixel buffer size mismatch" and delivers blank
// buffers — apps see a dead feed. Until we ship our own virtual camera
// driver, non-16:9 ratios are composed INSIDE this frame: the output
// window CSS-letterboxes the chosen ratio, and portrait phone viewers in
// fill mode crop the side bars away.
const CAM_W = 1280;
const CAM_H = 720;

// ── OBS Studio detection ───────────────────────────────────────
// The virtual camera driver comes from OBS Studio 28+ (Camera Extension on
// macOS, DirectShow filter on Windows). We only check presence and point the
// user at the download page — no privileged installs.
let obsWinBin = null;   // resolved OBS bin dir on Windows (for PATH prepend)

function findObsMac() {
  if (fs.existsSync('/Applications/OBS.app')) return '/Applications/OBS.app';
  try {
    const out = execSync(
      `mdfind "kMDItemCFBundleIdentifier == 'com.obsproject.obs-studio'"`,
      { encoding: 'utf8', timeout: 5000 },
    ).trim();
    if (out) return out.split('\n')[0];
  } catch {}
  return null;
}

function findObsWinBin() {
  try {
    const out = execSync('reg query "HKLM\\SOFTWARE\\OBS Studio" /ve', {
      encoding: 'utf8', timeout: 5000,
    });
    const m = out.match(/REG_SZ\s+(.+)/);
    if (m) {
      const bin = path.join(m[1].trim(), 'bin', '64bit');
      if (fs.existsSync(bin)) return bin;
    }
  } catch {}
  const fallback = 'C:\\Program Files\\obs-studio\\bin\\64bit';
  return fs.existsSync(fallback) ? fallback : null;
}

async function checkObs() {
  if (IS_MAC) {
    if (findObsMac()) return true;
  } else if (IS_WIN) {
    obsWinBin = findObsWinBin();
    if (obsWinBin) return true;
  } else {
    return true;
  }

  const { response } = await dialog.showMessageBox({
    type: 'info',
    title: 'OBS Studio Not Found',
    message: 'Laolao needs OBS Studio 28+ for its virtual camera driver',
    detail:
      'Captions will still work in this window, but the "OBS Virtual Camera" ' +
      'will not appear in WeChat, Zoom, or FaceTime until OBS Studio is ' +
      'installed. Install OBS, then relaunch Laolao.',
    buttons: ['Open Download Page', 'Continue Without Camera'],
    defaultId: 0,
    cancelId: 1,
  });
  if (response === 0) shell.openExternal('https://obsproject.com');
  return false;
}

// ── Python child processes (supervised) ────────────────────────
let isQuitting = false;

function spawnPython(script, args = []) {
  // On Windows, add the OBS bin dir to PATH so obs-virtualcam-module64.dll
  // can find its dependencies (obs.dll etc.) when pyvirtualcam loads it.
  const env = { ...process.env };
  if (IS_WIN) {
    const obsBin = obsWinBin || 'C:\\Program Files\\obs-studio\\bin\\64bit';
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
  return proc;
}

// Restart a child with exponential backoff when it exits unexpectedly.
// Backoff: 1s → 2s → 4s … capped at 15s; reset after 60s of healthy uptime.
function supervise(name, spawnFn) {
  let proc        = null;
  let backoffMs   = 1000;
  let startedAt   = 0;
  let intentional = false;   // restart() in flight — not a crash

  function start() {
    if (isQuitting) return;
    startedAt = Date.now();
    proc = spawnFn();
    proc.on('exit', code => {
      console.log(`[${name}] exited (${code})`);
      if (isQuitting) return;
      if (intentional) {
        // Reconfigure restart (e.g. new camera dimensions) — no backoff
        // penalty; brief pause so the OS releases the device cleanly.
        intentional = false;
        backoffMs = 1000;
        console.log(`[${name}] restarting (reconfigure)…`);
        setTimeout(start, 300);
        return;
      }
      if (Date.now() - startedAt > 60_000) backoffMs = 1000;
      const delay = backoffMs;
      backoffMs = Math.min(backoffMs * 2, 15_000);
      console.log(`[${name}] restarting in ${delay} ms…`);
      setTimeout(start, delay);
    });
  }

  start();
  return {
    kill() { try { proc?.kill(); } catch {} },
    // Kill + respawn with the CURRENT config (spawnFn reads live variables).
    restart() {
      if (isQuitting) return;
      if (!proc || proc.exitCode !== null) { start(); return; }
      intentional = true;
      try { proc.kill(); } catch { intentional = false; }
    },
  };
}

let serverSup = null;
let vcamSup   = null;

function killAll() {
  [serverSup, vcamSup].forEach(s => s?.kill());
}

// ── Port readiness polling ─────────────────────────────────────
// Resolves true once a TCP connect to the port succeeds, false on timeout.
// First run may download Whisper models, so allow a generous server timeout.
function waitForPort(port, { timeoutMs, intervalMs = 250, label }) {
  return new Promise(resolve => {
    const deadline = Date.now() + timeoutMs;
    let lastLog = 0;

    function attempt() {
      if (isQuitting) return resolve(false);
      let settled = false;
      const sock = new net.Socket();
      sock.setTimeout(1000);

      const fail = () => {
        if (settled) return;
        settled = true;
        sock.destroy();
        if (Date.now() > deadline) {
          console.log(`[startup] gave up waiting for ${label} on :${port}`);
          return resolve(false);
        }
        if (Date.now() - lastLog > 10_000) {
          console.log(`[startup] waiting for ${label} on :${port}…`);
          lastLog = Date.now();
        }
        setTimeout(attempt, intervalMs);
      };

      sock.once('connect', () => {
        if (settled) return;
        settled = true;
        sock.destroy();
        console.log(`[startup] ${label} ready on :${port}`);
        resolve(true);
      });
      sock.once('error',   fail);
      sock.once('timeout', fail);
      sock.connect(port, '127.0.0.1');
    }

    attempt();
  });
}

// ── Frame capture → virtual camera pipeline ────────────────────
// Two windows, one overlay file:
//   mainWindow   — visible control surface (toolbar, panels, mirrored
//                  self-view). NEVER captured.
//   outputWindow — hidden, exactly camW×camH, loads the overlay with
//                  ?output=1 (chrome-free, un-mirrored). This is what the
//                  far end sees via the virtual camera.
let mainWindow   = null;
let outputWindow = null;
let vcSocket     = null;

function connectVcSocket() {
  if (isQuitting) return;
  const sock = new net.Socket();
  sock.connect(VCAM_PORT, '127.0.0.1', () => {
    console.log('[vcam] socket connected — starting capture loop');
    vcSocket = sock;
    captureLoop();
  });
  sock.on('error', () => {});
  sock.on('close', () => {
    vcSocket = null;
    if (!isQuitting) setTimeout(connectVcSocket, 1000);
  });
}

let captureRunning = false;
function captureLoop() {
  if (captureRunning) return;
  captureRunning = true;

  async function tick() {
    if (!outputWindow || outputWindow.isDestroyed() || !vcSocket) {
      captureRunning = false;
      return;
    }
    try {
      const img  = await outputWindow.webContents.capturePage();
      const size = img.getSize();
      const targetAspect = CAM_W / CAM_H;
      if (size.width > 0 && size.height > 0) {
        // Aspect-safe: center-crop to the camera aspect before resizing.
        // The output window is exactly CAM_W×CAM_H so this is normally a
        // no-op safety net against squashed frames.
        let frame = img;
        const srcAspect = size.width / size.height;
        if (Math.abs(srcAspect - targetAspect) > 0.01) {
          let cw = size.width, ch = size.height;
          if (srcAspect > targetAspect) cw = Math.round(size.height * targetAspect);
          else                          ch = Math.round(size.width / targetAspect);
          frame = img.crop({
            x: Math.floor((size.width  - cw) / 2),
            y: Math.floor((size.height - ch) / 2),
            width: cw, height: ch,
          });
        }
        const scaled = frame.resize({ width: CAM_W, height: CAM_H });
        // Quality 92: localhost TCP has headroom, and caption text edges must
        // survive the call app's re-encode.
        const jpeg   = scaled.toJPEG(92);
        const header = Buffer.allocUnsafe(4);
        header.writeUInt32BE(jpeg.length);
        vcSocket.write(header);
        vcSocket.write(jpeg);
      }
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
  // Auto-grant camera and microphone without a browser-style prompt
  session.defaultSession.setPermissionRequestHandler((_wc, permission, cb) => {
    cb(['camera', 'microphone', 'media', 'mediaKeySystem'].includes(permission));
  });

  // Trigger the macOS microphone permission dialog. The renderer captures the
  // mic via getUserMedia (with echo cancellation), so the app needs OS access.
  if (IS_MAC && systemPreferences.askForMediaAccess) {
    await systemPreferences.askForMediaAccess('microphone');
  }

  const obsAvailable = await checkObs();

  // The renderer streams mic audio over WebSocket on every platform, so
  // Python never opens the mic itself (unified path; Chromium provides AEC).
  serverSup = supervise('server', () => spawnPython(SERVER_PY, ['--no-mic']));

  if (obsAvailable) {
    vcamSup = supervise('virtual_cam', () =>
      spawnPython(VCAM_PY, [String(CAM_W), String(CAM_H), String(CAM_FPS)]));
  } else {
    console.log('[vcam] OBS not found — running captions-only (no virtual camera)');
  }

  // Control window: toolbar + mirrored self-view. Never captured.
  mainWindow = new BrowserWindow({
    width: 1280,
    height: 720,
    title: '老老 Laolao',
    webPreferences: {
      preload: path.join(__dirname, 'preload.js'),
      contextIsolation: true,
    },
  });

  mainWindow.loadFile(OVERLAY);
  mainWindow.on('closed', () => {
    mainWindow = null;
    // The hidden output window would otherwise keep the app alive.
    if (outputWindow && !outputWindow.isDestroyed()) outputWindow.destroy();
  });

  // Output window: hidden, chrome-free, exactly CAM_W×CAM_H — the virtual
  // camera frame. paintWhenInitiallyHidden + throttling off keep a
  // never-shown window painting so capturePage() returns real frames.
  outputWindow = new BrowserWindow({
    width: CAM_W,
    height: CAM_H,
    useContentSize: true,
    show: false,
    resizable: false,
    skipTaskbar: true,
    paintWhenInitiallyHidden: true,
    webPreferences: {
      preload: path.join(__dirname, 'preload.js'),
      contextIsolation: true,
      backgroundThrottling: false,
    },
  });

  outputWindow.loadFile(OVERLAY, { query: { output: '1' } });
  outputWindow.webContents.setBackgroundThrottling(false);
  outputWindow.on('closed', () => { outputWindow = null; });

  // Readiness (informational for the server; gating for the vcam socket).
  waitForPort(WS_PORT, { timeoutMs: 120_000, label: 'caption server' });
  if (obsAvailable) {
    // Even on timeout, start the connect loop — it retries quietly forever,
    // so a slow or restarting virtual_cam.py is picked up whenever it binds.
    waitForPort(VCAM_PORT, { timeoutMs: 60_000, label: 'virtual camera' })
      .then(() => connectVcSocket());
  }
});

app.on('window-all-closed', () => {
  isQuitting = true;
  killAll();
  app.quit();
});

app.on('before-quit', () => {
  isQuitting = true;
  killAll();
});
