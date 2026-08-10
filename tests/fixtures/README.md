# tests/fixtures — generated audio

Regenerate with `python tests/generate_test_audio.py`.
The `.wav` files are gitignored; the `.txt` ground truth and this file are not.

Every speech WAV has a `<stem>.txt` holding **only** its transcript —
that is the contract `docs/snapdragon/acceptance/check.py` (A2/A3) relies on.
Provenance lives in `<stem>.source.json` and in the table below.

| fixture | format | duration | peak | rms | ground truth | origin |
|---|---|---|---|---|---|---|
| `chinese_speech.wav` | 16000 Hz / 1ch / 16-bit | 4.204s | 23197 | 4504.4 | 甚至出现交易几乎停滞的情况 | aishell1-BAC009S0764W0121 — Apache License 2.0 (AISHELL-1 / OpenSLR SLR33) |
| `en_long_speech.wav` | 16000 Hz / 1ch / 16-bit | 7.87s | 21399 | 3026.1 | And so, my fellow Americans, ask not what your country can do for you; ask what you can do for your country. | Windows TTS |
| `english_speech.wav` | 16000 Hz / 1ch / 16-bit | 3.335s | 22649 | 3389.4 | Hello grandma, I miss you very much. | Windows TTS |
| `silence_2s.wav` | 16000 Hz / 1ch / 16-bit | 2.0s | 0 | 0.0 | — | synthetic (numpy) |
| `tone_440hz_1s.wav` | 16000 Hz / 1ch / 16-bit | 1.0s | 16383 | 11584.4 | — | synthetic (numpy) |

## Third-party audio

### `chinese_speech.wav`

- Source: <https://huggingface.co/zrjin/icefall-asr-aishell-zipformer-2023-10-24/resolve/main/test_wavs/BAC009S0764W0121.wav>
- Transcript published at: <https://huggingface.co/zrjin/icefall-asr-aishell-zipformer-2023-10-24/resolve/main/test_wavs/transcript.txt> (key `BAC009S0764W0121`)
- License: Apache License 2.0 (AISHELL-1 / OpenSLR SLR33) — <https://www.openslr.org/33/>
- Attribution: AISHELL-1 Mandarin speech corpus, Beijing Shell Shell Technology Co., Ltd. Released under the Apache License v2.0 via OpenSLR SLR33.
- sha256 (as downloaded): `46dbc998c9d1d48111267c40741dd3200f2e5bcf4075f8c4c97f4451160dce50`
- Processing: peak-normalised to -3 dBFS; already 16 kHz mono 16-bit

