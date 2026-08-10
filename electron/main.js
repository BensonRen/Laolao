const { app, BrowserWindow, dialog, ipcMain, screen, session, shell, systemPreferences } = require('electron');
const { spawn, execSync }                      = require('child_process');
const path = require('path');
const fs   = require('fs');
const net  = require('net');

const IS_MAC = process.platform === 'darwin';
const IS_WIN = process.platform === 'win32';

// Keep the hidden/transparent output window's getUserMedia alive. macOS marks
// non-foreground/occluded web contents as hidden after a few seconds, and
// Chromium then MUTES camera tracks on hidden documents — the camera opens (OS
// "in use" light on) but zero frames arrive and capturePage is black. These
// switches stop Chromium from backgrounding the output window's capture.
app.commandLine.appendSwitch('disable-features', 'MacWebContentsOcclusion');
app.commandLine.appendSwitch('disable-backgrounding-occluded-windows');
app.commandLine.appendSwitch('disable-renderer-backgrounding');

// ── Paths ──────────────────────────────────────────────────────
// Dev: the repo is one level up, no questions asked. Packaged: the engine
// lives in a repo checkout resolved from (in order) the LAOLAO_ROOT env var,
// the `root` field of <userData>/settings.json, then ~/code/Laolao — each
// candidate must actually contain server.py and a venv. If none qualifies,
// ensureRoot() walks the user through picking/fixing one at startup.
let ROOT, VENV_PY, SERVER_PY, VCAM_PY, OVERLAY;

function venvPyFor(root) {
  return IS_WIN
    ? path.join(root, 'venv', 'Scripts', 'python.exe')
    : path.join(root, 'venv', 'bin', 'python');
}

function rootValid(root) {
  return !!root
    && fs.existsSync(path.join(root, 'server.py'))
    && fs.existsSync(venvPyFor(root));
}

function applyRoot(root) {
  ROOT      = root;
  VENV_PY   = venvPyFor(root);
  SERVER_PY = path.join(root, 'server.py');
  VCAM_PY   = path.join(root, 'virtual_cam.py');
  OVERLAY   = path.join(root, 'overlay', 'index.html');
}

applyRoot(path.join(__dirname, '..'));   // dev default; packaged apps re-resolve in ensureRoot()

// The virtual-camera sink may need a DIFFERENT interpreter than the caption
// engine. On Windows ARM64 the OBS DirectShow filter ships as x64 only
// (obs-virtualcam-module64.dll is machine 0x8664, and there is no ARM64
// build), and a DirectShow filter is loaded *in-process* — so a native-ARM64
// python can never drive that camera while an emulated-x64 one can. Because
// virtual_cam.py already runs as its own process fed over TCP, that process
// boundary doubles as an architecture boundary for free: the engine stays
// native (fast Whisper) and only the thin frame sink is emulated.
//
// Resolved lazily — readSettings() needs app.getPath(), which is not available
// at module load. Order: LAOLAO_PYTHON_CAMERA env → `pythonCamera` in
// settings.json → the engine interpreter, so every other platform is unchanged.
function cameraPy() {
  const fromEnv = process.env.LAOLAO_PYTHON_CAMERA;
  if (fromEnv && fs.existsSync(fromEnv)) return fromEnv;
  try {
    const fromSettings = readSettings().pythonCamera;
    if (fromSettings && fs.existsSync(fromSettings)) return fromSettings;
  } catch {}
  return VENV_PY;
}

// ── Settings (<userData>/settings.json) ────────────────────────
function settingsFile() { return path.join(app.getPath('userData'), 'settings.json'); }

function readSettings() {
  try { return JSON.parse(fs.readFileSync(settingsFile(), 'utf8')); } catch { return {}; }
}

function writeSettings(patch) {
  const merged = { ...readSettings(), ...patch };
  try {
    fs.mkdirSync(path.dirname(settingsFile()), { recursive: true });
    fs.writeFileSync(settingsFile(), JSON.stringify(merged, null, 2));
  } catch (e) { console.error('[settings] write failed:', e.message); }
}

