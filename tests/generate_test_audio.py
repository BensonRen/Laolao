"""
generate_test_audio.py — Generate test WAV files for Laolao tests.

Usage (standalone):
    python tests/generate_test_audio.py [--output-dir tests/fixtures]
    python tests/generate_test_audio.py --no-download   # skip network fetch

All audio generation uses only stdlib + numpy.  The `wave` module from
stdlib is used for WAV I/O so scipy is NOT required.

Platform TTS
------------
* macOS   — `say` with a Mandarin voice (Tingting/Meijia) and an English
            voice (Samantha/Alex), post-processed to 16 kHz mono.
* Windows — SAPI via ``System.Speech.Synthesis.SpeechSynthesizer`` driven
            from PowerShell, writing 16 kHz / 16-bit / mono directly through
            ``SpeechAudioFormatInfo`` so no resampling is needed.

Ground truth
------------
Every *speech* WAV is written together with a ``.txt`` file of the same stem
holding **only** the transcript.  That is the contract
``docs/snapdragon/acceptance/check.py`` relies on (A2/A3): a transcript with
nothing to compare against proves nothing.  Provenance (voice, source URL,
license) goes into a ``<stem>.source.json`` sidecar and ``README.md`` so it
never contaminates the expected text.

Mandarin without a Mandarin voice
---------------------------------
Windows ships no Chinese SAPI voice unless a language pack is installed, and
installing one needs an interactive admin flow.  When no Chinese voice is
present the generator falls back to downloading an openly-licensed Mandarin
clip whose transcript is *published by the source* (see ``MANDARIN_SOURCES``).
It never invents a transcript.
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import shutil
import subprocess
import sys
import tarfile
import tempfile
import urllib.error
import urllib.request
import wave
from pathlib import Path

import numpy as np

SAMPLE_RATE = 16000  # Hz — matches Whisper's expected input
SAMPLE_WIDTH = 2     # bytes — 16-bit PCM
CHANNELS = 1         # mono


# ---------------------------------------------------------------------------
# Core generators
# ---------------------------------------------------------------------------

def generate_silence(duration_s: float = 2.0, sample_rate: int = SAMPLE_RATE) -> np.ndarray:
    """Return an int16 numpy array of silence (zeros)."""
    n_samples = int(duration_s * sample_rate)
    return np.zeros(n_samples, dtype=np.int16)


def generate_sine_tone(
    freq: float = 440.0,
    duration_s: float = 1.0,
    amplitude: float = 0.5,
    sample_rate: int = SAMPLE_RATE,
) -> np.ndarray:
    """Return an int16 numpy array containing a pure sine tone at *freq* Hz.

    Useful for VAD threshold testing (a loud, non-speech signal that should
    trigger energy-based VAD but be ignored by speech-focused VAD).

    Args:
        freq:       Tone frequency in Hz.
        duration_s: Duration in seconds.
        amplitude:  Peak amplitude as a fraction of the int16 range [0, 1].
        sample_rate: Sample rate in Hz.

    Returns:
        1-D int16 numpy array.
    """
    n_samples = int(duration_s * sample_rate)
    t = np.linspace(0, duration_s, n_samples, endpoint=False)
    wave_f32 = np.sin(2 * np.pi * freq * t) * amplitude
    return (wave_f32 * 32767).astype(np.int16)


# ---------------------------------------------------------------------------
# WAV I/O
# ---------------------------------------------------------------------------

def save_wav(path: Path, audio: np.ndarray, sample_rate: int = SAMPLE_RATE) -> None:
    """Write a 1-D int16 numpy array to a mono 16-bit WAV file.

    Uses the stdlib ``wave`` module — no scipy or soundfile needed.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as wf:
        wf.setnchannels(1)         # mono
        wf.setsampwidth(2)         # 16-bit = 2 bytes
        wf.setframerate(sample_rate)
        wf.writeframes(audio.astype(np.int16).tobytes())


def load_wav(path: Path) -> tuple[np.ndarray, int]:
    """Load a WAV file and return (int16 array, sample_rate).

    Only handles mono 16-bit WAV files (the format we write).
    """
    with wave.open(str(path), "rb") as wf:
        sr = wf.getframerate()
        n_frames = wf.getnframes()
        raw = wf.readframes(n_frames)
    audio = np.frombuffer(raw, dtype=np.int16).copy()
    return audio, sr


