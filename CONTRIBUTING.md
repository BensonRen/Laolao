# Contributing to Laolao

Thanks for looking. The project is small on purpose: `server.py` is the whole
caption engine, `backends/` is one file per inference runtime, and
`overlay/index.html` is the entire UI. Most changes touch one of those.

## Running the tests

Generate the audio fixtures once; the ground truth beside them is committed,
the audio is not:

```bash
python tests/generate_test_audio.py
```

Then:

```bash
pytest tests/ -m "not slow"        # fast: no model download, runs everywhere (~15 s)
pytest tests/ -m slow -v -s        # inference tests, needs a model downloaded
python tests/bench_decode.py       # beam width vs latency vs accuracy, on real audio
```

The fast suite is what CI runs on Ubuntu, macOS and Windows x64. It includes
the caption engine (`test_utterance_processor.py`), the beam search against a
scripted toy model (`test_beam_search.py`), and the VAD. None of those need a
GPU, an NPU or a microphone.

**Windows on ARM64 (Snapdragon):** use the ARM64 environment, not `venv`:

```powershell
.\.venv-arm64\Scripts\python.exe -m pytest tests\test_beam_search.py tests\test_utterance_processor.py
.\.venv-arm64\Scripts\python.exe tests\acceptance_arm64.py      # with server.py --no-mic running
```

`tests/acceptance_arm64.py` is the ten-criterion end-to-end check behind the
README's "verified" claim for that platform. If you change anything on the ARM64
lane, please run it and paste the summary line in your PR.

## What a useful bug report contains

1. **The platform row** from the README table you are on.
2. **The `ENGINE` line** from the log. It is printed once at startup and says
   which backend, model, device and beam settings actually loaded — the ARM64
   lane may have substituted a model, and this is where it says so.
3. What you said, what appeared, and roughly when. A ten-second screen
   recording of the overlay beats any description.

## Changing a backend

Every backend implements `transcribe(audio, language, beam_size=None) -> str`
over int16 16 kHz mono. If your runtime cannot run beam search itself, do not
reimplement it: write a small adapter for `backends/beam_search.py`, which is how
the ONNX, Qualcomm QNN and MLX backends all share one search. The unit tests for
that module need no weights and will catch the mistakes that matter.

`docs/DECODING.md` has the measurements. If your change moves latency or
accuracy, update the table there with numbers from `tests/bench_decode.py` on
the machine you measured.

## Platform claims

The README table distinguishes *verified* (a clean machine, from `git clone`,
with the acceptance checks passing) from *should work*. Please keep it honest:
promote a row only with a run behind it, and say what hardware it was.

## Style

No linters are enforced. Match the file you are in. Comments should say *why*,
not *what*; several of the non-obvious decisions in this codebase exist only
because a comment explains what broke without them.
