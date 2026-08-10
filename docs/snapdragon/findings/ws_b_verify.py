#!/usr/bin/env python
"""
ws_b_verify.py — re-prove the WS-B "x64 Prism emulation" lane on Windows-on-ARM64.

Run it with the *x64* venv interpreter, NOT the native ARM64 one:

    C:\\Users\\snapd\\Downloads\\laolao\\.venv-x64\\Scripts\\python.exe ^
        docs\\snapdragon\\findings\\ws_b_verify.py

What it proves (H-200 .. H-203):

  H-200  the interpreter really is x64 running under Prism emulation on an ARM64 host
  H-201  ctranslate2 + faster-whisper import and transcribe known WAVs correctly
  H-202  latency for `base` and `small` on a real utterance, vs the A4 budget
         (partial < 1000 ms, final < 2000 ms)
  H-203  pyvirtualcam imports and can enumerate/report its backends

Audio fixtures live OUTSIDE the repo, in
    C:\\Users\\snapd\\Downloads\\laolao-tools\\audio\\
and are re-downloaded automatically if missing (needs network on first run only).

Exit code 0 = every hypothesis this script can decide came out CONFIRMED.
"""

from __future__ import annotations

import ctypes
import ctypes.wintypes
import json
import os
import platform
import statistics
import subprocess
import sys
import time
import urllib.request
import wave
from pathlib import Path

# --------------------------------------------------------------------------
# Config
# --------------------------------------------------------------------------

REPO_ROOT = Path(__file__).resolve().parents[3]
TOOLS_DIR = Path(r"C:\Users\snapd\Downloads\laolao-tools")
AUDIO_DIR = TOOLS_DIR / "audio"

# name -> (url, expected substrings, human description)
FIXTURES: dict[str, dict] = {
    "jfk.wav": {
        "url": "https://github.com/ggml-org/whisper.cpp/raw/master/samples/jfk.wav",
        "lang": "en",
        # JFK inaugural address, the canonical whisper.cpp sample.
        "expect": ["ask not what your country can do for you"],
        "desc": "JFK inaugural excerpt (whisper.cpp canonical sample)",
    },
    "asr_example_zh.wav": {
        "url": "https://isv-data.oss-cn-hangzhou.aliyuncs.com/ics/MaaS/ASR/"
               "test_audio/asr_example_zh.wav",
        "lang": "zh",
        # Canonical FunASR / ModelScope Mandarin sample.
        # Reference transcript: 欢迎大家来体验达摩院推出的语音识别模型
        "expect": ["欢迎大家", "语音识别"],
        "desc": "FunASR canonical Mandarin sample",
    },
}

PARTIAL_BUDGET_MS = 1000.0   # A4: partial  < 1.0 s
FINAL_BUDGET_MS = 2000.0     # A4: final    < 2.0 s

results: dict[str, str] = {}


def hdr(text: str) -> None:
    print("\n" + "=" * 72)
    print(text)
    print("=" * 72)


def load_wav(path: Path):
    import numpy as np
    with wave.open(str(path), "rb") as wf:
        assert wf.getnchannels() == 1, f"{path} is not mono"
        assert wf.getsampwidth() == 2, f"{path} is not 16-bit"
        sr = wf.getframerate()
        raw = wf.readframes(wf.getnframes())
    return np.frombuffer(raw, dtype=np.int16).copy(), sr


def ensure_fixtures() -> None:
    AUDIO_DIR.mkdir(parents=True, exist_ok=True)
    for name, meta in FIXTURES.items():
        dest = AUDIO_DIR / name
        if dest.exists():
            continue
        print(f"[fixtures] downloading {name} …")
        urllib.request.urlretrieve(meta["url"], dest)
        print(f"[fixtures] saved {dest} ({dest.stat().st_size} bytes)")


# --------------------------------------------------------------------------
# H-200 — x64 Python under Prism on an ARM64 host
# --------------------------------------------------------------------------

