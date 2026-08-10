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


def _levenshtein(a: str, b: str) -> int:
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb)))
        prev = cur
    return prev[-1]


def cer(ref: str, hyp: str) -> float:
    """Character error rate on normalised text.

    Substring containment is NOT an accuracy test: `norm(got) in norm(expect)`
    means a transcript of the single word "Hello" scores a pass against
    "Hello grandma, I miss you very much." A2/A3 used exactly that rule, so a
    backend that emitted one correct word out of eight was graded PASS. An edit
    distance cannot be fooled that way.
    """
    r, h = norm(ref), norm(hyp)
    if not r:
        return 0.0 if not h else 1.0
    return _levenshtein(r, h) / len(r)


def traditional_chars(text: str) -> list[str]:
    """Characters OpenCC's t2s would rewrite — i.e. genuinely Traditional.

    The previous implementation intersected the transcript with a hand-written
    21-character list ("繁體東車馬語說們個過還發沒學國會來時對開關"). Measured
    against this repo's own Mandarin fixture, a *fully Traditional* transcript
    of it — 甚至出現交易幾乎停滯的情況, produced by the `small` model on the CPU
    EP with qnn_strict:false — contains four Traditional characters
    (現 幾 滯 況), none of which are on that list. So A3 reported PASS on
    Traditional output: a false pass on the one thing A3 exists to check.
    OpenCC already ships in this venv; ask it instead of guessing.
    """
    try:
        from opencc import OpenCC
    except Exception:
        # No OpenCC: fall back to the old marker set, but say so loudly.
        markers = set("繁體東車馬語說們個過還發沒學國會來時對開關")
        return sorted(set(text) & markers)
    simplified = OpenCC("t2s").convert(text)
    return sorted({a for a, b in zip(text, simplified) if a != b})


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
    # Edit distance, not substring containment — see cer() for why the old
    # rule graded a one-word transcript as a pass.
    c = cer(expect, got)
    ok = c <= 0.15
    return _r("A2", a2.title, PASS if ok else FAIL,
              f"expected={expect!r} got={got!r}  CER={c*100:.1f}% (budget 15%)  ({dt:.2f}s)",
              expected=expect, got=got, cer=round(c, 4), seconds=round(dt, 3),
              wav=str(wav))


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
    # Ask OpenCC which characters are Traditional instead of guessing from a
    # 21-character list that misses 現/幾/滯/況 (see traditional_chars()).
    trad_hits = traditional_chars(got)

    # And compare against the ground truth. "contains CJK and no Traditional
    # characters" is satisfied by *any* Simplified sentence, including one with
    # nothing to do with the audio — it was never an accuracy check.
    expect_file = wav.with_suffix(".txt")
    c = None
    if expect_file.exists():
        c = cer(expect_file.read_text(encoding="utf-8").strip(), got)

    ok = has_cjk and not trad_hits and (c is None or c <= 0.20)
    status = PASS if ok else FAIL
    ev = f"got={got!r} cjk={has_cjk} traditional_chars={trad_hits}"
    if c is not None:
        ev += f" CER={c*100:.1f}% (budget 20%)"
    if trad_hits:
        ev += ("  (Traditional output — the backend does NOT apply OpenCC; t2s "
               "lives in server.py's UtteranceProcessor, which this check bypasses)")
    if not expect_file.exists():
        ev += "  (NO GROUND TRUTH — accuracy unverified)"
    return _r("A3", a3.title, status, ev, got=got, traditional=trad_hits,
              cer=None if c is None else round(c, 4), wav=str(wav))


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
    rng = np.random.default_rng(1234)      # fixed seed: this must be reproducible

    def noise(dbfs):
        """Gaussian noise whose RMS sits at `dbfs` relative to full scale."""
        rms = (10.0 ** (dbfs / 20.0)) * 32768.0
        return np.clip(rng.standard_normal(n) * rms, -32768, 32767).astype(np.int16)

    def run(make_chunk):
        v.reset()
        return [bool(v.is_speech(make_chunk(k))) for k in range(reps)]

    # "Silence" is ROOM TONE, not digital zeros. An energy VAD cannot fail on
    # an all-zero buffer, so the original version of this check was passing for
    # a reason unrelated to whether the VAD works. -55 dBFS is a quiet room.
    QUIET_DBFS = -55.0
    sil = run(lambda k: noise(QUIET_DBFS))

    def speech_chunk(k):
        c = src[start + k * n: start + (k + 1) * n]
        return np.pad(c, (0, n - len(c))) if len(c) < n else c

    spk = run(speech_chunk)

    # Where does it start calling steady noise "speech"? That floor is the
    # hallucination risk: anything above it feeds non-speech to Whisper, which
    # answers with its canonical inventions ("Thank you very much.").
    floor = None
    for dbfs in (-60, -55, -50, -45, -40, -35, -30, -25):
        if any(run(lambda k, d=dbfs: noise(d))):
            floor = dbfs
            break

    detect_at = next((i + 1 for i, s in enumerate(spk) if s), None)
    ok = (not any(sil)) and any(spk)
    ev = (f"vad={name} fixture={wav.name}  "
          f"room-tone@{QUIET_DBFS:.0f}dBFS={sil} (want all False)  "
          f"speech={spk} (want some True)")
    if detect_at:
        ev += f"  detected after {detect_at} chunk(s) = {detect_at * n / 16000:.2f}s"
    ev += (f"  false-positive floor: steady noise reads as speech from "
           f"{floor} dBFS" if floor is not None
           else "  false-positive floor: none up to -25 dBFS")
    if floor is not None and floor <= -40:
        ev += "  ⚠ a noisy room will feed non-speech to Whisper and invite hallucinated captions"
    return _r("A5", a5.title, PASS if ok else FAIL, ev,
              vad=name, silence=sil, speech=spk, detect_chunk=detect_at,
              false_positive_floor_dbfs=floor, fixture=str(wav))


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

    import socket
    import numpy as np

    cfg = json.loads((REPO / "config.json").read_text())
    port = cfg.get("ws_port", 8765)

    # Probe the port FIRST. Otherwise a message-read timeout is indistinguishable
    # from an absent server: asyncio.TimeoutError is an alias of TimeoutError,
    # which subclasses OSError, so "server never replied" was being reported as
    # "no server is running" — a genuinely misleading diagnosis.
    s = socket.socket()
    s.settimeout(2)
    try:
        s.connect(("127.0.0.1", port))
    except OSError as e:
        return _r("A6", a6.title, BLOCKED,
                  f"nothing listening on 127.0.0.1:{port} ({e!r}) — "
                  "start server.py --no-mic first")
    finally:
        s.close()

    # Match the fixture's language to the server, else an English clip decoded
    # as Chinese yields real-but-nonsense captions.
    wav = find_fixture("english_speech", "english", "jfk")
    lang = "en"
    if wav is None:
        wav = find_fixture("chinese_speech", "chinese")
        lang = "zh"
    if wav is None:
        return _r("A6", a6.title, BLOCKED, "no fixture to stream")
    audio = read_wav_int16(wav)

    async def run():
        async with websockets.connect(f"ws://127.0.0.1:{port}", open_timeout=10) as ws:
            await ws.send(json.dumps({"type": "set_language", "language": lang}))
            chunk = 16000 // 4  # 250 ms

            async def feed():
                for i in range(0, len(audio), chunk):
                    await ws.send(audio[i:i + chunk].tobytes())
                    await asyncio.sleep(0.05)
                # Trailing silence: the server finalises an utterance only after
                # `silence_chunks` quiet chunks. Without this the stream just
                # stops and no final is ever emitted.
                quiet = np.zeros(chunk, dtype=np.int16)
                for _ in range(20):
                    await ws.send(quiet.tobytes())
                    await asyncio.sleep(0.05)

            task = asyncio.create_task(feed())
            caps, deadline = [], time.time() + 45
            try:
                while time.time() < deadline:
                    try:
                        msg = await asyncio.wait_for(ws.recv(), timeout=5)
                    except asyncio.TimeoutError:
                        if caps:
                            break          # got captions, server just went quiet
                        continue
                    if isinstance(msg, bytes):
                        continue
                    d = json.loads(msg)
                    if d.get("type") in ("partial", "final") and d.get("text", "").strip():
                        caps.append(d)
                        if d["type"] == "final":
                            break
            finally:
                task.cancel()
            return caps

    try:
        caps = asyncio.run(run())
    except Exception as e:
        return _r("A6", a6.title, FAIL, f"websocket round trip failed: {e!r}")

    if not caps:
        return _r("A6", a6.title, FAIL,
                  "connected and streamed PCM but no caption ever arrived")
    finals = [c for c in caps if c["type"] == "final"]
    texts = [c["text"] for c in caps]
    expect_file = wav.with_suffix(".txt")
    ev = (f"fixture={wav.name} lang={lang}  {len(caps)} caption msg(s), "
          f"{len(finals)} final  texts={texts[-3:]}")
    if expect_file.exists():
        exp = expect_file.read_text(encoding="utf-8").strip()
        best = max(texts, key=len)
        ev += f"  expected≈{exp!r}"
        if norm(exp) not in norm(best) and norm(best) not in norm(exp):
            ev += "  (text differs from ground truth — round trip works, accuracy checked by A2)"
    return _r("A6", a6.title, PASS, ev, captions=caps, fixture=str(wav))


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
    # HKCR, not HKLM. COM resolves a CLSID through HKEY_CLASSES_ROOT, which is
    # HKCU\Software\Classes merged OVER HKLM\Software\Classes. The no-admin
    # install this port ships registers the x64 filter per-user in HKCU, so an
    # HKLM-only enumeration reported `dll=obs-virtualcam-module32.dll,
    # views=32` and failed A8 — while an x64 CoCreateInstance of the very same
    # CLSID returned S_OK, because it resolved the HKCU x64 entry HKLM never
    # sees. Reading a hive COM does not consult is the same class of bug as
    # the original Win32_PnPEntity mistake.
    for view, root in (("64", "Registry::HKEY_CLASSES_ROOT\\CLSID"),
                       ("32", "Registry::HKEY_CLASSES_ROOT\\WOW6432Node\\CLSID")):
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


