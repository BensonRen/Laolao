#!/usr/bin/env python3
"""
Laolao Snapdragon ARM64 — acceptance harness for criteria A1..A10.

This is the grading instrument for the port. It is deliberately hostile: a
criterion only passes when this script observes it, with evidence captured in
the report. "Should work" is not a result here.

Usage
-----
    python docs/snapdragon/acceptance/check.py                # run everything
    python docs/snapdragon/acceptance/check.py --only A1 A2   # subset
    python docs/snapdragon/acceptance/check.py --json out.json

Run it with the interpreter of the lane under test:
    .venv-arm64\\Scripts\\python  ...   (native ONNX lane)
    .venv-x64\\Scripts\\python    ...   (Prism-emulated lane)

Exit code is 0 only when no criterion FAILED. SKIP/BLOCKED do not fail the run
(they mean "not yet reachable"), but they are reported loudly and never count
as success.
"""

from __future__ import annotations

import argparse
import json
import platform
import subprocess
import sys
import time
import wave
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Callable

REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO))

PASS, FAIL, SKIP, BLOCKED = "PASS", "FAIL", "SKIP", "BLOCKED"


@dataclass
class Result:
    id: str
    title: str
    status: str = SKIP
    evidence: str = ""
    detail: dict = field(default_factory=dict)


CHECKS: list[tuple[str, str, Callable[[], Result]]] = []


def check(cid: str, title: str):
    def deco(fn):
        CHECKS.append((cid, title, fn))
        return fn
    return deco


def _r(cid, title, status, evidence, **detail) -> Result:
    return Result(cid, title, status, evidence, detail)


# ─────────────────────────────────────────────────────────────────────
# Fixtures
# ─────────────────────────────────────────────────────────────────────

FIXTURES = REPO / "tests" / "fixtures"


def find_fixture(*name_hints: str) -> Path | None:
    """Return the first .wav under tests/fixtures matching any hint."""
    if not FIXTURES.is_dir():
        return None
    wavs = sorted(FIXTURES.rglob("*.wav"))
    for hint in name_hints:
        for w in wavs:
            if hint.lower() in w.name.lower():
                return w
    return wavs[0] if wavs else None


def read_wav_int16(path: Path):
    """Load a mono 16 kHz int16 numpy array, resampling naively if needed."""
    import numpy as np
    with wave.open(str(path), "rb") as wf:
        ch, width, rate, n = wf.getnchannels(), wf.getsampwidth(), wf.getframerate(), wf.getnframes()
        raw = wf.readframes(n)
    if width != 2:
        raise ValueError(f"{path.name}: expected 16-bit PCM, got {width*8}-bit")
    a = np.frombuffer(raw, dtype=np.int16)
    if ch > 1:
        a = a.reshape(-1, ch).mean(axis=1).astype(np.int16)
    if rate != 16000:
        # nearest-neighbour resample; adequate for a smoke check
        idx = (np.arange(int(len(a) * 16000 / rate)) * rate / 16000).astype(np.int64)
        a = a[np.clip(idx, 0, len(a) - 1)]
    return a


def norm(s: str) -> str:
    """Normalise text for comparison: lowercase, strip punctuation/space."""
    import re
    return re.sub(r"[\s\W_]+", "", s.lower())


# ─────────────────────────────────────────────────────────────────────
# A1 — server boots on this interpreter with a real backend, no ctranslate2
# ─────────────────────────────────────────────────────────────────────

@check("A1", "server.py boots with a backend and no ctranslate2 on ARM64")
def a1():
    arch = platform.machine()
    try:
        from backends import get_backend
    except Exception as e:
        return _r("A1", a1.title, FAIL, f"cannot import backends: {e!r}", arch=arch)

    cfg = json.loads((REPO / "config.json").read_text()) if (REPO / "config.json").exists() else {}
    try:
        be = get_backend(cfg)
    except Exception as e:
        return _r("A1", a1.title, FAIL, f"get_backend() raised: {e!r}", arch=arch)

    ct = "ctranslate2" in sys.modules
    # On native ARM64 ctranslate2 must NOT be what we loaded. Under the
    # emulated x64 lane it legitimately is, so only flag the arch mismatch.
    if arch.lower() in ("arm64", "aarch64") and ct:
        return _r("A1", a1.title, FAIL,
                  "ctranslate2 imported on ARM64 — impossible, check the lane",
                  backend=be.name, arch=arch)
    return _r("A1", a1.title, PASS,
              f"backend={be.name} arch={arch} ctranslate2_loaded={ct}",
              backend=be.name, arch=arch, ctranslate2=ct)


