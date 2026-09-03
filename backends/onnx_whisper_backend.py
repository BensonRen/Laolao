"""
ONNX Runtime Whisper backends — native Windows-on-ARM64 (Snapdragon X / X2 Elite).

Why this exists
---------------
`faster-whisper` needs `ctranslate2`, which has **no** win-arm64 distribution, and
`openai-whisper` needs `torch`, which also has none.  The only inference runtime with a
native win-arm64 wheel is **onnxruntime** (plus `onnxruntime-qnn` for the Hexagon NPU).

Two backends live here:

``QnnWhisperBackend``   Qualcomm AI Hub's *precompiled QNN* Whisper export running on the
                       Hexagon NPU.  ~130 ms end-to-end for a 5 s utterance with
                       whisper-base.  Preferred when the QNN EP is available.

``OnnxWhisperBackend``  The portable fallback: the `onnx-community/whisper-*`
                       Transformers.js-style export (encoder + merged KV-cache decoder)
                       on the CPU EP.  ~0.45 s (tiny) / ~1.1 s (base) for the same clip.

Both share:
  * a pure-numpy log-mel front end (no torch, no librosa, no scipy)
  * a hand-rolled decode loop with KV caching — greedy, or length-normalised
    beam search when ``beam_size`` > 1
  * HuggingFace `tokenizers` for BPE detokenisation (it has a cp310-abi3 win_arm64
    wheel; `tiktoken` does **not**, so openai-whisper's tokenizer path is unavailable)

Dependencies (all have win-arm64 wheels):
    onnxruntime, onnxruntime-qnn, numpy, tokenizers
    (deliberately NOT huggingface_hub — it needs PyYAML, which has no win-arm64 wheel)

Models are fetched once into ``model_dir`` (default ``<repo>/../laolao-tools/models``)
and everything afterwards is offline.
"""

from __future__ import annotations

import json
import logging
import os
import threading
from pathlib import Path

import numpy as np

from backends.base import BaseBackend
from backends.beam_search import (
    DEFAULT_BEAM_SIZE,
    DEFAULT_LENGTH_PENALTY,
    beam_search,
    log_softmax,
)

log = logging.getLogger("laolao.backends.onnx")

# ── Whisper front-end constants (must match the training-time feature extractor) ──
SAMPLE_RATE = 16000
N_FFT = 400
HOP_LENGTH = 160
CHUNK_SECONDS = 30
N_SAMPLES = SAMPLE_RATE * CHUNK_SECONDS          # 480 000
N_FRAMES = N_SAMPLES // HOP_LENGTH               # 3 000

DEFAULT_MODEL_DIR = Path(
    os.environ.get(
        "LAOLAO_MODEL_DIR",
        Path(__file__).resolve().parent.parent.parent / "laolao-tools" / "models",
    )
)

# onnx-community repo ids for each Laolao model size
_REPO_FOR_SIZE = {
    "tiny": "onnx-community/whisper-tiny",
    "base": "onnx-community/whisper-base",
    "small": "onnx-community/whisper-small",
    "medium": "onnx-community/whisper-medium",
    "large-v3": "onnx-community/whisper-large-v3",
    "large-v3-turbo": "onnx-community/whisper-large-v3-turbo",
}

_HUB_PATTERNS = [
    "*.json",
    "merges.txt",
    "onnx/encoder_model{q}.onnx",
    "onnx/decoder_model_merged{q}.onnx",
]

# Files each lane actually opens. Listed explicitly rather than glob-downloaded
# because we fetch them ourselves — see hf_download().
_HF_TOKENIZER_FILES = ("tokenizer.json", "generation_config.json", "config.json")

HF_ENDPOINT = os.environ.get("HF_ENDPOINT", "https://huggingface.co").rstrip("/")


def hf_download(repo: str, filename: str, dest: Path, timeout: float = 120.0) -> Path:
    """Fetch one file from a HuggingFace repo to *dest*.

    Deliberately not ``huggingface_hub.snapshot_download``.  That package pulls
    ``PyYAML``, which publishes **no** win-arm64 wheel at any version and cannot be
    built on a machine without MSVC — so requiring it makes a clean Snapdragon
    install impossible, which is the one platform this module exists for.  We need
    five static files over plain HTTPS; the stdlib already does that, and the
    Qualcomm asset fetch below has always done it this way.
    """
    import urllib.request

    if os.environ.get("HF_HUB_OFFLINE", "").lower() in ("1", "true", "yes"):
        raise RuntimeError(
            f"HF_HUB_OFFLINE is set but {dest} is missing — the model was never "
            f"downloaded on this machine. Run setup once with network access."
        )

    url = f"{HF_ENDPOINT}/{repo}/resolve/main/{filename}"
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(dest.suffix + ".part")
    log.info("Downloading %s → %s", url, dest)
    req = urllib.request.Request(url, headers={"User-Agent": "laolao/0.2"})
    with urllib.request.urlopen(req, timeout=timeout) as r, open(tmp, "wb") as f:
        while chunk := r.read(1 << 20):
            f.write(chunk)
    # Rename only once the body is fully on disk, so an interrupted download can
    # never leave a truncated ONNX file that loads and then misbehaves.
    tmp.replace(dest)
    return dest