def load_wav_any(path: Path) -> tuple[np.ndarray, int]:
    """Load *any* PCM WAV (8/16/32-bit, mono or multi-channel) as int16 mono.

    Returns ``(int16 mono array, sample_rate)``.  Used to normalise audio that
    we did not write ourselves (downloaded corpus samples).
    """
    with wave.open(str(path), "rb") as wf:
        n_ch = wf.getnchannels()
        width = wf.getsampwidth()
        sr = wf.getframerate()
        raw = wf.readframes(wf.getnframes())

    if width == 1:                                   # unsigned 8-bit
        a = (np.frombuffer(raw, dtype=np.uint8).astype(np.int16) - 128) * 256
    elif width == 2:
        a = np.frombuffer(raw, dtype=np.int16).astype(np.int32)
    elif width == 4:                                 # 32-bit int PCM
        a = (np.frombuffer(raw, dtype=np.int32) >> 16).astype(np.int32)
    else:
        raise ValueError(f"{path.name}: unsupported sample width {width * 8}-bit")

    if n_ch > 1:
        a = a[: len(a) // n_ch * n_ch].reshape(-1, n_ch).mean(axis=1)

    return np.clip(np.rint(a), -32768, 32767).astype(np.int16), sr


def resample_to(audio: np.ndarray, src_rate: int, dst_rate: int = SAMPLE_RATE) -> np.ndarray:
    """Linearly resample an int16 mono array.  No scipy, no new dependencies."""
    if src_rate == dst_rate or len(audio) == 0:
        return audio.astype(np.int16)
    n_out = int(round(len(audio) * dst_rate / src_rate))
    x_out = np.linspace(0, len(audio) - 1, n_out)
    y = np.interp(x_out, np.arange(len(audio)), audio.astype(np.float64))
    return np.clip(np.rint(y), -32768, 32767).astype(np.int16)


def normalize_wav_file(src: Path, dst: Path) -> None:
    """Rewrite *src* as a 16 kHz / 16-bit / mono PCM WAV at *dst*."""
    audio, sr = load_wav_any(src)
    save_wav(dst, resample_to(audio, sr, SAMPLE_RATE))


# ---------------------------------------------------------------------------
# Ground truth + verification
# ---------------------------------------------------------------------------

def write_ground_truth(
    wav_path: Path,
    transcript: str,
    **provenance: object,
) -> Path:
    """Write the ground-truth transcript beside *wav_path*.

    ``<stem>.txt`` holds **only** the transcript — ``check.py`` reads the whole
    file and compares it to the STT output, so nothing else may go in there.
    Provenance (voice, source URL, license) is written to ``<stem>.source.json``.

    Returns the path of the ``.txt`` file.
    """
    txt_path = wav_path.with_suffix(".txt")
    txt_path.write_text(transcript.strip() + "\n", encoding="utf-8")
    if provenance:
        meta = {"wav": wav_path.name, "transcript": transcript.strip(), **provenance}
        wav_path.with_suffix(".source.json").write_text(
            json.dumps(meta, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
        )
    return txt_path


def inspect_wav(path: Path) -> dict:
    """Read a WAV back off disk and report what it *actually* contains.

    Never trust the writer: a "speech" fixture that is silent is a classic
    failure mode, so peak/RMS amplitude are measured, not assumed.
    """
    with wave.open(str(path), "rb") as wf:
        n_ch, width, sr, n_frames = (
            wf.getnchannels(), wf.getsampwidth(), wf.getframerate(), wf.getnframes()
        )
        raw = wf.readframes(n_frames)
    a = np.frombuffer(raw, dtype=np.int16) if width == 2 else np.zeros(0, dtype=np.int16)
    peak = int(np.abs(a).max()) if a.size else 0
    rms = float(np.sqrt(np.mean(a.astype(np.float64) ** 2))) if a.size else 0.0
    return {
        "path": str(path),
        "channels": n_ch,
        "bits": width * 8,
        "sample_rate": sr,
        "frames": n_frames,
        "duration_s": round(n_frames / sr, 3) if sr else 0.0,
        "peak": peak,
        "rms": round(rms, 1),
        "bytes": path.stat().st_size,
    }


def verify_wav(
    path: Path,
    *,
    expect_speech: bool,
    require_transcript: bool = True,
    min_duration_s: float = 0.3,
    min_peak: int = 1000,
) -> tuple[bool, str]:
    """Validate a fixture by reading it back.  Returns ``(ok, message)``."""
    if not path.exists():
        return False, f"{path.name}: missing"
    try:
        info = inspect_wav(path)
    except Exception as exc:                              # noqa: BLE001
        return False, f"{path.name}: unreadable ({exc!r})"

    problems: list[str] = []
    if info["sample_rate"] != SAMPLE_RATE:
        problems.append(f"sample_rate={info['sample_rate']} (want {SAMPLE_RATE})")
    if info["channels"] != CHANNELS:
        problems.append(f"channels={info['channels']} (want {CHANNELS})")
    if info["bits"] != SAMPLE_WIDTH * 8:
        problems.append(f"bits={info['bits']} (want {SAMPLE_WIDTH * 8})")
    if expect_speech:
        if info["duration_s"] < min_duration_s:
            problems.append(f"duration={info['duration_s']}s < {min_duration_s}s")
        if info["peak"] < min_peak:
            problems.append(f"peak={info['peak']} < {min_peak} — file is (near) silent")
        if info["rms"] < 50:
            problems.append(f"rms={info['rms']} — no meaningful signal")
        if require_transcript:
            txt = path.with_suffix(".txt")
            if not txt.exists() or not txt.read_text(encoding="utf-8").strip():
                problems.append("missing/empty ground-truth .txt")

    summary = (
        f"{path.name}: {info['sample_rate']} Hz, {info['channels']}ch, "
        f"{info['bits']}-bit, {info['duration_s']}s, peak={info['peak']}, "
        f"rms={info['rms']}"
    )
    return (not problems), summary + ("  FAILED: " + "; ".join(problems) if problems else "  OK")


# ---------------------------------------------------------------------------
# macOS TTS helpers
# ---------------------------------------------------------------------------

_MACOS_MANDARIN_VOICES = ["Tingting", "Meijia"]   # zh-CN
_MACOS_CANTONESE_VOICES = ["Sin-ji"]               # zh-HK
_MACOS_ENGLISH_VOICES = ["Samantha", "Alex"]       # en-US


def _available_macos_voices() -> list[str]:
    """Return list of installed `say` voices on macOS."""
    result = subprocess.run(
        ["say", "--voice", "?"],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        return []
    return [line.split()[0] for line in result.stdout.splitlines() if line.strip()]


def _pick_voice(candidates: list[str], available: list[str]) -> str | None:
    """Return the first candidate voice that is available, or None."""
    for voice in candidates:
        if voice in available:
            return voice
    return None


def generate_chinese_tts_macos(
    text: str,
    output_path: Path,
) -> bool:
    """Generate Mandarin speech via macOS `say` command.

    Uses Tingting or Meijia (Mandarin) or falls back to Sin-ji (Cantonese).
    The audio is saved as a 16 kHz mono WAV at *output_path*.

    Args:
        text:        Chinese text to synthesise.
        output_path: Destination WAV file path.

    Returns:
        True on success, False if not on macOS or no suitable voice is found.
    """
    if platform.system() != "Darwin":
        return False
    if not shutil.which("say"):
        return False

    available = _available_macos_voices()
    voice = _pick_voice(_MACOS_MANDARIN_VOICES + _MACOS_CANTONESE_VOICES, available)
    if voice is None:
        print(
            f"[generate_test_audio] No Mandarin/Cantonese voice found. "
            f"Available voices: {available[:10]}",
            file=sys.stderr,
        )
        return False

    return _run_macos_say(text, voice, output_path)


def generate_english_tts_macos(
    text: str,
    output_path: Path,
) -> bool:
    """Generate English speech via macOS `say` command.

    Args:
        text:        English text to synthesise.
        output_path: Destination WAV file path.

    Returns:
        True on success, False if not on macOS.
    """
    if platform.system() != "Darwin":
        return False
    if not shutil.which("say"):
        return False

    available = _available_macos_voices()
    voice = _pick_voice(_MACOS_ENGLISH_VOICES, available) or (available[0] if available else None)
    if voice is None:
        return False

    return _run_macos_say(text, voice, output_path)


def _run_macos_say(text: str, voice: str, output_path: Path) -> bool:
    """Run `say` with ffmpeg post-processing to produce a 16 kHz mono WAV."""
    output_path.parent.mkdir(parents=True, exist_ok=True)

    # `say` outputs AIFF by default; we use ffmpeg to convert if available,
    # otherwise save as AIFF and convert manually via the wave module fallback.
    aiff_path = output_path.with_suffix(".aiff")

    say_result = subprocess.run(
        ["say", "--voice", voice, "--output-file", str(aiff_path), text],
        capture_output=True,
        check=False,
    )
    if say_result.returncode != 0:
        print(
            f"[generate_test_audio] `say` failed: {say_result.stderr.decode()}",
            file=sys.stderr,
        )
        return False

    # Convert AIFF → 16 kHz mono WAV via ffmpeg (preferred).
    if shutil.which("ffmpeg"):
        ff_result = subprocess.run(
            [
                "ffmpeg", "-y",
                "-i", str(aiff_path),
                "-ar", "16000",
                "-ac", "1",
                "-sample_fmt", "s16",
                str(output_path),
            ],
            capture_output=True,
            check=False,
        )
        aiff_path.unlink(missing_ok=True)
        if ff_result.returncode != 0:
            print(
                f"[generate_test_audio] ffmpeg conversion failed: "
                f"{ff_result.stderr.decode()[-500:]}",
                file=sys.stderr,
            )
            return False
        return True

    # Fallback: use macOS built-in afconvert (AIFF → 16 kHz mono WAV).
    if shutil.which("afconvert"):
        af_result = subprocess.run(
            [
                "afconvert",
                "-f", "WAVE",
                "-d", "LEI16@16000",
                "-c", "1",
                str(aiff_path),
                str(output_path),
            ],
            capture_output=True,
            check=False,
        )
        aiff_path.unlink(missing_ok=True)
        if af_result.returncode == 0:
            return True
        print(
            f"[generate_test_audio] afconvert failed: {af_result.stderr.decode()}",
            file=sys.stderr,
        )
        return False

    aiff_path.unlink(missing_ok=True)
    print(
        "[generate_test_audio] Neither ffmpeg nor afconvert found — cannot convert AIFF to WAV.",
        file=sys.stderr,
    )
    return False


# ---------------------------------------------------------------------------
# Windows SAPI TTS helpers
# ---------------------------------------------------------------------------
# Mirrors the macOS block above: a voice picker, one entry point per language,
# and a single `_run_windows_sapi` that does the actual synthesis.
#
# SAPI can write the WAV in the exact format we need, so unlike the macOS path
# there is no ffmpeg/afconvert post-processing step and no resampling.

# Substring matched case-insensitively against the installed voice names.
_WINDOWS_MANDARIN_VOICES = ["Huihui", "Yaoyao", "Kangkang", "zh-CN", "Chinese"]
_WINDOWS_ENGLISH_VOICES = ["Zira", "David", "Mark", "en-US", "English"]

# Synthesise straight into Whisper's input format: 16 kHz, 16-bit, mono.
_SAPI_SCRIPT = r"""
$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName System.Speech
$text = [IO.File]::ReadAllText($env:LAOLAO_TTS_TEXTFILE, [Text.Encoding]::UTF8)
$synth = New-Object System.Speech.Synthesis.SpeechSynthesizer
try {
    if ($env:LAOLAO_TTS_VOICE) { $synth.SelectVoice($env:LAOLAO_TTS_VOICE) }
    $synth.Rate = 0
    $synth.Volume = 100
    $fmt = New-Object System.Speech.AudioFormat.SpeechAudioFormatInfo(
        %(rate)d,
        [System.Speech.AudioFormat.AudioBitsPerSample]::Sixteen,
        [System.Speech.AudioFormat.AudioChannel]::Mono)
    $synth.SetOutputToWaveFile($env:LAOLAO_TTS_OUT, $fmt)
    $synth.Speak($text)
    $synth.SetOutputToNull()
} finally {
    $synth.Dispose()
}
"""

_LIST_VOICES_SCRIPT = r"""
$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName System.Speech
$synth = New-Object System.Speech.Synthesis.SpeechSynthesizer
try {
    foreach ($v in $synth.GetInstalledVoices()) {
        if ($v.Enabled) {
            $i = $v.VoiceInfo
            Write-Output ("{0}`t{1}" -f $i.Name, $i.Culture.Name)
        }
    }
} finally {
    $synth.Dispose()
}
"""


def _powershell(script: str, env_extra: dict[str, str] | None = None) -> subprocess.CompletedProcess:
    """Run a PowerShell snippet, passing arguments via the environment.

    Arguments travel in environment variables rather than the command line so
    that Unicode text and quotes can never break the script or be injected.
    """
    exe = shutil.which("powershell") or shutil.which("pwsh")
    if exe is None:
        raise FileNotFoundError("neither powershell nor pwsh is on PATH")
    env = dict(os.environ)
    env.update(env_extra or {})
    with tempfile.TemporaryDirectory() as tmp:
        ps1 = Path(tmp) / "laolao_tts.ps1"
        ps1.write_text(script, encoding="utf-8-sig")
        return subprocess.run(
            [exe, "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
             "-File", str(ps1)],
            capture_output=True, text=True, check=False, env=env,
        )


def _available_windows_voices() -> list[tuple[str, str]]:
    """Return installed SAPI voices as ``[(name, culture), ...]``."""
    if platform.system() != "Windows":
        return []
    try:
        result = _powershell(_LIST_VOICES_SCRIPT)
    except FileNotFoundError:
        return []
    if result.returncode != 0:
        print(
            f"[generate_test_audio] Could not enumerate SAPI voices: "
            f"{result.stderr.strip()[:400]}",
            file=sys.stderr,
        )
        return []
    voices: list[tuple[str, str]] = []
    for line in result.stdout.splitlines():
        if "\t" in line:
            name, culture = line.split("\t", 1)
            voices.append((name.strip(), culture.strip()))
    return voices


def _pick_windows_voice(
    candidates: list[str],
    available: list[tuple[str, str]],
) -> str | None:
    """Return the first installed voice whose name or culture matches a hint."""
    for hint in candidates:
        needle = hint.lower()
        for name, culture in available:
            if needle in name.lower() or needle in culture.lower():
                return name
    return None


def generate_chinese_tts_windows(text: str, output_path: Path) -> bool:
    """Generate Mandarin speech via Windows SAPI.

    Windows only ships Chinese voices when the corresponding language pack is
    installed, which needs an interactive/admin flow.  On a stock en-US image
    this returns False and the caller must fall back to a downloaded fixture.

    Args:
        text:        Chinese text to synthesise.
        output_path: Destination WAV file path.

    Returns:
        True on success, False if not on Windows or no Chinese voice exists.
    """
    if platform.system() != "Windows":
        return False

    available = _available_windows_voices()
    voice = _pick_windows_voice(_WINDOWS_MANDARIN_VOICES, available)
    if voice is None:
        print(
            f"[generate_test_audio] No Chinese SAPI voice installed. "
            f"Available voices: {[f'{n} ({c})' for n, c in available]}",
            file=sys.stderr,
        )
        return False

    return _run_windows_sapi(text, voice, output_path)


def generate_english_tts_windows(text: str, output_path: Path) -> bool:
    """Generate English speech via Windows SAPI.

    Args:
        text:        English text to synthesise.
        output_path: Destination WAV file path.

    Returns:
        True on success, False if not on Windows or no voice is available.
    """
    if platform.system() != "Windows":
        return False

    available = _available_windows_voices()
    voice = (
        _pick_windows_voice(_WINDOWS_ENGLISH_VOICES, available)
        or (available[0][0] if available else None)
    )
    if voice is None:
        print(
            "[generate_test_audio] No SAPI voice is installed — cannot synthesise English.",
            file=sys.stderr,
        )
        return False

    return _run_windows_sapi(text, voice, output_path)


def _run_windows_sapi(
    text: str,
    voice: str,
    output_path: Path,
    sample_rate: int = SAMPLE_RATE,
) -> bool:
    """Speak *text* with SAPI *voice* straight into a 16 kHz mono 16-bit WAV.

    ``SpeechAudioFormatInfo`` pins the output format, so the file needs no
    resampling.  The result is read back and validated before we claim success.
    """
    output_path.parent.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory() as tmp:
        text_file = Path(tmp) / "text.txt"
        text_file.write_text(text, encoding="utf-8")
        try:
            result = _powershell(
                _SAPI_SCRIPT % {"rate": sample_rate},
                {
                    "LAOLAO_TTS_TEXTFILE": str(text_file),
                    "LAOLAO_TTS_VOICE": voice,
                    "LAOLAO_TTS_OUT": str(output_path),
                },
            )
        except FileNotFoundError as exc:
            print(f"[generate_test_audio] {exc}", file=sys.stderr)
            return False

    if result.returncode != 0:
        print(
            f"[generate_test_audio] SAPI synthesis failed ({voice}): "
            f"{result.stderr.strip()[:600]}",
            file=sys.stderr,
        )
        return False

    ok, message = verify_wav(output_path, expect_speech=True, require_transcript=False)
    if not ok:
        # A silent or wrongly-formatted WAV is worse than no WAV at all: it
        # would silently poison every downstream STT comparison.
        print(f"[generate_test_audio] SAPI output rejected — {message}", file=sys.stderr)
        return False
    return True


# ---------------------------------------------------------------------------
# Mandarin fallback — openly-licensed clips with a PUBLISHED transcript
# ---------------------------------------------------------------------------
# Used when the host has no Mandarin TTS voice (the normal case on a stock
# Windows en-US image).  Every entry must carry a transcript copied verbatim
# from the upstream source plus the URL that publishes it — a transcript we
# guessed by ear would make A3 meaningless.

MANDARIN_SOURCES: list[dict] = [
    {
        "id": "aishell1-BAC009S0764W0121",
        "description": (
            "AISHELL-1 test-set utterance BAC009S0764W0121, redistributed as a "
            "test_wavs/ sample in the icefall AISHELL Zipformer model repo."
        ),
        # Byte-identical mirrors — the same three utterances plus transcript.txt
        # are shipped with several icefall/sherpa AISHELL model repos.
        "wav_urls": [
            "https://huggingface.co/zrjin/icefall-asr-aishell-zipformer-2023-10-24/"
            "resolve/main/test_wavs/BAC009S0764W0121.wav",
            "https://huggingface.co/csukuangfj/"
            "icefall-aishell-pruned-transducer-stateless3-2022-06-20/"
            "resolve/main/test_wavs/BAC009S0764W0121.wav",
            "https://huggingface.co/marcoyang/"
            "icefall-asr-aishell-zipformer-pruned-transducer-stateless7-2023-03-21/"
            "resolve/main/test_wavs/BAC009S0764W0121.wav",
        ],
        "sha256": "46dbc998c9d1d48111267c40741dd3200f2e5bcf4075f8c4c97f4451160dce50",
        # The transcript is FETCHED from the corpus file at generation time and
        # parsed by utterance id — it is never typed in by hand.  The literal
        # below is only a tripwire that fires if upstream ever changes.
        "transcript_url": (
            "https://huggingface.co/zrjin/icefall-asr-aishell-zipformer-2023-10-24/"
            "resolve/main/test_wavs/transcript.txt"
        ),
        "transcript_key": "BAC009S0764W0121",
        "transcript_expected": "甚至出现交易几乎停滞的情况",
        "license": "Apache License 2.0 (AISHELL-1 / OpenSLR SLR33)",
        "license_url": "https://www.openslr.org/33/",
        "attribution": (
            "AISHELL-1 Mandarin speech corpus, Beijing Shell Shell Technology Co., Ltd. "
            "Released under the Apache License v2.0 via OpenSLR SLR33."
        ),
    },
]


def _aishell_source(utt_id: str, sha256: str, expected: str) -> dict:
    """Build a MANDARIN_SOURCES entry for one AISHELL-1 test utterance.

    All three clips ride in the same icefall test_wavs/ directory behind the same
    transcript.txt, so the only per-utterance facts are the id, the checksum and
    the tripwire transcript. Spelling that out once keeps the extra fixtures from
    being three more copies of a 30-line record.
    """
    mirrors = [
        "https://huggingface.co/zrjin/icefall-asr-aishell-zipformer-2023-10-24/"
        f"resolve/main/test_wavs/{utt_id}.wav",
        "https://huggingface.co/csukuangfj/"
        "icefall-aishell-pruned-transducer-stateless3-2022-06-20/"
        f"resolve/main/test_wavs/{utt_id}.wav",
        "https://huggingface.co/marcoyang/"
        "icefall-asr-aishell-zipformer-pruned-transducer-stateless7-2023-03-21/"
        f"resolve/main/test_wavs/{utt_id}.wav",
    ]
    return {
        "id": f"aishell1-{utt_id}",
        "description": (
            f"AISHELL-1 test-set utterance {utt_id}, redistributed as a "
            "test_wavs/ sample in the icefall AISHELL Zipformer model repo."
        ),
        "wav_urls": mirrors,
        "sha256": sha256,
        "transcript_url": (
            "https://huggingface.co/zrjin/icefall-asr-aishell-zipformer-2023-10-24/"
            "resolve/main/test_wavs/transcript.txt"
        ),
        "transcript_key": utt_id,
        "transcript_expected": expected,
        "license": "Apache License 2.0 (AISHELL-1 / OpenSLR SLR33)",
        "license_url": "https://www.openslr.org/33/",
        "attribution": (
            "AISHELL-1 Mandarin speech corpus, Beijing Shell Shell Technology Co., Ltd. "
            "Released under the Apache License v2.0 via OpenSLR SLR33."
        ),
    }


# Additional Mandarin utterances, each written to its own fixture file.
#
# MANDARIN_SOURCES above is a *fallback chain* — the first entry that downloads
# wins and the rest are never used. These are different: one fixture each, so
# that character error rate is measured over ~40 characters of three speakers'
# sentences rather than the 13 characters of a single one. A CER computed on one
# short utterance moves in 7.7% steps and cannot distinguish two decoders.
MANDARIN_EXTRA_SOURCES: list[dict] = [
    _aishell_source(
        "BAC009S0764W0122",
        "5760c7ace0923a499f605d373011c7aaf658e304a0999a493c63669c756384e1",
        "一二线城市虽然也处于调整中",
    ),
    _aishell_source(
        "BAC009S0764W0123",
        "2c566703c6b1b075568cac56810846a48bb127c740d7a36de66084816035dd42",
        "但因为聚集了过多公共资源",
    ),
]


def _http_get(url: str, dest: Path, timeout: int = 120) -> bool:
    """Download *url* to *dest*.  Plain HTTP(S), no auth, no extra deps."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    req = urllib.request.Request(url, headers={"User-Agent": "laolao-fixtures/1.0"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp, dest.open("wb") as fh:
            shutil.copyfileobj(resp, fh)
    except (urllib.error.URLError, OSError, TimeoutError) as exc:
        print(f"[generate_test_audio] Download failed for {url}: {exc!r}", file=sys.stderr)
        return False
    return dest.exists() and dest.stat().st_size > 0


def _fetch_kaldi_transcript(url: str, utt_id: str, timeout: int = 60) -> str | None:
    """Pull the reference text for *utt_id* out of a Kaldi-style transcript file.

    The file has one ``<utt-id> <word> <word> …`` line per utterance; Mandarin
    references are word-segmented with spaces, which we strip so the result is
    the plain character sequence a recogniser would emit.
    """
    with tempfile.TemporaryDirectory() as tmp:
        dest = Path(tmp) / "transcript.txt"
        if not _http_get(url, dest, timeout=timeout):
            return None
        text = dest.read_text(encoding="utf-8")
    for line in text.splitlines():
        parts = line.split(None, 1)
        if len(parts) == 2 and parts[0] == utt_id:
            return "".join(parts[1].split())
    print(
        f"[generate_test_audio] {utt_id} not found in {url}",
        file=sys.stderr,
    )
    return None


def peak_normalize(audio: np.ndarray, target_dbfs: float = -3.0) -> np.ndarray:
    """Scale an int16 array so its peak sits at *target_dbfs*.

    Gain only — no filtering, no resampling, so the spoken content (and hence
    the transcript) is untouched.  AISHELL-1 is recorded very quietly (peak
    around -25 dBFS); leaving it that way would let an energy-based VAD gate
    the whole utterance out and make A3 fail for a reason that has nothing to
    do with the recogniser.
    """
    peak = int(np.abs(audio).max()) if audio.size else 0
    if peak == 0:
        return audio
    target = 32767 * (10 ** (target_dbfs / 20.0))
    return np.clip(np.rint(audio.astype(np.float64) * (target / peak)),
                   -32768, 32767).astype(np.int16)


def download_mandarin_fixture(
    output_path: Path,
    sources: list[dict] | None = None,
) -> dict | None:
    """Fetch an openly-licensed Mandarin clip and normalise it to 16 kHz mono.

    Tries each entry of *sources* in order (and each mirror within an entry).
    The transcript is downloaded from the source alongside the audio, so the
    ground truth is always the corpus's own, never ours.

    Returns a provenance dict for the candidate that succeeded — the source
    record plus the resolved ``transcript`` and ``resolved_url`` — or None if
    every candidate failed / no candidate is configured.
    """
    import hashlib

    sources = MANDARIN_SOURCES if sources is None else sources
    if not sources:
        return None

    output_path.parent.mkdir(parents=True, exist_ok=True)
    for src in sources:
        transcript = _fetch_kaldi_transcript(src["transcript_url"], src["transcript_key"])
        if not transcript:
            print(
                f"[generate_test_audio] No published transcript for {src['id']} — "
                "refusing to ship audio without ground truth.",
                file=sys.stderr,
            )
            continue
        if transcript != src.get("transcript_expected"):
            print(
                f"[generate_test_audio] WARNING: upstream transcript for "
                f"{src['transcript_key']} changed: {transcript!r} != "
                f"{src.get('transcript_expected')!r}. Using the upstream value.",
                file=sys.stderr,
            )

        for url in src.get("wav_urls") or [src["wav_url"]]:
            with tempfile.TemporaryDirectory() as tmp:
                tmpdir = Path(tmp)
                raw = tmpdir / "clip.wav"

                if src.get("archive_member"):
                    archive = tmpdir / "bundle.tar.bz2"
                    if not _http_get(url, archive):
                        continue
                    member = src["archive_member"]
                    try:
                        with tarfile.open(archive) as tf:
                            # Match on the tail of the path: release bundles
                            # wrap everything in a versioned top-level dir.
                            names = [n for n in tf.getnames() if n.endswith(member)]
                            if not names:
                                print(f"[generate_test_audio] {member} not in archive",
                                      file=sys.stderr)
                                continue
                            fh = tf.extractfile(names[0])
                            if fh is None:
                                continue
                            raw.write_bytes(fh.read())
                    except (tarfile.TarError, OSError) as exc:
                        print(f"[generate_test_audio] Extract failed: {exc!r}", file=sys.stderr)
                        continue
                elif not _http_get(url, raw):
                    continue

                digest = hashlib.sha256(raw.read_bytes()).hexdigest()
                if src.get("sha256") and digest != src["sha256"]:
                    print(
                        f"[generate_test_audio] WARNING: {url} sha256 {digest} != "
                        f"expected {src['sha256']} — upstream may have re-encoded it.",
                        file=sys.stderr,
                    )

                try:
                    audio, sr = load_wav_any(raw)
                    audio = peak_normalize(resample_to(audio, sr, SAMPLE_RATE))
                    save_wav(output_path, audio)
                except Exception as exc:                    # noqa: BLE001
                    print(f"[generate_test_audio] Normalise failed: {exc!r}", file=sys.stderr)
                    continue

            ok, message = verify_wav(output_path, expect_speech=True, require_transcript=False)
            if not ok:
                print(f"[generate_test_audio] Rejected {src['id']} — {message}", file=sys.stderr)
                output_path.unlink(missing_ok=True)
                continue
            return {**src, "transcript": transcript, "resolved_url": url, "sha256_actual": digest}

    return None


# ---------------------------------------------------------------------------
# Fixture bundle
# ---------------------------------------------------------------------------

# The canonical texts.  Kept here (not inline) so the ground-truth .txt files
# and the synthesiser can never drift apart.
ENGLISH_TEXT = "Hello grandma, I miss you very much."
ENGLISH_LONG_TEXT = (
    "And so, my fellow Americans, ask not what your country can do for you; "
    "ask what you can do for your country."
)
CHINESE_TEXT = "你好，奶奶。我今天很想念你。"


def generate_test_fixtures(output_dir: Path, allow_download: bool = True) -> dict[str, Path]:
    """Generate all standard test fixture WAV files in *output_dir*.

    Speech fixtures are always accompanied by a ``.txt`` ground truth of the
    same stem — that is what ``docs/snapdragon/acceptance/check.py`` (A2/A3)
    looks for.  The platform TTS path is chosen automatically.

    Returns a dict mapping fixture name → Path (only includes files that
    were successfully created).
    """
    output_dir.mkdir(parents=True, exist_ok=True)
    created: dict[str, Path] = {}
    system = platform.system()

    # --- silence ---
    silence_path = output_dir / "silence_2s.wav"
    save_wav(silence_path, generate_silence(duration_s=2.0))
    print(f"[generate_test_audio] Created {silence_path}")
    created["silence_2s"] = silence_path

    # --- 440 Hz sine tone ---
    tone_path = output_dir / "tone_440hz_1s.wav"
    save_wav(tone_path, generate_sine_tone(freq=440.0, duration_s=1.0))
    print(f"[generate_test_audio] Created {tone_path}")
    created["tone_440hz_1s"] = tone_path

    # --- Chinese TTS ---
    chinese_path = output_dir / "chinese_speech.wav"
    if system == "Darwin":
        ok = generate_chinese_tts_macos(CHINESE_TEXT, chinese_path)
    elif system == "Windows":
        ok = generate_chinese_tts_windows(CHINESE_TEXT, chinese_path)
    else:
        ok = False
    if ok:
        write_ground_truth(
            chinese_path, CHINESE_TEXT,
            origin="tts", platform=system, language="zh",
            license="synthesised locally; no third-party audio",
        )
        print(f"[generate_test_audio] Created {chinese_path} (+ ground truth)")
        created["chinese_speech"] = chinese_path
    elif allow_download:
        # No Mandarin voice on this host.  Fall back to a corpus clip whose
        # transcript is published upstream.
        print("[generate_test_audio] No Mandarin TTS voice — trying openly-licensed download…")
        src = download_mandarin_fixture(chinese_path)
        if src:
            write_ground_truth(
                chinese_path, src["transcript"],
                origin="download", language="zh",
                source_id=src["id"],
                description=src.get("description"),
                source_url=src["resolved_url"],
                mirrors=src.get("wav_urls"),
                sha256=src.get("sha256_actual"),
                transcript_url=src["transcript_url"],
                transcript_key=src.get("transcript_key"),
                license=src["license"],
                license_url=src.get("license_url"),
                attribution=src.get("attribution"),
                processing="peak-normalised to -3 dBFS; already 16 kHz mono 16-bit",
            )
            print(f"[generate_test_audio] Created {chinese_path} from {src['id']} (+ ground truth)")
            created["chinese_speech"] = chinese_path
        else:
            print(
                "[generate_test_audio] Skipped chinese_speech.wav — no Mandarin voice "
                "and no downloadable clip with a published transcript.",
                file=sys.stderr,
            )
    else:
        print(
            "[generate_test_audio] Skipped chinese_speech.wav "
            "(no Mandarin TTS voice; --no-download given)",
        )

    # --- Extra Mandarin utterances (download only; no TTS equivalent) ---
    # Purely additive: their absence never fails anything, but when they are
    # present the accuracy benchmarks have real sentences to score against.
    if allow_download:
        for n, src_spec in enumerate(MANDARIN_EXTRA_SOURCES, start=2):
            extra_path = output_dir / f"chinese_speech_{n}.wav"
            if extra_path.exists():
                created[extra_path.stem] = extra_path
                continue
            src = download_mandarin_fixture(extra_path, sources=[src_spec])
            if not src:
                print(f"[generate_test_audio] Skipped {extra_path.name} "
                      "(download or transcript unavailable)", file=sys.stderr)
                continue
            write_ground_truth(
                extra_path, src["transcript"],
                origin="download", language="zh",
                source_id=src["id"],
                description=src.get("description"),
                source_url=src["resolved_url"],
                mirrors=src.get("wav_urls"),
                sha256=src.get("sha256_actual"),
                transcript_url=src["transcript_url"],
                transcript_key=src.get("transcript_key"),
                license=src["license"],
                license_url=src.get("license_url"),
                attribution=src.get("attribution"),
                processing="peak-normalised to -3 dBFS; already 16 kHz mono 16-bit",
            )
            print(f"[generate_test_audio] Created {extra_path} from {src['id']} (+ ground truth)")
            created[extra_path.stem] = extra_path

    # --- English TTS ---
    english_specs = [
        ("english_speech", output_dir / "english_speech.wav", ENGLISH_TEXT),
        # `check.py:find_fixture` prefers the first *.wav whose name contains
        # "english", so the long clip is named "en_long_*" to keep A2 graded
        # against the short canonical utterance while still giving the latency
        # and streaming checks several seconds of real speech.
        ("en_long_speech", output_dir / "en_long_speech.wav", ENGLISH_LONG_TEXT),
    ]
    for name, path, text in english_specs:
        if system == "Darwin":
            ok = generate_english_tts_macos(text, path)
        elif system == "Windows":
            ok = generate_english_tts_windows(text, path)
        else:
            ok = False
        if ok:
            write_ground_truth(
                path, text,
                origin="tts", platform=system, language="en",
                license="synthesised locally; no third-party audio",
            )
            print(f"[generate_test_audio] Created {path} (+ ground truth)")
            created[name] = path
        else:
            print(
                f"[generate_test_audio] Skipped {path.name} "
                "(no usable system TTS on this platform)",
            )

    return created


def write_fixture_readme(output_dir: Path, created: dict[str, Path]) -> Path:
    """Write a provenance README so a reader can audit every fixture's origin."""
    lines = [
        "# tests/fixtures — generated audio",
        "",
        "Regenerate with `python tests/generate_test_audio.py`.",
        "The `.wav` files are gitignored; the `.txt` ground truth and this file are not.",
        "",
        "Every speech WAV has a `<stem>.txt` holding **only** its transcript —",
        "that is the contract `docs/snapdragon/acceptance/check.py` (A2/A3) relies on.",
        "Provenance lives in `<stem>.source.json` and in the table below.",
        "",
        "| fixture | format | duration | peak | rms | ground truth | origin |",
        "|---|---|---|---|---|---|---|",
    ]
    for name, path in sorted(created.items()):
        try:
            info = inspect_wav(path)
        except Exception:                                   # noqa: BLE001
            continue
        txt = path.with_suffix(".txt")
        gt = txt.read_text(encoding="utf-8").strip() if txt.exists() else "—"
        meta_path = path.with_suffix(".source.json")
        origin = "synthetic (numpy)"
        if meta_path.exists():
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
            if meta.get("origin") == "download":
                origin = f"{meta.get('source_id')} — {meta.get('license')}"
            else:
                origin = f"{meta.get('platform')} TTS"
        lines.append(
            f"| `{path.name}` | {info['sample_rate']} Hz / {info['channels']}ch / "
            f"{info['bits']}-bit | {info['duration_s']}s | {info['peak']} | {info['rms']} | "
            f"{gt} | {origin} |"
        )

    downloaded = [
        p for p in created.values()
        if p.with_suffix(".source.json").exists()
        and json.loads(p.with_suffix(".source.json").read_text(encoding="utf-8")).get("origin")
        == "download"
    ]
    if downloaded:
        lines += ["", "## Third-party audio", ""]
        for p in downloaded:
            meta = json.loads(p.with_suffix(".source.json").read_text(encoding="utf-8"))
            lines += [
                f"### `{p.name}`",
                "",
                f"- Source: <{meta.get('source_url')}>",
                f"- Transcript published at: <{meta.get('transcript_url')}> "
                f"(key `{meta.get('transcript_key')}`)",
                f"- License: {meta.get('license')} — <{meta.get('license_url')}>",
                f"- Attribution: {meta.get('attribution')}",
                f"- sha256 (as downloaded): `{meta.get('sha256')}`",
                f"- Processing: {meta.get('processing')}",
                "",
            ]

    readme = output_dir / "README.md"
    readme.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return readme


def verify_fixtures(created: dict[str, Path]) -> bool:
    """Read every generated fixture back and print what it really contains."""
    # chinese_speech_2 / _3 are speech too, so match the prefix rather than
    # listing names -- a new utterance fixture should not silently be verified
    # as if it were allowed to be silent.
    speech = {"english_speech", "en_long_speech"}
    all_ok = True
    print("\n[generate_test_audio] Verification (read back from disk):")
    for name, path in created.items():
        is_speech = name in speech or name.startswith("chinese_speech")
        ok, message = verify_wav(path, expect_speech=is_speech)
        all_ok &= ok
        print(f"  {'PASS' if ok else 'FAIL'}  {message}")
    return all_ok


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Generate test audio fixtures for Laolao.",
    )
    parser.add_argument(
        "--output-dir",
        default=str(Path(__file__).parent / "fixtures"),
        help="Directory to write fixture WAV files into (default: tests/fixtures/)",
    )
    parser.add_argument(
        "--no-download",
        action="store_true",
        help="Never touch the network; skip the Mandarin fallback clip.",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    output_dir = Path(args.output_dir)
    created = generate_test_fixtures(output_dir, allow_download=not args.no_download)
    ok = verify_fixtures(created)
    write_fixture_readme(output_dir, created)
    print(f"\n[generate_test_audio] Done. {len(created)} fixture(s) created in {output_dir}/")
    if not ok:
        print("[generate_test_audio] One or more fixtures failed verification.", file=sys.stderr)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