# ─────────────────────────────────────────────────────────────────────
# A2 — known WAV transcribes to the expected text
# ─────────────────────────────────────────────────────────────────────

@check("A2", "known English WAV transcribes to expected text")
def a2():
    wav = find_fixture("english", "en_", "jfk", "hello")
    if wav is None:
        return _r("A2", a2.title, BLOCKED,
                  f"no fixture found under {FIXTURES} — run tests/generate_test_audio.py")
    expect_file = wav.with_suffix(".txt")
    if not expect_file.exists():
        return _r("A2", a2.title, BLOCKED,
                  f"no ground truth {expect_file.name} beside {wav.name}; "
                  "a transcript with nothing to compare against proves nothing")

    from backends import get_backend
    cfg = json.loads((REPO / "config.json").read_text())
    cfg["language"] = "en"
    be = get_backend(cfg)
    audio = read_wav_int16(wav)
    t0 = time.perf_counter()
    got = be.transcribe(audio, "en")
    dt = time.perf_counter() - t0

    expect = expect_file.read_text(encoding="utf-8").strip()
    ok = norm(expect) in norm(got) or norm(got) in norm(expect)
    return _r("A2", a2.title, PASS if ok else FAIL,
              f"expected≈{expect!r} got={got!r} ({dt:.2f}s)",
              expected=expect, got=got, seconds=round(dt, 3), wav=str(wav))


# ─────────────────────────────────────────────────────────────────────
# A3 — Mandarin transcribes, output is Simplified
# ─────────────────────────────────────────────────────────────────────

@check("A3", "Mandarin WAV transcribes and output is Simplified Chinese")
def a3():
    wav = find_fixture("chinese", "mandarin", "zh_", "zh-")
    if wav is None:
        return _r("A3", a3.title, BLOCKED, "no Mandarin fixture found")

    from backends import get_backend
    cfg = json.loads((REPO / "config.json").read_text())
    cfg["language"] = "zh"
    be = get_backend(cfg)
    got = be.transcribe(read_wav_int16(wav), "zh")

    if not got.strip():
        return _r("A3", a3.title, FAIL, "empty transcript for Mandarin fixture", wav=str(wav))

    has_cjk = any("一" <= c <= "鿿" for c in got)
    # Traditional-only characters that OpenCC t2s should have removed
    trad_markers = set("繁體東車馬語說們個過還發沒學國會來時對開關")
    trad_hits = sorted(set(got) & trad_markers)
    status = PASS if (has_cjk and not trad_hits) else (FAIL if not has_cjk else SKIP)
    ev = f"got={got!r} cjk={has_cjk} traditional_chars={trad_hits}"
    if trad_hits:
        ev += "  (t2s may not be applied — check config t2s=true)"
    return _r("A3", a3.title, status, ev, got=got, traditional=trad_hits, wav=str(wav))


# ─────────────────────────────────────────────────────────────────────
# A4 — latency budget
# ─────────────────────────────────────────────────────────────────────

@check("A4", "latency: partial < 1.0s, final < 2.0s")
def a4():
    import numpy as np
    from backends import get_backend
    cfg = json.loads((REPO / "config.json").read_text())
    be = get_backend(cfg)

    wav = find_fixture("english", "chinese", "zh_", "en_")
    if wav is not None:
        full = read_wav_int16(wav)
    else:
        full = (np.random.randn(16000 * 5) * 800).astype(np.int16)

    partial_audio = full[: 16000 * 2]          # 2s — a partial pass
    final_audio = full[: 16000 * 5]            # 5s — a full utterance

    be.transcribe(partial_audio[:16000], None)  # warm up, exclude first-pass cost

    t0 = time.perf_counter(); be.transcribe(partial_audio, None); p = time.perf_counter() - t0
    t0 = time.perf_counter(); be.transcribe(final_audio, None);   f = time.perf_counter() - t0

    ok = p < 1.0 and f < 2.0
    return _r("A4", a4.title, PASS if ok else FAIL,
              f"partial(2s audio)={p*1000:.0f}ms (budget 1000ms), "
              f"final(5s audio)={f*1000:.0f}ms (budget 2000ms)",
              partial_ms=round(p * 1000), final_ms=round(f * 1000),
              backend=be.name, synthetic=wav is None)


# ─────────────────────────────────────────────────────────────────────
# A5 — VAD discriminates speech from silence
# ─────────────────────────────────────────────────────────────────────