# Language codes Laolao uses that Whisper spells differently / may not have.
_LANG_ALIASES = {"yue": ["yue", "zh"], "zh": ["zh"], "cmn": ["zh"]}

# ═══════════════════════════════════════════════════════════════════════════════
# Pure-numpy log-mel front end
# ═══════════════════════════════════════════════════════════════════════════════

def _hz_to_mel_slaney(freq: np.ndarray | float) -> np.ndarray:
    """Slaney (auditory-toolbox) Hz→mel — the scale librosa/whisper use."""
    f_min, f_sp = 0.0, 200.0 / 3
    mels = (np.asarray(freq, dtype=np.float64) - f_min) / f_sp
    min_log_hz, min_log_mel = 1000.0, (1000.0 - f_min) / f_sp
    logstep = np.log(6.4) / 27.0
    log_region = np.asarray(freq, dtype=np.float64) >= min_log_hz
    mels = np.where(
        log_region,
        min_log_mel + np.log(np.asarray(freq, dtype=np.float64) / min_log_hz + 1e-300) / logstep,
        mels,
    )
    return mels


def _mel_to_hz_slaney(mels: np.ndarray) -> np.ndarray:
    f_min, f_sp = 0.0, 200.0 / 3
    freqs = f_min + f_sp * mels
    min_log_hz, min_log_mel = 1000.0, (1000.0 - f_min) / f_sp
    logstep = np.log(6.4) / 27.0
    log_region = mels >= min_log_mel
    return np.where(log_region, min_log_hz * np.exp(logstep * (mels - min_log_mel)), freqs)


def mel_filter_bank(n_mels: int = 80, n_fft: int = N_FFT, sr: int = SAMPLE_RATE) -> np.ndarray:
    """Slaney-normalised triangular mel filterbank, shape (n_mels, n_fft//2+1).

    Bit-for-bit equivalent (to <1e-7) with openai-whisper's ``assets/mel_filters.npz``
    and ``librosa.filters.mel(sr, n_fft, n_mels)``.  Verified in
    ``docs/snapdragon/findings/ws_a_verify.py``.
    """
    n_freqs = n_fft // 2 + 1
    fftfreqs = np.linspace(0, sr / 2.0, n_freqs, dtype=np.float64)

    mel_min, mel_max = _hz_to_mel_slaney(0.0), _hz_to_mel_slaney(sr / 2.0)
    mel_pts = np.linspace(mel_min, mel_max, n_mels + 2)
    hz_pts = _mel_to_hz_slaney(mel_pts)

    fdiff = np.diff(hz_pts)
    ramps = hz_pts.reshape(-1, 1) - fftfreqs.reshape(1, -1)

    weights = np.zeros((n_mels, n_freqs), dtype=np.float64)
    for i in range(n_mels):
        lower = -ramps[i] / fdiff[i]
        upper = ramps[i + 2] / fdiff[i + 1]
        weights[i] = np.maximum(0.0, np.minimum(lower, upper))

    # Slaney normalisation: constant energy per filter
    enorm = 2.0 / (hz_pts[2 : n_mels + 2] - hz_pts[:n_mels])
    weights *= enorm[:, np.newaxis]
    return weights.astype(np.float32)


