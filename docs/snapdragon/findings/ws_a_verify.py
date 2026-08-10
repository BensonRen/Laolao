"""
ws_a_verify.py — re-prove WS-A (native ARM64 speech-to-text) from scratch.

Run:
    C:\\Users\\snapd\\Downloads\\laolao\\.venv-arm64\\Scripts\\python.exe ^
        docs\\snapdragon\\findings\\ws_a_verify.py

    # options
    --skip-npu       only exercise the CPU ExecutionProvider path
    --skip-cpu       only exercise the QNN / Hexagon NPU path
    --turbo          also run whisper-large-v3-turbo on the NPU (2 GB download)
    --fresh          delete the cached Qualcomm asset first, proving auto-download

What it proves (each check prints PASS/FAIL and the evidence):

  H-100  Whisper ONNX runs under onnxruntime CPU EP on ARM64 and returns correct text
  H-101  a Whisper tokenizer works without any Rust-only wheel we cannot get
  H-103  the Whisper encoder runs on QNNExecutionProvider (Hexagon NPU), same tokens
  H-104  latency: partial < 1.0 s, final < 2.0 s on a ~5 s utterance
  H-105  Mandarin accuracy (CER against FLEURS cmn_hans_cn reference transcripts)

Prerequisites — the venv this script must run in:

    C:\\Users\\snapd\\AppData\\Local\\Programs\\Python\\Python311-arm64\\python.exe ^
        -m venv C:\\Users\\snapd\\Downloads\\laolao\\.venv-arm64
    .venv-arm64\\Scripts\\python.exe -m pip install ^
        onnxruntime-qnn numpy tokenizers huggingface_hub opencc-python-reimplemented httpx

Everything else (models, audio fixtures) is downloaded on first run into
C:\\Users\\snapd\\Downloads\\laolao-tools\\ — outside the repo.
"""

from __future__ import annotations

import argparse
import io
import json
import os
import platform
import re
import struct
import sys
import time
import wave
from pathlib import Path

import numpy as np

REPO = Path(__file__).resolve().parents[3]
TOOLS = REPO.parent / "laolao-tools"
FIXTURES = TOOLS / "fixtures"
REPO_FIXTURES = REPO / "tests" / "fixtures"
MODELS = TOOLS / "models"

sys.path.insert(0, str(REPO))

RESULTS: list[tuple[str, bool, str]] = []


def check(hyp: str, ok: bool, evidence: str) -> None:
    RESULTS.append((hyp, ok, evidence))
    print(f"[{'PASS' if ok else 'FAIL'}] {hyp}: {evidence}")


# ─────────────────────────────────────────────────────────────────────────────
# audio helpers
# ─────────────────────────────────────────────────────────────────────────────

def read_wav_any(raw: bytes) -> tuple[np.ndarray, int]:
    """Read a RIFF WAV from bytes — handles PCM16 and IEEE-float32 (tag 3)."""
    assert raw[:4] == b"RIFF" and raw[8:12] == b"WAVE", raw[:12]
    pos, fmt, data = 12, None, None
    while pos + 8 <= len(raw):
        cid, csz = raw[pos:pos + 4], struct.unpack("<I", raw[pos + 4:pos + 8])[0]
        body = raw[pos + 8:pos + 8 + csz]
        if cid == b"fmt ":
            fmt = struct.unpack("<HHIIHH", body[:16])
        elif cid == b"data":
            data = body
        pos += 8 + csz + (csz & 1)
    tag, ch, sr, _, _, bits = fmt
    if tag == 3 and bits == 32:
        a = (np.clip(np.frombuffer(data, np.float32), -1, 1) * 32767).astype(np.int16)
    elif tag == 1 and bits == 16:
        a = np.frombuffer(data, np.int16)
    else:
        raise SystemExit(f"unsupported wav: tag={tag} bits={bits}")
    if ch > 1:
        a = a.reshape(-1, ch).mean(axis=1).astype(np.int16)
    return a.copy(), sr


def load_wav(path: Path) -> np.ndarray:
    with wave.open(str(path), "rb") as w:
        assert w.getframerate() == 16000 and w.getnchannels() == 1 and w.getsampwidth() == 2
        return np.frombuffer(w.readframes(w.getnframes()), np.int16).copy()