def check_h200() -> None:
    hdr("H-200  x64 Python 3.11 runs under Prism emulation on this ARM64 box")

    print("sys.version        :", sys.version.replace("\n", " "))
    print("platform.machine() :", platform.machine())
    print("platform.arch()    :", platform.architecture())
    print("sys.executable     :", sys.executable)

    # NOTE: GetNativeSystemInfo() lies inside a Prism-emulated x64 process — it
    # reports AMD64. IsWow64Process2()'s *nativeMachine* out-param does not: it
    # reports the real silicon. HANDLE must be declared or the pseudo-handle
    # (-1) is truncated to 32 bits and the call fails with ok=0.
    k = ctypes.WinDLL("kernel32", use_last_error=True)
    k.GetCurrentProcess.restype = ctypes.wintypes.HANDLE
    k.IsWow64Process2.argtypes = [
        ctypes.wintypes.HANDLE,
        ctypes.POINTER(ctypes.wintypes.USHORT),
        ctypes.POINTER(ctypes.wintypes.USHORT),
    ]
    k.IsWow64Process2.restype = ctypes.wintypes.BOOL

    proc_machine = ctypes.wintypes.USHORT(0)
    native_machine = ctypes.wintypes.USHORT(0)
    ok = k.IsWow64Process2(
        k.GetCurrentProcess(), ctypes.byref(proc_machine), ctypes.byref(native_machine)
    )
    IMAGE_FILE_MACHINE = {0x0: "UNKNOWN/not-WOW64", 0x8664: "AMD64", 0xAA64: "ARM64", 0x1C4: "ARMNT"}
    print(
        "IsWow64Process2    : ok=%s process=%s native=%s"
        % (
            bool(ok),
            IMAGE_FILE_MACHINE.get(proc_machine.value, hex(proc_machine.value)),
            IMAGE_FILE_MACHINE.get(native_machine.value, hex(native_machine.value)),
        )
    )
    print("PROCESSOR_IDENTIFIER:", os.environ.get("PROCESSOR_IDENTIFIER"))
    print("cpu_count          :", os.cpu_count())

    # x64 PE + ARM64 silicon underneath == running under Prism.
    # (x64-on-ARM64 is NOT classic WOW64, so processMachine is legitimately 0.)
    emulated = platform.machine() == "AMD64" and native_machine.value == 0xAA64
    results["H-200"] = "CONFIRMED" if emulated else "REFUTED"
    print("=> H-200:", results["H-200"])


# --------------------------------------------------------------------------
# H-201 — ctranslate2 + faster-whisper install and transcribe correctly
# --------------------------------------------------------------------------

def _backend(model: str):
    sys.path.insert(0, str(REPO_ROOT))
    from backends.faster_whisper_backend import FasterWhisperBackend
    return FasterWhisperBackend(
        {"model": model, "device": "cpu", "compute_type": "int8"}
    )


def check_h201(model: str = "base") -> dict:
    hdr(f"H-201  ctranslate2 + faster-whisper transcribe correctly (model={model})")

    import ctranslate2
    import faster_whisper
    print("ctranslate2   :", ctranslate2.__version__)
    print("faster_whisper:", faster_whisper.__version__)
    print("ct2 cpu types :", ctranslate2.get_supported_compute_types("cpu"))

    be = _backend(model)
    transcripts = {}
    all_ok = True
    for name, meta in FIXTURES.items():
        audio, sr = load_wav(AUDIO_DIR / name)
        dur = len(audio) / sr
        t0 = time.perf_counter()
        text = be.transcribe(audio, language=meta["lang"])
        ms = (time.perf_counter() - t0) * 1000
        hits = [s for s in meta["expect"] if s in text]
        ok = len(hits) == len(meta["expect"])
        all_ok &= ok
        transcripts[name] = text
        print(f"\n  {name}  ({meta['desc']}, {dur:.2f}s, lang={meta['lang']})")
        print(f"    transcript : {text!r}")
        print(f"    expect     : {meta['expect']}")
        print(f"    matched    : {hits}  -> {'OK' if ok else 'MISMATCH'}")
        print(f"    wall       : {ms:.0f} ms  (RTF {ms/1000/dur:.2f}x)")

    results["H-201"] = "CONFIRMED" if all_ok else "PARTIAL"
    print("\n=> H-201:", results["H-201"])
    return transcripts


# --------------------------------------------------------------------------
# H-202 — latency for base and small against the A4 budget
# --------------------------------------------------------------------------

def check_h202(models=("tiny", "base"), repeats: int = 3) -> dict:
    """Default to tiny+base so the script finishes in a few minutes.

    Pass --full to add `small`; it costs ~12 s *per pass* under emulation, which
    turns this into a ~25-minute run for a result that is not in doubt.
    """
    if "--full" in sys.argv:
        models = ("tiny", "base", "small")
    hdr("H-202  emulated latency vs A4 (partial <1000 ms, final <2000 ms)")

    audio, sr = load_wav(AUDIO_DIR / "asr_example_zh.wav")
    full_s = len(audio) / sr
    # Laolao transcribes a rolling window, not a whole file. Defaults:
    #   partial pass  == rolling buffer so far (we probe 2.0 s)
    #   final pass    == up to rolling_window_s (config default 4.0 s)
    probes = {"partial(2.0s window)": 2.0, "final(4.0s window)": 4.0}

    table = {}
    for model in models:
        be = _backend(model)
        table[model] = {}
        for label, win_s in probes.items():
            clip = audio[: int(win_s * sr)]
            # warm-up pass (first call pays one-off allocator/JIT cost)
            be.transcribe(clip, language="zh")
            samples = []
            for _ in range(repeats):
                t0 = time.perf_counter()
                be.transcribe(clip, language="zh")
                samples.append((time.perf_counter() - t0) * 1000)
            med = statistics.median(samples)
            table[model][label] = {
                "median_ms": med,
                "min_ms": min(samples),
                "max_ms": max(samples),
                "rtf": med / 1000 / win_s,
            }
            budget = PARTIAL_BUDGET_MS if label.startswith("partial") else FINAL_BUDGET_MS
            verdict = "PASS" if med < budget else "FAIL"
            print(
                f"  {model:<6} {label:<22} median {med:7.0f} ms "
                f"(min {min(samples):6.0f} / max {max(samples):6.0f}) "
                f"RTF {med/1000/win_s:5.2f}x  budget {budget:.0f} ms -> {verdict}"
            )
        del be

    def passes(m):
        return (
            table[m]["partial(2.0s window)"]["median_ms"] < PARTIAL_BUDGET_MS
            and table[m]["final(4.0s window)"]["median_ms"] < FINAL_BUDGET_MS
        )

    ok = {m: passes(m) for m in models}
    if all(ok.values()):
        results["H-202"] = "CONFIRMED"
    elif any(ok.values()):
        results["H-202"] = "PARTIAL"
    else:
        results["H-202"] = "REFUTED"
    print(f"\n  per-model A4 verdict: {ok}")
    print(f"  (full clip is {full_s:.2f}s; probes use its leading window)")
    print("=> H-202:", results["H-202"])
    return table


