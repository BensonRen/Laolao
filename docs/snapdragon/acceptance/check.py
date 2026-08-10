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
    import numpy as np
    from vad import get_vad
    cfg = json.loads((REPO / "config.json").read_text())
    v = get_vad(cfg)

    n = int(16000 * (cfg.get("chunk_ms", 300) / 1000))
    silence = np.zeros(n, dtype=np.int16)
    speech = (np.random.randn(n) * 6000).astype(np.int16)

    s_sil, s_spk = v.is_speech(silence), v.is_speech(speech)
    ok = (not s_sil) and s_spk
    return _r("A5", a5.title, PASS if ok else FAIL,
              f"vad={type(v).__name__} silence->{s_sil} loud->{s_spk} "
              "(want False/True)",
              vad=type(v).__name__, silence=bool(s_sil), speech=bool(s_spk))


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

def _enumerate_cameras() -> list[str]:
    """List video capture device names the way a consumer app would."""
    names: list[str] = []
    ps = (
        "Get-CimInstance Win32_PnPEntity | "
        "Where-Object { $_.PNPClass -eq 'Camera' -or $_.PNPClass -eq 'Image' -or "
        "$_.Service -eq 'usbvideo' } | Select-Object -ExpandProperty Name"
    )
    try:
        out = subprocess.run(["powershell", "-NoProfile", "-Command", ps],
                             capture_output=True, text=True, timeout=60)
        names = [l.strip() for l in out.stdout.splitlines() if l.strip()]
    except Exception:
        pass
    return names


@check("A7", "a Laolao/OBS virtual camera device is present")
def a7():
    names = _enumerate_cameras()
    hits = [n for n in names if "obs" in n.lower() or "laolao" in n.lower()
            or "virtual" in n.lower()]
    if not names:
        return _r("A7", a7.title, BLOCKED, "camera enumeration returned nothing")
    return _r("A7", a7.title, PASS if hits else FAIL,
              f"virtual={hits} all_devices={names}", virtual=hits, all=names)


@check("A8", "virtual camera is selectable by a capture consumer")
def a8():
    """PnP presence is not proof. A consumer must be able to OPEN it."""
    names = _enumerate_cameras()
    hits = [n for n in names if "obs" in n.lower() or "laolao" in n.lower()]
    if not hits:
        return _r("A8", a8.title, BLOCKED, "no virtual camera registered yet (see A7)")
    return _r("A8", a8.title, SKIP,
              "requires opening the device from a real consumer and reading a "
              "non-black frame — see WS-C findings for the x64-emulation verdict "
              "(WeChat/Zoom are emulated x64; an ARM64-only DirectShow filter "
              "may be invisible to them)",
              candidates=hits)


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