def save_wav(path: Path, a: np.ndarray) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(16000)
        w.writeframes(a.astype(np.int16).tobytes())


def ensure_fleurs(n: int = 3) -> list[dict]:
    """Mandarin fixtures with dataset ground truth (google/fleurs, cmn_hans_cn)."""
    meta_path = FIXTURES / "fleurs_zh_meta.json"
    if meta_path.exists():
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        if all((FIXTURES / m["file"]).exists() for m in meta):
            return meta
    import httpx
    rows = []
    for off in (0, 20, 40):
        r = httpx.get("https://datasets-server.huggingface.co/rows",
                      params={"dataset": "google/fleurs", "config": "cmn_hans_cn",
                              "split": "validation", "offset": off, "length": 20}, timeout=90)
        r.raise_for_status()
        rows += r.json()["rows"]
    meta = []
    for row in sorted(rows, key=lambda x: x["row"]["num_samples"]):
        rr = row["row"]
        if not (3 * 16000 <= rr["num_samples"] <= 12 * 16000):
            continue
        a, sr = read_wav_any(httpx.get(rr["audio"][0]["src"], timeout=120,
                                       follow_redirects=True).content)
        name = f"fleurs_zh_{len(meta)}.wav"
        save_wav(FIXTURES / name, a)
        meta.append({"file": name, "sr": sr, "seconds": round(len(a) / sr, 2),
                     "transcription": rr["transcription"],
                     "raw_transcription": rr["raw_transcription"]})
        if len(meta) == n:
            break
    meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=1), encoding="utf-8")
    return meta


def ensure_english() -> list[tuple[Path, str]]:
    """English fixtures + ground truth: repo fixtures if present, else Windows SAPI TTS."""
    out = []
    for stem in ("english_speech", "en_long_speech"):
        wav, txt = REPO_FIXTURES / f"{stem}.wav", REPO_FIXTURES / f"{stem}.txt"
        if wav.exists() and txt.exists():
            out.append((wav, txt.read_text(encoding="utf-8").strip()))
    if out:
        return out
    # fallback: synthesise with Windows SAPI (System.Speech), 16 kHz mono PCM16
    import subprocess
    text = "Hello grandma, I miss you very much."
    wav = FIXTURES / "english_speech.wav"
    ps = (
        "Add-Type -AssemblyName System.Speech; "
        "$s=New-Object System.Speech.Synthesis.SpeechSynthesizer; "
        "$f=New-Object System.Speech.AudioFormat.SpeechAudioFormatInfo(16000,"
        "[System.Speech.AudioFormat.AudioBitsPerSample]::Sixteen,"
        "[System.Speech.AudioFormat.AudioChannel]::Mono); "
        f"$s.SetOutputToWaveFile('{wav}',$f); $s.Speak('{text}'); $s.Dispose()"
    )
    FIXTURES.mkdir(parents=True, exist_ok=True)
    subprocess.run(["powershell", "-NoProfile", "-Command", ps], check=True,
                   capture_output=True)
    return [(wav, text)]


# ─────────────────────────────────────────────────────────────────────────────
# scoring
# ─────────────────────────────────────────────────────────────────────────────

_PUNCT = re.compile(r"[\s，。、！？；：“”‘’（）《》,.!?;:\"'()<>\-—…·]")


def _edit(r: str, h: str) -> int:
    d = np.zeros((len(r) + 1, len(h) + 1), np.int32)
    d[:, 0] = np.arange(len(r) + 1); d[0, :] = np.arange(len(h) + 1)
    for i in range(1, len(r) + 1):
        for j in range(1, len(h) + 1):
            d[i, j] = min(d[i - 1, j] + 1, d[i, j - 1] + 1,
                          d[i - 1, j - 1] + (r[i - 1] != h[j - 1]))
    return int(d[len(r), len(h)])


def cer(ref: str, hyp: str) -> float:
    r, h = _PUNCT.sub("", ref), _PUNCT.sub("", hyp)
    return _edit(r, h) / max(1, len(r))


