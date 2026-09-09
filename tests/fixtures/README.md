# tests/fixtures — generated audio

Regenerate with `python tests/generate_test_audio.py`.
The `.wav` files are gitignored; the `.txt` ground truth and this file are not.

Every speech WAV has a `<stem>.txt` holding **only** its transcript —
that is the contract `tests/acceptance_arm64.py` (A2/A3) relies on.
Provenance lives in `<stem>.source.json` and in the table below.

| fixture | format | duration | peak | rms | ground truth | origin |
|---|---|---|---|---|---|---|
| `chinese_speech.wav` | 16000 Hz / 1ch / 16-bit | 3.234s | 19071 | 3886.0 | 你好，奶奶。我今天很想念你。 | Darwin TTS |
| `en_long_speech.wav` | 16000 Hz / 1ch / 16-bit | 6.405s | 26215 | 5134.4 | And so, my fellow Americans, ask not what your country can do for you; ask what you can do for your country. | Darwin TTS |
| `english_speech.wav` | 16000 Hz / 1ch / 16-bit | 2.411s | 25628 | 5729.3 | Hello grandma, I miss you very much. | Darwin TTS |
| `silence_2s.wav` | 16000 Hz / 1ch / 16-bit | 2.0s | 0 | 0.0 | — | synthetic (numpy) |
| `tone_440hz_1s.wav` | 16000 Hz / 1ch / 16-bit | 1.0s | 16383 | 11584.4 | — | synthetic (numpy) |
