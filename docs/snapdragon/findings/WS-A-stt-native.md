# WS-A — Native ARM64 speech-to-text (ONNX Runtime → Hexagon NPU)

Owner: WS-A · Machine: Snapdragon X2 Elite (X2E88100), Windows 11 build 28000, ARM64
Date: 2026-08-09/10 · venv: `C:\Users\snapd\Downloads\laolao\.venv-arm64`
Reproduce: `.venv-arm64\Scripts\python.exe docs\snapdragon\findings\ws_a_verify.py --turbo`

## TL;DR

**The NATIVE lane works, and it is not a compromise — it is the fastest option on this box.**

Whisper runs on the Hexagon NPU through `onnxruntime-qnn` using Qualcomm AI Hub's
*precompiled QNN ONNX* export, which ships an asset built specifically for
`qualcomm-snapdragon-x2-elite`. A 5-second utterance transcribes end-to-end in
**~130 ms with whisper-base** and **~530 ms with whisper-large-v3-turbo** — against a
partial budget of 1.0 s and a final budget of 2.0 s. Mandarin CER with turbo is
**0.049** on FLEURS and **0.000** on the AISHELL-1 fixture.

No compiler was needed. No `ctranslate2`, no `torch`, no `tiktoken`.

| lane | model | 5 s utterance | zh CER (FLEURS n=3) | notes |
|---|---|---|---|---|
| **QNN / Hexagon NPU** | large-v3-turbo | **480–570 ms** | **0.049** | recommended; 2.1 GB on disk |
| **QNN / Hexagon NPU** | base | **91–164 ms** | 0.196 | recommended for low-power / partials |
| CPU EP (3 threads) | tiny | 396–499 ms | 0.435→0.212 | portable fallback |
| CPU EP (3 threads) | base | 1.1–1.6 s | 0.196 | too slow for partials |
| onnxruntime-genai | base | 1.16 s | not measured | works, but slowest path |

Deliverable backend: **`backends/onnx_whisper_backend.py`** — two `BaseBackend`
implementations (`QnnWhisperBackend`, `OnnxWhisperBackend`) plus
`get_onnx_whisper_backend(cfg)` which picks NPU→CPU. Nothing else in the repo was
touched.

---

## Hypothesis resolutions

### H-100 — a Whisper ONNX export runs end-to-end under onnxruntime CPU EP on ARM64 and returns correct text
**CONFIRMED.**

`onnx-community/whisper-tiny` and `-base` (encoder + `decoder_model_merged`) run on
`CPUExecutionProvider` with a pure-numpy log-mel front end and a hand-written greedy
decode loop.

```
$ .venv-arm64\Scripts\python.exe docs\snapdragon\findings\ws_a_verify.py --skip-npu
   cpu-tiny english_speech.wav      396ms WER=0.000
      expected: Hello grandma, I miss you very much.
      got     : Hello grandma! I miss you very much.
   cpu-tiny en_long_speech.wav      479ms WER=0.000
      expected: And so, my fellow Americans, ask not what your country can do for you; ask what you can do for your country.
      got     : And so, my fellow Americans ask not what your country can do for you. Ask what you can do for your country.
[PASS] H-100: onnx-community/whisper-tiny on CPUExecutionProvider: English WER<=0.05, worst latency 485 ms
```

WER is 0.000 for both (the diffs are punctuation only, which the WER normaliser strips).

Supporting evidence that the front end is right, not merely plausible — our numpy mel
filterbank is compared against openai-whisper's own `assets/mel_filters.npz`:

```
[PASS] mel-filterbank: max|ours - openai/whisper mel_filters.npz['mel_80']| = 1.863e-09
```

Gotcha found: the exported encoder's positional embedding is a fixed `[1500, 384]` add,
so **the encoder only accepts exactly 3000 mel frames (30 s)**. Shorter input fails:

```
frames=1000: FAIL Fail: [ONNXRuntimeError] : 1 : FAIL : Non-zero status code returned
  while running Add node. Name:'/Add_2' ... Attempting to broadcast an axis by a
  dimension other than 1. 500 by 1500
frames=3000: OK out=(1, 1500, 384)
```

### H-101 — a Whisper tokenizer usable WITHOUT Rust wheels exists
**CONFIRMED — and the premise was wrong in a useful way.**

