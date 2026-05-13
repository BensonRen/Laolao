"""
generate_test_audio.py — Generate synthetic test WAV files for Laolao tests.

Usage (standalone):
    python tests/generate_test_audio.py [--output-dir tests/fixtures]

All audio generation uses only stdlib + numpy.  The `wave` module from
stdlib is used for WAV I/O so scipy is NOT required.

macOS TTS (Chinese/English speech samples) is attempted when the `say`
command and a Mandarin voice are available.
"""

from __future__ import annotations

import argparse
import platform
import shutil
import subprocess
import sys
import wave
from pathlib import Path

import numpy as np

SAMPLE_RATE = 16000  # Hz — matches Whisper's expected input


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
# Fixture bundle
# ---------------------------------------------------------------------------

def generate_test_fixtures(output_dir: Path) -> dict[str, Path]:
    """Generate all standard test fixture WAV files in *output_dir*.

    Returns a dict mapping fixture name → Path (only includes files that
    were successfully created).
    """
    output_dir.mkdir(parents=True, exist_ok=True)
    created: dict[str, Path] = {}

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

    # --- Chinese TTS (macOS only) ---
    chinese_path = output_dir / "chinese_speech.wav"
    ok = generate_chinese_tts_macos(
        "你好，奶奶。我今天很想念你。",
        chinese_path,
    )
    if ok:
        print(f"[generate_test_audio] Created {chinese_path}")
        created["chinese_speech"] = chinese_path
    else:
        print(
            "[generate_test_audio] Skipped chinese_speech.wav "
            "(macOS with Mandarin/Cantonese voice required)",
        )

    # --- English TTS (macOS only) ---
    english_path = output_dir / "english_speech.wav"
    ok = generate_english_tts_macos(
        "Hello grandma, I miss you very much.",
        english_path,
    )
    if ok:
        print(f"[generate_test_audio] Created {english_path}")
        created["english_speech"] = english_path
    else:
        print(
            "[generate_test_audio] Skipped english_speech.wav "
            "(macOS with `say` required)",
        )

    return created


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Generate synthetic test audio fixtures for Laolao.",
    )
    parser.add_argument(
        "--output-dir",
        default=str(Path(__file__).parent / "fixtures"),
        help="Directory to write fixture WAV files into (default: tests/fixtures/)",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> None:
    args = _parse_args(argv)
    output_dir = Path(args.output_dir)
    created = generate_test_fixtures(output_dir)
    print(f"\n[generate_test_audio] Done. {len(created)} fixture(s) created in {output_dir}/")


if __name__ == "__main__":
    main()
