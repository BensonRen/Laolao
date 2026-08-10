# WS-F — Ground-truth audio fixtures for Windows ARM64

**Verdict: A2 and A3 are no longer blocked on fixtures.** Both criteria now reach the
recogniser call and fail only because no STT backend is installed yet. Proven below
with a stub backend: with fixtures alone, A2 **PASS** and A3 **PASS**.

| | before | after |
|---|---|---|
| A2 (English) | BLOCKED — `tests/fixtures/` had no ground truth | reaches `get_backend()`; **fixture side PASS** |
| A3 (Mandarin) | BLOCKED — no Mandarin fixture obtainable | reaches `get_backend()`; **fixture side PASS** |

---

## 1. The problem

`tests/generate_test_audio.py` only synthesised speech on macOS via `say`. On Windows
it emitted a silence file and a 440 Hz sine tone — no speech at all — so every STT
result in this port was unfalsifiable. `check.py` additionally requires a `<stem>.txt`
ground truth beside each speech WAV (`a2()`: *"a transcript with nothing to compare
against proves nothing"*), and nothing in the repo ever wrote one, on any platform.

Second problem: this machine has **no Chinese SAPI voice and no Chinese language pack**
(SAPI exposes exactly `Microsoft David Desktop` and `Microsoft Zira Desktop`, both
en-US; Speech_OneCore adds only Mark). Installing one requires an interactive
Settings/admin flow. So Mandarin ground truth had to come from outside the machine.

## 2. What changed in `tests/generate_test_audio.py`

Everything below is additive; the macOS `say` path is byte-for-byte unchanged in
behaviour and its public functions (`generate_chinese_tts_macos`,
`generate_english_tts_macos`, `_run_macos_say`, `load_wav`) keep their signatures —
`tests/test_backends.py` still imports them fine.

### Windows SAPI path (Part 1 — the real upstream fix)

Mirrors the macOS block's shape: `_available_windows_voices()` /
`_pick_windows_voice()` / `generate_english_tts_windows()` /
`generate_chinese_tts_windows()` / `_run_windows_sapi()`.

- Drives `System.Speech.Synthesis.SpeechSynthesizer` from PowerShell, with
  `SetOutputToWaveFile(path, SpeechAudioFormatInfo(16000, Sixteen, Mono))` — SAPI
  writes Whisper's exact input format, so **no resampling and no ffmpeg** are needed
  (unlike the macOS AIFF path).
- Text, voice and output path travel in **environment variables**, not on the command
  line, so Unicode and quotes cannot break the script or be injected.
- `Rate = 0`, `Volume = 100` pinned, so output is deterministic (verified: two runs
  produce byte-identical WAVs, sha256 below).
- Rejects its own output if the read-back check fails — a silent or mis-formatted WAV
  is worse than no WAV, because it would poison every downstream comparison silently.
- `generate_chinese_tts_windows` looks for Huihui/Yaoyao/Kangkang/`zh-CN`; on this
  machine it correctly finds none and returns False, logging the installed voice list.

### Ground truth, on every platform

`write_ground_truth()` writes `<stem>.txt` containing **only** the transcript (that is
what `check.py` reads and compares verbatim), plus a `<stem>.source.json` sidecar for
provenance so licensing/URLs can never contaminate the expected text. It is called
from `generate_test_fixtures()` for both the macOS and the Windows path — ground truth
no longer depends on which OS generated the audio.

### Validation, not assertion

`inspect_wav()` reopens each file and reports real channels / bit depth / sample rate /
frames / duration / peak / RMS. `verify_wav()` fails a fixture on wrong format, on
`peak < 1000` or `rms < 50` (the "speech file is actually silent" trap), and on a
missing ground-truth `.txt`. `verify_fixtures()` runs over everything created and the
CLI exits non-zero if any fixture fails.

### Other additions

`load_wav_any()` (8/16/32-bit, multi-channel → int16 mono), `resample_to()` (linear,
numpy only), `peak_normalize()`, `write_fixture_readme()`, and a `--no-download` flag
for a strictly offline run. No new dependencies — stdlib + numpy only.

## 3. Mandarin ground truth (Part 2)

**Source: AISHELL-1 test utterance `BAC009S0764W0121`.**

| | |
|---|---|
| Audio | <https://huggingface.co/zrjin/icefall-asr-aishell-zipformer-2023-10-24/resolve/main/test_wavs/BAC009S0764W0121.wav> |
| sha256 (as downloaded) | `46dbc998c9d1d48111267c40741dd3200f2e5bcf4075f8c4c97f4451160dce50` (134 570 bytes) |
| Transcript | <https://huggingface.co/zrjin/icefall-asr-aishell-zipformer-2023-10-24/resolve/main/test_wavs/transcript.txt> — Kaldi-style, key `BAC009S0764W0121` |
| Transcript text | `甚至出现交易几乎停滞的情况` |
| License | **Apache License v2.0** — AISHELL-1, Beijing Shell Shell Technology Co., Ltd, via OpenSLR SLR33 <https://www.openslr.org/33/> |
| Auth needed | none — plain `curl -sL` / `urllib`, verified HTTP 200 with `HF_TOKEN` unset |
| Native format | already 16 kHz / mono / 16-bit PCM, 4.204 s |

**The transcript is not typed in by hand and not transcribed by ear.** The generator
downloads `transcript.txt` from the source at generation time and parses the line whose
utterance id matches, stripping the corpus's word-segmentation spaces. The literal
string in `MANDARIN_SOURCES["transcript_expected"]` is only a tripwire that logs a
warning if upstream ever changes; the fetched value always wins. If the transcript
cannot be fetched, the generator **refuses to ship the audio at all** rather than
produce a WAV with no ground truth.

Corroboration (three independent publications of the same reference):

1. `test_wavs/transcript.txt` in the model repo — fetched and quoted verbatim above.
2. The same repo's AISHELL decoding logs, which carry the corpus's own reference:
   `BAC009S0764W0121-1620-0: ref=['甚','至','出','现','交','易','几','乎','停','滞','的','情','况']`.
3. sherpa-onnx Paraformer docs show `test_wavs/8k.wav` (the 8 kHz downsample of this
   same utterance) decoding to `甚至出现交易几乎停滞的情况` —
   <https://k2-fsa.github.io/sherpa/onnx/pretrained_models/offline-paraformer/paraformer-models.html>

Three byte-identical mirrors are configured as fallbacks (`csukuangfj/...-stateless3`,
`marcoyang/...-stateless7`) so a single repo going away does not break generation.

**One processing step, documented:** AISHELL-1 is recorded very quietly (peak 1701 =
−25.7 dBFS, RMS 330). The clip is **peak-normalised to −3 dBFS**. This is gain only —
no filtering, no resampling — so the spoken content and therefore the transcript are
untouched. Without it an energy-gated VAD could swallow the whole utterance and make
A3 fail for a reason unrelated to the recogniser.

### Candidates rejected

- **sherpa-onnx GitHub release tarballs.** The `asr-models` zh assets are enormous —
  `sherpa-onnx-paraformer-zh-2023-03-28.tar.bz2` is **1.03 GB**; the smallest zh asset
  is 77.9 MB. The individual `test_wavs/*.wav` are exposed on the HF mirrors instead.
- **sherpa-onnx Paraformer `test_wavs/0-2.wav`.** Fetchable and their texts are
  published, but those texts are *greedy-search decoder output captured from a console
  log*, not a human reference — and the clips' own provenance/license is undocumented
  upstream. Not acceptable as ground truth when a real corpus reference exists.
- **Common Voice zh-CN, THCHS-30, raw AISHELL-1 from OpenSLR.** Common Voice needs
  terms acceptance or an authenticated dataset fetch; OpenSLR only publishes
  whole-corpus tarballs (AISHELL-1 ≈ 15 GB, THCHS-30 ≈ 6.4 GB). No single-clip endpoint.

## 4. Fixtures produced — measured, not asserted

Read back from disk with `wave` + numpy after generation:

| fixture | format | duration | peak | RMS | ground truth `.txt` | origin |
|---|---|---|---|---|---|---|
| `silence_2s.wav` | 16000 Hz / 1ch / 16-bit | 2.000 s | 0 | 0.0 | n/a | numpy |
| `tone_440hz_1s.wav` | 16000 Hz / 1ch / 16-bit | 1.000 s | 16383 | 11584.4 | n/a | numpy |
| `english_speech.wav` | 16000 Hz / 1ch / 16-bit | 3.335 s | 22649 | 3389.4 | `Hello grandma, I miss you very much.` | SAPI, Zira, 16 kHz mono |
| `en_long_speech.wav` | 16000 Hz / 1ch / 16-bit | 7.870 s | 21399 | 3026.1 | `And so, my fellow Americans, ask not what your country can do for you; ask what you can do for your country.` | SAPI, Zira, 16 kHz mono |
| `chinese_speech.wav` | 16000 Hz / 1ch / 16-bit | 4.204 s | 23197 | 4504.4 | `甚至出现交易几乎停滞的情况` | AISHELL-1, Apache-2.0 |

Every speech file is comfortably non-silent (peak > 21 000, RMS > 3 000). `silence_2s`
is the only zero-peak file and is exempt by design.

### Reproducibility

Two independent runs on this machine produced byte-identical output:

```
20eaebffe1816e0ffa6f7f854f5ef4ea80d5349faaf0ce1fec1b713e7fde58fa  silence_2s.wav
9ca8ebc6c1c04348f8670430ef995a84ebb017f0519a4102448a0437082144c6  tone_440hz_1s.wav
6de65040d9942522e5a0354f5755d07859e08ae9aff33ff1c3bcbb0bdc35443a  english_speech.wav
fcdbcfc574136eb811f82b2809ba872f89843953bf1fbfa645f3bbbaae584ce2  en_long_speech.wav
a12713aae609181b96153df45dba3863215898b0d2344cbcc7e463bbbdab41f6  chinese_speech.wav
```

SAPI hashes depend on the installed voice, so they are stable per-machine rather than
universal; the *ground truth* is identical everywhere because it lives in the source.
`--no-download` was verified to produce the four network-free fixtures and cleanly skip
the Mandarin one.

### Why `en_long_speech.wav` is not called `english_long.wav`

`check.py:find_fixture()` scans hints in order and takes the first `*.wav` whose name
contains the hint, sorted alphabetically. `english_long.wav` would sort *before*
`english_speech.wav` and silently steal A2. Naming the long clip `en_long_*` keeps A2
graded against the short canonical utterance while still giving A4/A6 ~8 s of real
speech. Verified resolution:

```
A2 -> english_speech.wav | txt: True 'Hello grandma, I miss you very much.'
A3 -> chinese_speech.wav | txt: True '甚至出现交易几乎停滞的情况'
A4 -> english_speech.wav | txt: True
A6 -> english_speech.wav | txt: True
```

## 5. Acceptance run

Verbatim (long `harness exception:` lines wrapped for readability, marked `↩`):

```
$ python docs\snapdragon\acceptance\check.py --only A2 A3

Laolao ARM64 acceptance — python 3.11.9 ARM64 on Windows
==============================================================================
[FAIL ] A2  known English WAV transcribes to expected text
          harness exception: RuntimeError("No transcription backend is available on ↩
          Windows ARM64 (No module named 'faster_whisper').\nfaster-whisper cannot ↩
          work here: its ctranslate2 dependency ships no win-arm64 distribution.\n ↩
          Install the ONNX backend instead:  pip install onnxruntime\nSee ↩
          docs/snapdragon/NORTH_STAR.md for the full platform picture.")
[FAIL ] A3  Mandarin WAV transcribes and output is Simplified Chinese
          harness exception: RuntimeError("No transcription backend is available on ↩
          Windows ARM64 (No module named 'faster_whisper').\nfaster-whisper cannot ↩
          work here: its ctranslate2 dependency ships no win-arm64 distribution.\n ↩
          Install the ONNX backend instead:  pip install onnxruntime\nSee ↩
          docs/snapdragon/NORTH_STAR.md for the full platform picture.")
==============================================================================
PASS=0  FAIL=2  SKIP=0  BLOCKED=0
```

**Read this carefully — the change is that `BLOCKED` is now 0.** Both fixture gates in
`check.py` are cleared:

- `a2()`: `find_fixture(...)` returned a WAV (not `None` → no BLOCKED), and
  `wav.with_suffix(".txt")` exists (no BLOCKED), so execution proceeded to
  `from backends import get_backend`.
- `a3()`: `find_fixture(...)` returned `chinese_speech.wav`, so it too proceeded to
  `get_backend`.

The remaining failure is `get_backend()` raising — a backend problem owned by the STT
workstreams, not a fixture problem.

### Proof the fixture path grades correctly

`check.py`'s own `a2()`/`a3()` were run with a stub `backends` module injected, whose
`transcribe()` asserts it received an int16 numpy array of the right size and echoes
the fixture's ground truth. This exercises `find_fixture`, `read_wav_int16`, the `.txt`
comparison and the Simplified-Chinese assertion for real:

```
[PASS] A2  known English WAV transcribes to expected text
        expected≈'Hello grandma, I miss you very much.' got='Hello grandma, I miss you very much.' (0.00s)
[PASS] A3  Mandarin WAV transcribes and output is Simplified Chinese
        got='甚至出现交易几乎停滞的情况' cjk=True traditional_chars=[]
```

A3's Simplified check passes: the AISHELL reference contains no character in
`check.py`'s traditional-marker set, so a correct recogniser plus OpenCC `t2s` will
satisfy it.

**Verdict: A2 fixture-readiness PASS. A3 fixture-readiness PASS.** Neither is BLOCKED
on ground-truth audio any more. Both remain FAIL pending a working STT backend.

## 6. Handover notes / hazards

1. **Do not add a `tests/fixtures/*.wav` whose name sorts before `english_speech.wav`
   and contains "english".** It will hijack A2's `find_fixture` and, without a matching
   `.txt`, silently re-BLOCK the criterion. This actually happened during this
   workstream: an `english_long.wav` with no ground truth was present in
   `tests/fixtures/`. It is preserved at
   `<scratchpad>/old_fixtures/english_long.wav` and was **not** restored — regenerate
   the equivalent as `en_long_speech.wav`.
2. **Another workstream is writing into `tests/fixtures/` concurrently.** At the time of
   this run it had left `cantonese_speech.wav`, `chinese_speech2.wav`,
   `fleurs_zh_0/1/2.wav` and `fleurs_zh_meta.json` there. Those were left in place
   (none of them can shadow A2's or A3's `find_fixture` pick, verified above), but none
   of them carries a `.txt`, so none is usable as ground truth as-is. The FLEURS
   clips *do* have transcripts inside `fleurs_zh_meta.json` (Google FLEURS
   `cmn_hans_cn`) — however their `src` URLs are **signed HF datasets-server links with
   an `Expires=` parameter**, so they are not reproducibly re-fetchable and were not
   adopted as the canonical A3 fixture for that reason.
3. `tests/.gitignore` ignores `fixtures/*.wav` only. The `.txt` ground truth,
   `.source.json` provenance and `fixtures/README.md` are therefore committed while the
   audio stays generated — which is the right shape and was left unchanged.
4. Console encoding: run anything that prints the Chinese transcript with
   `PYTHONIOENCODING=utf-8`, or Windows `cp1252` stdout raises `UnicodeEncodeError`.
   The fixture files themselves are always written as UTF-8 explicitly.

## 7. Reproduce

```powershell
$py = "C:\Users\snapd\AppData\Local\Programs\Python\Python311-arm64\python.exe"
& $py tests\generate_test_audio.py                 # generate + self-verify
$env:PYTHONIOENCODING = "utf-8"
& $py docs\snapdragon\acceptance\check.py --only A2 A3
```