`tokenizers` (Rust) **does** publish a win-arm64 wheel; `tiktoken` does **not**.

```
$ .venv-arm64\Scripts\python.exe -m pip install --dry-run --only-binary=:all: --no-deps tokenizers
Collecting tokenizers
  Using cached tokenizers-0.23.1-cp310-abi3-win_arm64.whl.metadata (10 kB)
Would install tokenizers-0.23.1

$ ... tiktoken
ERROR: Could not find a version that satisfies the requirement tiktoken (from versions: none)
ERROR: No matching distribution found for tiktoken
```

So the HuggingFace tokenizer path is available and the openai-whisper tokenizer path is
not. Round-trip proof through the actual Whisper vocab:

```
[PASS] H-101: tokenizers 0.23.1 (win_arm64 wheel) round-trip [26410, 15766] -> '你好 grandma'
```

No pure-Python BPE fallback was needed. (If `tokenizers` ever regresses, `vocab.json` +
`merges.txt` are downloaded alongside the model, so a fallback stays possible.)

### H-102 — onnxruntime-genai supports Whisper and handles the decode loop
**CONFIRMED, but not adopted.**

`onnxruntime-genai 0.15.2` has a `cp311-cp311-win_arm64` wheel, reports
`is_qnn_available() == True`, and transcribes correctly with zero decode-loop code:

```
genai 0.15.2 qnn_available: True
model loaded 1.309 s
latency 1.158 s
TEXT:  And so, my fellow Americans, ask not what your country can do for you, ask what you can do for your country.
```
(model: `tonythethompson/Whisper-Base-GenAI-ONNX`, fixture `en_long_speech.wav`,
ground truth `And so, my fellow Americans, ask not what your country can do for you; ask what you can do for your country.`)

Why it was **not** chosen:
1. 1.16 s per utterance — ~9× slower than our QNN whisper-base path (0.13 s), and it
   ran on CPU; the genai model would need re-building to target QNN.
2. It needs a genai-format model (`genai_config.json`); the community exports are
   third-party and only cover a few sizes.
3. `onnxruntime-genai` bundles its own `onnxruntime`, which fights with
   `onnxruntime-qnn` in one venv (it was tested in a throwaway venv for that reason).

Keep it in the back pocket: it is the lowest-code way to a working transcriber if the
hand-written decode loop ever needs to go.

### H-103 — the Whisper encoder runs on QNNExecutionProvider (Hexagon NPU) with output matching CPU EP
**CONFIRMED.** Two independent proofs.

**(a) Numerics.** The stock `onnx-community/whisper-tiny` fp32 encoder, run through the
QNN EP vs the CPU EP on the same features:

```
--- tiny: CPU ref (1, 1500, 384) mean=0.03362
    NPU: init=4.2s run=892ms maxabs=0.0006 cos=1.000000 providers=['QNNExecutionProvider', 'CPUExecutionProvider']
    GPU: init=3.7s run=1084ms maxabs=0.0006 cos=1.000000 providers=['QNNExecutionProvider', 'CPUExecutionProvider']
```
Cosine similarity 1.000000, max abs error 6e-4 — the NPU result is correct. But it is
**slower** than CPU (892 ms vs 383 ms): a dynamic-shape fp32 graph is the worst case for
the HTP, and only part of the graph gets offloaded.

**(b) Tokens.** The Qualcomm AI Hub *precompiled* export (static shapes, fp16, built for
this chipset) produces **character-identical output** to the CPU EP:

```
[PASS] H-103: encoder EP=QNNExecutionProvider, English WER<=0.05, load 1.2s
[PASS] H-103-parity: NPU and CPU EP produced identical text on 3/3 Mandarin clips
```

**How to reach the QNN EP on ORT 2.x** — this changed and cost real time:
`onnxruntime-qnn` 2.4.0 is now a **plugin EP** package (`onnxruntime_qnn/`), not a
patched `onnxruntime`. Consequences:
- `ort.get_available_providers()` does **not** list `QNNExecutionProvider` until you call
  `ort.register_execution_provider_library("QNNExecutionProvider", onnxruntime_qnn.get_library_path())`.
