"""Beam search for mlx-whisper on Apple Silicon.

Why this exists
---------------
``mlx_whisper.transcribe(..., beam_size=N)`` does not work: mlx-whisper's decoder
raises ``NotImplementedError("Beam search decoder is not yet implemented")``. So
asking for beam search on a Mac is not a matter of passing an argument — the
search has to be run here.

It is run through exactly the same driver as the Snapdragon NPU and the portable
ONNX lane (``backends.beam_search``); only the adapter differs, because the shape
of a decoder call differs. mlx-whisper's ``TextDecoder`` takes a batch and a KV
cache, so the beams batch into one call and reordering them is a row gather —
the same structure as the ONNX adapter.

Laolao never feeds more than ``rolling_window_s`` of audio (5 s by default), so a
single 30 s Whisper window always covers the whole utterance. That is what lets
this skip mlx-whisper's segment loop, timestamp rules and temperature-fallback
ladder, none of which apply to a window that is transcribed whole and thrown away
a fraction of a second later.
"""

from __future__ import annotations

import logging
from typing import Any

import numpy as np

from backends.beam_search import beam_search, log_softmax

log = logging.getLogger("laolao.backends.mlx.beam")


class MlxBeamDecoder:
    """Holds the loaded MLX model and runs beam search over it.

    Constructed lazily by ``MLXBackend`` on the first beam-search call, so a
    greedy-only configuration never pays for importing mlx or loading a second
    copy of the weights.
    """

    def __init__(self, hf_repo: str) -> None:
        import mlx.core as mx
        from mlx_whisper.load_models import load_model

        self._mx = mx
        self.model = load_model(hf_repo)
        self.n_mels = self.model.dims.n_mels
        self.multilingual = self.model.is_multilingual
        self.num_languages = self.model.num_languages
        self._tokenizers: dict[str | None, Any] = {}

    # ── tokenizer, one per language ──────────────────────────────────────────
    def tokenizer(self, language: str | None):
        if language not in self._tokenizers:
            from mlx_whisper.tokenizer import get_tokenizer

            self._tokenizers[language] = get_tokenizer(
                self.multilingual,
                num_languages=self.num_languages,
                language=language or "en",
                task="transcribe",
            )
        return self._tokenizers[language]

    def _suppressed(self, tok) -> np.ndarray:
        """Tokens Whisper must never emit, matching mlx-whisper's own defaults.

        ``SuppressTokens`` there masks the non-speech set plus the three
        start-of-transcript markers; we add the timestamp range because Laolao
        always forces ``<|notimestamps|>`` and a stray timestamp token would be
        rendered into the caption as literal text.
        """
        ids = set(tok.non_speech_tokens)
        ids.update({tok.sot, tok.sot_prev, tok.sot_lm})
        if tok.no_speech is not None:
            ids.add(tok.no_speech)
        return np.array(sorted(ids), dtype=np.int64)

    # ── the entry point ──────────────────────────────────────────────────────
    def transcribe(
        self,
        audio_f32: np.ndarray,
        language: str | None,
        beam_size: int,
        length_penalty: float = 1.0,
        max_new_tokens: int = 180,
    ) -> str:
        import mlx.core as mx
        from mlx_whisper.audio import log_mel_spectrogram, pad_or_trim, N_FRAMES

        tok = self.tokenizer(language)
        suppress = self._suppressed(tok)
        # Whisper is trained never to open with a space or an immediate EOT;
        # mlx-whisper enforces the same thing via SuppressBlank.
        begin_suppress = np.array(
            sorted({tok.encode(" ")[0], tok.eot}), dtype=np.int64
        )
        timestamp_begin = tok.timestamp_begin

        mel = log_mel_spectrogram(audio_f32, n_mels=self.n_mels, padding=0)
        mel = pad_or_trim(mel, N_FRAMES, axis=-2).astype(mx.float32)[None]
        audio_features = self.model.encoder(mel)

        prompt = list(tok.sot_sequence_including_notimestamps)

        def mask(logits: np.ndarray, at_start: bool) -> np.ndarray:
            logits[..., suppress] = -np.inf
            logits[..., timestamp_begin:] = -np.inf
            if at_start:
                logits[..., begin_suppress] = -np.inf
            return logits

        # ── prime the shared prompt once, at batch 1 ──────────────────────
        logits, cache, _ = self.model.decoder(
            mx.array([prompt]), audio_features, kv_cache=None
        )
        mx.eval(logits, cache)
        first = log_softmax(mask(np.array(logits[0, -1], copy=True).astype(np.float32),
                                 at_start=True))

        adapter = _MlxBeamAdapter(self, audio_features, cache, mask)
        tokens = beam_search(
            first,
            adapter,
            eot=tok.eot,
            beam_size=beam_size,
            max_new_tokens=max_new_tokens,
            length_penalty=length_penalty,
            start_index=len(prompt) - 1,
        )
        return tok.decode(tokens).strip()


class _MlxBeamAdapter:
    """Advances every MLX beam in one batched decoder call."""

    def __init__(self, owner: MlxBeamDecoder, audio_features, cache, mask) -> None:
        self._o = owner
        self._mx = owner._mx
        self._features = audio_features
        self._cache = cache
        self._mask = mask
        self._features_b = audio_features

    def _tile(self, arr, n: int):
        mx = self._mx
        return mx.repeat(arr, n, axis=0)

    def expand(self, n: int):
        # The encoder output and the cross-attention cache are functions of the
        # audio alone — identical for every beam and never reordered — so they
        # are tiled once here and only ever sliced afterwards.
        self._features_b = self._tile(self._features, n)
        return [
            (
                (self._tile(kv[0], n), self._tile(kv[1], n)),
                (self._tile(ck[0], n), self._tile(ck[1], n)),
            )
            for kv, ck in self._cache
        ]

    def step(self, last_tokens, state, index):
        mx = self._mx
        x = mx.array([[int(t)] for t in last_tokens])
        logits, cache, _ = self._o.model.decoder(
            x, self._features_b[: len(last_tokens)], kv_cache=state
        )
        mx.eval(logits, cache)
        raw = np.array(logits[:, -1], copy=True).astype(np.float32)
        return log_softmax(self._mask(raw, at_start=False)), cache

    def reorder(self, state, parents):
        mx = self._mx
        idx = mx.array(list(parents))
        # Only the self-attention cache is per-beam; the cross-attention half is
        # the same audio for every beam, so gathering it would be pure copying.
        return [
            ((kv[0][idx], kv[1][idx]), ck)
            for kv, ck in state
        ]