# A PE header says who *could* load a DLL. Only CoCreateInstance says who does.
# This is the same source a call app executes: resolve the CLSID, load the
# in-proc server, ask for IBaseFilter. HRESULT 0x800700C1 = wrong architecture.
_COCREATE_SRC = r'''
import ctypes, platform, sys
CLSID = "{A3FCE0F5-3493-419F-958A-ABA1250EC20B}"
IID_IBaseFilter = "{56A86895-0AD4-11CE-B03A-0020AF0BA770}"
class G(ctypes.Structure):
    _fields_ = [("a", ctypes.c_ulong), ("b", ctypes.c_ushort),
                ("c", ctypes.c_ushort), ("d", ctypes.c_ubyte * 8)]
def guid(s):
    g = G(); ctypes.OleDLL("ole32").CLSIDFromString(ctypes.c_wchar_p(s), ctypes.byref(g))
    return g
ctypes.windll.ole32.CoInitializeEx(None, 2)
p = ctypes.c_void_p()
h = ctypes.windll.ole32.CoCreateInstance(
    ctypes.byref(guid(CLSID)), None, 1, ctypes.byref(guid(IID_IBaseFilter)),
    ctypes.byref(p))
print(f"{platform.machine()} 0x{h & 0xFFFFFFFF:08X}")
'''