- Passing `providers=[("QNNExecutionProvider", {...})]` after registering **silently
  falls back to CPU** (`s.get_providers() == ['CPUExecutionProvider']`). You must select
  the device object instead:
  ```python
  dev = [d for d in ort.get_ep_devices()
         if d.ep_name == "QNNExecutionProvider" and "NPU" in str(d.device.type)][0]
  so.add_provider_for_devices([dev], {"htp_performance_mode": "burst"})
  sess = ort.InferenceSession(path, so)      # no providers= argument
  ```
  Both an NPU and a GPU QNN device enumerate; pick the NPU.

### H-104 — latency meets partial < 1.0 s, final < 2.0 s on a ~5 s utterance
**CONFIRMED on the NPU for both model sizes; CONFIRMED on CPU only for `tiny`.**

Per-utterance, end-to-end (`transcribe()` including mel, encoder, full greedy decode),
warm session, 10 repeats on the AISHELL fixture (4.2 s audio):

```
npu base   CER=0.077 p50=121ms p95=123ms min=114 max=123
npu turbo  CER=0.000 p50=480ms p95=486ms min=467 max=487
cpu tiny   CER=0.077 p50=451ms p95=487ms min=400 max=499
```

Across the whole 5-fixture suite (3.3–7.9 s of audio):

```
[PASS] H-104-npu: worst end-to-end latency 164 ms over 5 utterances
[PASS] H-104-cpu: whisper-tiny CPU worst latency 485 ms
[PASS] H-105:    whisper-large-v3-turbo ... worst latency 570 ms
```

Component breakdown, whisper-base on the NPU, 5 s clip:
```
mel=22ms  enc=23ms  dec=83ms (16 tok)  total=131ms
```
The numpy mel front end (22 ms) is now comparable to the whole encoder — it is the next
thing worth optimising, not the model.

**CPU thread count is a trap.** ORT's default (18 threads on this 18-core part) is far
slower than 3; the cores are heterogeneous and oversubscription hurts:

```
  tiny  thr= 1:   605.3 ms      base  thr= 1:  1091.7 ms
  tiny  thr= 2:   425.2 ms      base  thr= 2:   894.3 ms
  tiny  thr= 3:   382.6 ms      base  thr= 3:   843.4 ms
  tiny  thr= 4:   397.4 ms      base  thr= 4:   886.3 ms
  tiny  thr= 8:   541.0 ms      base  thr= 8:   892.4 ms
  tiny  thr=12:   674.2 ms      base  thr=12:  1324.7 ms
```
`OnnxWhisperBackend` therefore defaults to `intra_op_threads=3` via the factory.

Also measured: the int8 `*_quantized` exports speed the **encoder** up (tiny 383→210 ms)
but slow the **decoder** down badly (tiny full 448→628 ms), so they are off by default.

### H-105 — Mandarin accuracy is acceptable at the chosen model size
**CONFIRMED for `large-v3-turbo`; PARTIAL (marginal) for `base`.**

Ground truth: `google/fleurs` `cmn_hans_cn` validation transcriptions (fetched with the
audio via the HF datasets-server, so the label comes with the clip), plus the AISHELL-1
fixture (`BAC009S0764W0121`) that another workstream placed in `tests/fixtures/` with its
published transcript. CER computed after OpenCC `t2s` (which the
product applies anyway — raw Whisper emits Traditional and CER is 0.435 without it).

whisper-large-v3-turbo on the NPU:
```
   turbo fleurs_zh_0.wav         525ms CER=0.100
      expected: 内陆水道可以作为假期游玩的一个不错的主题。
      got     : 内漏水岛可以作为假期游玩的一个不错的主题
   turbo fleurs_zh_1.wav         524ms CER=0.000
      expected: 西班牙人开始了长达三个世纪的殖民时期。
      got     : 西班牙人开始了长达三个世纪的殖民时期
   turbo fleurs_zh_2.wav         538ms CER=0.048
      expected: 从国王到平民，它遍布十方的力量影响着每一个人。
      got     : 从国王到平民他遍布十方的力量影响着每一个人
[PASS] H-105: whisper-large-v3-turbo Mandarin mean CER = 0.049
```
AISHELL-1 `BAC009S0764W0121`: ref `甚至出现交易几乎停滞的情况` → turbo `甚至出现交易几乎停滞的情况`, **CER 0.000**.

whisper-base on the NPU, same clips: mean CER **0.196** (0.150 / 0.056 / 0.381) and
0.077 on AISHELL. Readable, but one word in five is wrong on harder sentences — for a
grandmother reading captions that is borderline. Use `base` for *partials* and
`large-v3-turbo` for *finals*, or just use turbo throughout (570 ms still fits the 2 s
final budget and the 1 s partial budget).