def log_mel_spectrogram(audio: np.ndarray, mel_filters: np.ndarray) -> np.ndarray:
    """float32 audio in [-1, 1] → (n_mels, 3000) Whisper log-mel features.

    Mirrors ``whisper.audio.log_mel_spectrogram`` / HF ``WhisperFeatureExtractor``:
    hann(400) periodic window, hop 160, centre-padded with reflect, power spectrum,
    log10, dynamic-range clamp at 8 dec, then ``(x + 4) / 4``.
    """
    if audio.shape[0] < N_SAMPLES:
        audio = np.pad(audio, (0, N_SAMPLES - audio.shape[0]))
    else:
        audio = audio[:N_SAMPLES]

    window = np.hanning(N_FFT + 1)[:-1].astype(np.float64)   # periodic hann
    padded = np.pad(audio.astype(np.float64), (N_FFT // 2, N_FFT // 2), mode="reflect")
    frames = np.lib.stride_tricks.sliding_window_view(padded, N_FFT)[::HOP_LENGTH]
    spec = np.fft.rfft(frames * window, axis=-1)
    magnitudes = (spec.real ** 2 + spec.imag ** 2)[:-1]      # drop the trailing frame

    mel = magnitudes @ mel_filters.T.astype(np.float64)      # (3000, n_mels)
    log_spec = np.log10(np.maximum(mel, 1e-10))
    log_spec = np.maximum(log_spec, log_spec.max() - 8.0)
    log_spec = (log_spec + 4.0) / 4.0
    return np.ascontiguousarray(log_spec.T, dtype=np.float32)  # (n_mels, 3000)


# ═══════════════════════════════════════════════════════════════════════════════
# Backend
# ═══════════════════════════════════════════════════════════════════════════════

class OnnxWhisperBackend(BaseBackend):
    """Whisper on onnxruntime — the native Windows-ARM64 lane."""

    def __init__(self, cfg: dict) -> None:
        super().__init__(cfg)
        import onnxruntime as ort
        from tokenizers import Tokenizer

        size = cfg.get("model", "base")
        self.model_dir = Path(cfg.get("model_dir") or DEFAULT_MODEL_DIR) / f"whisper-{size}"
        self.quantized = bool(cfg.get("quantized", False))
        self.max_new_tokens = int(cfg.get("max_new_tokens", 180))
        self.beam_size = max(1, int(cfg.get("beam_size", DEFAULT_BEAM_SIZE)))
        self.length_penalty = float(cfg.get("length_penalty", DEFAULT_LENGTH_PENALTY))
        self._lock = threading.Lock()

        if not (self.model_dir / "tokenizer.json").exists():
            self._download(size)

        suffix = "_quantized" if self.quantized else ""
        enc_path = self.model_dir / "onnx" / f"encoder_model{suffix}.onnx"
        dec_path = self.model_dir / "onnx" / f"decoder_model_merged{suffix}.onnx"
        for p in (enc_path, dec_path):
            if not p.exists():
                raise FileNotFoundError(f"missing ONNX file: {p}")

        so = ort.SessionOptions()
        so.log_severity_level = 3
        so.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
        if cfg.get("intra_op_threads"):
            so.intra_op_num_threads = int(cfg["intra_op_threads"])

        enc_providers = _resolve_providers(cfg.get("encoder_provider") or cfg.get("provider"))
        dec_providers = _resolve_providers(cfg.get("decoder_provider") or "cpu")

        self.encoder = ort.InferenceSession(str(enc_path), so, providers=enc_providers)
        self.decoder = ort.InferenceSession(str(dec_path), so, providers=dec_providers)
        self.encoder_provider = self.encoder.get_providers()[0]
        self.decoder_provider = self.decoder.get_providers()[0]

        self.tokenizer = Tokenizer.from_file(str(self.model_dir / "tokenizer.json"))
        self.config = json.loads((self.model_dir / "config.json").read_text(encoding="utf-8"))
        self.gen_config = json.loads(
            (self.model_dir / "generation_config.json").read_text(encoding="utf-8")
        )

        self.n_mels = int(self.config.get("num_mel_bins", 80))
        self.mel_filters = mel_filter_bank(self.n_mels)

        # Decoder cache geometry
        self.n_layers = int(self.config["decoder_layers"])
        self.n_heads = int(self.config["decoder_attention_heads"])
        self.head_dim = int(self.config["d_model"]) // self.n_heads

        gc = self.gen_config
        self.sot = int(gc.get("decoder_start_token_id", 50258))
        self.eot = int(gc.get("eos_token_id", 50257))
        self.no_timestamps = int(gc.get("no_timestamps_token_id", 50363))
        self.transcribe_tok = int(gc.get("task_to_id", {}).get("transcribe", 50359))
        self.lang_to_id = {k.strip("<|>"): int(v) for k, v in gc.get("lang_to_id", {}).items()}
        self.suppress = np.array(
            sorted(set(gc.get("suppress_tokens", [])) | {self.sot}), dtype=np.int64
        )
        self.begin_suppress = np.array(gc.get("begin_suppress_tokens", []), dtype=np.int64)
        # Never emit timestamp tokens — we force <|notimestamps|>
        self.timestamp_begin = self.no_timestamps + 1

        self._enc_out_name = self.encoder.get_outputs()[0].name
        self._dec_in_names = [i.name for i in self.decoder.get_inputs()]
        self._dec_out_names = [o.name for o in self.decoder.get_outputs()]

        log.info(
            "OnnxWhisperBackend: model=%s quantized=%s encoder_ep=%s decoder_ep=%s",
            size, self.quantized, self.encoder_provider, self.decoder_provider,
        )

    # ── model fetch ──────────────────────────────────────────────────────────
    def _download(self, size: str) -> None:
        repo = _REPO_FOR_SIZE.get(size)
        if repo is None:
            raise ValueError(f"no ONNX export mapped for model size {size!r}")
        q = "_quantized" if self.quantized else ""
        wanted = list(_HF_TOKENIZER_FILES) + [
            f"onnx/encoder_model{q}.onnx",
            f"onnx/decoder_model_merged{q}.onnx",
        ]
        for name in wanted:
            target = self.model_dir / name
            if not target.exists():
                hf_download(repo, name, target)

    # ── availability ─────────────────────────────────────────────────────────
    @classmethod
    def is_available(cls) -> bool:
        try:
            import onnxruntime  # noqa: F401
            import tokenizers   # noqa: F401
            return True
        except Exception:
            return False

    # ── the contract ─────────────────────────────────────────────────────────
    def transcribe(
        self,
        audio: np.ndarray,
        language: str | None = None,
        beam_size: int | None = None,
    ) -> str:
        if audio is None or len(audio) == 0:
            return ""
        if audio.dtype == np.int16:
            samples = audio.astype(np.float32) / 32768.0
        else:
            samples = np.asarray(audio, dtype=np.float32)

        beams = self.beam_size if beam_size is None else max(1, int(beam_size))

        with self._lock:
            features = log_mel_spectrogram(samples, self.mel_filters)[None]  # (1, mels, 3000)
            enc = self.encoder.run([self._enc_out_name], {"input_features": features})[0]
            if beams > 1:
                tokens = self._beam_decode(enc, language, beams)
            else:
                tokens = self._greedy_decode(enc, language)

        text = self.tokenizer.decode(tokens, skip_special_tokens=True)
        return text.strip()

    # ── decode loop ──────────────────────────────────────────────────────────
    def _prompt(self, language: str | None) -> list[int]:
        toks = [self.sot]
        lang_id = None
        if language and language != "auto":
            for cand in _LANG_ALIASES.get(language, [language]):
                if cand in self.lang_to_id:
                    lang_id = self.lang_to_id[cand]
                    break
        if lang_id is None:
            lang_id = self.lang_to_id.get("en", 50259)
        toks += [lang_id, self.transcribe_tok, self.no_timestamps]
        return toks

    def _empty_cache(self) -> dict[str, np.ndarray]:
        z = {}
        for layer in range(self.n_layers):
            for kind in ("decoder", "encoder"):
                for kv in ("key", "value"):
                    z[f"past_key_values.{layer}.{kind}.{kv}"] = np.zeros(
                        (1, self.n_heads, 0, self.head_dim), dtype=np.float32
                    )
        return z

    def _filter(self, logits: np.ndarray, at_start: bool) -> np.ndarray:
        """Mask tokens Whisper must never emit. Works on 1-D or [B, V] logits."""
        logits[..., self.suppress] = -np.inf
        logits[..., self.timestamp_begin:] = -np.inf     # no timestamps, ever
        if at_start and len(self.begin_suppress):
            logits[..., self.begin_suppress] = -np.inf
        return logits

    def _split_cache(self, named: dict) -> tuple[dict, dict]:
        """Split one decoder output into its (encoder cross, decoder self) caches.

        The cross-attention cache is a function of the audio alone, so it is
        computed once and reused unchanged; only the self-attention cache grows and
        needs reordering when beams are reshuffled.
        """
        enc, dec = {}, {}
        for i in range(self.n_layers):
            for kv in ("key", "value"):
                enc[f"past_key_values.{i}.encoder.{kv}"] = named[f"present.{i}.encoder.{kv}"]
                dec[f"past_key_values.{i}.decoder.{kv}"] = named[f"present.{i}.decoder.{kv}"]
        return enc, dec

    # ── beam search ──────────────────────────────────────────────────────────
    def _beam_decode(
        self, encoder_hidden: np.ndarray, language: str | None, beams: int
    ) -> list[int]:
        """Beam search with all beams batched into a single decoder call.

        This graph keeps a dynamic batch axis, so unlike the QNN export the beams
        fold into one run per step — the batch dimension *is* the beam dimension,
        and reshuffling beams is a row gather on the self-attention cache.
        """
        prompt = self._prompt(language)

        # Prompt pass at batch 1, since every beam shares this prefix.
        feeds = self._empty_cache()
        feeds["encoder_hidden_states"] = encoder_hidden
        feeds["input_ids"] = np.array([prompt], dtype=np.int64)
        feeds["use_cache_branch"] = np.array([False])
        named = dict(zip(self._dec_out_names, self.decoder.run(self._dec_out_names, feeds)))

        first_scores = log_softmax(
            self._filter(named["logits"][0, -1].astype(np.float32), at_start=True)
        )
        enc_cache, dec_cache = self._split_cache(named)

        return beam_search(
            first_scores,
            _OnnxBeamAdapter(self, encoder_hidden, enc_cache, dec_cache),
            eot=self.eot,
            beam_size=beams,
            max_new_tokens=self.max_new_tokens,
            length_penalty=self.length_penalty,
            start_index=len(prompt) - 1,
        )

    def _greedy_decode(self, encoder_hidden: np.ndarray, language: str | None) -> list[int]:
        prompt = self._prompt(language)
        generated: list[int] = []

        feeds = self._empty_cache()
        feeds["encoder_hidden_states"] = encoder_hidden
        feeds["input_ids"] = np.array([prompt], dtype=np.int64)
        feeds["use_cache_branch"] = np.array([False])

        enc_cache: dict[str, np.ndarray] = {}
        first = True

        for step in range(self.max_new_tokens):
            outs = self.decoder.run(self._dec_out_names, feeds)
            named = dict(zip(self._dec_out_names, outs))
            logits = self._filter(named["logits"][0, -1].astype(np.float32), at_start=first)
            next_tok = int(np.argmax(logits))
            if next_tok == self.eot:
                break
            generated.append(next_tok)

            if first:
                enc_cache = {
                    f"past_key_values.{i}.encoder.{kv}": named[f"present.{i}.encoder.{kv}"]
                    for i in range(self.n_layers)
                    for kv in ("key", "value")
                }
                first = False

            feeds = dict(enc_cache)
            for i in range(self.n_layers):
                for kv in ("key", "value"):
                    feeds[f"past_key_values.{i}.decoder.{kv}"] = named[f"present.{i}.decoder.{kv}"]
            feeds["encoder_hidden_states"] = encoder_hidden
            feeds["input_ids"] = np.array([[next_tok]], dtype=np.int64)
            feeds["use_cache_branch"] = np.array([True])

        return generated



class _OnnxBeamAdapter:
    """Advances every ONNX beam in one batched decoder call."""

    def __init__(self, backend: "OnnxWhisperBackend", encoder_hidden: np.ndarray,
                 enc_cache: dict, dec_cache: dict) -> None:
        self._b = backend
        self._hidden = encoder_hidden
        self._enc = enc_cache
        self._dec = dec_cache
        self._hidden_b = encoder_hidden
        self._enc_b: dict = enc_cache

    def expand(self, n: int) -> dict:
        # The cross-attention cache and the encoder states are a function of the
        # audio alone — identical across beams and never reordered — so they are
        # tiled once here and only ever sliced afterwards.
        self._hidden_b = np.repeat(self._hidden, n, axis=0)
        self._enc_b = {k: np.repeat(v, n, axis=0) for k, v in self._enc.items()}
        return {k: np.repeat(v, n, axis=0) for k, v in self._dec.items()}

    def step(self, last_tokens, state, index):
        n = len(last_tokens)
        feeds = {k: v[:n] for k, v in self._enc_b.items()}
        feeds.update(state)
        feeds["encoder_hidden_states"] = self._hidden_b[:n]
        feeds["input_ids"] = np.array([[t] for t in last_tokens], dtype=np.int64)
        feeds["use_cache_branch"] = np.array([True])

        out = self._b.decoder.run(self._b._dec_out_names, feeds)
        named = dict(zip(self._b._dec_out_names, out))
        scores = log_softmax(
            self._b._filter(named["logits"][:, -1].astype(np.float32), at_start=False)
        )
        _, dec = self._b._split_cache(named)
        return scores, dec

    def reorder(self, state, parents):
        idx = np.asarray(parents, dtype=np.int64)
        return {k: v[idx] for k, v in state.items()}

# ═══════════════════════════════════════════════════════════════════════════════
# Provider resolution (CPU / QNN-Hexagon)
# ═══════════════════════════════════════════════════════════════════════════════

def _resolve_providers(spec: str | None):
    """Map a friendly provider name to an onnxruntime provider list."""
    import onnxruntime as ort

    if spec in (None, "", "auto", "cpu"):
        return ["CPUExecutionProvider"]
    if spec in ("qnn", "npu", "htp"):
        register_qnn()
        avail = ort.get_available_providers()
        if "QNNExecutionProvider" in avail:
            return [
                ("QNNExecutionProvider", {"backend_path": _qnn_htp_path()}),
                "CPUExecutionProvider",
            ]
        log.warning("QNNExecutionProvider unavailable; falling back to CPU")
        return ["CPUExecutionProvider"]
    return [spec]


def _qnn_htp_path() -> str:
    try:
        import onnxruntime_qnn
        return onnxruntime_qnn.get_qnn_htp_path()
    except Exception:
        return "QnnHtp.dll"


_qnn_registered = False


def register_qnn() -> bool:
    """Register the onnxruntime-qnn plugin EP (ORT >= 2.x plugin-EP layout).

    Returns True if ``QNNExecutionProvider`` is available afterwards.
    """
    global _qnn_registered
    import onnxruntime as ort

    if "QNNExecutionProvider" in ort.get_available_providers():
        return True
    if _qnn_registered:
        return "QNNExecutionProvider" in ort.get_available_providers()
    try:
        import onnxruntime_qnn

        ort.register_execution_provider_library(
            onnxruntime_qnn.get_ep_name(), onnxruntime_qnn.get_library_path()
        )
        _qnn_registered = True
    except Exception as e:      # pragma: no cover - depends on ORT build
        log.debug("QNN EP registration failed: %s", e)
        return False
    return "QNNExecutionProvider" in ort.get_available_providers()


def qnn_npu_device():
    """Return the ``OrtEpDevice`` for the Hexagon NPU, or None."""
    import onnxruntime as ort

    if not register_qnn():
        return None
    for d in ort.get_ep_devices():
        if d.ep_name == "QNNExecutionProvider" and "NPU" in str(d.device.type):
            return d
    return None


# ═══════════════════════════════════════════════════════════════════════════════
# Qualcomm AI Hub precompiled-QNN backend (Hexagon NPU)
# ═══════════════════════════════════════════════════════════════════════════════

QAI_ASSET_URL = (
    "https://qaihub-public-assets.s3.us-west-2.amazonaws.com/qai-hub-models/models/"
    "{slug}/releases/v{version}/{slug}-precompiled_qnn_onnx-float-{chipset}.zip"
)

# Laolao model name → (Qualcomm AI Hub slug, HF repo that supplies the tokenizer)
_QAI_MODELS = {
    "base": ("whisper_base", "onnx-community/whisper-base"),
    "large-v3-turbo": ("whisper_large_v3_turbo", "onnx-community/whisper-large-v3-turbo"),
}
QAI_VERSION = os.environ.get("LAOLAO_QAI_VERSION", "0.59.0")


def detect_chipset() -> str:
    """Best-effort Snapdragon chipset slug for the Qualcomm AI Hub asset URL.

    ``platform.processor()`` only reports ``ARMv8 (64-bit) Family 8 Model 2 …`` on
    Windows-on-ARM, so read the marketing name out of the registry
    (``Snapdragon(R) X2 Elite - X2E88100 - Qualcomm Oryon(TM) CPU``).
    """
    name = ""
    try:
        import winreg

        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE,
                            r"HARDWARE\DESCRIPTION\System\CentralProcessor\0") as k:
            name = winreg.QueryValueEx(k, "ProcessorNameString")[0]
    except Exception:
        pass
    ident = f"{name} {os.environ.get('PROCESSOR_IDENTIFIER', '')}".lower()
    if "x2" in ident:
        return "qualcomm_snapdragon_x2_elite"
    return "qualcomm_snapdragon_x_elite"


class QnnWhisperBackend(BaseBackend):
    """Whisper on the Hexagon NPU via Qualcomm AI Hub's precompiled QNN ONNX export.

    The export is *not* HuggingFace-shaped.  Its contract, read off the ONNX graph:

        encoder(input_features fp16 [1, n_mels, 3000])
            -> k_cache_cross_i fp16 [H, 1, D, 1500], v_cache_cross_i fp16 [H, 1, 1500, D]

        decoder(input_ids int32 [1,1], attention_mask fp16 [1,1,1,S],
                k/v_cache_self_i_in, k/v_cache_cross_i, position_ids int32 [1])
            -> logits fp16 [1, V, 1, 1], k/v_cache_self_i_out

    The self-attention cache is a fixed-length **left-padded shift register** of length
    ``S - 1``; the attention mask un-masks only its last ``index + 1`` slots.  All of the
    geometry (layers, heads, head-dim, S, n_mels) is read from the graph at load time, so
    the same code drives whisper-base and whisper-large-v3-turbo.
    """

    NEG = np.float16(-65504.0)

    def __init__(self, cfg: dict) -> None:
        super().__init__(cfg)
        import onnxruntime as ort
        from tokenizers import Tokenizer

        size = cfg.get("model", "base")
        if size not in _QAI_MODELS:
            raise ValueError(
                f"no Qualcomm AI Hub precompiled QNN export for model {size!r}; "
                f"available: {sorted(_QAI_MODELS)}"
            )
        slug, hf_repo = _QAI_MODELS[size]
        self.chipset = cfg.get("chipset") or detect_chipset()
        self.max_new_tokens = int(cfg.get("max_new_tokens", 180))
        self.beam_size = max(1, int(cfg.get("beam_size", DEFAULT_BEAM_SIZE)))
        self.length_penalty = float(cfg.get("length_penalty", DEFAULT_LENGTH_PENALTY))
        self._lock = threading.Lock()

        root = Path(cfg.get("model_dir") or DEFAULT_MODEL_DIR)
        self.qai_dir = root / f"qai-{slug}-{self.chipset}"
        if not (self.qai_dir / "encoder.onnx").exists():
            self._download_qai(slug)

        dev = qnn_npu_device()
        if dev is None:
            raise RuntimeError("QNNExecutionProvider / NPU device not available")

        def _sess(path: Path):
            so = ort.SessionOptions()
            so.log_severity_level = 3
            so.add_provider_for_devices([dev], {"htp_performance_mode": cfg.get("htp_mode", "burst")})
            return ort.InferenceSession(str(path), so)

        self.encoder = _sess(self.qai_dir / "encoder.onnx")
        self.decoder = _sess(self.qai_dir / "decoder.onnx")
        if "QNNExecutionProvider" not in self.encoder.get_providers():
            raise RuntimeError(f"encoder did not bind to QNN: {self.encoder.get_providers()}")

        # ── read the geometry off the graph ────────────────────────────────
        enc_in = self.encoder.get_inputs()[0]
        self.n_mels = int(enc_in.shape[1])
        self.enc_out_names = [o.name for o in self.encoder.get_outputs()]
        self.dec_out_names = [o.name for o in self.decoder.get_outputs()]
        dec_in = {i.name: i for i in self.decoder.get_inputs()}
        self.n_layers = sum(1 for n in dec_in if n.startswith("k_cache_self_") and n.endswith("_in"))
        k0 = dec_in["k_cache_self_0_in"].shape         # [H, 1, D, S-1]
        self.n_heads, self.head_dim = int(k0[0]), int(k0[2])
        self.seq_len = int(dec_in["attention_mask"].shape[3])

        self.mel_filters = mel_filter_bank(self.n_mels)

        # ── tokenizer + special tokens from the matching HF repo ───────────
        self.hf_dir = root / f"whisper-{size}"
        for name in _HF_TOKENIZER_FILES:
            if not (self.hf_dir / name).exists():
                hf_download(hf_repo, name, self.hf_dir / name)
        self.tokenizer = Tokenizer.from_file(str(self.hf_dir / "tokenizer.json"))
        gc = json.loads((self.hf_dir / "generation_config.json").read_text(encoding="utf-8"))
        self.sot = int(gc["decoder_start_token_id"])
        self.eot = int(gc["eos_token_id"])
        self.no_timestamps = int(gc["no_timestamps_token_id"])
        self.transcribe_tok = int(gc["task_to_id"]["transcribe"])
        self.lang_to_id = {k.strip("<|>"): int(v) for k, v in gc["lang_to_id"].items()}
        self.suppress = np.array(sorted(set(gc.get("suppress_tokens", [])) | {self.sot}),
                                 dtype=np.int64)
        self.begin_suppress = np.array(gc.get("begin_suppress_tokens", []), dtype=np.int64)

        log.info(
            "QnnWhisperBackend: model=%s chipset=%s layers=%d heads=%d dim=%d "
            "seq=%d n_mels=%d ep=%s",
            size, self.chipset, self.n_layers, self.n_heads, self.head_dim,
            self.seq_len, self.n_mels, self.encoder.get_providers()[0],
        )

    # ── asset fetch ──────────────────────────────────────────────────────────
    def _download_qai(self, slug: str) -> None:
        import shutil
        import urllib.request
        import zipfile

        url = QAI_ASSET_URL.format(slug=slug, version=QAI_VERSION, chipset=self.chipset)
        self.qai_dir.mkdir(parents=True, exist_ok=True)
        zip_path = self.qai_dir / "asset.zip"
        log.info("Downloading Qualcomm AI Hub asset %s", url)
        urllib.request.urlretrieve(url, zip_path)
        with zipfile.ZipFile(zip_path) as z:
            z.extractall(self.qai_dir)
        zip_path.unlink(missing_ok=True)
        # the zip contains a single top-level directory — flatten it
        for child in list(self.qai_dir.iterdir()):
            if child.is_dir():
                for f in child.iterdir():
                    shutil.move(str(f), str(self.qai_dir / f.name))
                child.rmdir()

    @classmethod
    def is_available(cls) -> bool:
        try:
            import tokenizers  # noqa: F401
            return qnn_npu_device() is not None
        except Exception:
            return False

    # ── the contract ─────────────────────────────────────────────────────────
    def transcribe(
        self,
        audio: np.ndarray,
        language: str | None = None,
        beam_size: int | None = None,
    ) -> str:
        if audio is None or len(audio) == 0:
            return ""
        samples = (audio.astype(np.float32) / 32768.0) if audio.dtype == np.int16 \
            else np.asarray(audio, dtype=np.float32)

        beams = self.beam_size if beam_size is None else max(1, int(beam_size))

        with self._lock:
            feats = log_mel_spectrogram(samples, self.mel_filters)[None].astype(np.float16)
            cross = dict(zip(self.enc_out_names,
                             self.encoder.run(None, {"input_features": feats})))
            if beams > 1:
                tokens = self._beam_decode(cross, language, beams)
            else:
                tokens = self._greedy_decode(cross, language)
        return self.tokenizer.decode(tokens, skip_special_tokens=True).strip()

    def _prompt(self, language: str | None) -> list[int]:
        lang_id = None
        if language and language != "auto":
            for cand in _LANG_ALIASES.get(language, [language]):
                if cand in self.lang_to_id:
                    lang_id = self.lang_to_id[cand]
                    break
        if lang_id is None:
            lang_id = self.lang_to_id.get("en", 50259)
        return [self.sot, lang_id, self.transcribe_tok, self.no_timestamps]

    def _empty_self_cache(self) -> tuple[list[np.ndarray], list[np.ndarray]]:
        S, L, H, D = self.seq_len, self.n_layers, self.n_heads, self.head_dim
        k = [np.zeros((H, 1, D, S - 1), np.float16) for _ in range(L)]
        v = [np.zeros((H, 1, S - 1, D), np.float16) for _ in range(L)]
        return k, v

    def _step(
        self,
        cross: dict,
        token: int,
        index: int,
        k_self: list[np.ndarray],
        v_self: list[np.ndarray],
    ) -> tuple[np.ndarray, list[np.ndarray], list[np.ndarray]]:
        """Run one decoder position and return (raw logits, new k cache, new v cache).

        The caches are returned, never mutated in place — which is what makes beam
        search cheap here.  Several candidate tokens can descend from one parent
        beam and simply share a reference to that parent's cache, so a decode step
        costs one NPU call per *live beam*, not per candidate.
        """
        S, L = self.seq_len, self.n_layers
        mask = np.full((1, 1, 1, S), self.NEG, np.float16)
        mask[:, :, :, S - 1 - index:] = np.float16(0)

        feeds = dict(cross)
        for i in range(L):
            feeds[f"k_cache_self_{i}_in"] = k_self[i]
            feeds[f"v_cache_self_{i}_in"] = v_self[i]
        feeds["input_ids"] = np.array([[token]], np.int32)
        feeds["attention_mask"] = mask
        feeds["position_ids"] = np.array([index], np.int32)

        res = dict(zip(self.dec_out_names, self.decoder.run(None, feeds)))
        k_out = [res[f"k_cache_self_{i}_out"] for i in range(L)]
        v_out = [res[f"v_cache_self_{i}_out"] for i in range(L)]
        return res["logits"].reshape(-1).astype(np.float32), k_out, v_out

    def _filter(self, logits: np.ndarray, at_start: bool) -> np.ndarray:
        """Mask out tokens Whisper must never emit here (in place, on a copy)."""
        logits[self.suppress] = -np.inf
        logits[self.no_timestamps + 1:] = -np.inf      # no timestamps, ever
        if at_start and len(self.begin_suppress):
            logits[self.begin_suppress] = -np.inf
        return logits

    def _greedy_decode(self, cross: dict, language: str | None) -> list[int]:
        k_self, v_self = self._empty_self_cache()
        prompt = self._prompt(language)
        out: list[int] = []
        x = prompt[0]
        limit = min(self.seq_len, len(prompt) + self.max_new_tokens)

        for index in range(limit):
            logits, k_self, v_self = self._step(cross, x, index, k_self, v_self)

            if index + 1 < len(prompt):          # still feeding the forced prompt
                x = prompt[index + 1]
                continue

            nxt = int(np.argmax(self._filter(logits, at_start=not out)))
            if nxt == self.eot:
                break
            out.append(nxt)
            x = nxt
        return out

    # ── beam search ──────────────────────────────────────────────────────────
    def _beam_decode(self, cross: dict, language: str | None, beams: int) -> list[int]:
        """Beam search over the fixed-shape QNN decoder.

        The compiled QNN graph is batch-1 with static shapes, so unlike an ONNX
        Runtime graph with a dynamic batch axis the beams cannot be folded into one
        call — each live beam costs its own NPU invocation per step.  That is
        affordable specifically because this is large-v3-turbo: the turbo
        distillation cut the decoder from 32 layers to 4, so nearly all of the
        per-utterance cost sits in the encoder, which still runs exactly once.

        All the bookkeeping lives in backends/beam_search.py; this only says how a
        QNN beam advances and how beams are reshuffled.
        """
        prompt = self._prompt(language)

        # Prime the forced prompt once — every beam descends from that one cache.
        k_self, v_self = self._empty_self_cache()
        logits = np.zeros(1, np.float32)
        for index, tok in enumerate(prompt):
            logits, k_self, v_self = self._step(cross, tok, index, k_self, v_self)

        first_scores = log_softmax(self._filter(logits, at_start=True))

        return beam_search(
            first_scores,
            _QnnBeamAdapter(self, cross, k_self, v_self),
            eot=self.eot,
            beam_size=beams,
            max_new_tokens=self.max_new_tokens,
            length_penalty=self.length_penalty,
            start_index=len(prompt) - 1,
            max_index=self.seq_len - 1,
        )


class _QnnBeamAdapter:
    """Advances QNN beams one decoder call at a time (the graph is batch-1)."""

    def __init__(self, backend: "QnnWhisperBackend", cross: dict,
                 k0: list[np.ndarray], v0: list[np.ndarray]) -> None:
        self._b = backend
        self._cross = cross
        self._primed = (k0, v0)

    def expand(self, n: int) -> list[tuple[list, list]]:
        # The caches are never mutated in place, so n beams can share one object
        # until they actually diverge — no copying needed here.
        return [self._primed] * n

    def step(self, last_tokens, state, index):
        scores, new_state = [], []
        for token, (k, v) in zip(last_tokens, state):
            logits, nk, nv = self._b._step(self._cross, token, index, k, v)
            scores.append(log_softmax(self._b._filter(logits, at_start=False)))
            new_state.append((nk, nv))
        return np.stack(scores), new_state

    def reorder(self, state, parents):
        return [state[p] for p in parents]


# ═══════════════════════════════════════════════════════════════════════════════
# Factory — NPU first, CPU fallback
# ═══════════════════════════════════════════════════════════════════════════════

def get_onnx_whisper_backend(cfg: dict) -> BaseBackend:
    """Return the fastest working ONNX Whisper backend for this machine.

    ``cfg['device']``: ``auto`` (default) tries QNN/NPU then CPU; ``qnn``/``npu`` forces
    the NPU; ``cpu`` forces the portable CPU path.
    """
    device = cfg.get("device", "auto")
    if device in ("auto", "qnn", "npu", "hexagon"):
        try:
            return QnnWhisperBackend(cfg)
        except Exception as e:
            if device != "auto":
                raise
            log.info("QNN Whisper backend unavailable (%s); falling back to CPU EP", e)
    cpu_cfg = {**cfg, "intra_op_threads": cfg.get("intra_op_threads", 3)}
    return OnnxWhisperBackend(cpu_cfg)