def _cocreate_probe(exe: str) -> str | None:
    """Run the CoCreateInstance probe under `exe`; return 'ARCH 0xHRESULT'."""
    try:
        out = subprocess.run([exe, "-c", _COCREATE_SRC], capture_output=True,
                             text=True, timeout=90)
    except Exception:
        return None
    line = (out.stdout or "").strip().splitlines()
    return line[-1] if line else None


def _x64_interpreter() -> str | None:
    """An x64 python on this box — the architecture WeChat and Zoom run as."""
    for c in (REPO / ".venv-x64" / "Scripts" / "python.exe",
              Path.home() / "Downloads" / "laolao-tools" / "python311-x64" / "python.exe"):
        if c.exists():
            return str(c)
    return None


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

    # ── the part that actually proves it: CoCreateInstance ────────────────
    # A PE header only shows what *could* load. Do what a call app does.
    here = _cocreate_probe(sys.executable)
    x64_exe = _x64_interpreter()
    there = _cocreate_probe(x64_exe) if x64_exe else None
    probes = [p for p in (here, there) if p]
    real_x64 = any(p.startswith("AMD64") and p.endswith("0x00000000") for p in probes)
    real_arm = any(p.startswith("ARM64") and p.endswith("0x00000000") for p in probes)
    saw_x64_probe = any(p.startswith("AMD64") for p in probes)

    if saw_x64_probe:
        x64_ok = real_x64                    # measured beats inferred
        arm64_ok = real_arm or arm64_ok
        proof = f"CoCreateInstance probes: {probes} (0x800700C1 = wrong arch)"
    else:
        proof = ("NO x64 interpreter found — x64 loadability is INFERRED from the "
                 "PE header, not measured. Create .venv-x64 to make this real.")

    # WeChat / Zoom on Windows-ARM64 are x64 images running under Prism.
    verdict = ("x64-emulated call apps CAN load it" if x64_ok
               else "NO x64 filter — emulated call apps cannot load it")
    native = ("native ARM64 producers can load it" if arm64_ok
              else "native ARM64 producers CANNOT load it (must drive the camera "
                   "from an emulated-x64 helper process or out-of-process OBS)")
    status = PASS if x64_ok else FAIL
    return _r("A8", a8.title, status,
              "; ".join(report) + f"  ->  {verdict}; {native}\n{proof}",
              filters=virt, x64_loadable=x64_ok, arm64_loadable=arm64_ok,
              cocreate=probes)