Traditional→Simplified matters enormously and the repo already handles it
(`t2s` in `config.json`): without OpenCC, base scores 0.435 instead of 0.196.

---

## What was built

`backends/onnx_whisper_backend.py`, satisfying `BaseBackend.transcribe(audio, language) -> str`
with `audio` as 1-D int16 @ 16 kHz:

- `mel_filter_bank()` / `log_mel_spectrogram()` — pure numpy, no torch/librosa/scipy.
  Verified against openai-whisper's own filterbank to 1.9e-9.
- `OnnxWhisperBackend` — `onnx-community/whisper-*` encoder + merged-KV decoder on any
  EP (CPU by default), greedy decode with KV cache, `<|notimestamps|>` forced,
  `suppress_tokens` / `begin_suppress_tokens` applied from `generation_config.json`.
- `QnnWhisperBackend` — Qualcomm AI Hub precompiled QNN export on the Hexagon NPU.
  Auto-downloads the chipset-matched asset; chipset is read from the registry
  (`ProcessorNameString` → `Snapdragon(R) X2 Elite - X2E88100 …`), because
  `platform.processor()` only says `ARMv8 (64-bit) Family 8 Model 2`.
- `get_onnx_whisper_backend(cfg)` — `device: auto|qnn|cpu`, NPU first, CPU fallback.

The Qualcomm export is **not** HuggingFace-shaped; its contract had to be reverse
engineered from the graph:

```
encoder(input_features fp16 [1, n_mels, 3000])
    -> k_cache_cross_i fp16 [H,1,D,1500], v_cache_cross_i fp16 [H,1,1500,D]
decoder(input_ids int32 [1,1], attention_mask fp16 [1,1,1,200],
        k/v_cache_self_i_in, k/v_cache_cross_i, position_ids int32 [1])
    -> logits fp16 [1,V,1,1], k/v_cache_self_i_out
```
The self-attention cache is a **fixed-length left-padded shift register** of length 199;
the mask un-masks only its last `index+1` slots (`mask[..., 199-index:] = 0`). Prompt
tokens are fed one per step. All geometry (layers/heads/dim/seq/n_mels) is read off the
graph, so the same code drives `base` (6L/8H/64D/80 mels) and `large-v3-turbo`
(4L/20H/64D/128 mels). Tokenizer and `generation_config.json` come from the matching
`onnx-community` repo.

## Exact install commands that worked

```powershell
C:\Users\snapd\AppData\Local\Programs\Python\Python311-arm64\python.exe -m venv `
    C:\Users\snapd\Downloads\laolao\.venv-arm64
C:\Users\snapd\Downloads\laolao\.venv-arm64\Scripts\python.exe -m pip install --upgrade pip
C:\Users\snapd\Downloads\laolao\.venv-arm64\Scripts\python.exe -m pip install `
    onnxruntime-qnn numpy tokenizers huggingface_hub opencc-python-reimplemented httpx
```

Resolved versions (all native win-arm64 wheels, zero source builds):

```
httpx==0.28.1              onnxruntime==1.28.0
huggingface_hub==1.27.0    onnxruntime-qnn==2.4.0
numpy==2.4.6               opencc-python-reimplemented==0.1.7
tokenizers==0.23.1
```

`onnxruntime-genai==0.15.2` also installs (tested in a separate venv — do **not** put it
in the same venv as `onnxruntime-qnn`).