@check("A5", "VAD gates speech vs silence")
def a5():
    """Feed *sequences*, not single chunks, and use *real* speech.

    Two traps this check exists to avoid:

    - EnergyVAD applies a 3-frame majority filter, so it cannot report speech
      until two consecutive active chunks have arrived. A one-chunk probe
      always returns False and looks like a broken VAD.
    - Silero is a neural *speech* detector. Loud random noise is correctly
      rejected by it, so synthetic noise would fail a working Silero while
      passing a working EnergyVAD. Only real speech tests both.
    """
    import numpy as np
    from vad import get_vad
    cfg = json.loads((REPO / "config.json").read_text())
    v = get_vad(cfg)
    name = type(v).__name__
    n = int(16000 * (cfg.get("chunk_ms", 300) / 1000))

    wav = find_fixture("english_speech", "chinese_speech", "jfk", "english", "fleurs")
    if wav is None:
        return _r("A5", a5.title, BLOCKED,
                  f"vad={name} but no speech fixture available; synthetic noise "
                  "cannot validate a neural VAD", vad=name)

    src = read_wav_int16(wav)
    # Anchor on the loudest window so we test speech, not leading silence.
    if len(src) > n * 8:
        rms = [(float(np.sqrt(np.mean((src[i:i + n].astype(np.float32) / 32768) ** 2))), i)
               for i in range(0, len(src) - n, n)]
        start = max(rms)[1]
    else:
        start = 0

    reps = 6
    v.reset()
    sil = [bool(v.is_speech(np.zeros(n, dtype=np.int16))) for _ in range(reps)]
    v.reset()
    spk = []
    for k in range(reps):
        c = src[start + k * n: start + (k + 1) * n]
        if len(c) < n:
            c = np.pad(c, (0, n - len(c)))
        spk.append(bool(v.is_speech(c)))

    detect_at = next((i + 1 for i, s in enumerate(spk) if s), None)
    ok = (not any(sil)) and any(spk)
    ev = (f"vad={name} fixture={wav.name}  silence={sil} (want all False)  "
          f"speech={spk} (want some True)")
    if detect_at:
        ev += f"  detected after {detect_at} chunk(s) = {detect_at * n / 16000:.2f}s"
    return _r("A5", a5.title, PASS if ok else FAIL, ev,
              vad=name, silence=sil, speech=spk, detect_chunk=detect_at,
              fixture=str(wav))


# ─────────────────────────────────────────────────────────────────────
# A6 — mic → WebSocket → caption round trip
# ─────────────────────────────────────────────────────────────────────

@check("A6", "PCM over WebSocket yields a caption message")
def a6():
    """Streams a known WAV at the server as binary PCM and waits for a caption.

    Requires server.py already running with --no-mic on ws_port.
    """
    import asyncio
    try:
        import websockets
    except Exception as e:
        return _r("A6", a6.title, BLOCKED, f"websockets not installed: {e!r}")

    cfg = json.loads((REPO / "config.json").read_text())
    port = cfg.get("ws_port", 8765)
    wav = find_fixture("english", "chinese")
    if wav is None:
        return _r("A6", a6.title, BLOCKED, "no fixture to stream")
    audio = read_wav_int16(wav)

    async def run():
        uri = f"ws://127.0.0.1:{port}"
        async with websockets.connect(uri, open_timeout=5) as ws:
            chunk = 16000 // 2  # 500 ms
            async def feed():
                for i in range(0, len(audio), chunk):
                    await ws.send(audio[i:i + chunk].tobytes())
                    await asyncio.sleep(0.15)
            task = asyncio.create_task(feed())
            captions = []
            try:
                deadline = time.time() + 30
                while time.time() < deadline:
                    msg = await asyncio.wait_for(ws.recv(), timeout=10)
                    if isinstance(msg, bytes):
                        continue
                    d = json.loads(msg)
                    if d.get("type") in ("partial", "final") and d.get("text", "").strip():
                        captions.append(d)
                        if d["type"] == "final":
                            break
            finally:
                task.cancel()
            return captions

    try:
        caps = asyncio.run(run())
    except (OSError, ConnectionRefusedError) as e:
        return _r("A6", a6.title, BLOCKED,
                  f"no server on ws://127.0.0.1:{port} ({e!r}) — "
                  "start server.py --no-mic first")
    except Exception as e:
        return _r("A6", a6.title, FAIL, f"websocket round trip failed: {e!r}")

    if not caps:
        return _r("A6", a6.title, FAIL, "connected and streamed PCM but no caption arrived")
    return _r("A6", a6.title, PASS,
              f"{len(caps)} caption message(s); last={caps[-1]!r}", captions=caps)


