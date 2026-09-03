#!/usr/bin/env python3
"""Compare decoding beam widths on real audio: accuracy against latency.

This is the measurement that decides whether beam search is shippable. Beam width
buys accuracy at a cost in latency, and Laolao's latency budget is not a
preference — captions that arrive late are captions the reader has already
watched the speaker move past.

Run it on the machine you care about, because the answer is per-platform:

    python tests/bench_decode.py                       # every installed fixture
    python tests/bench_decode.py --beams 1 2 4 8
    python tests/bench_decode.py --repeat 5 --language zh

Reports, per beam width: the transcript, character error rate against the
fixture's ground truth, and median/p90 wall-clock transcription time.

CER is scored after the same Traditional → Simplified conversion the app applies
(config.json's "t2s"), because that is what the reader actually sees. Skipping it
is not neutral: under noise the model sometimes flips to Traditional characters,
and scoring the raw output then reports a ~25% error rate for a caption that
would have rendered perfectly. Use --no-t2s to see the unconverted output.

Fixtures come from tests/fixtures/, which is gitignored — generate it first:

    python tests/generate_test_audio.py
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
import time
import wave
import zlib
from pathlib import Path

import numpy as np

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

FIXTURES = PROJECT_ROOT / "tests" / "fixtures"


# ── metrics ──────────────────────────────────────────────────────────────────

def edit_distance(a: str, b: str) -> int:
    """Levenshtein distance, two rows instead of a full matrix."""
    if len(a) < len(b):
        a, b = b, a
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb)))
        prev = cur
    return prev[-1]


# Punctuation Whisper adds freely and the corpora do not transcribe. Scoring it
# would report a "wrong character" for a comma the reference never had, which
# tells us nothing about whether the words were heard correctly.
_IGNORED = set(" \t\n，。！？、；：,.!?;:「」《》\"'()（）…—-")


def normalise(text: str) -> str:
    return "".join(ch for ch in text if ch not in _IGNORED)


def cer(reference: str, hypothesis: str) -> float:
    ref, hyp = normalise(reference), normalise(hypothesis)
    if not ref:
        return 0.0 if not hyp else 1.0
    return edit_distance(ref, hyp) / len(ref)


# ── fixtures ─────────────────────────────────────────────────────────────────

def read_wav(path: Path) -> tuple[np.ndarray, int]:
    with wave.open(str(path), "rb") as w:
        if w.getsampwidth() != 2:
            raise ValueError(f"{path}: expected 16-bit PCM")
        frames = w.readframes(w.getnframes())
        audio = np.frombuffer(frames, dtype=np.int16)
        if w.getnchannels() == 2:
            audio = audio.reshape(-1, 2).mean(axis=1).astype(np.int16)
        return audio, w.getframerate()


def load_fixtures(only: list[str] | None) -> list[dict]:
    out = []
    for meta_path in sorted(FIXTURES.glob("*.source.json")):
        meta = json.loads(meta_path.read_text(encoding="utf-8-sig"))
        stem = meta_path.name.removesuffix(".source.json")
        if only and stem not in only:
            continue
        wav = FIXTURES / meta.get("wav", f"{stem}.wav")
        if not wav.exists():
            print(f"  skip {stem}: {wav.name} not generated")
            continue
        audio, sr = read_wav(wav)
        if sr != 16000:
            print(f"  skip {stem}: {sr} Hz, expected 16000")
            continue
        out.append({
            "name": stem,
            "audio": audio,
            "seconds": len(audio) / sr,
            "transcript": meta.get("transcript", ""),
            "language": meta.get("language"),
        })
    return out


def add_noise(audio: np.ndarray, snr_db: float, seed: int = 0) -> np.ndarray:
    """Mix white noise into int16 audio at a given signal-to-noise ratio.

    Deterministic per (fixture, SNR) so a rerun scores the identical waveform —
    a benchmark whose input moves between runs cannot compare two decoders.
    """
    sig = audio.astype(np.float64)
    power = np.mean(sig ** 2)
    if power <= 0:
        return audio
    rng = np.random.default_rng(seed)
    noise = rng.normal(0.0, 1.0, size=sig.shape)
    noise *= np.sqrt(power / (10 ** (snr_db / 10)) / np.mean(noise ** 2))
    return np.clip(sig + noise, -32768, 32767).astype(np.int16)


# ── the benchmark ────────────────────────────────────────────────────────────

def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--beams", type=int, nargs="+", default=[1, 4],
                    help="beam widths to compare (default: 1 4)")
    ap.add_argument("--repeat", type=int, default=3,
                    help="timed runs per width, after one warm-up (default: 3)")
    ap.add_argument("--language", default=None,
                    help="force a language instead of the fixture's own")
    ap.add_argument("--fixtures", nargs="*", default=None,
                    help="fixture stems to run (default: all)")
    ap.add_argument("--snr", type=float, nargs="*", default=None, metavar="DB",
                    help="also score each fixture with white noise mixed in at "
                         "these signal-to-noise ratios, e.g. --snr 10 5 0. Clean "
                         "read speech is where beam search has least to offer; "
                         "degraded audio is where it should show up.")
    ap.add_argument("--no-t2s", action="store_true",
                    help="score the raw model output instead of the Simplified "
                         "text the app would actually display")
    ap.add_argument("--config", default=str(PROJECT_ROOT / "config.json"))
    ap.add_argument("--model", default=None, help="override config.json's model")
    args = ap.parse_args()

    # utf-8-sig: a config written by Notepad or PowerShell carries a BOM.
    cfg = json.loads(Path(args.config).read_text(encoding="utf-8-sig"))
    if args.model:
        cfg["model"] = args.model

    # Same conversion server.py applies to every zh/yue caption.
    to_simplified = None
    if cfg.get("t2s", True) and not args.no_t2s:
        try:
            from opencc import OpenCC

            _cc = OpenCC("t2s")
            to_simplified = _cc.convert
        except Exception:                                   # noqa: BLE001
            print("note: opencc unavailable — scoring raw output")

    from backends import get_backend

    t0 = time.perf_counter()
    backend = get_backend(cfg)
    print(f"backend : {backend.name}  (loaded in {time.perf_counter() - t0:.1f}s)")
    print(f"model   : {getattr(backend, 'cfg', cfg).get('model', cfg.get('model'))}")

    fixtures = load_fixtures(args.fixtures)
    if not fixtures:
        print("\nNo fixtures found. Run: python tests/generate_test_audio.py")
        return 1

    # Each fixture is scored clean, then again at every requested SNR.
    conditions = [(fx, None) for fx in fixtures]
    for snr in args.snr or []:
        conditions += [(fx, snr) for fx in fixtures]

    rows = []
    for fx, snr in conditions:
        language = args.language or fx["language"]
        label = fx["name"] if snr is None else f"{fx['name']}@{snr:g}dB"
        # crc32, not hash(): Python randomises string hashing per process, so
        # hash() would give a different noise waveform on every run and quietly
        # break the comparability this benchmark exists for.
        audio = fx["audio"] if snr is None else add_noise(
            fx["audio"], snr, seed=zlib.crc32(fx["name"].encode())
        )
        print(f"\n─ {label}  ({fx['seconds']:.1f}s, lang={language})")
        print(f"  reference : {fx['transcript']}")

        for beams in args.beams:
            # One warm-up pass: the first call through a QNN context or a fresh
            # ORT session pays a one-off cost that is not the steady-state
            # latency a caller would see.
            backend.transcribe(audio, language, beam_size=beams)

            times, text = [], ""
            for _ in range(args.repeat):
                t = time.perf_counter()
                text = backend.transcribe(audio, language, beam_size=beams)
                times.append((time.perf_counter() - t) * 1000)

            if to_simplified is not None and (language or "") in ("zh", "yue"):
                text = to_simplified(text)
            err = cer(fx["transcript"], text)
            rows.append({
                "fixture": label, "beams": beams, "cer": err,
                "median_ms": statistics.median(times), "max_ms": max(times),
                "rtf": statistics.median(times) / 1000 / fx["seconds"],
                "text": text,
            })
            print(f"  beam {beams:<2} CER {err * 100:5.1f}%  "
                  f"median {statistics.median(times):7.1f} ms  "
                  f"max {max(times):7.1f} ms  |  {text}")

    print("\n" + "=" * 78)
    print(f"{'fixture':<26}{'beam':>5}{'CER':>9}{'median ms':>12}{'max ms':>10}{'RTF':>8}")
    print("-" * 78)
    for r in rows:
        print(f"{r['fixture']:<26}{r['beams']:>5}{r['cer'] * 100:>8.1f}%"
              f"{r['median_ms']:>12.1f}{r['max_ms']:>10.1f}{r['rtf']:>8.2f}")

    baseline = [r for r in rows if r["beams"] == min(args.beams)]
    if baseline and len(args.beams) > 1:
        base_ms = statistics.median([r["median_ms"] for r in baseline])
        base_cer = statistics.mean([r["cer"] for r in baseline])
        for beams in args.beams:
            if beams == min(args.beams):
                continue
            sel = [r for r in rows if r["beams"] == beams]
            ms = statistics.median([r["median_ms"] for r in sel])
            c = statistics.mean([r["cer"] for r in sel])
            print(f"\nbeam {beams} vs beam {min(args.beams)}: "
                  f"{ms / base_ms:.2f}x latency, "
                  f"CER {base_cer * 100:.1f}% -> {c * 100:.1f}%")

    print("\nRTF is transcription time / audio duration. Laolao re-transcribes a "
          "rolling window, so\nwhat matters is the absolute median against the "
          "partial budget, not RTF alone.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