# ─────────────────────────────────────────────────────────────────────
# A9 / A10 — offline operation, one-click launch
# ─────────────────────────────────────────────────────────────────────

@check("A9", "runs fully offline after model download")
def a9():
    """Re-run A2 in a subprocess with every hub client forced offline.

    Not a perfect substitute for pulling the network cable (that needs admin),
    but it is falsifiable: if any code path still reaches for the network to
    load a model or tokenizer, these variables turn it into a hard error
    instead of a silent download.
    """
    import os
    if os.environ.get("LAOLAO_A9_CHILD"):
        return _r("A9", a9.title, SKIP, "skipped inside the offline child run")

    env = {
        **os.environ,
        "LAOLAO_A9_CHILD": "1",
        "HF_HUB_OFFLINE": "1",
        "TRANSFORMERS_OFFLINE": "1",
        "HF_DATASETS_OFFLINE": "1",
        "HF_HUB_DISABLE_TELEMETRY": "1",
        "PYTHONIOENCODING": "utf-8",
    }
    try:
        p = subprocess.run(
            [sys.executable, str(Path(__file__).resolve()), "--only", "A2"],
            capture_output=True, text=True, timeout=600, env=env,
            encoding="utf-8", errors="replace")
    except subprocess.TimeoutExpired:
        return _r("A9", a9.title, FAIL, "offline A2 run timed out")

    tail = (p.stdout or "")[-700:] + (p.stderr or "")[-400:]
    ok = p.returncode == 0 and "[PASS " in (p.stdout or "")
    return _r("A9", a9.title, PASS if ok else FAIL,
              f"offline A2 exit={p.returncode}\n{tail.strip()}",
              returncode=p.returncode)


# CLSID of the OBS DirectShow virtual-camera filter (the device Laolao tells
# the user to pick). Same value laolao-vcam-setup.ps1 registers.
VCAM_CLSID = "{A3FCE0F5-3493-419F-958A-ABA1250EC20B}"

LAUNCHER_BAT = REPO / "Laolao-arm64.bat"
LAUNCH_PS1 = REPO / "docs" / "snapdragon" / "launch.ps1"
SETUP_PS1 = REPO / "docs" / "snapdragon" / "setup-arm64.ps1"

# Patterns that mean the launcher would pop a UAC prompt. A one-click launch
# that stops on "Do you want to allow this app to make changes?" is not one
# click, and on a locked-down family machine it is not possible at all.
ELEVATION_MARKERS = ("-verb runas", "verb='runas'", 'verb="runas"',
                     "requireadministrator", "requestedexecutionlevel")