# ─────────────────────────────────────────────────────────────────────
# A7 / A8 — virtual camera exists and enumerates
# ─────────────────────────────────────────────────────────────────────

def _ps(cmd: str) -> list[str]:
    try:
        out = subprocess.run(["powershell", "-NoProfile", "-Command", cmd],
                             capture_output=True, text=True, timeout=60)
        return [l.strip() for l in out.stdout.splitlines() if l.strip()]
    except Exception:
        return []


def _pnp_cameras() -> list[str]:
    """Hardware cameras, as the PnP subsystem sees them."""
    return _ps(
        "Get-CimInstance Win32_PnPEntity | "
        "Where-Object { $_.PNPClass -eq 'Camera' -or $_.PNPClass -eq 'Image' -or "
        "$_.Service -eq 'usbvideo' } | Select-Object -ExpandProperty Name"
    )


# CLSID_VideoInputDeviceCategory — where DirectShow capture sources register.
DSHOW_CAT = "{860BB310-5D01-11D0-BD3B-00A0C911CE86}"


def _dshow_filters() -> list[dict]:
    """Software DirectShow capture filters (e.g. OBS Virtual Camera).

    These are COM servers, NOT PnP devices — a virtual camera is invisible to
    Win32_PnPEntity no matter how correctly it is installed. Enumerating the
    wrong namespace here produces a confident false negative.
    """
    # NB: build this without f-strings — the PowerShell body is full of braces,
    # and an earlier f-string version silently produced a path expression that
    # PowerShell never evaluated, so every DLL came back empty.
    template = (
        "$root='%ROOT%';"
        "$c=Join-Path $root '%CAT%\\Instance';"
        "if (Test-Path $c) { Get-ChildItem $c | ForEach-Object {"
        "  $n=$_.PSChildName;"
        "  $f=(Get-ItemProperty $_.PSPath).FriendlyName;"
        "  $ip=Join-Path $root ($n + '\\InprocServer32');"
        "  $s='';"
        "  if (Test-Path $ip) { $s=(Get-ItemProperty $ip).'(default)' }"
        "  \"$n|$f|$s\""
        "} }"
    )
    found: dict[str, dict] = {}
    for view, root in (("64", "HKLM:\\SOFTWARE\\Classes\\CLSID"),
                       ("32", "HKLM:\\SOFTWARE\\Classes\\WOW6432Node\\CLSID")):
        cmd = template.replace("%ROOT%", root).replace("%CAT%", DSHOW_CAT)
        for line in _ps(cmd):
            parts = line.split("|")
            if len(parts) < 2:
                continue
            clsid, name, dll = parts[0], parts[1], (parts[2] if len(parts) > 2 else "")
            e = found.setdefault(clsid, {"clsid": clsid, "name": name, "views": [], "dll": ""})
            e["views"].append(view)
            if dll and not e["dll"]:
                e["dll"] = dll
    return list(found.values())


_PE_MACHINES = {0x8664: "AMD64(x64)", 0x014C: "i386(x86)",
                0xAA64: "ARM64", 0x01C4: "ARMv7"}


def _pe_machine(path: str) -> str:
    """Read a PE file's machine type — decides who can load it in-process."""
    try:
        with open(path, "rb") as f:
            if f.read(2) != b"MZ":
                return "not-PE"
            f.seek(0x3C)
            off = int.from_bytes(f.read(4), "little")
            f.seek(off)
            if f.read(4) != b"PE\0\0":
                return "bad-PE"
            m = int.from_bytes(f.read(2), "little")
        return _PE_MACHINES.get(m, f"0x{m:04x}")
    except Exception as e:
        return f"unreadable ({e.__class__.__name__})"


def _is_virtual(name: str) -> bool:
    n = name.lower()
    return "obs" in n or "laolao" in n or "virtual" in n


@check("A7", "a Laolao/OBS virtual camera is registered")
def a7():
    hw = _pnp_cameras()
    filters = _dshow_filters()
    virt = [f for f in filters if _is_virtual(f["name"])]
    ev = (f"dshow_filters={[(f['name'], 'views=' + '/'.join(f['views'])) for f in filters]}  "
          f"pnp_hardware={hw}")
    if not virt:
        return _r("A7", a7.title, FAIL, "no virtual camera filter registered.  " + ev,
                  filters=filters, hardware=hw)
    return _r("A7", a7.title, PASS, ev, virtual=virt, filters=filters, hardware=hw)