// Returns the resolved root (packaged), or null if the user chose to quit.
async function ensureRoot() {
  if (!app.isPackaged) return ROOT;

  const candidates = [
    process.env.LAOLAO_ROOT,
    readSettings().root,
    path.join(app.getPath('home'), 'code', 'Laolao'),
  ];
  for (const c of candidates) if (rootValid(c)) return c;

  // Nothing valid — explain and let the user point us at the checkout.
  for (;;) {
    const { response } = await dialog.showMessageBox({
      type: 'error',
      title: 'Laolao Setup Needed',
      message: 'Laolao could not find its caption engine',
      detail:
        'Laolao runs a local Python engine from a repo folder containing ' +
        'server.py and a venv/. Expected at ~/code/Laolao.\n\n' +
        'To fix:\n' +
        '  1. git clone https://github.com/BensonRen/Laolao ~/code/Laolao\n' +
        '  2. cd ~/code/Laolao && ./setup.sh\n\n' +
        'If it lives elsewhere, click "Choose Folder…" (or set LAOLAO_ROOT).',
      buttons: ['Choose Folder…', 'Quit'],
      defaultId: 0,
      cancelId: 1,
    });
    if (response === 1) return null;

    const pick = await dialog.showOpenDialog({ properties: ['openDirectory'] });
    if (pick.canceled || !pick.filePaths[0]) continue;
    if (rootValid(pick.filePaths[0])) {
      writeSettings({ root: pick.filePaths[0] });
      return pick.filePaths[0];
    }
    await dialog.showMessageBox({
      type: 'warning',
      message: 'That folder is missing server.py or venv/',
      detail: 'Pick the Laolao repo folder after running setup.sh inside it.',
      buttons: ['OK'],
    });
  }
}

// ── Camera-pipeline diagnostics ────────────────────────────────
// Everything about the camera path — output-window getUserMedia, capturePage
// sizes, black-frame detection, IPC frames to the control window, and the
// renderer's own diag lines — is timestamped into one file so a "black on
// open" report can be traced end to end. Truncated fresh each launch.
const DEBUG_LOG = path.join(app.getPath('home'), 'laolao-camera-debug.log');
try { fs.writeFileSync(DEBUG_LOG, ''); } catch {}

function diag(line) {
  const stamp = new Date().toISOString().slice(11, 23);
  const msg = `${stamp}  ${line}\n`;
  try { fs.appendFileSync(DEBUG_LOG, msg); } catch {}
  process.stderr.write(`[diag] ${msg}`);
}

// True if a nativeImage is (near) pure black — sample the BGRA bitmap sparsely.
function imageIsBlack(img) {
  try {
    const buf = img.getBitmap();           // BGRA, 4 bytes/pixel
    if (!buf || !buf.length) return true;
    let checked = 0, lit = 0;
    for (let i = 0; i < buf.length; i += 4 * 997) {   // sparse stride, prime
      checked++;
      if (buf[i] > 8 || buf[i + 1] > 8 || buf[i + 2] > 8) lit++;
    }
    return checked > 0 && lit === 0;
  } catch { return false; }
}

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

// CLSID of OBS's DirectShow virtual-camera filter.
const OBS_VCAM_CLSID = '{A3FCE0F5-3493-419F-958A-ABA1250EC20B}';

function regQueryDefault(key, view = '') {
  try {
    const out = execSync(`reg query "${key}" /ve ${view}`.trim(),
                         { encoding: 'utf8', timeout: 5000 });
    const m = out.match(/REG_SZ\s+(.+)/);
    return m ? m[1].trim() : null;
  } catch { return null; }
}

