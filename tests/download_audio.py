"""
download_audio.py — Download short audio clips for Laolao testing.

Requires yt-dlp and ffmpeg:
    pip install yt-dlp
    # macOS: brew install ffmpeg
    # Ubuntu: sudo apt install ffmpeg

Usage:
    python tests/download_audio.py --url URL [--output-dir tests/fixtures] [--duration 60]

The script downloads audio-only, converts to 16 kHz mono WAV, and trims to
the first *duration* seconds.
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from pathlib import Path


# ---------------------------------------------------------------------------
# Test source catalogue
# ---------------------------------------------------------------------------
# NOTE: YouTube URLs change and videos get removed.  The entries below use
# placeholder URLs; supply your own with --url at runtime.
# Criteria for good test sources:
#   - Short (< 2 min) so fixture files stay small
#   - Clear Mandarin Chinese speech
#   - Official/stable channels

TEST_SOURCES: list[dict] = [
    {
        "id": "chinese_news_short",
        "url": "https://www.youtube.com/watch?v=PLACEHOLDER_REPLACE_ME",
        "description": "Short Mandarin news clip",
        "language": "zh",
        "expected_keywords": ["的", "是", "在"],
    },
    {
        "id": "mandarin_lesson_short",
        "url": "https://www.youtube.com/watch?v=PLACEHOLDER_REPLACE_ME_2",
        "description": "Short Mandarin language lesson",
        "language": "zh",
        "expected_keywords": ["你好", "谢谢", "中文"],
    },
]


# ---------------------------------------------------------------------------
# Core download function
# ---------------------------------------------------------------------------

def download_audio(
    url: str,
    output_path: Path,
    max_seconds: int = 60,
) -> bool:
    """Download audio from *url*, convert to 16 kHz mono WAV, and save to *output_path*.

    Uses yt-dlp for downloading and ffmpeg for audio conversion.  Clips the
    output to the first *max_seconds* seconds so fixture files stay small.

    Args:
        url:         YouTube (or other yt-dlp-supported) URL.
        output_path: Destination path for the output WAV file.
        max_seconds: Maximum duration to keep (default: 60 s).

    Returns:
        True on success, False on any error.
    """
    if not shutil.which("yt-dlp"):
        print(
            "ERROR: yt-dlp is not installed.\n"
            "Install it with:  pip install yt-dlp\n"
            "Then re-run this script.",
            file=sys.stderr,
        )
        return False

    if not shutil.which("ffmpeg"):
        print(
            "ERROR: ffmpeg is not installed.\n"
            "  macOS:  brew install ffmpeg\n"
            "  Ubuntu: sudo apt install ffmpeg\n"
            "Then re-run this script.",
            file=sys.stderr,
        )
        return False

    output_path.parent.mkdir(parents=True, exist_ok=True)

    # yt-dlp output template (without extension — yt-dlp adds it).
    template = str(output_path.with_suffix(""))

    cmd = [
        "yt-dlp",
        "--no-playlist",
        "--extract-audio",
        "--audio-format", "wav",
        # Limit to first max_seconds seconds.
        "--download-sections", f"*0-{max_seconds}",
        # Post-process with ffmpeg to resample to 16 kHz mono int16 PCM.
        "--postprocessor-args",
        f"ffmpeg:-ar 16000 -ac 1 -sample_fmt s16",
        "--output", f"{template}.%(ext)s",
        url,
    ]

    print(f"[download_audio] Downloading: {url}")
    print(f"[download_audio] Command: {' '.join(cmd)}")

    result = subprocess.run(cmd, capture_output=False, check=False)

    if result.returncode != 0:
        print(f"[download_audio] yt-dlp failed with exit code {result.returncode}", file=sys.stderr)
        return False

    # yt-dlp may produce e.g. output_path.stem + ".wav"
    candidate = output_path.parent / (output_path.stem + ".wav")
    if candidate.exists() and candidate != output_path:
        candidate.rename(output_path)

    if not output_path.exists():
        print(
            f"[download_audio] Expected output file not found: {output_path}",
            file=sys.stderr,
        )
        return False

    size_kb = output_path.stat().st_size // 1024
    print(f"[download_audio] Saved {output_path} ({size_kb} KB)")
    return True


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Download short audio clips from YouTube for Laolao integration tests.\n"
            "Requires: pip install yt-dlp  AND  ffmpeg on PATH."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--url",
        required=True,
        help="YouTube (or other yt-dlp-supported) URL to download.",
    )
    parser.add_argument(
        "--output-dir",
        default=str(Path(__file__).parent / "fixtures"),
        help="Directory to save downloaded WAV files (default: tests/fixtures/).",
    )
    parser.add_argument(
        "--output-name",
        default="downloaded_audio.wav",
        help="Output filename (default: downloaded_audio.wav).",
    )
    parser.add_argument(
        "--duration",
        type=int,
        default=60,
        help="Maximum clip duration in seconds (default: 60).",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> None:
    args = _parse_args(argv)
    output_path = Path(args.output_dir) / args.output_name
    ok = download_audio(args.url, output_path, max_seconds=args.duration)
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