def _ps_parse_ok(path: Path) -> str:
    """'PARSE_OK' or the first syntax errors. A launcher that cannot even be
    tokenised fails at double-click time with a wall of red — cheap to catch."""
    cmd = ("$e=$null;"
           f"$null=[System.Management.Automation.PSParser]::Tokenize("
           f"(Get-Content -Raw -LiteralPath '{path}'),[ref]$e);"
           "if ($e.Count) { $e | ForEach-Object { 'ERR line ' + $_.Token.StartLine "
           "+ ': ' + $_.Message } } else { 'PARSE_OK' }")
    out = _ps(cmd)
    return "PARSE_OK" if out == ["PARSE_OK"] else "; ".join(out[:4]) or "no output"


def _port_owner(port: int) -> dict | None:
    """Who is LISTENING on a port: pid, image path, PARENT image path, cmdline.

    The parent matters. A Windows venv's `python.exe` is a stub that re-execs
    its base interpreter, and it is the *child* that owns the socket — so the
    listener's own path is `...\\Python311-arm64\\python.exe` even when the
    engine was correctly started from `.venv-arm64`. Judging the lane by the
    listener's path alone reports the wrong interpreter.
    """
    out = _ps(
        f"$c = Get-NetTCPConnection -LocalPort {port} -State Listen "
        "-ErrorAction SilentlyContinue | Select-Object -First 1;"
        "if ($c) {"
        " $p = Get-CimInstance Win32_Process -Filter \"ProcessId=$($c.OwningProcess)\";"
        " $par = $null;"
        " if ($p) { $par = Get-CimInstance Win32_Process -Filter \"ProcessId=$($p.ParentProcessId)\" -ErrorAction SilentlyContinue }"
        ' "$($p.ProcessId)|$($p.Name)|$($p.ExecutablePath)|$($par.ExecutablePath)|$($p.CommandLine)" }'
    )
    if not out:
        return None
    parts = (out[0].split("|", 4) + ["", "", "", "", ""])[:5]
    return {"pid": parts[0], "name": parts[1], "path": parts[2],
            "parent_path": parts[3], "cmdline": parts[4]}


def _pids(procname: str) -> list[str]:
    return _ps(f"Get-Process {procname} -ErrorAction SilentlyContinue | "
               "Select-Object -ExpandProperty Id")


def _run_launcher(args: list[str], timeout: int) -> subprocess.CompletedProcess:
    """Invoke the .bat exactly as a double-click would, minus the read-me pause."""
    import os
    env = {**os.environ, "LAOLAO_NO_PAUSE": "1", "PYTHONIOENCODING": "utf-8"}
    return subprocess.run(["cmd", "/c", str(LAUNCHER_BAT), *args],
                          capture_output=True, text=True, timeout=timeout,
                          cwd=str(REPO), env=env, encoding="utf-8", errors="replace")


def _captions_from_port(port: int, seconds: int = 60) -> list[dict]:
    """Stream a speech fixture at a *running* engine and collect captions.

    Deliberately independent of A6: A6 proves the protocol works against a
    server the harness was told to expect. Here the point is that the server
    the *launcher* started is genuinely transcribing, so this re-does the round
    trip against whatever the launcher left running.
    """
    import asyncio
    import numpy as np
    import websockets

    wav = find_fixture("chinese_speech", "english_speech", "chinese", "english")
    if wav is None:
        return []
    lang = "zh" if "chinese" in wav.name.lower() else "en"
    audio = read_wav_int16(wav)

    async def run():
        async with websockets.connect(f"ws://127.0.0.1:{port}", open_timeout=10) as ws:
            await ws.send(json.dumps({"type": "set_language", "language": lang}))
            step = 4000

            async def feed():
                for i in range(0, len(audio), step):
                    await ws.send(audio[i:i + step].tobytes())
                    await asyncio.sleep(0.05)
                quiet = np.zeros(step, dtype=np.int16)
                for _ in range(24):
                    await ws.send(quiet.tobytes())
                    await asyncio.sleep(0.05)

            task = asyncio.create_task(feed())
            caps, deadline = [], time.time() + seconds
            try:
                while time.time() < deadline:
                    try:
                        msg = await asyncio.wait_for(ws.recv(), timeout=5)
                    except asyncio.TimeoutError:
                        if caps:
                            break
                        continue
                    if isinstance(msg, bytes):
                        continue
                    d = json.loads(msg)
                    if d.get("type") in ("partial", "final") and d.get("text", "").strip():
                        caps.append(d)
                        if d["type"] == "final":
                            break
            finally:
                task.cancel()
            return caps

    try:
        return asyncio.run(run())
    except Exception:
        return []