// Walk a candidate ladder instead of trusting one registry key. A portable /
// zip OBS registers its virtual camera without ever writing HKLM\SOFTWARE\OBS
// Studio, so registry-only detection reports "OBS not found" on a machine
// where the camera is installed and working.
function findObsWinBin() {
  const candidates = [];
  const addRoot = (root) => {
    if (!root) return;
    // 64bit for a normal x64 install; an ARM64 distribution uses a different
    // bin dir but still ships the x64 DirectShow filter.
    for (const sub of [['bin', '64bit'], ['bin', 'ARM64'], ['bin']]) {
      candidates.push(path.join(root, ...sub));
    }
  };

  // Explicit overrides win — the only workable answer for portable installs.
  if (process.env.LAOLAO_OBS_BIN) candidates.push(process.env.LAOLAO_OBS_BIN);
  try { if (readSettings().obsBin) candidates.push(readSettings().obsBin); } catch {}

  // Installer-based install.
  addRoot(regQueryDefault('HKLM\\SOFTWARE\\OBS Studio'));

  // The filter itself. This is the trace that actually matters: if the CLSID
  // resolves to a DLL on disk, the virtual camera is installed no matter how
  // OBS got there. Path shape:
  //   <obsroot>\data\obs-plugins\win-dshow\obs-virtualcam-module64.dll
  //
  // Query every key path AND every view flag, because registry redirection on
  // Windows ARM64 is genuinely counter-intuitive: `reg query ... /reg:64`
  // resolves the ARM64-*native* view, which is empty here (there is no ARM64
  // build of the filter), while the x64 registration lives in a view reg.exe
  // only reaches via /reg:32 — and PowerShell's HKLM: provider shows yet
  // another merge. Trusting any single view yields a confident false negative.
  for (const keyBase of ['', '\\WOW6432Node']) {
    const key = `HKLM\\SOFTWARE\\Classes${keyBase}\\CLSID\\${OBS_VCAM_CLSID}\\InprocServer32`;
    for (const view of ['', '/reg:64', '/reg:32']) {
      const dll = regQueryDefault(key, view);
      if (dll && fs.existsSync(dll)) {
        candidates.push(path.dirname(dll));
        addRoot(path.resolve(path.dirname(dll), '..', '..', '..'));
      }
    }
  }

  // Conventional locations.
  addRoot('C:\\Program Files\\obs-studio');
  addRoot('C:\\Program Files (x86)\\obs-studio');

  for (const c of candidates) {
    if (c && fs.existsSync(c)) return c;
  }
  return null;
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

function spawnPython(script, args = [], { exe = null } = {}) {
  // On Windows, add the OBS bin dir to PATH so obs-virtualcam-module64.dll
  // can find its dependencies (obs.dll etc.) when pyvirtualcam loads it.
  const env = { ...process.env };
  if (IS_WIN && obsWinBin) {
    env.PATH = `${obsWinBin};${env.PATH || ''}`;
  }

  const proc = spawn(exe || VENV_PY, [script, ...args], {
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
// Crash-loop cutoff: 5 consecutive exits with <10s uptime stop the restarts
// and surface a dialog (Retry / Continue) instead of churning forever.
// All (re)starts flow through schedule()/start() with a single pending timer,
// so only one child can ever be alive per supervisor.
function supervise(name, spawnFn, { friendlyName = name, failHint = '' } = {}) {
  const FAST_CRASH_MS    = 10_000;
  const MAX_FAST_CRASHES = 5;

  let proc         = null;
  let alive        = false;   // spawn 'error' and 'exit' can both fire — dedupe
  let backoffMs    = 1000;
  let startedAt    = 0;
  let intentional  = false;   // restart() in flight — not a crash
  let fastCrashes  = 0;       // consecutive exits with uptime < FAST_CRASH_MS
  let stopped      = false;   // crash-loop cutoff hit (until user retries)
  let pendingTimer = null;    // the one scheduled start

  function schedule(delay) {
    if (isQuitting || stopped) return;
    clearTimeout(pendingTimer);
    pendingTimer = setTimeout(start, delay);
  }

  function start() {
    clearTimeout(pendingTimer);
    pendingTimer = null;
    if (isQuitting || stopped || alive) return;
    startedAt = Date.now();
    proc = spawnFn();
    alive = true;

    const onDown = code => {
      if (!alive) return;
      alive = false;
      console.log(`[${name}] exited (${code})`);
      if (isQuitting || stopped) return;

      const uptime = Date.now() - startedAt;
      if (intentional) {
        // Reconfigure restart (e.g. new camera dimensions) — no backoff
        // penalty; brief pause so the OS releases the device cleanly.
        intentional = false;
        backoffMs = 1000;
        fastCrashes = 0;
        console.log(`[${name}] restarting (reconfigure)…`);
        schedule(300);
        return;
      }

      fastCrashes = uptime < FAST_CRASH_MS ? fastCrashes + 1 : 0;
      if (uptime > 60_000) backoffMs = 1000;

      if (fastCrashes >= MAX_FAST_CRASHES) {
        stopped = true;
        console.error(`[${name}] crashed ${fastCrashes}× in a row — giving up.`);
        showCrashDialog();
        return;
      }

      const delay = backoffMs;
      backoffMs = Math.min(backoffMs * 2, 15_000);
      console.log(`[${name}] restarting in ${delay} ms…`);
      schedule(delay);
    };

    proc.on('exit', onDown);
    // spawn failure (e.g. missing interpreter) emits 'error' with no 'exit'
    proc.on('error', err => {
      console.error(`[${name}] spawn error: ${err.message}`);
      onDown(-1);
    });
  }

  function showCrashDialog() {
    if (isQuitting) return;
    dialog.showMessageBox({
      type: 'error',
      title: 'Laolao',
      message: `${friendlyName} failed repeatedly`,
      detail: failHint,
      buttons: ['Retry', 'Continue'],
      defaultId: 0,
      cancelId: 1,
    }).then(({ response }) => {
      if (isQuitting || response !== 0) return;
      stopped = false;
      fastCrashes = 0;
      backoffMs = 1000;
      start();
    });
  }

  start();
  return {
    kill() {
      stopped = true;
      clearTimeout(pendingTimer);
      pendingTimer = null;
      try { proc?.kill(); } catch {}
    },
    // Kill + respawn with the CURRENT config (spawnFn reads live variables).
    restart() {
      if (isQuitting) return;
      stopped = false;
      fastCrashes = 0;
      clearTimeout(pendingTimer);
      pendingTimer = null;
      if (!alive) { start(); return; }
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
    console.log('[vcam] socket connected — frames now reach the virtual camera');
    vcSocket = sock;
    captureLoop();
  });
  sock.on('error', () => {});
  sock.on('close', () => {
    vcSocket = null;
    if (!isQuitting) setTimeout(connectVcSocket, 1000);
  });
}

// The capture loop is the single frame source for BOTH sinks: the virtual
// camera socket (30 fps, when connected) and the control window's self-view
// preview (every 2nd frame ≈ 15 fps, over IPC). It starts as soon as the
// output window loads — the preview must work even before/without the vcam.
let captureRunning = false;
let frameCount     = 0;
let capturedEver   = false;
let litEver        = false;
function captureLoop() {
  if (captureRunning) { diag('captureLoop: already running, skip'); return; }
  captureRunning = true;
  diag('captureLoop: started');

  async function tick() {
    if (!outputWindow || outputWindow.isDestroyed()) {
      captureRunning = false;
      diag('captureLoop: output window gone, stopping');
      return;
    }
    try {
      const img  = await outputWindow.webContents.capturePage();
      const size = img.getSize();
      const targetAspect = CAM_W / CAM_H;
      if (!capturedEver) {
        capturedEver = true;
        diag(`capturePage: first frame ${size.width}x${size.height}`);
      }
      if (size.width === 0 || size.height === 0) {
        if (frameCount % 30 === 0) diag('capturePage: ZERO-SIZE frame (window not painting?)');
      }
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
        frameCount++;

        // Black-frame telemetry: log the transition to first lit frame, and a
        // heartbeat every ~5s (150 frames @30fps) with black/lit + sink state.
        const black = imageIsBlack(scaled);
        if (!black && !litEver) {
          litEver = true;
          diag(`capturePage: FIRST NON-BLACK frame at frame #${frameCount}`);
        }
        if (frameCount % 150 === 0) {
          diag(`capturePage: frame #${frameCount} ${size.width}x${size.height} `
            + `black=${black} vcSocket=${!!vcSocket} `
            + `control=${!!(mainWindow && !mainWindow.isDestroyed())}`);
        }

        if (vcSocket) {
          const header = Buffer.allocUnsafe(4);
          header.writeUInt32BE(jpeg.length);
          vcSocket.write(header);
          vcSocket.write(jpeg);
        }
        // Self-view preview in the control window (half rate is plenty).
        if (frameCount % 2 === 0 && mainWindow && !mainWindow.isDestroyed()) {
          mainWindow.webContents.send('output-frame', jpeg);
        }
      }
    } catch (e) {
      // Previously swallowed silently — a capturePage exception here looks
      // identical to a black screen. Log it (throttled).
      if (frameCount % 30 === 0) diag(`capturePage: ERROR ${e && e.message}`);
    }

    setTimeout(tick, 1000 / CAM_FPS);
  }

  tick();
}

// ── IPC handlers ──────────────────────────────────────────────
ipcMain.on('diag', (_e, msg) => diag(msg));

ipcMain.handle('open-mic-settings', () => {
  const url = IS_MAC
    ? 'x-apple.systempreferences:com.apple.preference.security?Privacy_Microphone'
    : 'ms-settings:privacy-microphone';
  shell.openExternal(url);
});

// The output window's camera watchdog ran out of retries — relay to the
// control window so it can reopen the picker and ask the user.
ipcMain.handle('camera-failed', () => {
  if (mainWindow && !mainWindow.isDestroyed()) {
    mainWindow.webContents.send('camera-failed');
  }
});


// ── App lifecycle ──────────────────────────────────────────────
app.whenReady().then(async () => {
  // Auto-grant camera and microphone without a browser-style prompt
  session.defaultSession.setPermissionRequestHandler((_wc, permission, cb) => {
    cb(['camera', 'microphone', 'media', 'mediaKeySystem'].includes(permission));
  });

  // Trigger the macOS camera + microphone permission dialogs from the MAIN
  // process. The camera is opened in the transparent output window, where a
  // getUserMedia-triggered TCC prompt can't surface — so if we don't request
  // camera access here, macOS opens the device but delivers ZERO frames (the
  // "in use" light comes on, capture is black). Requesting both up front makes
  // the OS prompt appear and, once granted, frames flow.
  if (IS_MAC && systemPreferences.askForMediaAccess) {
    const cam = systemPreferences.getMediaAccessStatus('camera');
    const mic = systemPreferences.getMediaAccessStatus('microphone');
    console.log(`[perm] camera=${cam} microphone=${mic}`);
    await systemPreferences.askForMediaAccess('camera');
    await systemPreferences.askForMediaAccess('microphone');
    console.log('[perm] after request: '
      + `camera=${systemPreferences.getMediaAccessStatus('camera')} `
      + `microphone=${systemPreferences.getMediaAccessStatus('microphone')}`);
  }

  // Packaged apps must locate a valid repo checkout (server.py + venv)
  // before anything can spawn; dev resolves to __dirname/.. with no checks.
  const resolvedRoot = await ensureRoot();
  if (!resolvedRoot) { app.quit(); return; }
  applyRoot(resolvedRoot);
  console.log(`[startup] engine root: ${ROOT}`);

  // NOTE: checkObs() deliberately runs *after* the windows exist (below).
  // It awaits a modal dialog when OBS is missing, and awaiting it here meant a
  // first launch on a machine without OBS produced zero windows — just an
  // orphan dialog — which reads as "the app didn't start".

  // The renderer streams mic audio over WebSocket on every platform, so
  // Python never opens the mic itself (unified path; Chromium provides AEC).
  serverSup = supervise('server', () => spawnPython(SERVER_PY, ['--no-mic']), {
    friendlyName: 'Caption engine',
    failHint:
      'The Python caption server keeps crashing. Check that setup completed ' +
      `(run setup.sh in ${ROOT}) and that ${path.join(ROOT, 'venv')} is intact.`,
  });

  // Control window: toolbar + mirrored self-view. Never captured.
  // Electron centers new windows on whichever display has the mouse; on a
  // multi-display setup that can put the window off the screen the user is
  // looking at (reported as "the app opens to a black screen"). Pin it to
  // the primary display instead.
  const workArea = screen.getPrimaryDisplay().workArea;
  mainWindow = new BrowserWindow({
    width: 1280,
    height: 720,
    x: workArea.x + Math.max(0, Math.round((workArea.width  - 1280) / 2)),
    y: workArea.y + Math.max(0, Math.round((workArea.height - 720) / 2)),
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

  // Output window: chrome-free, exactly CAM_W×CAM_H — the virtual-camera frame.
  //
  // It must NOT be `show:false`: a hidden BrowserWindow reports
  // document.visibilityState 'hidden', and Chromium MUTES getUserMedia video
  // tracks on hidden documents (the camera opens — the OS "in use" light comes
  // on — but zero frames arrive, so capturePage is black). Instead we keep it
  // genuinely visible to Chromium but invisible to the user: fully transparent
  // (opacity 0), click-through, always-on-top so no opaque window occludes it
  // (occlusion would also mark it hidden). capturePage reads the page's own
  // render buffer regardless of window opacity, so captured frames are opaque.
  outputWindow = new BrowserWindow({
    width: CAM_W,
    height: CAM_H,
    x: workArea.x,
    y: workArea.y,
    useContentSize: true,
    show: true,
    opacity: 0,             // invisible to the user; still "visible" to Chromium
    focusable: false,
    resizable: false,
    skipTaskbar: true,
    alwaysOnTop: true,      // never occluded → Chromium keeps its media alive
    hasShadow: false,
    webPreferences: {
      preload: path.join(__dirname, 'preload.js'),
      contextIsolation: true,
      backgroundThrottling: false,
    },
  });
  outputWindow.setIgnoreMouseEvents(true);

  // Chromium CLAMPS a new window to the display work area at construction, so
  // on a desktop whose DIP work area is smaller than the camera frame (e.g.
  // 1024x768 on this Snapdragon at its default scaling) the output window is
  // born 1008x720 instead of 1280x720 — despite useContentSize and the width
  // requested above. capturePage() then returns 1008x720, and the aspect-safe
  // center-crop below trims the top and bottom ~21% to restore 16:9. The
  // caption bar lives at the bottom, so the captions are silently cropped off
  // the frame the far end receives — with black=false and a correct 1280x720
  // arriving at the sink, so every diagnostic still looks healthy.
  //
  // setContentSize() after construction is not subject to that clamp.
  // Verified against 8 window variants: offscreen, frame:false and minWidth
  // all still clamp; this is the one that holds.
  outputWindow.setContentSize(CAM_W, CAM_H);
  const got = outputWindow.getContentSize();
  if (got[0] !== CAM_W || got[1] !== CAM_H) {
    diag(`output window is ${got[0]}x${got[1]}, wanted ${CAM_W}x${CAM_H} — `
       + 'captions may be cropped from the virtual camera frame');
  }

  outputWindow.loadFile(OVERLAY, { query: { output: '1' } });
  outputWindow.webContents.setBackgroundThrottling(false);
  outputWindow.on('closed', () => { outputWindow = null; });
  // Frames flow to the control-window preview immediately; the vcam sink
  // joins in whenever its socket connects.
  outputWindow.webContents.once('did-finish-load', () => captureLoop());

  // Readiness (informational only — neither call gates the pipeline).
  // 300s, not 120s: the first ever load of a Qualcomm QNN context compiles it
  // for the device and took 117s measured on Snapdragon X2 with
  // large-v3-turbo (7s once cached). A 120s budget left three seconds of
  // headroom on the one run where a first-time user is already waiting.
  waitForPort(WS_PORT, { timeoutMs: 300_000, label: 'caption server' });

  // Always connect the frame sink, whether or not WE spawned virtual_cam.py.
  // This used to be gated on obsAvailable, which meant a perfectly healthy
  // sink already listening on :8766 was ignored outright. That matters now
  // that the sink is deliberately an out-of-process, differently-architected
  // helper (see cameraPy()) which may be started by something other than us.
  // The connect loop retries quietly forever, so a slow or restarting sink is
  // picked up whenever it binds.
  waitForPort(VCAM_PORT, { timeoutMs: 60_000, label: 'virtual camera' })
    .finally(() => connectVcSocket());

  // Now that there is a window to own it, find OBS and start our own sink.
  const obsAvailable = await checkObs();
  if (obsAvailable) {
    vcamSup = supervise('virtual_cam', () =>
      spawnPython(VCAM_PY, [String(CAM_W), String(CAM_H), String(CAM_FPS)],
                  { exe: cameraPy() }), {
      friendlyName: 'Virtual camera',
      failHint:
        'The virtual camera helper keeps crashing. Is OBS Studio 28 or newer ' +
        'installed? Its Camera Extension provides the driver Laolao uses.',
    });
  } else {
    console.log('[vcam] OBS not found — captions-only unless a sink is already on :8766');
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