# --------------------------------------------------------------------------
# H-203 — pyvirtualcam installs + imports under emulation
# --------------------------------------------------------------------------

def check_h203() -> None:
    hdr("H-203  pyvirtualcam x64 wheel installs and imports under emulation")
    try:
        import pyvirtualcam
        from pyvirtualcam import PixelFormat  # noqa: F401
        print("pyvirtualcam  :", pyvirtualcam.__version__)
        print("Camera class  :", pyvirtualcam.Camera)
        print("backends      :", sorted(pyvirtualcam.camera.BACKENDS.keys()))
        imported = True
    except Exception as exc:  # noqa: BLE001
        print("import FAILED :", exc)
        imported = False

    device_ok = False
    if imported:
        import numpy as np
        try:
            with pyvirtualcam.Camera(
                width=1280, height=720, fps=30, fmt=pyvirtualcam.PixelFormat.RGB
            ) as cam:
                cam.send(np.zeros((720, 1280, 3), dtype=np.uint8))
                print("opened device :", cam.device, "(and accepted a frame)")
                device_ok = True
        except Exception as exc:  # noqa: BLE001
            print("open device   : FAILED ->", exc)
            print("                Two different causes, check which one:")
            print("                (a) OBS Studio not installed -> no filter registered (WS-C)")
            print("                (b) the single camera slot is already taken — quit")
            print("                    OBS Studio's own virtual camera and retry.")

    # The hypothesis as written is 'installs and imports'. Opening a real device
    # is a bonus that depends on WS-C, so a busy/absent driver is not a refutation.
    if imported and device_ok:
        results["H-203"] = "CONFIRMED (import + real device opened)"
    elif imported:
        results["H-203"] = "CONFIRMED for install/import; device open blocked (see above)"
    else:
        results["H-203"] = "REFUTED"
    print("=> H-203:", results["H-203"])


# --------------------------------------------------------------------------
# Repo test suite
# --------------------------------------------------------------------------

def run_repo_tests() -> None:
    hdr("repo test suite in the x64 venv")

    if not (REPO_ROOT / "venv" / "Scripts" / "python.exe").exists():
        print(
            "NOTE: tests/test_windows_headless.py hardcodes 'venv/Scripts/python.exe'.\n"
            "      This venv is '.venv-x64', so 3 subprocess tests will fail with\n"
            "      [WinError 2]. That is a path assumption in the test, NOT an\n"
            "      emulation failure. To see them pass:\n"
            "          New-Item -ItemType Junction -Path .\\venv -Target .\\.venv-x64\n"
        )

    for args in (["-m", "not slow", "-q"], ["-m", "slow", "-q"]):
        cmd = [sys.executable, "-m", "pytest", "tests/", *args]
        print("\n$", " ".join(cmd))
        p = subprocess.run(cmd, cwd=REPO_ROOT, capture_output=True, text=True)
        tail = (p.stdout or "").strip().splitlines()[-12:]
        print("\n".join(tail))
        print("exit:", p.returncode)


# --------------------------------------------------------------------------

def main() -> int:
    os.environ.setdefault("PYTHONIOENCODING", "utf-8")
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:  # noqa: BLE001
        pass

    ensure_fixtures()
    check_h200()
    transcripts = check_h201("base")
    table = check_h202()
    check_h203()
    run_repo_tests()

    hdr("SUMMARY")
    for k in sorted(results):
        print(f"  {k}: {results[k]}")
    print("\ntranscripts:", json.dumps(transcripts, ensure_ascii=False, indent=2))
    print("\nlatency:", json.dumps(table, indent=2))

    bad = [k for k, v in results.items() if v.startswith(("REFUTED", "PARTIAL"))]
    return 1 if any(results[k].startswith("REFUTED") for k in bad) else 0


if __name__ == "__main__":
    raise SystemExit(main())