def wer(ref: str, hyp: str) -> float:
    r = _PUNCT.sub(" ", ref.lower()).split()
    h = _PUNCT.sub(" ", hyp.lower()).split()
    d = np.zeros((len(r) + 1, len(h) + 1), np.int32)
    d[:, 0] = np.arange(len(r) + 1); d[0, :] = np.arange(len(h) + 1)
    for i in range(1, len(r) + 1):
        for j in range(1, len(h) + 1):
            d[i, j] = min(d[i - 1, j] + 1, d[i, j - 1] + 1,
                          d[i - 1, j - 1] + (r[i - 1] != h[j - 1]))
    return float(d[len(r), len(h)]) / max(1, len(r))


# ─────────────────────────────────────────────────────────────────────────────

def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--skip-npu", action="store_true")
    ap.add_argument("--skip-cpu", action="store_true")
    ap.add_argument("--turbo", action="store_true")
    ap.add_argument("--fresh", action="store_true")
    args = ap.parse_args()

    import onnxruntime as ort
    from backends.onnx_whisper_backend import (
        OnnxWhisperBackend, QnnWhisperBackend, detect_chipset,
        log_mel_spectrogram, mel_filter_bank, register_qnn,
    )

    print("=" * 78)
    print(f"python      {sys.version.split()[0]}  machine={platform.machine()}")
    print(f"onnxruntime {ort.__version__}  providers={ort.get_available_providers()}")
    print(f"qnn plugin  registered={register_qnn()}  chipset={detect_chipset()}")
    print(f"providers   {ort.get_available_providers()}")
    print("=" * 78)

    # ── H-101: tokenizer without a Rust wheel we cannot obtain ───────────────
    try:
        import tokenizers
        from tokenizers import Tokenizer
        tok_dir = MODELS / "whisper-base"
        if not (tok_dir / "tokenizer.json").exists():
            from huggingface_hub import snapshot_download
            snapshot_download("onnx-community/whisper-base", local_dir=str(tok_dir),
                              allow_patterns=["*.json", "merges.txt"])
        t = Tokenizer.from_file(str(tok_dir / "tokenizer.json"))
        ids = t.encode("你好 grandma", add_special_tokens=False).ids
        rt = t.decode(ids)
        check("H-101", rt.strip() == "你好 grandma",
              f"tokenizers {tokenizers.__version__} (win_arm64 wheel) round-trip "
              f"{ids} -> {rt!r}")
    except Exception as e:
        check("H-101", False, f"tokenizer unavailable: {e!r}")
        return 1

    # ── mel front end matches openai/whisper's own filterbank ────────────────
    npz = MODELS / "mel_filters.npz"
    if not npz.exists():
        import urllib.request
        npz.parent.mkdir(parents=True, exist_ok=True)
        urllib.request.urlretrieve(
            "https://github.com/openai/whisper/raw/main/whisper/assets/mel_filters.npz", npz)
    ref80 = np.load(npz)["mel_80"]
    diff = float(np.abs(ref80 - mel_filter_bank(80)).max())
    check("mel-filterbank", diff < 1e-6,
          f"max|ours - openai/whisper mel_filters.npz['mel_80']| = {diff:.3e}")

    english = ensure_english()
    zh = ensure_fleurs()
    print(f"fixtures: {[p.name for p, _ in english]} + {[m['file'] for m in zh]}")

    import opencc
    t2s = opencc.OpenCC("t2s")

    def run_suite(tag: str, backend, wer_max: float, cer_max: float,
                  latency_max: float | None):
        lat, ok_all = [], True
        for wav, expect in english:
            audio = load_wav(wav)
            backend.transcribe(audio, "en")                       # warm
            t0 = time.perf_counter()
            hyp = backend.transcribe(audio, "en")
            dt = time.perf_counter() - t0
            lat.append((dt, len(audio) / 16000))
            w = wer(expect, hyp)
            ok_all &= w <= wer_max
            print(f"   {tag} {wav.name:20s} {dt*1000:6.0f}ms WER={w:.3f}")
            print(f"      expected: {expect}")
            print(f"      got     : {hyp}")
        cers = []
        for m in zh:
            audio = load_wav(FIXTURES / m["file"])
            backend.transcribe(audio, "zh")
            t0 = time.perf_counter()
            hyp = t2s.convert(backend.transcribe(audio, "zh"))
            dt = time.perf_counter() - t0
            lat.append((dt, m["seconds"]))
            c = cer(m["raw_transcription"], hyp)
            cers.append(c)
            print(f"   {tag} {m['file']:20s} {dt*1000:6.0f}ms CER={c:.3f}")
            print(f"      expected: {m['raw_transcription']}")
            print(f"      got     : {hyp}")
        mean_cer = float(np.mean(cers))
        worst = max(d for d, _ in lat)
        return ok_all, mean_cer, worst, lat

    # ── NPU path ─────────────────────────────────────────────────────────────
    if not args.skip_npu:
        if args.fresh:
            import shutil
            d = MODELS / f"qai-whisper_base-{detect_chipset()}"
            if d.exists():
                shutil.rmtree(d)
                print(f"--fresh: removed {d}")
        print("\n--- QNN / Hexagon NPU, whisper-base (Qualcomm AI Hub precompiled) ---")
        try:
            t0 = time.time()
            npu = QnnWhisperBackend({"model": "base"})
            load_s = time.time() - t0
            ok_en, mean_cer, worst, lat = run_suite("npu", npu, 0.05, 0.30, 2.0)
            check("H-103", ok_en and "QNNExecutionProvider" in npu.encoder.get_providers(),
                  f"encoder EP={npu.encoder.get_providers()[0]}, English WER<=0.05, "
                  f"load {load_s:.1f}s")
            check("H-104-npu", worst < 1.0,
                  f"worst end-to-end latency {worst*1000:.0f} ms over "
                  f"{len(lat)} utterances (partial budget 1.0 s, final 2.0 s)")
            check("H-105-base", mean_cer <= 0.30,
                  f"whisper-base Mandarin mean CER = {mean_cer:.3f} "
                  f"(usable but weak; turbo is far better)")

            # NPU vs CPU token-identity spot check
            if not args.skip_cpu:
                cpu = OnnxWhisperBackend({"model": "base", "intra_op_threads": 3})
                same = []
                for m in zh:
                    a = load_wav(FIXTURES / m["file"])
                    same.append(npu.transcribe(a, "zh") == cpu.transcribe(a, "zh"))
                check("H-103-parity", all(same),
                      f"NPU and CPU EP produced identical text on {len(same)}/"
                      f"{len(same)} Mandarin clips")
                del cpu
            del npu
        except Exception as e:
            check("H-103", False, f"{type(e).__name__}: {e}")

        if args.turbo:
            print("\n--- QNN / Hexagon NPU, whisper-large-v3-turbo ---")
            try:
                t0 = time.time()
                tb = QnnWhisperBackend({"model": "large-v3-turbo"})
                load_s = time.time() - t0
                ok_en, mean_cer, worst, _ = run_suite("turbo", tb, 0.05, 0.10, 2.0)
                check("H-105", mean_cer <= 0.10,
                      f"whisper-large-v3-turbo Mandarin mean CER = {mean_cer:.3f}, "
                      f"worst latency {worst*1000:.0f} ms, session load {load_s:.0f}s")
                del tb
            except Exception as e:
                check("H-105", False, f"{type(e).__name__}: {e}")

    # ── CPU path ─────────────────────────────────────────────────────────────
    if not args.skip_cpu:
        print("\n--- CPU ExecutionProvider, whisper-tiny / whisper-base ---")
        try:
            tiny = OnnxWhisperBackend({"model": "tiny", "intra_op_threads": 3})
            ok_en, mean_cer, worst, _ = run_suite("cpu-tiny", tiny, 0.05, 0.60, 2.0)
            check("H-100", ok_en,
                  f"onnx-community/whisper-tiny on CPUExecutionProvider: English "
                  f"WER<=0.05, worst latency {worst*1000:.0f} ms")
            check("H-104-cpu", worst < 2.0,
                  f"whisper-tiny CPU worst latency {worst*1000:.0f} ms "
                  f"(final budget 2.0 s; partial budget 1.0 s)")
            del tiny
        except Exception as e:
            check("H-100", False, f"{type(e).__name__}: {e}")

    print("\n" + "=" * 78)
    for hyp, ok, ev in RESULTS:
        print(f"{'PASS' if ok else 'FAIL'}  {hyp:16s} {ev}")
    failed = [h for h, ok, _ in RESULTS if not ok]
    print("=" * 78)
    print("ALL CHECKS PASSED" if not failed else f"FAILURES: {failed}")
    return 0 if not failed else 1


if __name__ == "__main__":
    os.environ.setdefault("PYTHONIOENCODING", "utf-8")
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    raise SystemExit(main())
