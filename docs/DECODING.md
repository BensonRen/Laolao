# Decoding: beam search, and what it actually bought

Laolao decodes Whisper with length-normalised beam search. This is the record of
how it is built, what it costs, and what it did *not* buy — so the next person to
touch `beam_size` is arguing with measurements rather than intuition.

## One search, three runtimes

The search itself lives once, in `backends/beam_search.py`: length normalisation,
candidate selection, when a finished hypothesis retires a beam, when early
stopping is provably safe. That is the part that is easy to get subtly wrong and
identical everywhere.

What differs per platform is only the *shape of a decoder call*, so each backend
supplies a small adapter:

| Runtime | Graph | Cost of a step |
|---|---|---|
| `QnnWhisperBackend` (Snapdragon NPU) | precompiled QNN, **batch-1, static shapes** | one NPU call **per live beam** |
| `OnnxWhisperBackend` (portable CPU) | onnx-community export, **dynamic batch axis** | one call for **all** beams |
| `MLXBackend` (Apple Silicon) | mlx-whisper `TextDecoder`, batched | one call for all beams |

Two structural optimisations apply to all three:

- **The forced prompt is decoded once.** Every beam shares the
  `<|sot|> <|lang|> <|transcribe|> <|notimestamps|>` prefix, so decoding it
  `beam_size` times would buy nothing.
- **Caches are never mutated in place.** Several candidate tokens descending from
  one parent share that parent's cache by reference, so cost scales with the
  number of *beams*, not the number of *candidates*. On the NPU lane, where every
  call is a round trip, getting this wrong would have cost several times the
  latency budget. `test_one_decoder_call_per_step_regardless_of_candidate_count`
  pins it.

`beam_size: 1` skips the module entirely and takes the original greedy path.

### Why the Mac needed its own file

`mlx_whisper.transcribe(..., beam_size=N)` does not work — mlx-whisper's decoder
raises `NotImplementedError("Beam search decoder is not yet implemented")`. So
`backends/mlx_beam.py` drives its `TextDecoder` through the shared search
directly. It reaches into internals that are not a stable public API, so a
failure degrades to greedy and logs once, rather than throwing on every
utterance: a slightly worse caption is a usable tool, an exception per utterance
is not.

## Measurements

whisper-large-v3-turbo, three AISHELL-1 test utterances (~37 characters), CER
scored after the Traditional → Simplified conversion the app applies.

Reproduce with `python tests/bench_decode.py --beams 1 4 --snr 10 5`.

### Latency

| Platform | Backend | beam 1 | beam 4 | Ratio |
|---|---|---|---|---|
| Snapdragon X2 Elite | QNN / Hexagon NPU | 457 ms | 856 ms | **1.87x** |
| Snapdragon X2 Elite | ONNX CPU EP (`base`) | 911 ms | 1314 ms | **1.44x** |
| Apple Silicon | MLX | 368 ms | 797 ms | **2.17x** |

Beam 4 costs well under 4x on every lane, for two different reasons. On the
batched lanes the beams genuinely fold into one call. On the NPU lane, where they
cannot, the saving comes from the model: turbo's distillation cut the decoder
from 32 layers to 4, so almost all of the per-utterance cost sits in the encoder,
which still runs exactly once. **large-v3-turbo is the model where beam search is
cheapest**, which is a large part of why this is affordable at all.

### Accuracy — the honest part

On clean corpus speech, beam 4 changed **no characters at all**. CER 5.6% at beam
1, 5.6% at beam 4.

With white noise mixed in (10 dB and 5 dB SNR) it fixed two utterances, broke
one, and moved mean CER from 15.2% to 14.2%. That is three utterances and should
be read as **"affordable"**, not **"better"**. Beam search does not repair a model
that is confidently wrong: `公共资源` → `公共寺院` survives beam 4 unchanged at
every SNR tested.

If you need the latency back, `"beam_size": 1` is a defensible setting on the
evidence here. It is not the default because the cost fits inside the budget and
the failure mode it guards against — a locally-attractive token dead-ending a
sentence — is real even though this fixture set is too small to show it.

## Why partials stay greedy

`partial_beam_size` defaults to `1` while `beam_size` defaults to `4`.

A partial is re-transcribed every `partial_interval_s`, superseded by the next
one, and then replaced outright by the final. Nobody keeps it. On the Snapdragon,
beam 4 at 856 ms does not fit the sub-1 s partial budget, while it sits
comfortably inside the sub-2 s final budget. Measured end-to-end through the
acceptance harness with beam 4 on finals: **partial 546 ms, final 861 ms**, both
passing.

Set `partial_beam_size` equal to `beam_size` on a machine with latency to spare.

## End-to-end verification

Snapdragon X2 Elite, wiped machine, `git clone` onward, beam 4 on finals:

```
PASS=10  FAIL=0  SKIP=0  BLOCKED=0
```

All ten criteria in `docs/snapdragon/acceptance/check.py`, including the live
WebSocket round trip (A6), the registered virtual camera (A7/A8), fully offline
operation (A9) and the one-command launcher (A10). Reproduce with
`docs\snapdragon\setup-arm64.ps1` followed by `check.py` with `server.py
--no-mic` running.

## Known gap

`language: "auto"` and beam search do not combine. Beam search forces a language
token into the prompt, so there is nothing to detect. On the Mac this falls back
to mlx-whisper's greedy path, which does real language detection, and logs once.
On the ONNX and QNN lanes `_prompt()` has always defaulted to English when no
language is given — a pre-existing behaviour that predates beam search and is not
fixed here. Set an explicit language for anything but English.