@check("A8", "virtual camera is loadable by the call apps that must use it")
def a8():
    """Registration is not loadability.

    A DirectShow filter is loaded *in-process* by the consumer, so the filter
    DLL's machine type decides who can actually open it. On Windows ARM64 the
    registry entry is visible to native-ARM64 apps too, which then fail at
    CoCreateInstance — visible but unusable is a real and confusing outcome,
    so it is graded separately from A7.
    """
    virt = [f for f in _dshow_filters() if _is_virtual(f["name"])]
    if not virt:
        return _r("A8", a8.title, BLOCKED, "no virtual camera registered yet (see A7)")

    report, arm64_ok, x64_ok = [], False, False
    for f in virt:
        dll = (f.get("dll") or "").strip('"')
        mach = _pe_machine(dll) if dll else "no InprocServer32 recorded"
        report.append(f"{f['name']}: dll={dll or '?'} machine={mach} "
                      f"registry_views={'/'.join(f['views'])}")
        if mach == "ARM64":
            arm64_ok = True
        if mach == "AMD64(x64)":
            x64_ok = True

    # WeChat / Zoom on Windows-ARM64 are x64 images running under Prism.
    verdict = ("x64-emulated call apps CAN load it" if x64_ok
               else "NO x64 filter — emulated call apps cannot load it")
    native = ("native ARM64 producers can load it" if arm64_ok
              else "native ARM64 producers CANNOT load it (must drive the camera "
                   "from an emulated-x64 helper process or out-of-process OBS)")
    status = PASS if x64_ok else FAIL
    return _r("A8", a8.title, status,
              "; ".join(report) + f"  ->  {verdict}; {native}",
              filters=virt, x64_loadable=x64_ok, arm64_loadable=arm64_ok)


# ─────────────────────────────────────────────────────────────────────
# A9 / A10 — offline operation, one-click launch
# ─────────────────────────────────────────────────────────────────────

@check("A9", "runs fully offline after model download")
def a9():
    return _r("A9", a9.title, SKIP,
              "verify by disabling the network adapter and re-running A2/A6; "
              "must be demonstrated, not assumed")


@check("A10", "one-command launch for a non-technical user")
def a10():
    cands = [REPO / "run-arm64.bat", REPO / "Laolao-arm64.bat",
             REPO / "docs" / "snapdragon" / "launch.ps1", REPO / "run.bat"]
    found = [p for p in cands if p.exists()]
    if not found:
        return _r("A10", a10.title, FAIL, "no launcher script exists yet")
    return _r("A10", a10.title, SKIP if len(found) == 1 and found[0].name == "run.bat"
              else PASS,
              f"launchers present: {[p.name for p in found]} "
              "(run.bat alone is the x86 path, not an ARM64 one-click)",
              launchers=[str(p) for p in found])


# ─────────────────────────────────────────────────────────────────────

def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", nargs="*", help="criterion ids, e.g. A1 A2")
    ap.add_argument("--json", help="write results to this path")
    args = ap.parse_args()

    for _, title, fn in CHECKS:
        fn.title = title  # let checks reference their own title

    selected = [(c, t, f) for c, t, f in CHECKS
                if not args.only or c in {s.upper() for s in args.only}]

    print(f"Laolao ARM64 acceptance — python {platform.python_version()} "
          f"{platform.machine()} on {platform.system()}")
    print("=" * 78)

    results: list[Result] = []
    for cid, title, fn in selected:
        try:
            res = fn()
        except Exception as e:
            import traceback
            res = _r(cid, title, FAIL, f"harness exception: {e!r}",
                     traceback=traceback.format_exc()[-1500:])
        results.append(res)
        icon = {PASS: "PASS ", FAIL: "FAIL ", SKIP: "skip ", BLOCKED: "block"}[res.status]
        print(f"[{icon}] {res.id}  {res.title}")
        for line in (res.evidence or "").splitlines():
            print(f"          {line}")

    print("=" * 78)
    tally = {s: sum(1 for r in results if r.status == s) for s in (PASS, FAIL, SKIP, BLOCKED)}
    print(f"PASS={tally[PASS]}  FAIL={tally[FAIL]}  "
          f"SKIP={tally[SKIP]}  BLOCKED={tally[BLOCKED]}")

    if args.json:
        Path(args.json).write_text(
            json.dumps([asdict(r) for r in results], indent=2, ensure_ascii=False),
            encoding="utf-8")
        print(f"wrote {args.json}")

    return 1 if tally[FAIL] else 0


if __name__ == "__main__":
    sys.exit(main())