Model artefacts, all outside the repo under `C:\Users\snapd\Downloads\laolao-tools\models\`:

| dir | size | source |
|---|---|---|
| `qai-whisper_base-qualcomm_snapdragon_x2_elite` | 193 MB | `qaihub-public-assets…/whisper_base/releases/v0.59.0/whisper_base-precompiled_qnn_onnx-float-qualcomm_snapdragon_x2_elite.zip` |
| `qai-whisper_large_v3_turbo-qualcomm_snapdragon_x2_elite` | 2.1 GB | same pattern, `whisper_large_v3_turbo` |
| `whisper-tiny` / `whisper-base` | 188 / 356 MB | `onnx-community/whisper-*` (ONNX + tokenizer) |
| `whisper-large-v3-turbo` | 4.3 MB | `onnx-community/whisper-large-v3-turbo`, JSON only (tokenizer for the QNN path) |
| `mel_filters.npz` | 8 KB | `openai/whisper` — used only to *verify* our filterbank, not at runtime |

Everything is offline after the first fetch (relevant to A9).

## Fixtures

- `tests/fixtures/english_speech.wav`, `en_long_speech.wav`, `chinese_speech.wav`
  (+ `.txt` ground truth) — created by another workstream; consumed read-only here.
- `C:\Users\snapd\Downloads\laolao-tools\fixtures\fleurs_zh_{0,1,2}.wav` +
  `fleurs_zh_meta.json` — Mandarin clips *with dataset reference transcriptions*, pulled
  from `google/fleurs` `cmn_hans_cn` via the HF datasets-server rows API. Kept outside
  the repo so WS-A does not collide with the shared fixtures directory. `ws_a_verify.py`
  regenerates them if missing.
  Note: FLEURS audio is **IEEE-float32 WAV (format tag 3)**; stdlib `wave` raises
  `unknown format: 3`, so the script has a small RIFF parser.
- Windows SAPI (`System.Speech`) can synthesise English fixtures (`Microsoft David` /
  `Zira`) but **no Mandarin voice is installed** — that is why the zh fixtures are real
  recordings from a labelled corpus rather than TTS.

## What is still broken / not done

1. **`get_backend()` in `backends/__init__.py` is not wired.** Per instructions no
   existing file was modified. The orchestrator needs to add, before the faster-whisper
   fallback:
   ```python
   from backends.onnx_whisper_backend import get_onnx_whisper_backend
   return get_onnx_whisper_backend(cfg)
   ```
   Also note `config.json` uses `model: base` — that maps onto the QNN `base` asset;
   `small`/`medium` have **no** Qualcomm precompiled export, so those sizes silently fall
   back to the slow CPU path. Recommend restricting the ARM64 model choices to
   `base` and `large-v3-turbo` (and `tiny` on CPU).
2. **Cold start.** The very first load of the turbo QNN context (1.75 GB encoder binary)
   took **117 s**; warm it is 7 s, and base is ~1.2 s. `server.py`'s startup budget is
   120 s (`electron/main.js`) — that is uncomfortably tight for a first run with turbo.
   Needs either a pre-warm step during install or a "downloading model…" UI state.
3. **Thread-safety / concurrency.** Both backends take a `threading.Lock` around a whole
   `transcribe()`. `server.py` uses a single `_tx_worker` thread, so this is fine today,
   but a partial and a final cannot overlap.
4. **30-second padding tax.** Every call encodes a full 30 s window regardless of
   utterance length. On the NPU that costs 23 ms (irrelevant); on CPU it is the whole
   bill. Fixing it would need ONNX graph surgery to slice the positional embedding.
5. **Mel front end is 22 ms** — now ~17 % of the base NPU budget. An FFT over 3000 frames
   in numpy; could be cut with a strided real-FFT batch or by moving it into the graph.
6. **Cantonese (`yue`) untested.** A fixture was fetched
   (`laolao-tools/fixtures/cantonese_speech.wav`) but no reference transcript was found,
   so no claim is made. `<|yue|>` does exist in the large-v3 token set; the backend maps
   `yue → yue → zh` with a fallback.
7. **Silence hallucinates**, as stock Whisper always does — 2 s of digital silence
   returns `你不想要我` with `language="zh"`. Empty input correctly returns `""`. The
   backend does **not** filter this; it relies on `server.py`'s VAD gate and
   chars-per-second plausibility cap. Do not remove those on this lane.
8. **Not tested: long-running stability, memory footprint under sustained use, battery.**
   The HTP is held in `burst` performance mode for the whole session.

## Single most promising next step

**Run `large-v3-turbo` for finals and `base` for partials, both on the NPU, and wire it
into `server.py`.** The two sessions cost 2.3 GB of disk and both are already proven; the
partial path lands in ~130 ms and the final path in ~530 ms, so Laolao would be roughly
4× *inside* its latency budget with the best Chinese accuracy available offline
(CER 0.049 / 0.000). Everything needed for that is in
`backends/onnx_whisper_backend.py` today — it only needs `backends/__init__.py` to select
it and a `partial_model` / `final_model` config key.