@check("A10", "one-command launch: the launcher actually brings Laolao up")
def a10():
    """Run the launcher. Do not admire it.

    The original version of this check listed launcher filenames and called
    that a PASS. That grades the existence of a file, not the criterion —
    a launcher that crashes on line 1 passed it. A10 is the only criterion
    whose subject is a *non-technical user*, so it has to answer: after one
    double-click, with no admin rights, is the product actually up?

    So this now tears the machine down to a cold state, runs the launcher the
    way Explorer would, and then verifies the outcome from the outside:
    the caption engine is transcribing, OBS is running, and the virtual camera
    is registered to a DLL the call apps can load. Then it runs it a second
    time to prove idempotence, and stops everything again.

    Set LAOLAO_A10_STATIC=1 to skip the live part (files + syntax only, which
    can never be better than SKIP).
    """
    import os

    port = json.loads((REPO / "config.json").read_text()).get("ws_port", 8765)
    notes: list[str] = []

    # ── static: the artefacts must exist and be launchable ───────────────
    for p in (LAUNCHER_BAT, LAUNCH_PS1, SETUP_PS1):
        if not p.exists():
            return _r("A10", a10.title, FAIL, f"missing launcher artefact: {p}")

    for p in (LAUNCH_PS1, SETUP_PS1):
        verdict = _ps_parse_ok(p)
        if verdict != "PARSE_OK":
            return _r("A10", a10.title, FAIL, f"{p.name} does not parse: {verdict}")
    notes.append("files present, PowerShell parses")

    blob = "\n".join(p.read_text(encoding="utf-8", errors="replace").lower()
                     for p in (LAUNCHER_BAT, LAUNCH_PS1, SETUP_PS1))
    hits = [m for m in ELEVATION_MARKERS if m in blob]
    if hits:
        return _r("A10", a10.title, FAIL,
                  f"launcher would request elevation ({hits}) — a UAC prompt is "
                  "not a one-click launch for a non-technical user")
    elevated = (_ps("(New-Object Security.Principal.WindowsPrincipal("
                    "[Security.Principal.WindowsIdentity]::GetCurrent()))"
                    ".IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)")
                or ["?"])[0]
    notes.append(f"no elevation markers (harness itself elevated={elevated})")

    if platform.system() != "Windows":
        return _r("A10", a10.title, SKIP,
                  "static checks only — not on Windows. " + "; ".join(notes))
    if os.environ.get("LAOLAO_A10_STATIC"):
        return _r("A10", a10.title, SKIP,
                  "LAOLAO_A10_STATIC=1, live launch skipped. " + "; ".join(notes))

    # ── cold state ───────────────────────────────────────────────────────
    try:
        _run_launcher(["-Stop"], timeout=240)
    except subprocess.TimeoutExpired:
        return _r("A10", a10.title, FAIL, "launcher -Stop hung")
    cold_bad = []
    if _port_owner(port):
        cold_bad.append(f"something still listening on {port}")
    if _pids("obs64"):
        cold_bad.append("obs64 still running")
    if cold_bad:
        return _r("A10", a10.title, FAIL,
                  "-Stop left the machine dirty (" + "; ".join(cold_bad) +
                  ") — cannot test a cold start")
    notes.append("cold: port free, OBS down")

    # ── the double-click ─────────────────────────────────────────────────
    t0 = time.perf_counter()
    try:
        # -NoBrowser only suppresses the caption *preview* window; the engine
        # and camera come up exactly as they would for a real user.
        first = _run_launcher(["-NoBrowser"], timeout=1800)
    except subprocess.TimeoutExpired:
        _run_launcher(["-Stop"], timeout=240)
        return _r("A10", a10.title, FAIL, "launcher never finished (30 min)")
    dt = time.perf_counter() - t0
    if first.returncode != 0:
        tail = (first.stdout or "")[-900:] + (first.stderr or "")[-300:]
        _run_launcher(["-Stop"], timeout=240)
        return _r("A10", a10.title, FAIL,
                  f"launcher exited {first.returncode} after {dt:.0f}s\n{tail.strip()}")
    notes.append(f"launcher exit 0 in {dt:.0f}s")

    failures: list[str] = []

    # 1. the caption engine is up, and it is the native ARM64 venv
    owner = _port_owner(port)
    if not owner:
        failures.append(f"nothing listening on {port} after launch")
    else:
        notes.append(f"engine pid={owner['pid']} cmd={owner['cmdline'][:60]!r}")
        lineage = (owner["path"] + " " + owner["parent_path"]).lower()
        if "venv-arm64" not in lineage:
            failures.append("the engine is not from .venv-arm64 "
                            f"(image={owner['path']} parent={owner['parent_path']}) — "
                            "the launcher must not silently use a system interpreter")
        else:
            notes.append("engine lane = .venv-arm64 (native ARM64)")

    # 2. it is transcribing, not merely bound to a socket
    caps = _captions_from_port(port) if owner else []
    if owner and not caps:
        failures.append("engine is listening but produced no caption for a speech fixture")
    elif caps:
        notes.append(f"captions={[c['text'] for c in caps][-2:]}")

    # 3. the camera the user is about to be told to pick actually exists,
    #    and is backed by a DLL their (emulated x64) call app can load
    obs = _pids("obs64")
    if not obs:
        failures.append("OBS is not running — nothing is producing camera frames")
    else:
        notes.append(f"obs64 pid={obs[0]}")
    virt = [f for f in _dshow_filters() if _is_virtual(f["name"])]
    machines = {f["name"]: _pe_machine((f.get("dll") or "").strip('"')) for f in virt}
    if not virt:
        failures.append("no virtual camera registered after launch")
    elif "AMD64(x64)" not in machines.values():
        failures.append(f"virtual camera is registered to {machines} — x64 call apps "
                        "(WeChat/Zoom) would see it in the picker and get a dead feed")
    else:
        notes.append(f"camera filter {machines}")

    # 3b. and it was registered somewhere an ordinary user can write.
    # HKLM needs administrator; HKCU does not, and HKEY_CLASSES_ROOT merges the
    # two so DirectShow resolves either. If the only registration is in HKLM,
    # this machine was set up by an admin and the launcher is not really a
    # no-admin one-click for the next person.
    hive = _ps(
        f"$c='{VCAM_CLSID}';"
        "'HKCU=' + (Test-Path \"HKCU:\\SOFTWARE\\Classes\\CLSID\\$c\\InprocServer32\") +"
        "' HKLM=' + (Test-Path \"HKLM:\\SOFTWARE\\Classes\\CLSID\\$c\\InprocServer32\")")
    hive_s = hive[0] if hive else "unknown"
    if "HKCU=True" not in hive_s:
        failures.append(f"camera not registered per-user ({hive_s}) — "
                        "that registration needed administrator rights")
    else:
        notes.append(f"registration {hive_s} (per-user, no admin)")

    # 4. idempotent: a second double-click must not duplicate or break anything
    try:
        second = _run_launcher(["-NoBrowser"], timeout=900)
    except subprocess.TimeoutExpired:
        second = None
    if second is None or second.returncode != 0:
        failures.append("second run of the launcher did not succeed (not idempotent)")
    else:
        owner2, obs2 = _port_owner(port), _pids("obs64")
        if owner and (not owner2 or owner2["pid"] != owner["pid"]):
            failures.append(f"second run replaced the engine "
                            f"({owner['pid']} -> {owner2 and owner2['pid']})")
        elif obs and obs2 and set(obs2) != set(obs):
            failures.append(f"second run restarted OBS ({obs} -> {obs2})")
        else:
            notes.append("second run reused the running engine and camera")

    # ── put the machine back ─────────────────────────────────────────────
    try:
        _run_launcher(["-Stop"], timeout=240)
    except subprocess.TimeoutExpired:
        failures.append("-Stop hung at cleanup")
    if _port_owner(port):
        failures.append("-Stop did not free the port")
    else:
        notes.append("stopped cleanly")

    status = FAIL if failures else PASS
    ev = "  ".join(notes)
    if failures:
        ev = "FAILURES: " + "; ".join(failures) + "\n          " + ev
    return _r("A10", a10.title, status, ev,
              launchers=[str(LAUNCHER_BAT), str(LAUNCH_PS1), str(SETUP_PS1)],
              seconds=round(dt, 1), captions=caps, filters=virt, failures=failures)


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
